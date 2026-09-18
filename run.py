"""
Deligo Map Service API

Usage:
    python run.py
    python run.py --port 8000
    python run.py --reload
"""

import argparse
import asyncio
import os
import sys
import uvicorn
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file


def configure_windows_event_loop() -> None:
    # On Windows, Proactor loop can emit noisy _call_connection_lost tracebacks
    # when sockets/pipes close during shutdown. Selector loop is more stable here.
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            # Do not block startup if policy cannot be changed.
            pass


def main():
    configure_windows_event_loop()

    parser = argparse.ArgumentParser(description="Start the Deligo Mapper API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--workers", type=int, default=1, help="Number of uvicorn worker processes (default: 1)")
    args = parser.parse_args()

    if args.reload and args.workers > 1:
        print("Warning: --reload is incompatible with --workers > 1; ignoring --workers")
        args.workers = 1

    # An SSE stream never ends on its own, and uvicorn's default graceful
    # shutdown waits for every open connection — so SIGTERM alone would leave
    # the container hanging until Docker's grace period ran out (a `compose up
    # --build` then sits for minutes on "Recreate"). Cut the wait short: open
    # streams are dropped and the browser reconnects with Last-Event-ID.
    graceful_timeout = int(os.getenv("SHUTDOWN_GRACE_SECONDS", "5"))

    uvicorn.run(
        "src.api.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else None,
        timeout_graceful_shutdown=graceful_timeout,
    )


if __name__ == "__main__":
    main()
