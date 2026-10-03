"""FastAPI application factory and lifecycle management.

Configures database initialization, logging, routers, static files, background scheduler,
CORS for localhost and LAN IP (192.168.1.30), and bi-directional WebSocket API endpoints.
"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router as api_router
from app.config import get_settings
from app.database.database import init_db
from app.logging_config import get_logger, setup_logging
from app.scanner.scheduler import get_scan_scheduler
from app.services.scan_service import ScanService
from app.web.routes import web_router

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown routines."""
    settings = get_settings()
    setup_logging(log_dir=settings.LOG_DIR, log_level=settings.LOG_LEVEL)
    logger.info("Starting NAS File Monitoring System v%s...", __version__)
    logger.info("NAS Target Directory: %s", settings.NAS_DIRECTORY)
    logger.info("Database URL: %s", settings.DATABASE_URL)
    logger.info(
        "Server accessible via: http://localhost:%d and http://192.168.1.30:%d",
        settings.APP_PORT,
        settings.APP_PORT,
    )

    # Initialize SQLite database and models
    init_db()
    logger.info("Database initialized successfully.")

    # Start background scheduler
    scheduler = get_scan_scheduler()
    scheduler.start()

    # Track main event loop in WebSocket manager
    import asyncio
    from app.websocket_manager import ws_manager

    try:
        ws_manager.set_event_loop(asyncio.get_running_loop())
    except Exception:
        pass

    yield

    logger.info("Shutting down NAS File Monitoring System...")
    scheduler.stop(wait=False)


def create_app() -> FastAPI:
    """Create and configure FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="NAS File Monitoring and Duplicate File Tracking System",
        description="Production-ready NAS file monitoring, duplicate tracking, and historical activity ledger.",
        version=__version__,
        lifespan=lifespan,
    )

    # Enable CORS for localhost and 192.168.1.30 (and all LAN clients)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static assets
    static_dir = Path(__file__).resolve().parent / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Register API routers with BOTH /api and /api/v1 prefixes
    app.include_router(api_router, prefix="/api")
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(web_router)

    async def handle_websocket_connection(websocket: WebSocket):
        """Handle WebSocket connection lifecycle and bi-directional API queries."""
        from app.database.database import SessionLocal
        from app.websocket_manager import (
            build_dashboard_payload,
            handle_ws_api_command,
            ws_manager,
        )

        await ws_manager.connect(websocket)

        # Send current system state immediately on connection
        try:
            db = SessionLocal()
            try:
                payload = build_dashboard_payload(db)
                await websocket.send_json({
                    "type": "INITIAL_STATE",
                    "data": payload,
                })
            finally:
                db.close()
        except Exception as e:
            logger.warning("Error sending initial WebSocket payload: %s", e)

        try:
            while True:
                raw_data = await websocket.receive_text()
                raw_data_stripped = raw_data.strip()

                if raw_data_stripped == "ping":
                    await websocket.send_text("pong")
                    continue
                elif raw_data_stripped == "refresh":
                    db = SessionLocal()
                    try:
                        payload = build_dashboard_payload(db)
                        await websocket.send_json({
                            "type": "REFRESH_RESPONSE",
                            "data": payload,
                        })
                    finally:
                        db.close()
                    continue

                # Parse JSON command if sent
                try:
                    command = json.loads(raw_data)
                except Exception:
                    command = {"action": raw_data_stripped}

                db = SessionLocal()
                try:
                    response_payload = handle_ws_api_command(db, command)
                    await websocket.send_json(response_payload)
                finally:
                    db.close()

        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as e:
            logger.info("WebSocket connection closed: %s", e)
            ws_manager.disconnect(websocket)

    # WebSocket endpoints
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await handle_websocket_connection(websocket)

    @app.websocket("/api/ws")
    async def api_websocket_endpoint(websocket: WebSocket):
        await handle_websocket_connection(websocket)

    @app.websocket("/api/v1/ws")
    async def api_v1_websocket_endpoint(websocket: WebSocket):
        await handle_websocket_connection(websocket)

    # Top-level health endpoints
    @app.get("/health", tags=["System"])
    @app.get("/api/health", tags=["System"])
    @app.get("/api/v1/health", tags=["System"])
    def health_check():
        """Health check endpoint to verify system status."""
        nas_exists = settings.NAS_DIRECTORY.exists() and settings.NAS_DIRECTORY.is_dir()
        scheduler = get_scan_scheduler()
        return {
            "status": "healthy" if nas_exists else "degraded",
            "version": __version__,
            "nas_directory": str(settings.NAS_DIRECTORY),
            "nas_available": nas_exists,
            "database": True,
            "scanner_running": ScanService.is_scan_running(),
            "scheduler_running": scheduler.is_running,
            "allowed_hosts": settings.get_allowed_hosts(),
        }

    return app


app = create_app()
