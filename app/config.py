"""Application configuration, read from environment variables.

All secrets come from the environment (see .env.example). Nothing secret is
hard-coded here; development defaults are deliberately unsafe for production
and the app refuses to start in production with them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEV_SECRET = "dev-only-insecure-secret-change-me"


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", DEV_SECRET))
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/praktikumfinder.db")
    )
    base_url: str = field(default_factory=lambda: os.getenv("BASE_URL", "http://localhost:8000"))
    # Cookies are only marked Secure when served over HTTPS.
    secure_cookies: bool = field(default_factory=lambda: _bool("SECURE_COOKIES", False))
    seed_demo_data: bool = field(default_factory=lambda: _bool("SEED_DEMO_DATA", True))
    # 0 disables the background scraper scheduler; admins can still run sources manually.
    scrape_interval_hours: float = field(
        default_factory=lambda: float(os.getenv("SCRAPE_INTERVAL_HOURS", "0"))
    )
    scraper_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SCRAPER_USER_AGENT",
            "PraktikumFinderBot/0.1 (+https://example.org/bot; contact: admin@example.org)",
        )
    )
    # Minimum seconds between two requests to the same host.
    scraper_min_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("SCRAPER_MIN_DELAY_SECONDS", "2"))
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def validate(self) -> None:
        if self.is_production:
            if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be set to a random value of 32+ characters in production.")
            if not self.secure_cookies:
                raise RuntimeError("SECURE_COOKIES must be true in production (serve the app over HTTPS).")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Used by tests after changing environment variables."""
    global _settings
    _settings = None
