# invariant/spec/_version.py
from __future__ import annotations

import re
from functools import total_ordering
from typing import Any


class InvalidVersion(ValueError):
    """Raised when an Invariant concrete version cannot be parsed."""

    def __init__(self, version: object) -> None:
        self.version = version
        super().__init__(f"Invalid version: {version!r}")


_VERSION_RE = re.compile(
    r"""
    ^\s*
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?
        (?P<release>[0-9]+(?:\.[0-9]+)*)
        (?P<pre>
            [-_.]?
            (?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)
            [-_.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [-_.]?
                (?P<post_l>post|rev|r)
                [-_.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>
            [-_.]?
            dev
            [-_.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_PRE_LABELS = {
    "alpha": "a",
    "a": "a",
    "beta": "b",
    "b": "b",
    "c": "rc",
    "rc": "rc",
    "pre": "rc",
    "preview": "rc",
}
_PRE_RANK = {"a": 0, "b": 1, "rc": 2}

_LocalPart = int | str
_Pre = tuple[str, int]


def _release_key(release: tuple[int, ...]) -> tuple[int, ...]:
    """Ignore trailing release zeroes for ordering/equality (1 == 1.0 == 1.0.0)."""
    out = release
    while out and out[-1] == 0:
        out = out[:-1]
    return out


def _parse_local(local: str | None) -> tuple[_LocalPart, ...] | None:
    if local is None:
        return None
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"[-_.]", local)
    )


@total_ordering
class Version:
    """
    Concrete plugin version used by Invariant.

    The accepted syntax and precedence intentionally preserve the concrete-version
    behavior Invariant previously obtained from ``packaging.version.Version``:
    epochs, arbitrary numeric release segments, a/b/rc prereleases, post releases,
    dev releases, and local version labels. Version specifier expressions are not
    part of this type.
    """

    __slots__ = (
        "_key_cache",
        "_local_parts",
        "dev",
        "epoch",
        "local",
        "post",
        "pre",
        "release",
    )

    def __init__(self, version: str) -> None:
        if not isinstance(version, str):
            raise TypeError("version must be a string")

        match = _VERSION_RE.match(version)
        if match is None:
            raise InvalidVersion(version)

        self.epoch = int(match.group("epoch") or 0)
        self.release = tuple(int(part) for part in match.group("release").split("."))

        pre_label = match.group("pre_l")
        if pre_label is None:
            self.pre: _Pre | None = None
        else:
            normalized_label = _PRE_LABELS[pre_label.lower()]
            self.pre = (normalized_label, int(match.group("pre_n") or 0))

        if match.group("post") is None:
            self.post: int | None = None
        else:
            self.post = int(match.group("post_n1") or match.group("post_n2") or 0)

        if match.group("dev") is None:
            self.dev: int | None = None
        else:
            self.dev = int(match.group("dev_n") or 0)

        self._local_parts = _parse_local(match.group("local"))
        self.local = (
            None
            if self._local_parts is None
            else ".".join(str(part) for part in self._local_parts)
        )
        self._key_cache = self._build_key()

    def _build_key(self) -> tuple[Any, ...]:
        release = _release_key(self.release)

        # A dev-only release sorts before an explicit prerelease. Otherwise the
        # absence of a prerelease means the final release and sorts after a/b/rc.
        if self.pre is None:
            pre_key: tuple[Any, ...] = (
                (-1,) if self.dev is not None and self.post is None else (1,)
            )
        else:
            pre_key = (0, _PRE_RANK[self.pre[0]], self.pre[1])

        # No post release sorts before any post release.
        post_key: tuple[Any, ...] = (-1,) if self.post is None else (0, self.post)

        # A dev release sorts before the corresponding non-dev release.
        dev_key: tuple[Any, ...] = (1,) if self.dev is None else (0, self.dev)

        # A public version sorts before the same version with a local label.
        if self._local_parts is None:
            local_key: tuple[Any, ...] = (-1,)
        else:
            local_key = (
                0,
                tuple(
                    (1, part) if isinstance(part, int) else (0, part)
                    for part in self._local_parts
                ),
            )

        return (self.epoch, release, pre_key, post_key, dev_key, local_key)

    def __repr__(self) -> str:
        return f"<Version({str(self)!r})>"

    def __str__(self) -> str:
        out = (f"{self.epoch}!" if self.epoch else "") + ".".join(
            str(part) for part in self.release
        )
        if self.pre is not None:
            out += f"{self.pre[0]}{self.pre[1]}"
        if self.post is not None:
            out += f".post{self.post}"
        if self.dev is not None:
            out += f".dev{self.dev}"
        if self.local is not None:
            out += f"+{self.local}"
        return out

    def __hash__(self) -> int:
        return hash(self._key_cache)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key_cache == other._key_cache

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key_cache < other._key_cache
