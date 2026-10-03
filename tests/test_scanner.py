"""Tests for recursive filesystem scanner."""

from datetime import datetime, timezone
import os
from pathlib import Path
from unittest.mock import patch

from app.scanner import DirectoryScanner, scan_directory_files


def test_scan_empty_directory(tmp_path):
    """Verify scanning an empty directory returns zero files and no errors."""
    empty_dir = tmp_path / "empty_nas"
    empty_dir.mkdir()

    result = scan_directory_files(empty_dir)
    assert result.total_count == 0
    assert result.error_count == 0
    assert result.files == []


def test_scan_nested_hierarchy(tmp_path):
    """Verify recursive scanning discovers files across nested subdirectories with correct metadata."""
    nas_root = tmp_path / "nas_root"
    nas_root.mkdir()

    # Create files at root and nested subfolders
    file_root = nas_root / "root_doc.pdf"
    file_root.write_bytes(b"content at root" * 100)

    sub_dir = nas_root / "2026" / "Q3"
    sub_dir.mkdir(parents=True)
    nested_file = sub_dir / "report_q3.xlsx"
    nested_file.write_bytes(b"quarterly report data" * 50)

    scanner = DirectoryScanner(nas_root)
    result = scanner.scan()

    assert result.total_count == 2
    assert result.error_count == 0

    files_by_name = {f.file_name: f for f in result.files}
    assert "root_doc.pdf" in files_by_name
    assert "report_q3.xlsx" in files_by_name

    # Check root file metadata
    rf = files_by_name["root_doc.pdf"]
    assert rf.relative_path == "root_doc.pdf"
    assert rf.file_size == len(b"content at root" * 100)
    assert isinstance(rf.mtime, datetime)
    assert rf.mtime.tzinfo == timezone.utc

    # Check nested file metadata
    nf = files_by_name["report_q3.xlsx"]
    assert nf.relative_path == os.path.join("2026", "Q3", "report_q3.xlsx")
    assert nf.file_size == len(b"quarterly report data" * 50)
    assert nf.file_path == str(nested_file.resolve())


def test_scan_non_existent_directory(tmp_path):
    """Verify scanning a non-existent directory returns an error."""
    missing_dir = tmp_path / "does_not_exist"
    result = scan_directory_files(missing_dir)

    assert result.total_count == 0
    assert result.error_count == 1
    assert "does not exist" in result.errors[0]["error"]


def test_scan_target_is_file(tmp_path):
    """Verify scanning a file instead of a directory returns an error."""
    regular_file = tmp_path / "some_file.txt"
    regular_file.write_text("just text")

    result = scan_directory_files(regular_file)
    assert result.total_count == 0
    assert result.error_count == 1
    assert "not a directory" in result.errors[0]["error"]


def test_scan_permission_error_handling(tmp_path):
    """Verify scanner handles permission errors on individual files without failing the whole scan."""
    nas_dir = tmp_path / "nas_perm_test"
    nas_dir.mkdir()

    normal_file = nas_dir / "normal.txt"
    normal_file.write_text("normal file")

    unreadable_file = nas_dir / "protected.txt"
    unreadable_file.write_text("protected content")

    original_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        if self.name == "protected.txt":
            raise PermissionError("Simulated permission denied")
        return original_stat(self, *args, **kwargs)

    with patch.object(Path, "stat", mock_stat):
        result = scan_directory_files(nas_dir)

        # Normal file should still be discovered
        assert result.total_count == 1
        assert result.files[0].file_name == "normal.txt"

        # Protected file should be recorded as an error
        assert result.error_count == 1
        assert "Simulated permission denied" in result.errors[0]["error"]


def test_scan_large_file_metadata_without_reading(tmp_path):
    """Verify scanner captures large file sizes efficiently using stat without reading into memory."""
    nas_dir = tmp_path / "nas_large"
    nas_dir.mkdir()

    large_file = nas_dir / "large.bin"
    # Create a sparse file of 50MB
    with open(large_file, "wb") as f:
        f.seek(50 * 1024 * 1024 - 1)
        f.write(b"\0")

    result = scan_directory_files(nas_dir)
    assert result.total_count == 1
    file_info = result.files[0]
    assert file_info.file_size == 50 * 1024 * 1024
    assert file_info.file_name == "large.bin"


def test_scan_stability_threshold(tmp_path):
    """Verify stability_seconds skips files modified more recently than the threshold."""
    import time
    nas_dir = tmp_path / "nas_stability"
    nas_dir.mkdir()

    recent_file = nas_dir / "recent.txt"
    recent_file.write_text("in flight")

    # Scanning with high stability threshold (e.g. 10 seconds) should skip this brand new file
    res_skipped = scan_directory_files(nas_dir, stability_seconds=10)
    assert res_skipped.total_count == 0

    # Scanning with 0 stability threshold processes it immediately
    res_allowed = scan_directory_files(nas_dir, stability_seconds=0)
    assert res_allowed.total_count == 1
    assert res_allowed.files[0].file_name == "recent.txt"

