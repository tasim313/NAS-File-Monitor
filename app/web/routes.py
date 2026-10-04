"""FastAPI router for Web UI dashboard and monitoring views (Jinja2)."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
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
from app.services.file_service import FileService
from app.services.scan_service import ScanService

from app.datetime_util import format_dhaka_12h, format_dhaka_time_only

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["format_dhaka"] = format_dhaka_12h
templates.env.filters["format_dhaka_time"] = format_dhaka_time_only

web_router = APIRouter(include_in_schema=False)


def _common_context(request: Request, active_page: str) -> dict:
    """Return common template context variables."""
    settings = get_settings()
    return {
        "request": request,
        "active_page": active_page,
        "monitored_dir": str(settings.NAS_DIRECTORY),
    }


@web_router.get("/", response_class=HTMLResponse)
@web_router.get("/dashboard", response_class=HTMLResponse)
def dashboard_view(request: Request, db: Session = Depends(get_db)):
    """Render the main system dashboard."""
    settings = get_settings()
    nas_available, nas_msg = settings.validate_nas_directory()
    scheduler = get_scan_scheduler()

    # Query file counts
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

    ctx = _common_context(request, "dashboard")
    ctx.update({
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
    })
    return templates.TemplateResponse(request=request, name="dashboard.html", context=ctx)


@web_router.get("/files/new", response_class=HTMLResponse)
def new_files_view(
    request: Request,
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render newly added files (List 1)."""
    stmt = select(File).where(File.status == "NEW", File.is_present.is_(True))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(File.file_name.ilike(pattern), File.file_path.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(desc(File.first_seen_at)).limit(limit).offset(offset)).all()

    ctx = _common_context(request, "new")
    ctx.update({
        "query": q,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [f.to_dict() for f in items],
    })
    return templates.TemplateResponse(request=request, name="files_new.html", context=ctx)


@web_router.get("/files/previous", response_class=HTMLResponse)
def previous_files_view(
    request: Request,
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render previously added files (List 2)."""
    stmt = select(File).where(File.status == "EXISTING", File.is_present.is_(True))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(File.file_name.ilike(pattern), File.file_path.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(desc(File.last_seen_at)).limit(limit).offset(offset)).all()

    ctx = _common_context(request, "previous")
    ctx.update({
        "query": q,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [f.to_dict() for f in items],
    })
    return templates.TemplateResponse(request=request, name="files_previous.html", context=ctx)


@web_router.get("/files/duplicates", response_class=HTMLResponse)
def duplicates_view(
    request: Request,
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render duplicate files and groups (List 3)."""
    stmt = (
        select(DuplicateGroup)
        .options(
            joinedload(DuplicateGroup.first_file),
            joinedload(DuplicateGroup.latest_file),
            joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
        )
        .where(DuplicateGroup.duplicate_count >= 2)
    )
    if q:
        stmt = stmt.where(DuplicateGroup.sha256.ilike(f"%{q}%"))

    total = db.scalar(select(func.count(DuplicateGroup.id)).where(DuplicateGroup.duplicate_count >= 2)) or 0
    groups = db.scalars(
        stmt.order_by(desc(DuplicateGroup.latest_seen_at)).limit(limit).offset(offset)
    ).unique().all()

    formatted_groups = []
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
        formatted_groups.append(g_dict)

    ctx = _common_context(request, "duplicates")
    ctx.update({
        "query": q,
        "limit": limit,
        "offset": offset,
        "total": total,
        "groups": formatted_groups,
    })
    return templates.TemplateResponse(request=request, name="files_duplicates.html", context=ctx)


@web_router.get("/files/removed", response_class=HTMLResponse)
def removed_files_view(
    request: Request,
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render removed files."""
    stmt = select(File).where(File.is_present.is_(False))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(File.file_name.ilike(pattern), File.file_path.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(desc(File.removed_at)).limit(limit).offset(offset)).all()

    ctx = _common_context(request, "removed")
    ctx.update({
        "query": q,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [f.to_dict() for f in items],
    })
    return templates.TemplateResponse(request=request, name="files_removed.html", context=ctx)


@web_router.get("/events", response_class=HTMLResponse)
def events_view(
    request: Request,
    q: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render chronological activity events."""
    stmt = select(FileEvent)
    if event_type:
        stmt = stmt.where(FileEvent.event_type == event_type)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(FileEvent.file_name.ilike(pattern), FileEvent.file_path.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(desc(FileEvent.event_time)).limit(limit).offset(offset)).all()

    ctx = _common_context(request, "events")
    ctx.update({
        "query": q,
        "event_type": event_type,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [e.to_dict() for e in items],
    })
    return templates.TemplateResponse(request=request, name="events.html", context=ctx)


@web_router.get("/scans", response_class=HTMLResponse)
def scans_view(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render scan runs history."""
    total = db.scalar(select(func.count(ScanRun.id))) or 0
    runs = ScanService.get_scan_runs(db, limit=limit, offset=offset)

    ctx = _common_context(request, "scans")
    ctx.update({
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [r.to_dict() for r in runs],
    })
    return templates.TemplateResponse(request=request, name="scans.html", context=ctx)


@web_router.get("/search", response_class=HTMLResponse)
def search_view(
    request: Request,
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    presence: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Render global file search across all records."""
    stmt = select(File)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                File.file_name.ilike(pattern),
                File.file_path.ilike(pattern),
                File.sha256.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(File.status == status)
    if presence == "true":
        stmt = stmt.where(File.is_present.is_(True))
    elif presence == "false":
        stmt = stmt.where(File.is_present.is_(False))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(desc(File.last_seen_at)).limit(limit).offset(offset)).all()

    ctx = _common_context(request, "search")
    ctx.update({
        "query": q,
        "search_query": q,
        "status_filter": status,
        "is_present": presence,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [f.to_dict() for f in items],
    })
    return templates.TemplateResponse(request=request, name="search.html", context=ctx)


@web_router.get("/files/{file_id}", response_class=HTMLResponse)
def file_detail_view(file_id: int, request: Request, db: Session = Depends(get_db)):
    """Render file details and historical audit timeline."""
    file_record = db.scalar(
        select(File)
        .options(
            joinedload(File.events),
            joinedload(File.duplicate_memberships).joinedload(DuplicateMember.duplicate_group),
        )
        .where(File.id == file_id)
    )
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File #{file_id} not found.")

    file_dict = file_record.to_dict()
    file_dict["events"] = [e.to_dict() for e in file_record.events]

    duplicate_groups = []
    for m in file_record.duplicate_memberships:
        if m.duplicate_group and m.duplicate_group.duplicate_count >= 2:
            duplicate_groups.append(m.duplicate_group.to_dict())

    ctx = _common_context(request, "details")
    ctx.update({
        "file": file_dict,
        "duplicate_groups": duplicate_groups,
    })
    return templates.TemplateResponse(request=request, name="file_detail.html", context=ctx)


@web_router.get("/api-docs", response_class=HTMLResponse)
@web_router.get("/api-docs/", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/api-doc", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/api-documents", response_class=HTMLResponse, include_in_schema=False)
def api_documents_view(request: Request):
    """Render API Documents and endpoint reference page."""
    ctx = _common_context(request, "api_docs")
    return templates.TemplateResponse(request=request, name="api_documents.html", context=ctx)


@web_router.get("/websocket-api", response_class=HTMLResponse)
@web_router.get("/websocket-api/", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/websocket-ap", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/websocket-ap/", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/websocket", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/websocket/", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/ws-api", response_class=HTMLResponse, include_in_schema=False)
@web_router.get("/ws-api/", response_class=HTMLResponse, include_in_schema=False)
def websocket_api_view(request: Request):
    """Render interactive WebSocket Live API Explorer for querying and streaming data."""
    ctx = _common_context(request, "websocket_api")
    return templates.TemplateResponse(request=request, name="websocket_api.html", context=ctx)


