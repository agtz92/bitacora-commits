"""Configuración leída de variables de entorno (ver .env.example)."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass(frozen=True)
class Settings:
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", "").strip())
    # Solo se guardan commits a partir de esta fecha (YYYY-MM-DD).
    since: str = field(default_factory=lambda: os.getenv("SINCE", "2025-01-01"))
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "America/Mexico_City"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "/data")))
    bot_authors: list[str] = field(default_factory=lambda: _list(
        "BOT_AUTHORS", "github-actions[bot],github-classroom[bot],Vercel,dependabot[bot]"))
    include_forks: bool = field(default_factory=lambda: _bool("INCLUDE_FORKS", True))
    exclude_repos: list[str] = field(default_factory=lambda: [x.lower() for x in _list("EXCLUDE_REPOS")])
    sync_workers: int = field(default_factory=lambda: int(os.getenv("SYNC_WORKERS", "4")))
    # 0 = solo on-demand. >0 = además sincroniza sola cada N horas.
    auto_sync_hours: float = field(default_factory=lambda: float(os.getenv("AUTO_SYNC_HOURS", "0")))
    basic_auth_user: str = field(default_factory=lambda: os.getenv("BASIC_AUTH_USER", ""))
    basic_auth_password: str = field(default_factory=lambda: os.getenv("BASIC_AUTH_PASSWORD", ""))
    meta_seed: Path = field(default_factory=lambda: Path(os.getenv("META_SEED", "/app/seed/meta.json")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bitacora.sqlite3"

    @property
    def mirrors_dir(self) -> Path:
        return self.data_dir / "mirrors"

    def is_bot(self, author: str) -> bool:
        return author.endswith("[bot]") or author in self.bot_authors


settings = Settings()
