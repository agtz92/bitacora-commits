"""Acceso a Postgres. Todas las tablas viven en el schema "bitacora"."""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings


@contextmanager
def session():
    if not settings.database_url:
        raise RuntimeError("Falta DATABASE_URL")
    # prepare_threshold=None: compatible con el pooler de Supabase en modo transacción.
    conn = psycopg.connect(settings.database_url, row_factory=dict_row, prepare_threshold=None,
                           connect_timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- clientes y etiquetas -------------------------------------------------

def norm_clients(values) -> list[str]:
    out = []
    for v in values or []:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def norm_tags(values) -> list[str]:
    out = []
    for v in values or []:
        v = str(v).strip().lower()
        if v and v not in out:
            out.append(v)
    return out


def set_meta(conn, repo_id: int, clients, tags) -> dict:
    clients, tags = norm_clients(clients), norm_tags(tags)
    with conn.cursor() as cur:
        cur.execute("delete from bitacora.repo_clients where repo_id=%s", (repo_id,))
        cur.execute("delete from bitacora.repo_tags where repo_id=%s", (repo_id,))
        if clients:
            cur.executemany("insert into bitacora.repo_clients (repo_id, client, pos) values (%s,%s,%s)",
                            [(repo_id, c, i) for i, c in enumerate(clients)])
        if tags:
            cur.executemany("insert into bitacora.repo_tags (repo_id, tag, pos) values (%s,%s,%s)",
                            [(repo_id, t, i) for i, t in enumerate(tags)])
    return {"clients": clients, "tags": tags}


def get_meta(conn) -> dict[int, dict]:
    meta: dict[int, dict] = {}
    for r in conn.execute("select repo_id, client from bitacora.repo_clients order by repo_id, pos"):
        meta.setdefault(r["repo_id"], {"clients": [], "tags": []})["clients"].append(r["client"])
    for r in conn.execute("select repo_id, tag from bitacora.repo_tags order by repo_id, pos"):
        meta.setdefault(r["repo_id"], {"clients": [], "tags": []})["tags"].append(r["tag"])
    return meta


def find_repo(conn, name: str):
    """Busca por nombre o owner/nombre, sin distinguir mayúsculas."""
    return conn.execute(
        "select * from bitacora.repos where (lower(name)=lower(%s) or lower(full_name)=lower(%s)) "
        "order by gone, pushed_at desc nulls last limit 1", (name, name)).fetchone()


def import_meta(conn, data: dict[str, dict], overwrite: bool = True) -> dict:
    """Importa {nombre_repo: {clients, tags}}. Los repos aún no descubiertos quedan pendientes."""
    applied, pending, skipped = 0, 0, 0
    for name, m in data.items():
        if not isinstance(m, dict):
            continue
        clients = m.get("clients") or ([m["client"]] if m.get("client") else [])
        row = find_repo(conn, name)
        if row:
            has = conn.execute(
                "select 1 from bitacora.repo_clients where repo_id=%(i)s "
                "union select 1 from bitacora.repo_tags where repo_id=%(i)s", {"i": row["id"]}).fetchone()
            if has and not overwrite:
                skipped += 1
                continue
            set_meta(conn, row["id"], clients, m.get("tags"))
            applied += 1
        else:
            conn.execute("insert into bitacora.pending_meta (name, payload) values (%s,%s) "
                         "on conflict (name) do update set payload=excluded.payload",
                         (name.lower(), Jsonb({"clients": clients, "tags": m.get("tags") or []})))
            pending += 1
    return {"applied": applied, "pending": pending, "skipped": skipped}


def apply_pending_meta(conn) -> int:
    n = 0
    for r in conn.execute("select name, payload from bitacora.pending_meta").fetchall():
        repo = find_repo(conn, r["name"])
        if repo:
            set_meta(conn, repo["id"], r["payload"].get("clients"), r["payload"].get("tags"))
            conn.execute("delete from bitacora.pending_meta where name=%s", (r["name"],))
            n += 1
    return n


def export_meta(conn) -> dict:
    names = {r["id"]: r["name"] for r in conn.execute("select id, name from bitacora.repos")}
    return {names[i]: m for i, m in get_meta(conn).items() if i in names}
