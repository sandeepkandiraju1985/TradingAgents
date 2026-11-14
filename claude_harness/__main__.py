"""Command line for the Claude harness: ``python -m claude_harness <command>``.

    start TICKER [--date YYYY-MM-DD] [--analysts market,sentiment,news,fundamentals] [--rounds N]
    data RUN_DIR TOOL [ARGS...]
    record RUN_DIR [--final]

Run ``python -m claude_harness data --help`` for the data tools.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m claude_harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="open a run folder for one stock")
    p.add_argument("ticker")
    p.add_argument("--date", help="analysis date, YYYY-MM-DD (default: the latest weekday)")
    p.add_argument("--analysts", default="market,sentiment,news,fundamentals")
    p.add_argument("--rounds", type=int, default=1, help="debate rounds for each debate")

    p = sub.add_parser("data", help="market data for a run, as of its date")
    p.add_argument("run_dir")
    p.add_argument("tool")
    p.add_argument("args", nargs="*")

    p = sub.add_parser("record", help="record the run's progress in the console")
    p.add_argument("run_dir")
    p.add_argument("--final", action="store_true", help="the decision is written: finish the run")

    args = parser.parse_args(argv)

    # Windows consoles default to a narrow code page; reports carry ₹ and other symbols.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from . import run, tools
    if args.command == "start":
        print(json.dumps(run.start(args.ticker, args.date, args.analysts, args.rounds), indent=2))
    elif args.command == "data":
        print(tools.call(args.run_dir, args.tool, args.args))
    else:
        print(json.dumps(run.record(args.run_dir, final=args.final), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
