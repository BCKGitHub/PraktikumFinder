import os
import re

import pyotp
import pytest

os.environ["SCRAPER_MIN_DELAY_SECONDS"] = "0"


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("SEED_DEMO_DATA", "true")
    monkeypatch.setenv("SCRAPE_INTERVAL_HOURS", "0")
    from app import config, db
    from app.security import rate_limiter

    config.reset_settings()
    db.reset_engine()
    rate_limiter.reset()
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
    db.reset_engine()
    config.reset_settings()


@pytest.fixture()
def db_session(app_client):
    from app.db import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


def csrf_from(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no csrf token in page"
    return m.group(1)


@pytest.fixture()
def admin_client(app_client, db_session):
    """A client logged in as admin with MFA."""
    from app.models import AdminUser
    from app.security import hash_password

    secret = pyotp.random_base32()
    db_session.add(AdminUser(email="admin@example.org", password_hash=hash_password("correct horse battery"),
                             totp_secret=secret, totp_enabled=True))
    db_session.commit()
    page = app_client.get("/admin/login")
    r = app_client.post("/admin/login", data={"email": "admin@example.org", "password": "correct horse battery",
                                              "csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert r.headers["location"] == "/admin/mfa"
    page = app_client.get("/admin/mfa")
    r = app_client.post("/admin/mfa", data={"code": pyotp.TOTP(secret).now(), "csrf_token": csrf_from(page.text)},
                        follow_redirects=False)
    assert r.headers["location"] == "/admin"
    app_client.csrf = csrf_from(app_client.get("/admin").text)
    return app_client
