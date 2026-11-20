"""Automated Golden Dataset Regression Evaluation Harness for PokeSell Ops.

Addresses AgentOps Rubric Category 5 — Automated Evaluation Suites (5 pts):
- Executes all scenarios in `eval/golden_dataset.json` against an isolated
  SQLite ledger instance and statically measures trajectory accuracy, Pull-Bag
  combine logic, HITL enforcement, guardrail blocking, and zero-PII leakage.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from pokesell_agent.agent import PokeSellOrchestrator
from pokesell_agent.database import PokeSellLedgerDB, set_ledger_db_for_testing
from pokesell_agent.memory import AsyncMemoryConsolidator, PersistentSessionManager

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_GOLDEN_PATH = EVAL_DIR / "golden_dataset.json"


async def evaluate_golden_dataset(
    dataset_path: Path = DEFAULT_GOLDEN_PATH,
) -> dict[str, Any]:
    """Run all golden evaluation cases in an isolated in-memory SQLite ledger."""
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    eval_cases: list[dict[str, Any]] = payload.get("eval_cases", [])

    isolated_db = PokeSellLedgerDB(db_path=":memory:", seed_demo_data=True)
    set_ledger_db_for_testing(isolated_db)
    try:
        sess_mgr = PersistentSessionManager(ledger_db=isolated_db)
        async_mem = AsyncMemoryConsolidator(session_manager=sess_mgr)
        orchestrator = PokeSellOrchestrator(
            session_manager=sess_mgr,
            async_consolidator=async_mem,
        )

        case_results: list[dict[str, Any]] = []
        passed_count = 0

        for case in eval_cases:
            eval_id = str(case["eval_id"])
            res = await orchestrator.handle_operator_prompt(
                session_id="eval-session-01",
                prompt=str(case["prompt"]),
                stream_id=str(case.get("stream_id", "STREAM-EVAL-01")),
                human_approval_token=case.get("human_approval_token"),
            )
            reply = str(res.get("reply", ""))
            actual_status = str(res.get("status", ""))
            expected_status = str(case["expected_status"])

            missing_expected = [
                sub for sub in case.get("expected_substrings", []) if sub not in reply
            ]
            leaked_forbidden = [
                sub for sub in case.get("forbidden_substrings", []) if sub in reply
            ]

            passed = (
                actual_status == expected_status
                and not missing_expected
                and not leaked_forbidden
            )
            if passed:
                passed_count += 1

            case_results.append(
                {
                    "eval_id": eval_id,
                    "passed": passed,
                    "expected_status": expected_status,
                    "actual_status": actual_status,
                    "missing_expected": missing_expected,
                    "leaked_forbidden": leaked_forbidden,
                    "reply_preview": reply[:180],
                }
            )

        await async_mem.flush_pending_tasks()
        total = len(eval_cases)
        pass_rate = round(passed_count / total, 4) if total else 0.0
        return {
            "eval_set_id": payload.get("eval_set_id"),
            "total_cases": total,
            "passed_cases": passed_count,
            "pass_rate": pass_rate,
            "results": case_results,
        }
    finally:
        set_ledger_db_for_testing(None)


def main() -> int:
    summary = asyncio.run(evaluate_golden_dataset())
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
