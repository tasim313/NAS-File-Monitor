# NAS File Monitoring and Duplicate File Tracking System

A production-ready Python-based NAS file monitoring and duplicate tracking system built with FastAPI, SQLAlchemy 2.0, and SQLite3.

## System Overview

The system monitors a designated NAS directory (default: `/media/requisition/Report`), tracking file lifecycle activity (added, existing, removed, restored, modified) and identifying duplicates based on SHA-256 content hashes with first-seen and latest-seen tracking.

---

## Phase 1 Implementation Summary

Phase 1 provides the core foundation of the application:
1. **Modular Architecture**: Clean separation into `database`, `scanner`, `services`, `api`, `web`, and `tests`.
2. **Configuration Management (`app/config.py`)**: Environment-driven settings via Pydantic BaseSettings with `.env` support and validation.
3. **Database Layer (`app/database/`)**:
   - SQLite3 with Write-Ahead Logging (`WAL`), `foreign_keys=ON`, and `busy_timeout=5000`.
   - **`files`**: Tracks file records, absolute/relative paths, sizes, SHA-256 hashes, timestamps, presence status, and duplicate flag.
   - **`file_events`**: Historical audit log recording lifecycle events (`ADDED`, `SEEN`, `REMOVED`, `DUPLICATE_DETECTED`, `RESTORED`, `MODIFIED`).
   - **`scan_runs`**: Records scan executions, start/end timestamps, file counts, and error status.
   - **`duplicate_groups`**: SHA-256 based duplicate groups tracking first and latest file occurrences and duplicate count.
   - **`duplicate_members`**: Relationship mapping duplicate group members.
   - Robust `UTCDateTime` TypeDecorator ensuring timezone-aware UTC timestamps across SQLite.
4. **Structured Logging (`app/logging_config.py`)**: Console output and rotating file handlers (`logs/app.log`, `logs/scanner.log`, `logs/error.log`).
5. **FastAPI Application & Health Check (`app/main.py`)**: Lifespan startup routines, DB schema auto-generation, and `/api/health` endpoint.
6. **Application Entrypoint (`run.py`)**: Starts Uvicorn server on configured host and port.
7. **Test Suite (`tests/`)**: Automated pytest suite covering configuration, SQLite pragmas, models CRUD, constraints, relationships, logging, and health endpoint.

---

## Directory Structure

```text
NAS File Monitoring and Duplicate File Tracking System/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── logging_config.py
│   ├── main.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── models.py
│   ├── scanner/
│   │   └── __init__.py
│   ├── services/
│   │   └── __init__.py
│   ├── api/
│   │   └── __init__.py
│   └── web/
│       ├── templates/
│       └── static/
├── data/
│   └── nas_monitor.db
├── logs/
│   ├── app.log
│   ├── scanner.log
│   └── error.log
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_database.py
│   ├── test_logging.py
│   └── test_api_basic.py
├── .env.example
├── requirements.txt
├── README.md
└── run.py
```

---

## Getting Started

### 1. Environment Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

Key environment variables:
- `NAS_DIRECTORY`: Monitored directory path (default: `/media/requisition/Report`)
- `DATABASE_URL`: SQLite database connection URL (default: `sqlite:///data/nas_monitor.db`)
- `SCAN_INTERVAL`: Seconds between automatic periodic scans (default: `60`)
- `HASH_ALGORITHM`: Hashing algorithm (default: `sha256`)
- `FILE_STABILITY_SECONDS`: Stability threshold to handle copying race conditions (default: `5`)
- `LOG_LEVEL`: Logging level (default: `INFO`)
- `APP_PORT`: Server port (default: `8000`)

### 3. Run Automated Tests

```bash
pytest -v
```

### 4. Run the Application

```bash
python run.py
```

Check health status:

```bash
curl http://localhost:8000/api/health
```
