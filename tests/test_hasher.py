"""Tests for chunked cryptographic file hashing."""

import hashlib
from pathlib import Path
import pytest

from app.scanner.hasher import (
    FileHasher,
    calculate_sha256,
    calculate_sha256_safe,
)


def test_hash_known_content(tmp_path):
    """Verify SHA-256 hash matches standard hashlib digest."""
    test_file = tmp_path / "sample.txt"
    content = b"The quick brown fox jumps over the lazy dog"
    test_file.write_bytes(content)

    expected_sha256 = hashlib.sha256(content).hexdigest()
    computed_sha256 = calculate_sha256(test_file)

    assert computed_sha256 == expected_sha256


def test_hash_empty_file(tmp_path):
    """Verify SHA-256 hash of empty file matches standard empty digest."""
    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")

    empty_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert calculate_sha256(empty_file) == empty_sha256


def test_hash_chunked_streaming(tmp_path):
    """Verify file larger than buffer chunk is hashed correctly across multiple chunks."""
    multi_chunk_file = tmp_path / "multi_chunk.bin"
    # Create 100KB file
    data = b"0123456789ABCDEF" * 6400
    multi_chunk_file.write_bytes(data)

    # Use a small chunk size of 1024 bytes (100 chunks)
    hasher = FileHasher(chunk_size=1024, algorithm="sha256")
    computed = hasher.compute_hash(multi_chunk_file)

    expected = hashlib.sha256(data).hexdigest()
    assert computed == expected


def test_identical_content_yields_identical_hash(tmp_path):
    """Verify different file paths with identical content produce identical hashes."""
    data = b"Duplicate report payload data 2026"
    file_a = tmp_path / "report_original.pdf"
    file_b = tmp_path / "report_copy.pdf"

    file_a.write_bytes(data)
    file_b.write_bytes(data)

    hash_a = calculate_sha256(file_a)
    hash_b = calculate_sha256(file_b)

    assert hash_a == hash_b


def test_different_content_yields_different_hash(tmp_path):
    """Verify same or different filenames with distinct content produce distinct hashes."""
    file_a = tmp_path / "report.pdf"
    file_b = tmp_path / "other_report.pdf"

    file_a.write_bytes(b"Content version 1")
    file_b.write_bytes(b"Content version 2")

    assert calculate_sha256(file_a) != calculate_sha256(file_b)


def test_hash_non_existent_file(tmp_path):
    """Verify compute_hash raises FileNotFoundError and safe helper returns error tuple."""
    missing = tmp_path / "ghost.txt"

    with pytest.raises(FileNotFoundError):
        calculate_sha256(missing)

    hash_val, err_msg = calculate_sha256_safe(missing)
    assert hash_val is None
    assert "File not found" in err_msg


def test_hash_directory_error(tmp_path):
    """Verify attempting to hash a directory raises IsADirectoryError and safe returns error."""
    some_dir = tmp_path / "subfolder"
    some_dir.mkdir()

    with pytest.raises(IsADirectoryError):
        calculate_sha256(some_dir)

    hash_val, err_msg = calculate_sha256_safe(some_dir)
    assert hash_val is None
    assert "Target is a directory" in err_msg


def test_hash_sha512_algorithm(tmp_path):
    """Verify configurable algorithm supports SHA-512."""
    data = b"Testing SHA-512 hashing support"
    test_file = tmp_path / "test512.txt"
    test_file.write_bytes(data)

    hasher = FileHasher(algorithm="sha512")
    computed = hasher.compute_hash(test_file)

    expected = hashlib.sha512(data).hexdigest()
    assert computed == expected
    assert len(computed) == 128
