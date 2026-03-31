"""
Deligo Map Service API

Usage:
    python run.py
    python run.py --port 8000
    python run.py --reload
"""

import argparse
import uvicorn
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file


def main():
    parser = argparse.ArgumentParser(description="Start the Deligo Mapper API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    uvicorn.run(
        "src.api.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
