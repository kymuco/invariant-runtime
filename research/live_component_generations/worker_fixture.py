"""Disposable subprocess fixture for worker-lifecycle research, not plugin API."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time

ARTIFACT_REVISION = "template"  # Patched in *copies* before starting workers.


def send(**payload: object) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--mode",
        choices=("normal", "fail_before_bind", "fail_after_bind", "hang_ready", "hang_stop", "ignore_terminate"),
        default="normal",
    )
    args = parser.parse_args()

    if args.mode == "fail_before_bind":
        send(kind="error", error="failure before resource allocation")
        return 11

    if args.mode == "ignore_terminate" and os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    resource = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        resource.bind(("127.0.0.1", args.port))
        resource.listen(1)
    except OSError as exc:
        send(kind="error", error=f"exclusive resource busy: {exc}")
        resource.close()
        return 12

    try:
        if args.mode == "fail_after_bind":
            send(kind="error", error="failure after allocating resource")
            while True:
                time.sleep(1)
        if args.mode == "hang_ready":
            while True:
                time.sleep(1)
        send(kind="ready", revision=ARTIFACT_REVISION, port=args.port, pid=os.getpid())
        for raw in sys.stdin:
            try:
                request = json.loads(raw)
            except (ValueError, TypeError):
                send(kind="error", error="invalid command")
                continue
            operation = request.get("op")
            if operation == "ping":
                send(kind="pong", revision=ARTIFACT_REVISION, pid=os.getpid())
            elif operation == "crash":
                os._exit(17)
            elif operation == "stop":
                if args.mode in ("hang_stop", "ignore_terminate"):
                    while True:
                        time.sleep(1)
                send(kind="stopping", pid=os.getpid())
                return 0
            else:
                send(kind="error", error="unknown command")
        return 0
    finally:
        resource.close()


if __name__ == "__main__":
    raise SystemExit(main())
