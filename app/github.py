"""Cliente mínimo de la API de GitHub (solo lectura)."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .config import settings

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class EmptyRepo(GitHubError):
    pass


def _get(url: str):
    if not settings.github_token:
        raise GitHubError("Falta GITHUB_TOKEN")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bitacora-commits",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode())
            link = resp.headers.get("Link", "")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        if e.code == 401:
            raise GitHubError("GitHub rechazó el token (401). Revisa GITHUB_TOKEN.") from e
        if e.code == 409:
            raise EmptyRepo("Repositorio vacío") from e
        if e.code == 403 and "rate limit" in detail.lower():
            raise GitHubError("Se alcanzó el límite de la API de GitHub; intenta en una hora.") from e
        raise GitHubError(f"GitHub respondió {e.code}: {detail}") from e
    m = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return body, (m.group(1) if m else None)


def _paged(url: str):
    while url:
        page, url = _get(url)
        yield page


def list_repos() -> list[dict]:
    """Todos los repos a los que el token tiene acceso: propios, de colaborador y de organizaciones."""
    url = f"{API}/user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=created"
    out = []
    for page in _paged(url):
        for r in page:
            if r.get("fork") and not settings.include_forks:
                continue
            if r["full_name"].lower() in settings.exclude_repos or r["name"].lower() in settings.exclude_repos:
                continue
            out.append({
                "id": r["id"], "full_name": r["full_name"], "owner": r["owner"]["login"], "name": r["name"],
                "private": bool(r.get("private")), "fork": bool(r.get("fork")),
                "archived": bool(r.get("archived")), "html_url": r.get("html_url"),
                "created_at": r.get("created_at"), "pushed_at": r.get("pushed_at"),
            })
    return out


def list_branches(full_name: str) -> list[tuple[str, str]]:
    try:
        return [(b["name"], b["commit"]["sha"])
                for page in _paged(f"{API}/repos/{full_name}/branches?per_page=100") for b in page]
    except EmptyRepo:
        return []


def commit_pages(full_name: str, branch: str, since_iso: str):
    """Commits de una rama, del más reciente al más antiguo, en páginas de 100."""
    q = urllib.parse.urlencode({"sha": branch, "since": since_iso, "per_page": 100})
    try:
        yield from _paged(f"{API}/repos/{full_name}/commits?{q}")
    except EmptyRepo:
        return
