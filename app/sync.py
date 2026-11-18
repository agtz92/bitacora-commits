"""Sincronización on-demand con GitHub, en pasos cortos (compatibles con funciones serverless).

1. start(): lista los repos del token, agrega los nuevos y marca para revisión los que tuvieron
   push desde la última vez (o todos si force=True). Crea una corrida en sync_runs.
2. step(): descarga commits de los repos marcados hasta agotar el presupuesto de tiempo y
   devuelve cuántos faltan. Se llama repetidamente hasta que no falte ninguno.

Por cada rama solo se piden commits si su último SHA cambió, y se deja de paginar al encontrar
una página de commits ya conocidos.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import db, github
from .config import settings


def _current_run(conn):
    return conn.execute("select * from bitacora.sync_runs where status='running' order by id desc limit 1").fetchone()


def _pending(conn) -> int:
    return conn.execute("select count(*) n from bitacora.repos where needs_fetch and not gone").fetchone()["n"]


def start(force: bool = False) -> dict:
    remote = github.list_repos()
    with db.session() as conn:
        run = _current_run(conn)
        known = {r["id"]: r for r in conn.execute("select id, fetched_pushed_at from bitacora.repos")}
        new_repos = []
        since = settings.since
        for r in remote:
            if r["id"] not in known:
                new_repos.append(r["full_name"])
            conn.execute(
                """insert into bitacora.repos (id, full_name, owner, name, private, fork, archived, html_url,
                       created_at, pushed_at, gone)
                   values (%(id)s,%(full_name)s,%(owner)s,%(name)s,%(private)s,%(fork)s,%(archived)s,%(html_url)s,
                       %(created_at)s,%(pushed_at)s,false)
                   on conflict (id) do update set full_name=excluded.full_name, owner=excluded.owner,
                       name=excluded.name, private=excluded.private, fork=excluded.fork,
                       archived=excluded.archived, html_url=excluded.html_url, created_at=excluded.created_at,
                       pushed_at=excluded.pushed_at, gone=false""", r)
        # Marcar para revisión: forzado, o con push nuevo. Sin push desde SINCE = nada que buscar.
        ids = [r["id"] for r in remote]
        conn.execute(
            """update bitacora.repos set needs_fetch = true
               where id = any(%s) and pushed_at is not null and pushed_at >= %s::date
                 and (%s or fetched_pushed_at is distinct from pushed_at)""", (ids, since, force))
        conn.execute("update bitacora.repos set gone = true, needs_fetch = false where not (id = any(%s))", (ids,))
        db.apply_pending_meta(conn)
        total = len(remote)
        if run:
            new = list(dict.fromkeys(list(run["new_repos"]) + new_repos))
            conn.execute("update bitacora.sync_runs set repos_total=%s, new_repos=%s, forced=forced or %s "
                         "where id=%s", (total, Jsonb(new), force, run["id"]))
        else:
            conn.execute("insert into bitacora.sync_runs (forced, repos_total, new_repos) values (%s,%s,%s)",
                         (force, total, Jsonb(new_repos)))
        return {"pending": _pending(conn), "new_repos": new_repos, "repos_total": total}


def _fetch_repo(repo: dict, known_shas: set[str], known_branches: dict[str, str]):
    tz = ZoneInfo(settings.timezone)
    since_d = date.fromisoformat(settings.since)
    since_iso = f"{(since_d - timedelta(days=2)).isoformat()}T00:00:00Z"
    branches = github.list_branches(repo["full_name"])
    seen = set(known_shas)
    rows = []
    for name, head in branches:
        if known_branches.get(name) == head:
            continue
        for page in github.commit_pages(repo["full_name"], name, since_iso):
            fresh = [c for c in page if c["sha"] not in seen]
            for c in fresh:
                seen.add(c["sha"])
                a = c["commit"]["author"] or {}
                when = datetime.fromisoformat(a.get("date", "").replace("Z", "+00:00"))
                local = when.astimezone(tz)
                if local.date() < since_d:
                    continue
                author = a.get("name") or ""
                login = (c.get("author") or {}).get("login") or ""
                msg = (c["commit"].get("message") or "").split("\n", 1)[0][:500]
                rows.append((repo["id"], c["sha"], when, local.date(), local.strftime("%H:%M"),
                             local.strftime("%Y-%m"), author, a.get("email"), settings.is_bot(author, login), msg))
            if not fresh:
                break  # página completa ya conocida: lo anterior también
    return rows, branches


def step(budget: float | None = None) -> dict:
    budget = settings.sync_budget if budget is None else budget
    deadline = time.monotonic() + budget
    with db.session() as conn:
        run = _current_run(conn)
        if not run:
            return status()
        queue = conn.execute("select id, full_name, pushed_at from bitacora.repos where needs_fetch and not gone "
                             "order by pushed_at desc nulls last").fetchall()
    workers = max(1, settings.sync_workers)
    new_commits, fetched, errors = 0, 0, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and time.monotonic() < deadline - 12:
            chunk, queue = queue[:workers], queue[workers:]
            with db.session() as conn:
                ctx = {}
                for r in chunk:
                    shas = {x["sha"] for x in conn.execute("select sha from bitacora.commits where repo_id=%s",
                                                          (r["id"],))}
                    br = {x["name"]: x["head_sha"] for x in conn.execute(
                        "select name, head_sha from bitacora.repo_branches where repo_id=%s", (r["id"],))}
                    ctx[r["id"]] = (shas, br)
            futures = [(r, pool.submit(_fetch_repo, r, *ctx[r["id"]])) for r in chunk]
            with db.session() as conn:
                for r, fut in futures:
                    try:
                        rows, branches = fut.result()
                        with conn.cursor() as cur:
                            if rows:
                                before = conn.execute("select count(*) n from bitacora.commits where repo_id=%s",
                                                      (r["id"],)).fetchone()["n"]
                                cur.executemany(
                                    "insert into bitacora.commits values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                    "on conflict do nothing", rows)
                                after = conn.execute("select count(*) n from bitacora.commits where repo_id=%s",
                                                     (r["id"],)).fetchone()["n"]
                                new_commits += after - before
                            cur.execute("delete from bitacora.repo_branches where repo_id=%s", (r["id"],))
                            if branches:
                                cur.executemany("insert into bitacora.repo_branches values (%s,%s,%s)",
                                                [(r["id"], n, s) for n, s in branches])
                        conn.execute("update bitacora.repos set needs_fetch=false, fetched_pushed_at=pushed_at, "
                                     "last_fetch_at=now(), last_error=null where id=%s", (r["id"],))
                    except Exception as e:  # noqa: BLE001 - un repo no debe tumbar la corrida
                        errors.append(f"{r['full_name']}: {e}")
                        conn.execute("update bitacora.repos set needs_fetch=false, last_error=%s where id=%s",
                                     (str(e)[:500], r["id"]))
                    fetched += 1
                conn.execute(
                    "update bitacora.sync_runs set repos_fetched=repos_fetched+%s, new_commits=new_commits+%s, "
                    "errors=errors || %s where id=%s", (fetched, new_commits, Jsonb(errors), run["id"]))
                fetched, new_commits, errors = 0, 0, []
    with db.session() as conn:
        if _pending(conn) == 0:
            conn.execute("update bitacora.sync_runs set status='done', finished_at=now() where id=%s", (run["id"],))
    return status()


def run(force: bool = False, budget: float | None = None) -> dict:
    """Inicia (o continúa) una revisión y trabaja hasta agotar el presupuesto."""
    t0 = time.monotonic()
    budget = settings.sync_budget if budget is None else budget
    with db.session() as conn:
        running = _current_run(conn)
    if force or not running:
        start(force)
    return step(max(5.0, budget - (time.monotonic() - t0)))


def status() -> dict:
    with db.session() as conn:
        r = conn.execute("select * from bitacora.sync_runs order by id desc limit 1").fetchone()
        pending = _pending(conn)
    last = None
    if r:
        last = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
    return {"running": bool(r and r["status"] == "running"), "pending": pending, "last": last}
