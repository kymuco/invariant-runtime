from pathlib import Path

from invariant.api import PluginService


def main() -> None:
    plugin_dir = Path(__file__).resolve().parent
    service = PluginService.from_manifest_paths([plugin_dir])
    try:
        hello = service.get("example:hello")
        print(hello.greet("Invariant"))
    finally:
        service.close()


if __name__ == "__main__":
    main()
