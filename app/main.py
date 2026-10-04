"""FastAPI application factory and lifecycle management.

Configures database initialization, logging, routers, static files, background scheduler,
CORS for localhost and LAN IP (192.168.1.30), and bi-directional WebSocket API endpoints.
"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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

    # HTTP GET fallbacks for WebSocket endpoints
    @app.get("/ws", tags=["WebSocket"])
    @app.get("/api/ws", tags=["WebSocket"])
    @app.get("/api/v1/ws", tags=["WebSocket"])
    def websocket_http_fallback(request: Request):
        """HTTP GET fallback for WebSocket endpoints.

        When opened in a web browser, automatically redirects to the interactive
        WebSocket Live API Explorer (/websocket-api). When accessed via cURL or API
        clients, returns helpful connection instructions and current live system state.
        """
        accept = request.headers.get("accept", "")
        if "text/html" in accept and "application/json" not in accept:
            return RedirectResponse(url="/websocket-api", status_code=303)

        from app.database.database import SessionLocal
        from app.websocket_manager import build_dashboard_payload

        db = SessionLocal()
        try:
            current_payload = build_dashboard_payload(db)
        finally:
            db.close()

        host = request.headers.get("host") or f"{settings.APP_HOST}:{settings.APP_PORT}"
        ws_protocol = "wss://" if request.url.scheme == "https" else "ws://"
        ws_url = f"{ws_protocol}{host}/ws"

        return JSONResponse(
            status_code=200,
            content={
                "status": "online",
                "endpoint": "/ws",
                "protocol": "WebSocket (RFC 6455)",
                "message": "WebSocket endpoint is active. Connect using WebSocket protocol or use the Interactive Web Explorer.",
                "websocket_url": ws_url,
                "interactive_web_explorer": f"{request.url.scheme}://{host}/websocket-api",
                "how_to_connect": {
                    "browser_javascript": f"const ws = new WebSocket('{ws_url}'); ws.onmessage = (e) => console.log(JSON.parse(e.data));",
                    "python_client": f"import websockets, asyncio; asyncio.run((await websockets.connect('{ws_url}')).recv())",
                    "curl_websocket": f"curl -i -N -H \"Connection: Upgrade\" -H \"Upgrade: websocket\" -H \"Sec-WebSocket-Version: 13\" -H \"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\" http://{host}/ws",
                },
                "data": current_payload,
            },
        )

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
