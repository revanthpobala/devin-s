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

    # 1. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Cache-Control Header Middleware
    @app.middleware("http")
    async def add_no_cache_header(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # 3. Static Files Mounts
    web_dir = config.BASE_DIR / "web"
    static_dir = web_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    data_dir = config.BASE_DIR / "data"
    if data_dir.exists():
        app.mount("/data", StaticFiles(directory=str(data_dir)), name="data")

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
