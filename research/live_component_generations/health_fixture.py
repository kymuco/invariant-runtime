"""Disposable worker with independent heartbeat and RPC paths for research."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time

REVISION = "template"  # Replaced in a separate version-pinned artifact.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--heartbeat-interval", type=float, default=0.04)
    args = parser.parse_args()
    output_lock = threading.Lock()
    heartbeat_enabled = threading.Event()
    heartbeat_enabled.set()
    done = threading.Event()

    def emit(**payload: object) -> None:
        with output_lock:
            print(json.dumps(payload, sort_keys=True), flush=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        owner.bind(("127.0.0.1", args.port))
        owner.listen(1)

        def heartbeats() -> None:
            counter = 0
            while not done.wait(args.heartbeat_interval):
                if heartbeat_enabled.is_set():
                    counter += 1
                    emit(kind="heartbeat", revision=REVISION, pid=os.getpid(), sequence=counter)

        monitor = threading.Thread(target=heartbeats, daemon=True)
        monitor.start()
        emit(kind="ready", revision=REVISION, pid=os.getpid(), port=args.port)
        try:
            for line in sys.stdin:
                message = json.loads(line)
                operation = message.get("op")
                request_id = message.get("id")
                if operation == "ping":
                    emit(kind="reply", id=request_id, revision=REVISION, value="pong")
                elif operation == "slow":
                    time.sleep(float(message.get("duration", 0.25)))
                    emit(kind="reply", id=request_id, revision=REVISION, value="slow_ok")
                elif operation == "silence":
                    heartbeat_enabled.clear()
                    emit(kind="reply", id=request_id, revision=REVISION, value="silenced")
                elif operation == "hang":
                    while True:
                        time.sleep(1)
                elif operation == "freeze":
                    heartbeat_enabled.clear()
                    while True:
                        time.sleep(1)
                elif operation == "crash":
                    os._exit(17)
                elif operation == "stop":
                    emit(kind="reply", id=request_id, revision=REVISION, value="stopping")
                    return 0
                else:
                    emit(kind="reply", id=request_id, revision=REVISION, value="unknown")
        finally:
            done.set()
            monitor.join(timeout=0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
