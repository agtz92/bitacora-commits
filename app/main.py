"""API web (protegida con contraseña) y endpoint MCP (protegido con URL secreta)."""
import hashlib
import hmac
import secrets
import time
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import db, mcp, summary, sync
from .config import settings

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Bitácora de commits", docs_url=None, redoc_url=None, openapi_url=None)

COOKIE = "bitacora_session"
SESSION_DAYS = 30
# Solo /api/* requiere sesión; /mcp/<secreto> se valida aparte y lo demás responde 404 normal.
OPEN_API = {"/api/login", "/api/logout", "/api/session"}


# ---- sesión ---------------------------------------------------------------

def _sign(exp: int) -> str:
    key = (settings.session_secret or "").encode()
    pw = hashlib.sha256(settings.app_password.encode()).hexdigest()  # cambiar la contraseña invalida sesiones
    return hmac.new(key, f"{exp}:{pw}".encode(), hashlib.sha256).hexdigest()


def _valid_session(token: str | None) -> bool:
    if not token or not settings.session_secret or not settings.app_password:
        return False
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sig, _sign(exp))


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    if not path.startswith("/api/") or path in OPEN_API:
        return await call_next(request)
    if not _valid_session(request.cookies.get(COOKIE)):
        return JSONResponse({"detail": "Inicia sesión"}, status_code=401)
    return await call_next(request)


class LoginIn(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    if not settings.app_password or not settings.session_secret:
        raise HTTPException(503, "Falta configurar APP_PASSWORD y SESSION_SECRET en Vercel")
    if not hmac.compare_digest(body.password.encode(), settings.app_password.encode()):
        time.sleep(1.0)
        raise HTTPException(401, "Contraseña incorrecta")
    exp = int(time.time()) + SESSION_DAYS * 86400
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=SESSION_DAYS * 86400, httponly=True,
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                    samesite="lax", path="/")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/session")
def session_state(request: Request):
    return {"authenticated": _valid_session(request.cookies.get(COOKIE)),
            "configured": bool(settings.app_password and settings.session_secret),
            "cookie_received": COOKIE in request.cookies}


@app.get("/healthz")
def healthz():
    try:
        with db.session() as conn:
            conn.execute("select 1 from bitacora.repos limit 1")
        return {"ok": True, "db": "ok"}
    except Exception as e:  # noqa: BLE001
        url = settings.database_url
        info = {"ok": False, "db": type(e).__name__,
                # Pistas sin exponer la contraseña:
                "url_set": bool(url),
                "url_scheme_ok": url.startswith(("postgresql://", "postgres://")),
                "url_has_spaces": any(ch.isspace() for ch in url),
                "url_port": url.rsplit(":", 1)[-1].split("/")[0] if "@" in url else None,
                "hint": _db_hint(e)}
        return JSONResponse(info, status_code=503)


def _db_hint(e: Exception) -> str:
    msg = str(e).lower()
    if "invalid dsn" in msg or "invalid connection option" in msg or "missing" in msg and "=" in msg:
        return "DATABASE_URL no tiene el formato postgresql://usuario:contraseña@host:6543/postgres"
    if "password authentication failed" in msg:
        return "Usuario o contraseña incorrectos"
    if "tenant or user not found" in msg:
        return "El host del pooler no corresponde al proyecto (prueba aws-0 en lugar de aws-1)"
    if "does not exist" in msg:
        return "Falta crear el schema o las tablas"
    if "permission denied" in msg:
        return "El usuario no tiene permisos sobre el schema bitacora"
    return type(e).__name__


@app.get("/")
@app.get("/index.html")
def index():
    return FileResponse(ROOT / "public" / "index.html")


# ---- datos ----------------------------------------------------------------

def _months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


@app.get("/api/matrix")
def matrix(bots: bool = False):
    with db.session() as conn:
        meta = db.get_meta(conn)
        repos = conn.execute("select * from bitacora.repos where not gone").fetchall()
        rows = conn.execute(
            "select repo_id, month, count(*) n, max(local_date) last from bitacora.commits "
            "where %s or not is_bot group by repo_id, month", (bots,)).fetchall()
        last_commit = conn.execute("select max(local_date) d from bitacora.commits").fetchone()["d"]
        client_projects = db.client_projects(conn)
    counts: dict[int, dict] = {}
    for r in rows:
        c = counts.setdefault(r["repo_id"], {"months": {}, "total": 0, "last": ""})
        c["months"][r["month"]] = r["n"]
        c["total"] += r["n"]
        c["last"] = max(c["last"], r["last"].isoformat())
    tz = ZoneInfo(settings.timezone)
    end = max(last_commit.isoformat() if last_commit else "", date.today().isoformat())[:7]
    out = []
    for r in repos:
        c = counts.get(r["id"])
        if not c:
            continue
        out.append({"id": r["id"], "name": r["name"], "full_name": r["full_name"], "owner": r["owner"],
                    "url": r["html_url"], "private": r["private"],
                    "created": r["created_at"].astimezone(tz).date().isoformat() if r["created_at"] else "",
                    "clients": meta.get(r["id"], {}).get("clients", []),
                    "tags": meta.get(r["id"], {}).get("tags", []),
                    "projects": meta.get(r["id"], {}).get("projects", {}),
                    "months": c["months"], "total": c["total"], "last": c["last"], "error": r["last_error"]})
    return {"months": _months(settings.since[:7], end), "repos": out, "client_projects": client_projects,
            "last_commit": last_commit.isoformat() if last_commit else None,
            "since": settings.since, "timezone": settings.timezone}


@app.get("/api/repos/{repo_id}/commits")
def repo_commits(repo_id: int, bots: bool = False):
    with db.session() as conn:
        repo = conn.execute("select id, full_name, html_url from bitacora.repos where id=%s", (repo_id,)).fetchone()
        if not repo:
            raise HTTPException(404, "Repositorio no encontrado")
        rows = conn.execute(
            "select sha, local_date, local_time, month, author_name, is_bot, message from bitacora.commits "
            "where repo_id=%s and (%s or not is_bot) order by local_date, local_time", (repo_id, bots)).fetchall()
    for r in rows:
        r["local_date"] = r["local_date"].isoformat()
    return {"repo": repo, "commits": rows}


class MetaIn(BaseModel):
    clients: list[str] = []
    tags: list[str] = []
    projects: dict[str, str] | None = None   # {cliente: proyecto}; None = conservar los actuales


@app.put("/api/repos/{repo_id}/meta")
def put_meta(repo_id: int, body: MetaIn):
    with db.session() as conn:
        if not conn.execute("select 1 from bitacora.repos where id=%s", (repo_id,)).fetchone():
            raise HTTPException(404, "Repositorio no encontrado")
        return db.set_meta(conn, repo_id, body.clients, body.tags, body.projects)


@app.get("/api/meta/export")
def meta_export():
    with db.session() as conn:
        return db.export_meta(conn)


@app.post("/api/meta/import")
async def meta_import(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "Se espera un objeto {repo: {clients, tags, projects}}")
    with db.session() as conn:
        return db.import_meta(conn, data, overwrite=True)


@app.post("/api/sync/start")
def sync_start(force: bool = False):
    return sync.start(force)


@app.post("/api/sync/step")
def sync_step():
    return sync.step()


@app.get("/api/sync")
def sync_status():
    return sync.status()


@app.get("/api/summary")
def get_summary(month_from: str | None = None, month_to: str | None = None, client: str | None = None,
                tag: str | None = None, repo: str | None = None, bots: bool = False, format: str = "json",
                messages: bool = True, project: str | None = None):
    f = summary.Filters(month_from or None, month_to or None, client or None, tag or None, repo or None, bots,
                        (project or None) if client else None)
    with db.session() as conn:
        data = summary.query(conn, f)
    stamp = f"{f.month_from or 'inicio'}_{f.month_to or 'hoy'}"
    if format == "md":
        return PlainTextResponse(summary.to_markdown(data, messages), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="resumen_{stamp}.md"'})
    if format == "csv":
        return Response("﻿" + summary.to_csv(data), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="commits_{stamp}.csv"'})
    if format == "mdtext":
        return PlainTextResponse(summary.to_markdown(data, messages))
    return data


# ---- MCP --------------------------------------------------------------------

def _mcp_ok(secret: str) -> bool:
    return bool(settings.mcp_secret) and secrets.compare_digest(secret.encode(), settings.mcp_secret.encode())


@app.post("/mcp/{secret}")
async def mcp_post(secret: str, request: Request):
    if not _mcp_ok(secret):
        return Response(status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}},
                            status_code=400)
    if isinstance(body, list):
        out = [r for r in (mcp.handle(m) for m in body) if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    res = mcp.handle(body)
    return JSONResponse(res) if res is not None else Response(status_code=202)


@app.get("/mcp/{secret}")
def mcp_get(secret: str):
    # Sin stream de servidor: el cliente usa solo POST.
    return Response(status_code=405 if _mcp_ok(secret) else 404, headers={"Allow": "POST"})


@app.delete("/mcp/{secret}")
def mcp_delete(secret: str):
    return Response(status_code=405 if _mcp_ok(secret) else 404)
