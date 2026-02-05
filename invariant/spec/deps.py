# invariant/spec/deps.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

DepsMode = Literal["none", "online", "bundle"]

def _parse_mode(mode: str) -> DepsMode:
    m = mode.strip().lower()
    match m:
        case "none":
            return "none"
        case "online":
            return "online"
        case "bundle":
            return "bundle"
        case _:
            raise ValueError("deps.mode must be one of: none|online|bundle")


@dataclass(frozen=True, slots=True)
class PluginDepsSpec:
    """
    Dependencies spec for plugin isolation.

    Modes:
      - none   : run in core venv (no isolated env)
      - online : create isolated venv and download wheels from index
      - bundle : create isolated venv and install from plugin-provided wheelhouse
    """

    mode: DepsMode = "none"

    # required for online/bundle
    requirements: str | None = None  # path relative to plugin_root

    # bundle mode only
    wheels_dir: str | None = None  # path relative to plugin_root

    # online mode (optional pip config)
    index_url: str | None = None
    extra_index_urls: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ()
    only_binary: bool = True

    @staticmethod
    def from_manifest(raw: Any) -> PluginDepsSpec:
        if raw is None:
            return PluginDepsSpec(mode="none")
        if not isinstance(raw, Mapping):
            raise TypeError("deps must be an object")
        # normalize Mapping -> dict (safe copy)
        raw = dict(raw)

        mode_raw = raw.get("mode", "none")
        if not isinstance(mode_raw, str):
            raise TypeError("deps.mode must be string")
        mode = _parse_mode(mode_raw)

        requirements = raw.get("requirements")
        if requirements is not None and not isinstance(requirements, str):
            raise TypeError("deps.requirements must be string or null")

        wheels_dir = raw.get("wheels_dir")
        if wheels_dir is not None and not isinstance(wheels_dir, str):
            raise TypeError("deps.wheels_dir must be string or null")

        index_url = raw.get("index_url")
        if index_url is not None and not isinstance(index_url, str):
            raise TypeError("deps.index_url must be string or null")

        extra = raw.get("extra_index_urls") or ()
        if not isinstance(extra, (list, tuple)):
            raise TypeError("deps.extra_index_urls must be list/tuple")
        extra_index_urls = tuple(str(x) for x in extra)

        th = raw.get("trusted_hosts") or ()
        if not isinstance(th, (list, tuple)):
            raise TypeError("deps.trusted_hosts must be list/tuple")
        trusted_hosts = tuple(str(x) for x in th)

        only_binary = raw.get("only_binary", True)
        if not isinstance(only_binary, bool):
            raise TypeError("deps.only_binary must be boolean")

        spec = PluginDepsSpec(
            mode=mode,
            requirements=requirements,
            wheels_dir=wheels_dir,
            index_url=index_url,
            extra_index_urls=extra_index_urls,
            trusted_hosts=trusted_hosts,
            only_binary=only_binary,
        )

        # Validate required fields for isolated modes
        if spec.mode != "none":
            if not spec.requirements:
                raise ValueError("deps.requirements is required for online/bundle mode")
            if spec.mode == "bundle" and not spec.wheels_dir:
                raise ValueError("deps.wheels_dir is required for bundle mode")

        return spec

    def is_isolated(self) -> bool:
        return self.mode in ("online", "bundle")
    