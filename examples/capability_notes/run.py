from __future__ import annotations

from pathlib import Path

from invariant.api import EphemeralPolicy, PluginService, SandboxLimits


def main() -> None:
    example_dir = Path(__file__).resolve().parent
    service = PluginService.from_manifest_paths([example_dir])
    policy = EphemeralPolicy(limits=SandboxLimits(allow_fs_write=True))

    try:
        with service.execution(policy=policy) as execution:
            notes = execution.get("example:notes")
            notes.save("governed dependency")
            print(f"Notes: {notes.load()}")
    finally:
        service.close()


if __name__ == "__main__":
    main()
