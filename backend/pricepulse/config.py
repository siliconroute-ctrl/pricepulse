"""Settings, read from environment variables (and a local .env file if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=value lines, # comments. Real env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BACKEND_DIR / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    """All tunable settings in one place. See .env.example for descriptions."""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("PP_DATA_DIR", BACKEND_DIR / "data")))
    database_url: str = field(default_factory=lambda: os.environ.get("PP_DATABASE_URL", ""))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.environ.get(
                "PP_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if o.strip()
        ]
    )
    rate_limit_per_min: int = field(default_factory=lambda: int(_float("PP_RATE_LIMIT_PER_MIN", 60)))
    trust_proxy: bool = field(default_factory=lambda: os.environ.get("PP_TRUST_PROXY", "0") == "1")
    cache_max_age: int = field(default_factory=lambda: int(_float("PP_CACHE_MAX_AGE", 300)))
    match_auto: float = field(default_factory=lambda: _float("PP_MATCH_AUTO", 88))
    match_review: float = field(default_factory=lambda: _float("PP_MATCH_REVIEW", 70))
    slot_hours: int = field(default_factory=lambda: int(_float("PP_SLOT_HOURS", 6)))
    stale_hours: int = field(default_factory=lambda: int(_float("PP_STALE_HOURS", 48)))
    alert_hours: int = field(default_factory=lambda: int(_float("PP_ALERT_HOURS", 24)))
    frontend_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PP_FRONTEND_DIR", BACKEND_DIR.parent / "frontend"))
    )
    regions_file: Path = field(
        default_factory=lambda: Path(os.environ.get("PP_REGIONS_FILE", BACKEND_DIR / "regions.yaml"))
    )

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'pricepulse.db'}"


def get_settings() -> Settings:
    """Fresh settings each call, so tests can change env vars."""
    return Settings()
