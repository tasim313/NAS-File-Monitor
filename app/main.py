"""FastAPI application factory and lifecycle management.

Configures database initialization, logging, routers, static files, and background scheduler.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
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
    from fastapi import WebSocket, WebSocketDisconnect

    app = FastAPI(
        title="NAS File Monitoring and Duplicate File Tracking System",
        description="Production-ready NAS file monitoring, duplicate tracking, and historical activity ledger.",
        version=__version__,
        lifespan=lifespan,
    )

    # Mount static assets
    static_dir = Path(__file__).resolve().parent / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Register API and Web UI routers
    app.include_router(api_router)
    app.include_router(web_router)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time live updates without browser refresh."""
        from app.database.database import SessionLocal
        from app.websocket_manager import ws_manager, build_dashboard_payload

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
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
                elif data == "refresh":
                    db = SessionLocal()
                    try:
                        payload = build_dashboard_payload(db)
                        await websocket.send_json({
                            "type": "REFRESH_RESPONSE",
                            "data": payload,
                        })
                    finally:
                        db.close()
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as e:
            logger.info("WebSocket connection closed: %s", e)
            ws_manager.disconnect(websocket)

    @app.get("/api/health", tags=["System"])
    def health_check():
        """Health check endpoint to verify system status."""
        settings = get_settings()
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
        }

    return app


app = create_app()

