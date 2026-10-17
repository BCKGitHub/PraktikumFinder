"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .db import SessionLocal, init_db
from .listings import expire_outdated
from .postcodes import load_bundled
from .routers import admin, employer, public
from .security import AdminRequired, SecurityHeadersMiddleware
from .seed import seed_demo, seed_reference
from .web import render

log = logging.getLogger("praktikumfinder")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _housekeeping() -> None:
    db = SessionLocal()
    try:
        n = expire_outdated(db)
        db.commit()
        if n:
            log.info("expired %d listings", n)
    finally:
        db.close()


def _link_check() -> None:
    from .linkcheck import check_links

    db = SessionLocal()
    try:
        check_links(db)
    finally:
        db.close()


async def _scheduler(interval_hours: float) -> None:
    from .scrapers.runner import run_all_enabled

    while True:
        await asyncio.sleep(interval_hours * 3600)
        def job():
            db = SessionLocal()
            try:
                run_all_enabled(db)
            finally:
                db.close()
        await asyncio.to_thread(job)
        await asyncio.to_thread(_housekeeping)
        await asyncio.to_thread(_link_check)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    db = SessionLocal()
    try:
        seed_reference(db)
        if settings.load_germany_postcodes:
            n = load_bundled(db)
            if n:
                log.info("loaded %d postcodes for Germany", n)
        if settings.seed_demo_data:
            seed_demo(db)
    finally:
        db.close()
    _housekeeping()
    task = None
    if settings.scrape_interval_hours > 0:
        task = asyncio.create_task(_scheduler(settings.scrape_interval_hours))
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PraktikumFinder", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, session_cookie="pf_session",
        max_age=8 * 3600, same_site="lax", https_only=settings.secure_cookies,
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        # Logs path and status only - never query strings (they can contain postcodes/age).
        start = time.perf_counter()
        response = await call_next(request)
        if not request.url.path.startswith("/static"):
            log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code,
                     (time.perf_counter() - start) * 1000)
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(public.router)
    app.include_router(employer.router)
    app.include_router(admin.router)

    @app.exception_handler(AdminRequired)
    async def _admin_required(request: Request, exc: AdminRequired):
        return RedirectResponse("/admin/login", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if exc.status_code in (301, 302, 303, 307) and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return render(request, "pages/error.html", status_code=exc.status_code,
                      code=exc.status_code, message=exc.detail)

    @app.exception_handler(Exception)
    async def _server_error(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return render(request, "pages/error.html", status_code=500, code=500,
                      message="Da ist etwas schiefgelaufen. Bitte versuche es später noch einmal.")

    return app


app = create_app()
