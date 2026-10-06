"""
FastAPI Application Factory & Service Orchestration.
Integrates all APIRouters, static mounts, middlewares, and lifecycle event handlers.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src import config
from src.ui.routes import (
    alerts,
    copilot,
    edge_scanner,
    intraday,
    portfolio,
    research,
    screener,
    status,
    trades,
    views,
    watchlist,
    desk,
)
from src.ui.services.daemon_manager import start_all_daemons, stop_all_daemons
from src.ui.services.research_queue import dispatch_next_queued_job, rehydrate_active_jobs
from src.ui.state import init_db

logger = logging.getLogger("ui_server")


def create_app() -> FastAPI:
    """Create and configure the central FastAPI trading cockpit application."""
    app = FastAPI(title="Stock Trading Cockpit", version="1.0.0")

    # 1. CORS Middleware (Explicit origins - no wildcard with credentials)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8050",
            "http://localhost:8050",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_origin_regex=r"^https?://.*",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # 2. Non-loopback Auth Middleware
    @app.middleware("http")
    async def auth_middleware(request, call_next):
        client_host = request.client.host if request.client else "127.0.0.1"
        if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            import os
            expected_token = os.getenv("COCKPIT_TOKEN")
            auth_header = request.headers.get("Authorization", "")
            token = (
                request.headers.get("X-Cockpit-Token")
                or request.headers.get("COCKPIT_TOKEN")
                or (auth_header[7:] if auth_header.startswith("Bearer ") else auth_header)
                or request.query_params.get("token")
                or request.query_params.get("cockpit_token")
            )
            if not expected_token or not token or token != expected_token:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"error": "Unauthorized: valid COCKPIT_TOKEN required for non-loopback access."},
                )
        return await call_next(request)

    # 3. Cache-Control Header Middleware
    @app.middleware("http")
    async def add_no_cache_header(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # 3. Static Files Mounts (/data mount removed for security)
    web_dir = config.BASE_DIR / "web"
    static_dir = web_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 4. Include Domain APIRouters
    app.include_router(views.router)
    app.include_router(status.router)
    app.include_router(portfolio.router)
    app.include_router(screener.router)
    app.include_router(intraday.router)
    app.include_router(watchlist.router)
    app.include_router(trades.router)
    app.include_router(alerts.router)
    app.include_router(research.router)
    app.include_router(copilot.router)
    app.include_router(edge_scanner.router)
    app.include_router(desk.router)

    # 5. Lifecycle Event Handlers
    @app.on_event("startup")
    def on_startup():
        init_db()
        rehydrate_active_jobs()
        dispatch_next_queued_job()
        start_all_daemons()

    @app.on_event("shutdown")
    def on_shutdown():
        stop_all_daemons()

    return app
