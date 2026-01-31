# invariant/ops/ids.py
from __future__ import annotations

import re


_WORKSPACE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def normalize_workspace_id(raw: str) -> str:
    """
    Strict workspace id.
    - 1..64 chars
    - [a-zA-Z0-9._-]
    - must start with alnum
    - no path separators, no whitespace

    Examples:
      'project-x', 'demo_01', 'a.b-c'
    """
    if not isinstance(raw, str):
        raise TypeError("workspace_id must be str")

    ws = raw.strip()
    if not ws:
        raise ValueError("workspace_id must be non-empty")

    # hard blocks
    if "/" in ws or "\\" in ws:
        raise ValueError("workspace_id must not contain path separators")
    if ".." in ws:
        raise ValueError("workspace_id must not contain '..'")

    if not _WORKSPACE_RE.match(ws):
        raise ValueError(
            "workspace_id must match: ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$"
        )

    return ws
