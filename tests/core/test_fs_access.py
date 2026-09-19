# tests/test_fs_access.py
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
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


def test_atomic_write_retries_transient_permission_error(monkeypatch, tmp_path: Path) -> None:
    fs = FSAccess(root=tmp_path, write=True, delete=True)
    original_replace = os.replace
    replace_calls = {"count": 0}
    sleep_calls: list[float] = []

    def flaky_replace(src: str, dst: str) -> None:
        replace_calls["count"] += 1
        if replace_calls["count"] < 3:
            raise PermissionError("busy")
        original_replace(src, dst)

    monkeypatch.setattr("invariant.exec.caps.fs.os.replace", flaky_replace)
    monkeypatch.setattr(
        "invariant.exec.caps.fs.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    fs.write_text("note.txt", "hello world\n", atomic=True)

    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello world\n"
    assert replace_calls["count"] == 3
    assert sleep_calls == [0.02, 0.02]


def test_atomic_write_re_raises_after_retry_budget(monkeypatch, tmp_path: Path) -> None:
    fs = FSAccess(root=tmp_path, write=True, delete=True)
    replace_calls = {"count": 0}
    sleep_calls: list[float] = []

    def always_fail(src: str, dst: str) -> None:
        del src, dst
        replace_calls["count"] += 1
        raise PermissionError("still busy")

    monkeypatch.setattr("invariant.exec.caps.fs.os.replace", always_fail)
    monkeypatch.setattr(
        "invariant.exec.caps.fs.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    with pytest.raises(PermissionError, match="still busy"):
        fs.write_text("note.txt", "hello world\n", atomic=True)

    assert replace_calls["count"] == 10
    assert sleep_calls == [0.02] * 10


def test_mkdir_creates_nested_directory_and_reports_creation(tmp_path: Path) -> None:
    fs = _mk_fs(tmp_path, write=True)

    created = fs.mkdir("artifacts/output", parents=True, exist_ok=True)

    assert created is True
    assert (tmp_path / "artifacts" / "output").is_dir()


def test_mkdir_existing_directory_reports_not_created(tmp_path: Path) -> None:
    fs = _mk_fs(tmp_path, write=True)
    (tmp_path / "existing").mkdir()

    created = fs.mkdir("existing", exist_ok=True)

    assert created is False


def test_mkdir_existing_directory_respects_exist_ok_false(tmp_path: Path) -> None:
    fs = _mk_fs(tmp_path, write=True)
    (tmp_path / "existing").mkdir()

    with pytest.raises(FileExistsError):
        fs.mkdir("existing", exist_ok=False)


def test_mkdir_requires_write_authority(tmp_path: Path) -> None:
    fs = _mk_fs(tmp_path, write=False)

    with pytest.raises(FSWriteDenied, match="Write denied"):
        fs.mkdir("blocked")


def test_mkdir_blocks_path_traversal(tmp_path: Path) -> None:
    fs = _mk_fs(tmp_path, write=True)

    with pytest.raises(FSPathTraversalBlocked):
        fs.mkdir("../escape")



def test_mkdir_reports_exactly_one_creator_under_race(tmp_path: Path) -> None:
    fs = _mk_fs(tmp_path, write=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: fs.mkdir("reservation", exist_ok=True), range(16)))

    assert results.count(True) == 1
    assert results.count(False) == 15
