"""Cliente mínimo de la API de GitHub (solo lectura)."""
import json
import re
import urllib.request

from .config import settings

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def _get(url: str):
    if not settings.github_token:
        raise GitHubError("Falta GITHUB_TOKEN en .env")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bitacora-commits",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
            link = resp.headers.get("Link", "")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise GitHubError("GitHub rechazó el token (401). Revisa GITHUB_TOKEN.") from e
        raise GitHubError(f"GitHub respondió {e.code}: {detail}") from e
    m = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return body, (m.group(1) if m else None)


def viewer_login() -> str:
    body, _ = _get(f"{API}/user")
    return body["login"]


def list_repos() -> list[dict]:
    """Todos los repos a los que el token tiene acceso: propios, de colaborador y de organizaciones."""
    url = f"{API}/user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=created"
    repos = []
    while url:
        page, url = _get(url)
        repos.extend(page)
    out = []
    for r in repos:
        if r.get("fork") and not settings.include_forks:
            continue
        if r["full_name"].lower() in settings.exclude_repos or r["name"].lower() in settings.exclude_repos:
            continue
        out.append({
            "id": r["id"],
            "full_name": r["full_name"],
            "owner": r["owner"]["login"],
            "name": r["name"],
            "private": int(bool(r.get("private"))),
            "fork": int(bool(r.get("fork"))),
            "archived": int(bool(r.get("archived"))),
            "html_url": r.get("html_url"),
            "created_at": r.get("created_at"),
            "pushed_at": r.get("pushed_at"),
            "clone_url": r.get("clone_url"),
        })
    return out
