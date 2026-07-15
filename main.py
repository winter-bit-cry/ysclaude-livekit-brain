from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    port = os.getenv("PORT", "8080")
    processes = [
        subprocess.Popen([
            sys.executable,
            "-m",
            "uvicorn",
            "token_server:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ]),
        subprocess.Popen([sys.executable, "agent.py", "start"]),
    ]
    stopping = False

    def shutdown(_signum: int | None = None, _frame: object | None = None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        while not stopping:
            for process in processes:
                status = process.poll()
                if status is not None:
                    print(
                        f"Brain process {process.args!r} exited with status {status}; stopping service.",
                        file=sys.stderr,
                        flush=True,
                    )
                    shutdown()
                    return status or 1
            time.sleep(0.5)
    finally:
        shutdown()
        deadline = time.monotonic() + 10
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
        for process in processes:
            process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

