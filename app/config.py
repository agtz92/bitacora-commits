"""Configuración leída de variables de entorno (ver .env.example)."""
import os
from dataclasses import dataclass, field


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", "").strip())
    # Contraseña para entrar a la página web.
    app_password: str = field(default_factory=lambda: os.getenv("APP_PASSWORD", ""))
    # Firma las cookies de sesión.
    session_secret: str = field(default_factory=lambda: os.getenv("SESSION_SECRET", ""))
    # El endpoint MCP vive en /mcp/<MCP_SECRET>; la URL completa es la credencial.
    mcp_secret: str = field(default_factory=lambda: os.getenv("MCP_SECRET", ""))
    since: str = field(default_factory=lambda: os.getenv("SINCE", "2025-01-01"))
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "America/Mexico_City"))
    bot_authors: list[str] = field(default_factory=lambda: _list(
        "BOT_AUTHORS", "github-actions[bot],github-classroom[bot],Vercel,dependabot[bot]"))
    include_forks: bool = field(default_factory=lambda: _bool("INCLUDE_FORKS", True))
    exclude_repos: list[str] = field(default_factory=lambda: [x.lower() for x in _list("EXCLUDE_REPOS")])
    sync_workers: int = field(default_factory=lambda: int(os.getenv("SYNC_WORKERS", "6")))
    # Segundos de trabajo por llamada de sincronización (Vercel corta a los 60 s).
    sync_budget: float = field(default_factory=lambda: float(os.getenv("SYNC_BUDGET_SECONDS", "40")))

    def is_bot(self, author: str, login: str = "") -> bool:
        return author.endswith("[bot]") or login.endswith("[bot]") or author in self.bot_authors


settings = Settings()
