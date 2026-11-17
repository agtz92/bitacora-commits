"""Sincronización on-demand: descubre repos nuevos y descarga sus commits.

Cada repo se guarda como espejo git "bare" parcial (sin contenido de archivos, solo historial)
en /data/mirrors/<id>.git. Solo se vuelven a descargar los repos cuyo `pushed_at` cambió desde
la última vez, así que una sincronización normal tarda segundos.
"""
import base64
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, github
from .config import settings

SEP_FIELD, SEP_REC = "\x1f", "\x1e"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_env() -> dict:
    env = dict(os.environ)
    token = base64.b64encode(f"x-access-token:{settings.github_token}".encode()).decode()
    # El token viaja como header y nunca se escribe en la config del espejo.
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {token}",
    })
    return env


def _git(args: list[str], cwd: Path | None = None, timeout: int = 900) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, env=_git_env(), capture_output=True,
                         text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-400:] or f"git {args[0]} falló")
    return res.stdout


def fetch_repo_commits(repo: dict) -> list[tuple]:
    """Actualiza el espejo del repo y devuelve filas de commits desde SINCE."""
    tz = ZoneInfo(settings.timezone)
    since = date.fromisoformat(settings.since)
    mirror = settings.mirrors_dir / f"{repo['id']}.git"
    url = f"https://github.com/{repo['full_name']}.git"
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--quiet", "--bare", "--filter=blob:none", url, str(mirror)])
    else:
        _git(["remote", "set-url", "origin", url], cwd=mirror)
    _git(["fetch", "--quiet", "--prune", "--filter=blob:none", "origin",
          "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"], cwd=mirror)
    if not _git(["for-each-ref", "--count=1", "refs/heads"], cwd=mirror).strip():
        return []  # repo vacío
    out = _git(["log", "--all", f"--since={(since - timedelta(days=2)).isoformat()}",
                f"--format=%H{SEP_FIELD}%aI{SEP_FIELD}%an{SEP_FIELD}%ae{SEP_FIELD}%s{SEP_REC}"], cwd=mirror)
    rows = []
    for rec in out.split(SEP_REC):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, when, name, email, subject = rec.split(SEP_FIELD, 4)
        local = datetime.fromisoformat(when).astimezone(tz)
        if local.date() < since:
            continue
        rows.append((repo["id"], sha, when, local.strftime("%Y-%m-%d"), local.strftime("%H:%M"),
                     local.strftime("%Y-%m"), name, email, int(settings.is_bot(name)), subject[:500]))
    return rows


class SyncManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.status: dict = {"running": False, "phase": "idle", "done": 0, "total": 0, "current": None,
                             "last": None, "error": None}

    def start(self, force: bool = False) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._run_locked, args=(force,), daemon=True).start()
        return True

    def run_blocking(self, force: bool = False) -> dict:
        with self._lock:
            return self._run(force)

    def _run_locked(self, force: bool):
        try:
            self._run(force)
        finally:
            self._lock.release()

    def _run(self, force: bool) -> dict:
        st = self.status
        st.update(running=True, phase="Buscando repositorios en GitHub", done=0, total=0, current=None, error=None)
        started = _now()
        errors: list[str] = []
        new_repos: list[str] = []
        new_commits = 0
        to_fetch: list[dict] = []
        try:
            remote = github.list_repos()
            with db.session() as conn:
                known = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM repos")}
                seen = set()
                for r in remote:
                    seen.add(r["id"])
                    if r["id"] not in known:
                        new_repos.append(r["full_name"])
                        conn.execute(
                            "INSERT INTO repos (id, full_name, owner, name, private, fork, archived, html_url,"
                            " created_at, pushed_at, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (r["id"], r["full_name"], r["owner"], r["name"], r["private"], r["fork"],
                             r["archived"], r["html_url"], r["created_at"], r["pushed_at"], started))
                    else:
                        conn.execute(
                            "UPDATE repos SET full_name=?, owner=?, name=?, private=?, fork=?, archived=?,"
                            " html_url=?, created_at=?, pushed_at=?, gone=0 WHERE id=?",
                            (r["full_name"], r["owner"], r["name"], r["private"], r["fork"], r["archived"],
                             r["html_url"], r["created_at"], r["pushed_at"], r["id"]))
                    prev = known.get(r["id"], {})
                    pushed = r["pushed_at"] or ""
                    # Repos sin push desde SINCE no tienen commits que nos interesen.
                    if pushed and pushed[:10] < settings.since and not prev.get("fetched_pushed_at") and not force:
                        continue
                    if force or prev.get("fetched_pushed_at") != pushed:
                        to_fetch.append(r)
                for rid in set(known) - seen:
                    conn.execute("UPDATE repos SET gone=1 WHERE id=?", (rid,))
                db.apply_pending_meta(conn)

            st.update(phase="Descargando commits", total=len(to_fetch))
            with ThreadPoolExecutor(max_workers=max(1, settings.sync_workers)) as pool:
                futures = {pool.submit(fetch_repo_commits, r): r for r in to_fetch}
                for fut in as_completed(futures):
                    r = futures[fut]
                    st["current"] = r["full_name"]
                    with db.session() as conn:
                        try:
                            rows = fut.result()
                            before = conn.total_changes
                            conn.executemany(
                                "INSERT OR IGNORE INTO commits VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                            new_commits += conn.total_changes - before
                            conn.execute("UPDATE repos SET fetched_pushed_at=?, last_fetch_at=?, last_error=NULL"
                                         " WHERE id=?", (r["pushed_at"], _now(), r["id"]))
                        except Exception as e:  # noqa: BLE001 - un repo no debe tumbar la corrida
                            msg = f"{r['full_name']}: {e}"
                            errors.append(msg)
                            conn.execute("UPDATE repos SET last_error=? WHERE id=?", (str(e)[:500], r["id"]))
                    st["done"] += 1
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            st["error"] = str(e)
        finished = _now()
        result = {"started_at": started, "finished_at": finished, "forced": force,
                  "repos_total": None, "new_repos": new_repos, "repos_fetched": len(to_fetch),
                  "new_commits": new_commits, "errors": errors}
        with db.session() as conn:
            result["repos_total"] = conn.execute("SELECT COUNT(*) FROM repos WHERE gone=0").fetchone()[0]
            conn.execute(
                "INSERT INTO sync_runs (started_at, finished_at, forced, repos_total, new_repos, repos_fetched,"
                " new_commits, errors) VALUES (?,?,?,?,?,?,?,?)",
                (started, finished, int(force), result["repos_total"], json.dumps(new_repos), len(to_fetch),
                 new_commits, json.dumps(errors)))
        st.update(running=False, phase="idle", current=None, last=result)
        return result


manager = SyncManager()


def last_run() -> dict | None:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    d = dict(r)
    d["new_repos"] = json.loads(d["new_repos"] or "[]")
    d["errors"] = json.loads(d["errors"] or "[]")
    return d
