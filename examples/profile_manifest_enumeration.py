"""Read-only comparison of manifest discovery enumeration implementations.

The production ManifestSource.discover stays unchanged. This is a measured
candidate investigation, NOT an authorization to replace Path.rglob().
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import platform
import statistics
import sys
import time
from itertools import zip_longest
from pathlib import Path
from typing import Any


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare exact manifest enumeration with a read-only candidate."
    )
    parser.add_argument("--proof-root", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=40)
    return parser.parse_args()


def _validate_samples(count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("samples must be an integer")
    if not 5 <= count <= 120:
        raise ValueError("samples must be between 5 and 120")


def _baseline(roots: list[Path]) -> list[Path]:
    """Mirror the current manifest discovery enumeration."""
    found: list[Path] = []
    for root in roots:
        if root.exists():
            found.extend(root.rglob("manifest.json"))
    return found


def _candidate(roots: list[Path]) -> list[Path]:
    """Experiment only: os.walk with explicit no-followlinks policy."""
    found: list[Path] = []
    marker = os.path.normcase("manifest.json")
    for root in roots:
        if not root.exists():
            continue
        for folder, _dirs, filenames in os.walk(root, followlinks=False):
            for filename in filenames:
                if os.path.normcase(filename) == marker:
                    found.append(Path(folder) / filename)
    return found


def _compare(roots: list[Path]) -> dict[str, Any]:
    baseline = [str(path) for path in _baseline(roots)]
    candidate = [str(path) for path in _candidate(roots)]
    equal_sequence = baseline == candidate
    same_multiset = sorted(baseline) == sorted(candidate)
    return {
        "eligible": equal_sequence,
        "same_order_and_paths": equal_sequence,
        "same_multiset": same_multiset,
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "first_differences": [
            {"position": i, "baseline": lhs, "candidate": rhs}
            for i, (lhs, rhs) in enumerate(
                zip_longest(baseline, candidate, fillvalue=None)
            )
            if lhs != rhs
        ][:10],
        "baseline_paths": baseline,
        "candidate_paths": candidate,
    }


def _stats(samples: list[float]) -> dict[str, Any]:
    if not samples:
        raise ValueError("No measurements")
    ordered = sorted(samples)
    return {
        "n": len(samples),
        "mean_ms": round(statistics.mean(samples), 4),
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(ordered[math.ceil(0.95 * len(samples)) - 1], 4),
        "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
        "samples_ms": [round(x, 4) for x in samples],
    }


def _installed_runplane():
    runplane = importlib.import_module("runplane")
    imported = Path(runplane.__file__).resolve()
    venv = Path(sys.prefix).resolve()
    if sys.prefix == sys.base_prefix or not imported.is_relative_to(venv):
        raise RuntimeError("Use an independently installed Runplane consumer venv")
    return runplane, imported


def _manifest_roots(package_file: Path) -> list[Path]:
    bootstrap = importlib.import_module("runplane.bootstrap")
    stack = bootstrap.runtime_stack()
    packs = package_file.parents[1] / "plugin_packs"
    return [
        *(packs / "core" / item for item in stack["required_plugin_dirs"]),
        *(
            packs / item
            for item in stack["optional_plugin_packs"]
            if (packs / item).is_dir()
        ),
    ]


def _inventory(runplane) -> dict[str, Any]:
    with runplane.Runplane.create() as sdk:
        plugins = [(item.id, item.version) for item in sdk.service.list_plugins()]
        effects = [effect["id"] for effect in sdk.list_effects()]
    encoded = json.dumps(
        {"plugins": plugins, "effects": effects}, sort_keys=True
    ).encode("utf-8")
    return {
        "plugins": plugins,
        "effects": effects,
        "plugins_count": len(plugins),
        "effects_count": len(effects),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def run(proof_root: Path, samples: int) -> dict[str, Any]:
    _validate_samples(samples)
    proof_root = proof_root.expanduser().resolve()
    if proof_root.exists():
        raise ValueError("Proof root must be new and not overwrite any files")

    runplane, imported = _installed_runplane()
    roots = _manifest_roots(imported)
    before = _inventory(runplane)
    parity = _compare(roots)

    for _ in range(5):
        _baseline(roots)
        _candidate(roots)

    times: dict[str, list[float]] = {"rglob": [], "os_walk": []}
    for index in range(samples):
        if index % 2:
            actions = (("os_walk", _candidate), ("rglob", _baseline))
        else:
            actions = (("rglob", _baseline), ("os_walk", _candidate))
        for name, fn in actions:
            start = time.perf_counter_ns()
            observed = fn(roots)
            duration_ms = (time.perf_counter_ns() - start) / 1e6
            times[name].append(duration_ms)
            # Detect a changed directory or a nondeterministic enumeration.
            expected = parity[
                "baseline_paths" if name == "rglob" else "candidate_paths"
            ]
            if [str(path) for path in observed] != expected:
                raise RuntimeError(f"{name} changed during measurement; cannot compare")

    after = _inventory(runplane)
    if before != after:
        raise RuntimeError("SDK plugin/effect inventory changed during pilot")

    outcome = {
        "schema": "INVARIANT_MANIFEST_ENUMERATION_PARITY_PERF_V1",
        "pass": True,
        "candidate_eligible": parity["eligible"],
        "environment": {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "runplane_import": str(imported),
        },
        "methodology": {
            "samples": samples,
            "warmup_pairs": 5,
            "clock": "perf_counter_ns",
            "alternating": True,
            "repeats_are_separate": True,
            "notes": (
                "This only compares enumeration: manifest parsing, "
                "containment revalidation, and plugin registration are "
                "not included. Exact path sequence is required; lower "
                "latency alone does not establish semantic equivalence."
            ),
        },
        "roots": [str(path) for path in roots],
        "inventory_before": before,
        "inventory_after": after,
        "parity": parity,
        "timing": {key: _stats(value) for key, value in times.items()},
    }
    proof_root.mkdir(parents=True)
    output = proof_root / "manifest-enumeration-proof.json"
    output.write_text(
        json.dumps(outcome, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "INVARIANT_MANIFEST_ENUMERATION_PILOT_JSON="
        + json.dumps(
            {
                "pass": True,
                "candidate_eligible": outcome["candidate_eligible"],
                "proof": str(output),
                "manifest_count": parity["baseline_count"],
                "inventory_sha256": before["sha256"],
                "rglob_median_ms": outcome["timing"]["rglob"]["median_ms"],
                "os_walk_median_ms": outcome["timing"]["os_walk"]["median_ms"],
            },
            sort_keys=True,
        )
    )
    return outcome


if __name__ == "__main__":
    try:
        arguments = _arguments()
        run(arguments.proof_root, arguments.samples)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print(f"INVARIANT_MANIFEST_ENUMERATION_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
