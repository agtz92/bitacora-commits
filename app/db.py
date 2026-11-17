"""SQLite: esquema y acceso. Un archivo en el volumen /data."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id              INTEGER PRIMARY KEY,      -- id de GitHub (sobrevive renombres)
    full_name       TEXT NOT NULL,
    owner           TEXT NOT NULL,
    name            TEXT NOT NULL,
    private         INTEGER NOT NULL DEFAULT 0,
    fork            INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0,
    html_url        TEXT,
    created_at      TEXT,                     -- ISO UTC según GitHub
    pushed_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    fetched_pushed_at TEXT,                   -- pushed_at de la última descarga de commits
    last_fetch_at   TEXT,
    last_error      TEXT,
    gone            INTEGER NOT NULL DEFAULT 0 -- ya no aparece en la cuenta
);
CREATE TABLE IF NOT EXISTS commits (
    repo_id      INTEGER NOT NULL REFERENCES repos(id),
    sha          TEXT NOT NULL,
    committed_at TEXT NOT NULL,               -- fecha de autor, ISO con zona original
    local_date   TEXT NOT NULL,               -- YYYY-MM-DD en TIMEZONE
    local_time   TEXT NOT NULL,               -- HH:MM en TIMEZONE
    month        TEXT NOT NULL,               -- YYYY-MM en TIMEZONE
    author_name  TEXT NOT NULL,
    author_email TEXT,
    is_bot       INTEGER NOT NULL DEFAULT 0,
    message      TEXT NOT NULL,
    PRIMARY KEY (repo_id, sha)
);
CREATE INDEX IF NOT EXISTS commits_month ON commits(month);
CREATE TABLE IF NOT EXISTS repo_clients (
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    client  TEXT NOT NULL,
    PRIMARY KEY (repo_id, client)
);
CREATE TABLE IF NOT EXISTS repo_tags (
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    tag     TEXT NOT NULL,
    PRIMARY KEY (repo_id, tag)
);
-- Clientes/etiquetas importados para repos que aún no se han descubierto.
CREATE TABLE IF NOT EXISTS pending_meta (
    name    TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    forced      INTEGER NOT NULL DEFAULT 0,
    repos_total INTEGER,
    new_repos   TEXT,                         -- JSON lista de full_name
    repos_fetched INTEGER,
    new_commits INTEGER,
    errors      TEXT                          -- JSON lista
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)


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


def set_meta(conn: sqlite3.Connection, repo_id: int, clients, tags) -> dict:
    clients, tags = norm_clients(clients), norm_tags(tags)
    conn.execute("DELETE FROM repo_clients WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM repo_tags WHERE repo_id=?", (repo_id,))
    conn.executemany("INSERT INTO repo_clients VALUES (?,?)", [(repo_id, c) for c in clients])
    conn.executemany("INSERT INTO repo_tags VALUES (?,?)", [(repo_id, t) for t in tags])
    return {"clients": clients, "tags": tags}


def get_meta(conn: sqlite3.Connection) -> dict[int, dict]:
    meta: dict[int, dict] = {}
    for r in conn.execute("SELECT repo_id, client FROM repo_clients ORDER BY rowid"):
        meta.setdefault(r["repo_id"], {"clients": [], "tags": []})["clients"].append(r["client"])
    for r in conn.execute("SELECT repo_id, tag FROM repo_tags ORDER BY rowid"):
        meta.setdefault(r["repo_id"], {"clients": [], "tags": []})["tags"].append(r["tag"])
    return meta


def import_meta(conn: sqlite3.Connection, data: dict[str, dict], overwrite: bool = False) -> dict:
    """Importa {nombre_repo: {clients, tags}}. Los repos aún no descubiertos quedan pendientes."""
    applied, pending = 0, 0
    for name, m in data.items():
        row = conn.execute(
            "SELECT id FROM repos WHERE lower(name)=lower(?) OR lower(full_name)=lower(?)", (name, name)
        ).fetchone()
        if row:
            has = conn.execute(
                "SELECT 1 FROM repo_clients WHERE repo_id=? UNION SELECT 1 FROM repo_tags WHERE repo_id=?",
                (row["id"], row["id"])).fetchone()
            if has and not overwrite:
                continue
            set_meta(conn, row["id"], m.get("clients"), m.get("tags"))
            applied += 1
        else:
            conn.execute("INSERT OR REPLACE INTO pending_meta VALUES (?,?)", (name.lower(), json.dumps(m)))
            pending += 1
    return {"applied": applied, "pending": pending}


def apply_pending_meta(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT name, payload FROM pending_meta").fetchall()
    n = 0
    for r in rows:
        repo = conn.execute(
            "SELECT id FROM repos WHERE lower(name)=? OR lower(full_name)=?", (r["name"], r["name"])).fetchone()
        if repo:
            m = json.loads(r["payload"])
            set_meta(conn, repo["id"], m.get("clients"), m.get("tags"))
            conn.execute("DELETE FROM pending_meta WHERE name=?", (r["name"],))
            n += 1
    return n


def export_meta(conn: sqlite3.Connection) -> dict:
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM repos")}
    return {names[i]: m for i, m in get_meta(conn).items() if i in names}
