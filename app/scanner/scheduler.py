"""Background periodic scan scheduler using APScheduler."""

from datetime import datetime
import threading
from typing import Any, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.database.database import SessionLocal
from app.logging_config import get_logger
from app.services.scan_service import ScanInProgressError, ScanService

logger = get_logger("app.scanner")


class ScanScheduler:
    """Manages background periodic scanning and manual on-demand scan triggers."""

    def __init__(self, interval_seconds: Optional[int] = None):
        settings = get_settings()
        self.interval_seconds = interval_seconds or settings.SCAN_INTERVAL
        self._scheduler: Optional[BackgroundScheduler] = None
        self._lock = threading.Lock()
        self._last_scheduled_run: Optional[datetime] = None

    def _execute_scheduled_scan(self) -> None:
        """Background job function executed on the configured periodic interval."""
        logger.info("Executing scheduled periodic NAS scan...")
        self._last_scheduled_run = datetime.now()

        db = SessionLocal()
        try:
            summary = ScanService.scan_directory(db, enforce_lock=True)
            logger.info("Scheduled scan finished successfully. Total files: %d", summary.get("total_files", 0))
        except ScanInProgressError:
            logger.warning("Scheduled scan skipped: another scan is already in progress.")
        except Exception as e:
            logger.error("Scheduled scan failed with exception: %s", e, exc_info=True)
        finally:
            db.close()

    def start(self) -> None:
        """Start the background scheduler."""
        with self._lock:
            if self._scheduler and self._scheduler.running:
                logger.info("Scheduler is already running.")
                return

            self._scheduler = BackgroundScheduler(daemon=True)
            self._scheduler.add_job(
                func=self._execute_scheduled_scan,
                trigger=IntervalTrigger(seconds=self.interval_seconds),
                id="periodic_nas_scan",
                name="Periodic NAS Directory Scan",
                replace_existing=True,
                max_instances=1,
            )
            self._scheduler.start()
            logger.info("Background scan scheduler started with interval of %d seconds.", self.interval_seconds)

    def stop(self, wait: bool = False) -> None:
        """Stop the background scheduler."""
        with self._lock:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=wait)
                logger.info("Background scan scheduler stopped.")
            self._scheduler = None

    @property
    def is_running(self) -> bool:
        """Check whether the scheduler is active and running."""
        return self._scheduler is not None and self._scheduler.running

    def get_status(self) -> Dict[str, Any]:
        """Return the current scheduler health and job status."""
        next_run = None
        if self._scheduler and self._scheduler.running:
            job = self._scheduler.get_job("periodic_nas_scan")
            if job and job.next_run_time:
                next_run = job.next_run_time.isoformat()

        return {
            "is_running": self.is_running,
            "interval_seconds": self.interval_seconds,
            "is_scan_active": ScanService.is_scan_running(),
            "next_run_time": next_run,
            "last_run_time": self._last_scheduled_run.isoformat() if self._last_scheduled_run else None,
        }

    def trigger_manual_scan(self, directory: Optional[Any] = None) -> Dict[str, Any]:
        """Trigger an immediate manual scan (SCAN NOW).
        
        Args:
            directory: Optional target directory path.
            
        Returns:
            Structured summary of the completed scan.
            
        Raises:
            ScanInProgressError: If a scan is already executing.
        """
        logger.info("Manual scan triggered via trigger_manual_scan()")
        db = SessionLocal()
        try:
            return ScanService.scan_directory(db, directory=directory, enforce_lock=True)
        finally:
            db.close()



# Singleton scheduler instance
_scheduler_instance: Optional[ScanScheduler] = None
_instance_lock = threading.Lock()


def get_scan_scheduler() -> ScanScheduler:
    """Return the application singleton ScanScheduler instance."""
    global _scheduler_instance
    with _instance_lock:
        if _scheduler_instance is None:
            _scheduler_instance = ScanScheduler()
        return _scheduler_instance
