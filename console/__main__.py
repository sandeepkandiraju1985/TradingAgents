"""Start the console: ``python -m console`` from the project folder.

The project folder matters: that is where your .env (with the Gemini key) is
read from.
"""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m console", description="TradingAgents Console")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--db", help="database file (default: ~/.tradingagents/console.db)")
    args = parser.parse_args()

    import uvicorn

    import tradingagents  # noqa: F401  loads .env from this folder before anything reads the environment

    from .engine import Console
    from .server import create_app
    from .store import Store, default_db_path

    # One line per agent step from the console; libraries only report problems.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("console").setLevel(logging.INFO)
    db = args.db or default_db_path()
    console = Console(Store(db))
    console.start_monitor()
    app = create_app(console)

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  TradingAgents Console: {url}\n  Data: {db}\n  Stop with Ctrl+C.\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        console.stop()


if __name__ == "__main__":
    main()
