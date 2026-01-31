# invariant/ops/fs.py
from __future__ import annotations

import shutil
import time
from pathlib import Path


def rmtree_best_effort(path: Path, *, retries: int = 5, delay: float = 0.05) -> None:
    """
    Best-effort directory removal.
    Windows can keep file handles briefly; retry a bit.

    Never raises if the path is already gone.
    Raises only if it still fails after retries.
    """
    if not path.exists():
        return

    last_exc: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except Exception as e:
            last_exc = e
            time.sleep(delay)

    if last_exc:
        raise last_exc
