"""FastAPI REST API router defining all monitoring, dashboard, and management endpoints."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database.database import get_db
from app.database.models import (
    DuplicateGroup,
    DuplicateMember,
    File,
    FileEvent,
    ScanRun,
)
from app.scanner.scheduler import get_scan_scheduler
from app.services.duplicate_service import DuplicateService
from app.services.file_service import FileService
from app.services.scan_service import ScanInProgressError, ScanService

router = APIRouter(tags=["Monitoring API"])


@router.get("", summary="API Directory", include_in_schema=False)
@router.get("/", summary="API Directory", include_in_schema=False)
def get_api_root() -> Dict[str, Any]:
    """Return JSON directory of all available REST API endpoints."""
    from app import __version__
    return {
        "status": "online",
        "system": "NAS File Monitoring and Duplicate Tracking System",
        "version": __version__,
        "endpoints": {
            "dashboard": "/api/dashboard",
            "files": "/api/files",
            "new_files": "/api/files/new",
            "previous_files": "/api/files/previous",
            "duplicates": "/api/files/duplicates",
            "removed_files": "/api/files/removed",
            "file_details": "/api/files/{id}",
            "events": "/api/events",
            "scans": "/api/scans",
            "manual_scan": "/api/scan (POST)",
            "health": "/api/health",
            "export_files": "/api/export/files?format=csv|json",
            "export_duplicates": "/api/export/duplicates?format=csv|json",
            "websocket": "/ws",
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
        },
    }


@router.get("/health", summary="Service Health Check")
@router.get("/health/", summary="Service Health Check", include_in_schema=False)
def api_health():
    """Service health check returning status, version, and NAS availability flag."""
    from app import __version__
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


@router.get("/dashboard", summary="Dashboard Statistics")
@router.get("/dashboard/", summary="Dashboard Statistics", include_in_schema=False)
@router.get("/scans/dashboard", summary="Dashboard Statistics (Alias)", include_in_schema=False)
@router.get("/scans/dashboard/", summary="Dashboard Statistics (Alias)", include_in_schema=False)
def get_dashboard_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve aggregate statistics, latest scan status, and recent activity for the dashboard."""
    settings = get_settings()
    nas_available, nas_msg = settings.validate_nas_directory()
    scheduler = get_scan_scheduler()

    # File counts
    total_present = db.scalar(
        select(func.count(File.id)).where(File.is_present.is_(True))
    ) or 0
    new_files_count = db.scalar(
        select(func.count(File.id)).where(File.status == "NEW", File.is_present.is_(True))
    ) or 0
    existing_files_count = db.scalar(
        select(func.count(File.id)).where(File.status == "EXISTING", File.is_present.is_(True))
    ) or 0
    removed_files_count = db.scalar(
        select(func.count(File.id)).where(File.is_present.is_(False))
    ) or 0
    duplicate_files_count = db.scalar(
        select(func.count(File.id)).where(File.is_duplicate.is_(True), File.is_present.is_(True))
    ) or 0
    duplicate_groups_count = db.scalar(
        select(func.count(DuplicateGroup.id)).where(DuplicateGroup.duplicate_count >= 2)
    ) or 0

    latest_scan = ScanService.get_latest_scan_run(db)
    recent_events = FileService.get_file_events(db, limit=10)

    return {
        "system": {
            "nas_directory": str(settings.NAS_DIRECTORY),
            "nas_available": nas_available,
            "nas_message": nas_msg,
            "scanner_running": ScanService.is_scan_running(),
            "scheduler_active": scheduler.is_running,
            "scan_interval": settings.SCAN_INTERVAL,
        },
        "stats": {
            "total_files": total_present,
            "new_files": new_files_count,
            "existing_files": existing_files_count,
            "removed_files": removed_files_count,
            "duplicate_files": duplicate_files_count,
            "duplicate_groups": duplicate_groups_count,
        },
        "latest_scan": latest_scan.to_dict() if latest_scan else None,
        "recent_events": [e.to_dict() for e in recent_events],
    }


@router.get("/files", summary="Global Files List & Search")
def list_files(
    query: Optional[str] = Query(None, description="Search term for name, path, or SHA256"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    is_present: Optional[bool] = Query(None, description="Filter by current presence"),
    is_duplicate: Optional[bool] = Query(None, description="Filter by duplicate status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search and paginate files across the entire historical registry."""
    stmt = select(File)

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                File.file_name.ilike(pattern),
                File.file_path.ilike(pattern),
                File.sha256.ilike(pattern),
            )
        )
    if status_filter:
        stmt = stmt.where(File.status == status_filter)
    if is_present is not None:
        stmt = stmt.where(File.is_present == is_present)
    if is_duplicate is not None:
        stmt = stmt.where(File.is_duplicate == is_duplicate)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    items = db.scalars(stmt.order_by(desc(File.last_seen_at)).limit(limit).offset(offset)).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [f.to_dict() for f in items],
    }


@router.get("/files/new", summary="Newly Added Files (List 1)")
def list_new_files(
    query: Optional[str] = Query(None, description="Filter by filename or path"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List 1: Retrieve newly added files detected in the most recent scan."""
    stmt = select(File).where(File.status == "NEW", File.is_present.is_(True))

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(File.file_name.ilike(pattern), File.file_path.ilike(pattern)))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    items = db.scalars(stmt.order_by(desc(File.first_seen_at)).limit(limit).offset(offset)).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [f.to_dict() for f in items],
    }


@router.get("/files/previous", summary="Previously Added Files (List 2)")
def list_previous_files(
    query: Optional[str] = Query(None, description="Filter by filename or path"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List 2: Retrieve files that were already detected during previous scans."""
    stmt = select(File).where(File.status == "EXISTING", File.is_present.is_(True))

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(File.file_name.ilike(pattern), File.file_path.ilike(pattern)))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    items = db.scalars(stmt.order_by(desc(File.last_seen_at)).limit(limit).offset(offset)).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [f.to_dict() for f in items],
    }


@router.get("/files/removed", summary="Removed Files")
def list_removed_files(
    query: Optional[str] = Query(None, description="Filter by filename or path"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve historically tracked files that are no longer present on disk."""
    stmt = select(File).where(File.is_present.is_(False))

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(File.file_name.ilike(pattern), File.file_path.ilike(pattern)))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    items = db.scalars(stmt.order_by(desc(File.removed_at)).limit(limit).offset(offset)).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [f.to_dict() for f in items],
    }


@router.get("/files/duplicates", summary="Duplicate Files & Groups (List 3)")
def list_duplicates(
    query: Optional[str] = Query(None, description="Filter by SHA256 or filename"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List 3: Retrieve duplicate groups with first and latest occurrences and all members."""
    stmt = (
        select(DuplicateGroup)
        .options(
            joinedload(DuplicateGroup.first_file),
            joinedload(DuplicateGroup.latest_file),
            joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
        )
        .where(DuplicateGroup.duplicate_count >= 2)
    )

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(DuplicateGroup.sha256.ilike(pattern))

    count_stmt = select(func.count(DuplicateGroup.id)).where(DuplicateGroup.duplicate_count >= 2)
    total = db.scalar(count_stmt) or 0

    groups = db.scalars(
        stmt.order_by(desc(DuplicateGroup.latest_seen_at)).limit(limit).offset(offset)
    ).unique().all()

    results = []
    for g in groups:
        g_dict = g.to_dict()
        g_dict["first_file"] = g.first_file.to_dict() if g.first_file else None
        g_dict["latest_file"] = g.latest_file.to_dict() if g.latest_file else None
        g_dict["members"] = [
            {
                "member_id": m.id,
                "first_seen_at": m.first_seen_at.isoformat() if m.first_seen_at else None,
                "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None,
                "file": m.file.to_dict() if m.file else None,
            }
            for m in g.members
            if m.file and m.file.is_present
        ]
        results.append(g_dict)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "groups": results,
    }


@router.get("/files/{file_id}", summary="File Details & Event History")
def get_file_details(file_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve detailed metadata for a file including its duplicate group and complete event ledger."""
    file_record = db.scalar(
        select(File)
        .options(
            joinedload(File.events),
            joinedload(File.duplicate_memberships).joinedload(DuplicateMember.duplicate_group),
        )
        .where(File.id == file_id)
    )

    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with ID {file_id} not found.",
        )

    file_dict = file_record.to_dict()
    file_dict["events"] = [e.to_dict() for e in file_record.events]

    duplicate_groups = []
    for m in file_record.duplicate_memberships:
        if m.duplicate_group and m.duplicate_group.duplicate_count >= 2:
            duplicate_groups.append(m.duplicate_group.to_dict())
    file_dict["duplicate_groups"] = duplicate_groups

    return file_dict


@router.get("/events", summary="Historical File Activity Events")
def list_file_events(
    file_id: Optional[int] = Query(None, description="Filter by file ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type (ADDED, SEEN, REMOVED, etc.)"),
    query: Optional[str] = Query(None, description="Filter by filename or path"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve chronological audit timeline events."""
    stmt = select(FileEvent)

    if file_id is not None:
        stmt = stmt.where(FileEvent.file_id == file_id)
    if event_type:
        stmt = stmt.where(FileEvent.event_type == event_type)
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(FileEvent.file_name.ilike(pattern), FileEvent.file_path.ilike(pattern)))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    items = db.scalars(stmt.order_by(desc(FileEvent.event_time)).limit(limit).offset(offset)).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [e.to_dict() for e in items],
    }


@router.get("/scans", summary="Scan Runs History")
def list_scan_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve historical scan runs and performance metrics."""
    total = db.scalar(select(func.count(ScanRun.id))) or 0
    runs = ScanService.get_scan_runs(db, limit=limit, offset=offset)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [r.to_dict() for r in runs],
    }


@router.get("/scans/{scan_id}", summary="Single Scan Run Details")
def get_scan_run(scan_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve a single scan execution run by ID."""
    run = ScanService.get_scan_run_by_id(db, scan_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan run with ID {scan_id} not found.",
        )
    return run.to_dict()


@router.post("/scan", summary="Trigger Manual Scan (SCAN NOW)")
def trigger_scan() -> Dict[str, Any]:
    """Trigger an immediate manual scan of the monitored directory."""
    scheduler = get_scan_scheduler()
    try:
        summary = scheduler.trigger_manual_scan()
        return summary
    except ScanInProgressError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scan is already in progress. Please wait for it to complete.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scan failed: {str(e)}",
        )


@router.get("/export/files", summary="Export Files (CSV / JSON)")
def export_files(
    format: str = Query("csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
):
    """Export all tracked files as CSV or JSON."""
    import csv
    import io
    from fastapi.responses import JSONResponse, Response

    files = db.scalars(select(File).order_by(File.file_path)).all()

    if format == "json":
        return JSONResponse(content=[f.to_dict() for f in files])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "File Name", "File Path", "Relative Path", "File Size (Bytes)",
        "SHA-256", "Status", "Is Present", "Is Duplicate", "First Seen At", "Last Seen At", "Removed At"
    ])
    for f in files:
        writer.writerow([
            f.id, f.file_name, f.file_path, f.relative_path, f.file_size,
            f.sha256 or "", f.status, f.is_present, f.is_duplicate,
            f.first_seen_at.isoformat() if f.first_seen_at else "",
            f.last_seen_at.isoformat() if f.last_seen_at else "",
            f.removed_at.isoformat() if f.removed_at else "",
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nas_files_export.csv"},
    )


@router.get("/export/duplicates", summary="Export Duplicate Groups (CSV / JSON)")
def export_duplicates(
    format: str = Query("csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
):
    """Export duplicate file groups as CSV or JSON."""
    import csv
    import io
    from fastapi.responses import JSONResponse, Response

    groups = db.scalars(
        select(DuplicateGroup)
        .options(
            joinedload(DuplicateGroup.first_file),
            joinedload(DuplicateGroup.latest_file),
            joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
        )
        .where(DuplicateGroup.duplicate_count >= 2)
        .order_by(desc(DuplicateGroup.duplicate_count))
    ).unique().all()

    if format == "json":
        results = []
        for g in groups:
            g_dict = g.to_dict()
            g_dict["first_file"] = g.first_file.to_dict() if g.first_file else None
            g_dict["latest_file"] = g.latest_file.to_dict() if g.latest_file else None
            g_dict["members"] = [
                m.file.to_dict() for m in g.members if m.file and m.file.is_present
            ]
            results.append(g_dict)
        return JSONResponse(content=results)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Group ID", "SHA-256", "Copies Count", "File Size (Bytes)",
        "First Seen At", "First File Path", "Latest Seen At", "Latest File Path", "All Present Paths"
    ])
    for g in groups:
        first_path = g.first_file.file_path if g.first_file else ""
        latest_path = g.latest_file.file_path if g.latest_file else ""
        all_paths = "; ".join(
            m.file.file_path for m in g.members if m.file and m.file.is_present
        )
        writer.writerow([
            g.id, g.sha256, g.duplicate_count, g.file_size or 0,
            g.first_seen_at.isoformat() if g.first_seen_at else "",
            first_path,
            g.latest_seen_at.isoformat() if g.latest_seen_at else "",
            latest_path,
            all_paths,
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nas_duplicates_export.csv"},
    )


