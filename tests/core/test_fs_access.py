# tests/test_fs_access.py
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from invariant.exec.caps.fs import (
    FSAccess,
    FSDeleteDenied,
    FSPathTraversalBlocked,
    FSReadLimitExceeded,
    FSWriteDenied,
    FSWriteLimitExceeded,
)


def _mk_fs(tmp: Path, *, write: bool = True, delete: bool = True, r: int = 1024, w: int = 1024) -> FSAccess:
    return FSAccess(root=tmp, write=write, delete=delete, read_limit=r, write_limit=w)


def test_resolve_blocks_absolute(tmp_path: Path):
    fs = _mk_fs(tmp_path)
    with pytest.raises(FSPathTraversalBlocked):
        fs.resolve(str(tmp_path / "x"))


def test_resolve_blocks_traversal(tmp_path: Path):
    fs = _mk_fs(tmp_path)
    with pytest.raises(FSPathTraversalBlocked):
        fs.resolve("../x")
    with pytest.raises(FSPathTraversalBlocked):
        fs.resolve("a/../b")


def test_resolve_allows_dotdot_like_segment(tmp_path: Path):
    fs = _mk_fs(tmp_path)
    # "..a" is NOT traversal
    p = fs.resolve("..a/file.txt")
    assert str(p).endswith(os.path.join("..a", "file.txt"))


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Drive-relative paths exist only on Windows")
def test_resolve_blocks_drive_paths_on_windows(tmp_path: Path):
    fs = _mk_fs(tmp_path)
    with pytest.raises(FSPathTraversalBlocked):
        fs.resolve("C:foo")


def test_read_limit_enforced(tmp_path: Path):
    fs = _mk_fs(tmp_path, r=10)
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    with pytest.raises(FSReadLimitExceeded):
        fs.read_bytes("a.bin")


def test_write_denied(tmp_path: Path):
    fs = _mk_fs(tmp_path, write=False)
    with pytest.raises(FSWriteDenied):
        fs.write_text("x.txt", "hi")


def test_write_limit_enforced(tmp_path: Path):
    fs = _mk_fs(tmp_path, w=5)
    with pytest.raises(FSWriteLimitExceeded):
        fs.write_bytes("x.bin", b"123456")


def test_delete_denied(tmp_path: Path):
    fs = _mk_fs(tmp_path, delete=False)
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FSDeleteDenied):
        fs.delete_file("x.txt")


def test_atomic_write_roundtrip(tmp_path: Path):
    fs = _mk_fs(tmp_path, write=True)
    fs.write_text("t.txt", "one", atomic=True)
    assert fs.read_text("t.txt") == "one"
    fs.write_text("t.txt", "two", atomic=True)
    assert fs.read_text("t.txt") == "two"


def test_list_dir_bounded(tmp_path: Path):
    fs = _mk_fs(tmp_path, write=True)
    for i in range(20):
        fs.write_text(f"f{i}.txt", "x")
    out = fs.list_dir(".", max_entries=5)
    assert len(out) == 5


def test_symlink_escape_blocked_best_effort(tmp_path: Path):
    fs = _mk_fs(tmp_path, write=True, delete=True)
    outside = tmp_path.parent / ("outside_" + next(tempfile._get_candidate_names()))
    outside.mkdir(exist_ok=True)
    (outside / "pwn.txt").write_text("nope", encoding="utf-8")

    # Create symlink inside root pointing to outside
    link = tmp_path / "link_out"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except Exception:
        pytest.skip("symlink creation not supported/allowed on this platform")

    # Any attempt to traverse into symlinked path should be blocked
    with pytest.raises(FSPathTraversalBlocked):
        fs.read_text("link_out/pwn.txt")


def test_delete_tree_does_not_follow_symlinks(tmp_path: Path):
    fs = _mk_fs(tmp_path, write=True, delete=True)
    outside = tmp_path.parent / ("outside_" + next(tempfile._get_candidate_names()))
    outside.mkdir(exist_ok=True)
    victim = outside / "victim.txt"
    victim.write_text("keep", encoding="utf-8")

    link = tmp_path / "link_out"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except Exception:
        pytest.skip("symlink creation not supported/allowed on this platform")

    # delete_tree should not delete outside content
    try:
        fs.delete_tree(".")
    except Exception:
        # best-effort behavior may raise depending on platform; key point: outside remains
        pass

    assert victim.exists()
