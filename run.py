#!/usr/bin/env python3
"""Application entry point and CLI runner for NAS File Monitoring and Duplicate File Tracking System."""

import argparse
import sys
import uvicorn

from app.config import get_settings
from app.database.database import SessionLocal, init_db
from app.logging_config import get_logger, setup_logging
from app.services.scan_service import ScanService


def run_single_scan():
    """Execute a single manual scan directly from the CLI without starting the web server."""
    settings = get_settings()
    setup_logging(log_dir=settings.LOG_DIR, log_level=settings.LOG_LEVEL)
    logger = get_logger("app.cli")
    logger.info("Initializing database...")
    init_db()

    logger.info("Starting one-off filesystem scan of %s...", settings.NAS_DIRECTORY)
    db = SessionLocal()
    try:
        summary = ScanService.scan_directory(db, enforce_lock=False)
        print("\n" + "=" * 60)
        print("          SCAN SUMMARY RESULTS")
        print("=" * 60)
        print(f"Status:            {summary.get('status')}")
        print(f"Scan Run ID:       #{summary.get('scan_id')}")
        print(f"Total Files:       {summary.get('total_files')}")
        print(f"Newly Added:       {summary.get('new_count')}")
        print(f"Existing Files:    {summary.get('existing_count')}")
        print(f"Removed Files:     {summary.get('removed_count')}")
        print(f"Modified Files:    {summary.get('modified_count')}")
        print(f"Duplicates:        {summary.get('duplicate_count')}")
        print(f"Errors:            {len(summary.get('errors', []))}")
        print("=" * 60 + "\n")
    finally:
        db.close()


def main():
    """Parse CLI options and run either web server or one-off scan."""
    parser = argparse.ArgumentParser(
        description="NAS File Monitoring and Duplicate File Tracking System"
    )
    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="Run a single filesystem scan in the terminal and exit without starting the web server",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Custom bind host (overrides APP_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Custom bind port (overrides APP_PORT)",
    )

    args = parser.parse_args()

    if args.scan_once:
        run_single_scan()
        sys.exit(0)

    settings = get_settings()
    host = args.host or settings.APP_HOST
    port = args.port or settings.APP_PORT

    print("\n" + "=" * 70)
    print("  🚀 NAS File Monitoring and Duplicate File Tracking System")
    print("=" * 70)
    print(f"  • Localhost Access:  http://localhost:{port}/")
    print(f"  • Loopback Access:   http://127.0.0.1:{port}/")
    print(f"  • Network/LAN IP:    http://192.168.1.30:{port}/")
    print(f"  • WebSocket Live:    ws://localhost:{port}/ws  &  ws://192.168.1.30:{port}/ws")
    print(f"  • REST & Swagger:    http://localhost:{port}/docs")
    print(f"  • Listening on:      {host}:{port} (All Network Interfaces)")
    print("=" * 70 + "\n")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()

