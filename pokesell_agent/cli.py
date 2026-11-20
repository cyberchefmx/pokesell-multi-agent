"""Interactive & Single-Command Terminal CLI for PokeSell Ops Live Streams."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from pokesell_agent.agent import default_orchestrator


async def _run_cli() -> int:
    parser = argparse.ArgumentParser(
        description="PokeSell Ops — Pokémon TCG Live-Stream Multi-Agent CLI"
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Natural-language live-stream command (omit for interactive REPL).",
    )
    parser.add_argument(
        "--stream-id",
        default="STREAM-LIVE-001",
        help="Active live stream identifier (default: STREAM-LIVE-001).",
    )
    parser.add_argument(
        "--session-id",
        default="cli-operator-session",
        help="Persistent session ID for cross-turn memory.",
    )
    parser.add_argument(
        "--approval-token",
        default=None,
        help="Optional HITL approval token (e.g., APPROVE-VOID-SALE-0001).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full structured JSON payload.",
    )
    args = parser.parse_args()

    if args.prompt:
        res = await default_orchestrator.handle_operator_prompt(
            session_id=args.session_id,
            prompt=args.prompt,
            stream_id=args.stream_id,
            human_approval_token=args.approval_token,
        )
        await default_orchestrator.async_consolidator.flush_pending_tasks()
        if args.json:
            sys.stdout.write(json.dumps(res, indent=2) + "\n")
        else:
            sys.stdout.write(res["reply"] + "\n")
        return 0

    sys.stdout.write(
        f"PokeSell Ops Live-Stream CLI (Stream: {args.stream_id}) — Type 'exit' to quit.\n"
    )
    while True:
        try:
            line = input("pokesell> ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            break
        if not line or line.lower() in ("exit", "quit"):
            break
        res = await default_orchestrator.handle_operator_prompt(
            session_id=args.session_id,
            prompt=line,
            stream_id=args.stream_id,
        )
        sys.stdout.write(res["reply"] + "\n\n")

    await default_orchestrator.async_consolidator.flush_pending_tasks()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run_cli()))


if __name__ == "__main__":
    main()
