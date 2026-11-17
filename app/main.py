"""API y página web."""
import base64
import json
import secrets
import threading
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import db, summary
from .config import settings
from .sync import last_run, manager

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Bitácora de commits")


@app.on_event("startup")
def _startup():
    db.init()
    with db.session() as conn:
        empty = not conn.execute("SELECT 1 FROM repo_clients UNION SELECT 1 FROM repo_tags "
                                 "UNION SELECT 1 FROM pending_meta LIMIT 1").fetchone()
        if empty and settings.meta_seed.exists():
            db.import_meta(conn, json.loads(settings.meta_seed.read_text()))
    if settings.auto_sync_hours > 0:
        def loop():
            while True:
                manager.start()
                time.sleep(settings.auto_sync_hours * 3600)
        threading.Thread(target=loop, daemon=True).start()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if settings.basic_auth_user and request.url.path != "/healthz":
        expected = base64.b64encode(f"{settings.basic_auth_user}:{settings.basic_auth_password}".encode()).decode()
        got = request.headers.get("authorization", "").removeprefix("Basic ").strip()
        if not secrets.compare_digest(got, expected):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="bitacora"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


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
        repos = [dict(r) for r in conn.execute("SELECT * FROM repos WHERE gone=0")]
        q = "SELECT repo_id, month, COUNT(*) n, MAX(local_date) last FROM commits"
        q += "" if bots else " WHERE is_bot=0"
        counts: dict[int, dict] = {}
        for r in conn.execute(q + " GROUP BY repo_id, month"):
            c = counts.setdefault(r["repo_id"], {"months": {}, "total": 0, "last": ""})
            c["months"][r["month"]] = r["n"]
            c["total"] += r["n"]
            c["last"] = max(c["last"], r["last"])
        last_commit = conn.execute("SELECT MAX(local_date) FROM commits").fetchone()[0]
    end = max(last_commit or "", date.today().isoformat())[:7]
    out = []
    for r in repos:
        c = counts.get(r["id"])
        if not c:
            continue
        out.append({"id": r["id"], "name": r["name"], "full_name": r["full_name"], "owner": r["owner"],
                    "url": r["html_url"], "private": bool(r["private"]), "created": (r["created_at"] or "")[:10],
                    "clients": meta.get(r["id"], {}).get("clients", []),
                    "tags": meta.get(r["id"], {}).get("tags", []),
                    "months": c["months"], "total": c["total"], "last": c["last"], "error": r["last_error"]})
    return {"months": _months(settings.since[:7], end), "repos": out, "last_commit": last_commit,
            "since": settings.since, "timezone": settings.timezone}


@app.get("/api/repos/{repo_id}/commits")
def repo_commits(repo_id: int, bots: bool = False):
    with db.session() as conn:
        repo = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
        if not repo:
            raise HTTPException(404, "Repositorio no encontrado")
        q = ("SELECT sha, local_date, local_time, month, author_name, is_bot, message FROM commits "
             "WHERE repo_id=?" + ("" if bots else " AND is_bot=0") + " ORDER BY local_date, local_time")
        rows = [dict(r) for r in conn.execute(q, (repo_id,))]
    return {"repo": dict(repo), "commits": rows}


class MetaIn(BaseModel):
    clients: list[str] = []
    tags: list[str] = []


@app.put("/api/repos/{repo_id}/meta")
def put_meta(repo_id: int, body: MetaIn):
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM repos WHERE id=?", (repo_id,)).fetchone():
            raise HTTPException(404, "Repositorio no encontrado")
        return db.set_meta(conn, repo_id, body.clients, body.tags)


@app.get("/api/meta/options")
def meta_options():
    with db.session() as conn:
        clients = [r[0] for r in conn.execute("SELECT DISTINCT client FROM repo_clients ORDER BY client")]
        tags = [r[0] for r in conn.execute("SELECT DISTINCT tag FROM repo_tags ORDER BY tag")]
    return {"clients": clients, "tags": tags}


@app.get("/api/meta/export")
def meta_export():
    with db.session() as conn:
        return db.export_meta(conn)


@app.post("/api/sync")
def sync_start(force: bool = False):
    started = manager.start(force=force)
    return JSONResponse({"started": started, "status": manager.status}, status_code=202 if started else 409)


@app.get("/api/sync")
def sync_status():
    st = dict(manager.status)
    if not st.get("last"):
        st["last"] = last_run()
    return st


@app.get("/api/summary")
def get_summary(month_from: str | None = None, month_to: str | None = None, client: str | None = None,
                tag: str | None = None, repo: str | None = None, bots: bool = False, format: str = "json",
                messages: bool = True):
    f = summary.Filters(month_from or None, month_to or None, client or None, tag or None, repo or None, bots)
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
