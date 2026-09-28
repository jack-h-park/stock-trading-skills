#!/usr/bin/env python3
"""
codex_result.py — read one `codex exec --json` event stream for run-review.sh.

run-review.sh used to parse the single JSON object `claude -p --output-format
json` prints. `codex exec --json` prints a JSONL event stream instead, and three
things the wrapper needs live in different events:

    usage    turn.completed .usage (input includes cached input, OpenAI-style)
    failure  turn.failed .error.message, else the last top-level `error` event
    answer   the last item.completed of type agent_message

Two shapes in that stream look like failures and are not, both seen live:

  * item.completed items of type "error" are warnings ("Model metadata for ... not
    found", "Skill descriptions were shortened"). A run that emits them can
    still complete.
  * Top-level `error` events include "Reconnecting... N/5" — codex retrying on
    its own. Only the final one, or turn.failed, says why the run ended.

Subcommands (all read the JSONL file; none raise on a malformed line):

    usage JSONL JOB MODEL LOGFILE   append one trader-usage row; exits 0 always
    retryable JSONL                 exit 0 if a second attempt could answer
                                    differently, 1 if not
    reason JSONL                    print one line saying why the run failed
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROVIDER = "openai-codex"

# Capacity and transport answers. A request the server rejected on its merits
# (400 invalid request, 401 not logged in, 403) gets the same answer twice.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}
RETRYABLE_TEXT = (
    "overloaded",
    "rate limit",
    "stream disconnected",
    "timed out",
    "temporarily unavailable",
)
_STATUS = re.compile(r"(?:status[\"']?\s*[:=]?\s*|HTTP\s+|status\s+)(\d{3})\b", re.IGNORECASE)


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def usage_totals(events: list[dict[str, Any]]) -> dict[str, int] | None:
    """Summed usage across every completed turn, or None if no turn completed."""
    totals = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
    seen = False
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        seen = True
        totals["input"] += int(usage.get("input_tokens") or 0)
        totals["cached"] += int(usage.get("cached_input_tokens") or 0)
        totals["output"] += int(usage.get("output_tokens") or 0)
        totals["reasoning"] += int(usage.get("reasoning_output_tokens") or 0)
    return totals if seen else None


def usage_row(
    events: list[dict[str, Any]], job: str, model: str, day: str, env: str
) -> dict[str, Any] | None:
    """One trader-usage.jsonl row in the schema the control plane already reads.

    `input_tokens` is UNCACHED input: codex reports input inclusive of cached
    input, while the existing rows (from `claude -p`) keep cache reads apart, and
    the collector prices the two at different rates. `cost_usd` is omitted on
    purpose — codex does not report one, and a guessed figure would be
    indistinguishable from a measured one. The control plane prices the row from
    its own table, keyed on `provider` + `model`.
    """
    totals = usage_totals(events)
    if totals is None:
        return None
    cached = min(totals["cached"], totals["input"])
    return {
        "date": day,
        "job": job,
        "provider": PROVIDER,
        "model": model,
        "input_tokens": totals["input"] - cached,
        "output_tokens": totals["output"],
        "cache_read_tokens": cached,
        "reasoning_output_tokens": totals["reasoning"],
        "env": env,
    }


def _message_text(raw: Any) -> str:
    """An error message, unwrapped when codex nests the server's JSON inside it."""
    text = str(raw or "").strip()
    if text.startswith("{"):
        try:
            inner = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(inner, dict):
            err = inner.get("error")
            if isinstance(err, dict) and err.get("message"):
                status = inner.get("status")
                prefix = f"HTTP {status}: " if status else ""
                return prefix + str(err["message"])
    return text


def failure_message(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("type") == "turn.failed":
            err = event.get("error")
            return _message_text(err.get("message") if isinstance(err, dict) else err)
    for event in reversed(events):
        if event.get("type") == "error":
            text = _message_text(event.get("message"))
            if not text.startswith("Reconnecting"):
                return text
    return None


def final_answer(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
        ):
            return str(item.get("text") or "").strip() or None
    return None


def is_retryable(events: list[dict[str, Any]]) -> bool:
    # No events at all: the CLI died before saying anything (killed, crashed,
    # binary missing). A second attempt costs nothing if it dies again.
    if not events:
        return True
    message = failure_message(events)
    if not message:
        return False
    for match in _STATUS.finditer(message):
        if int(match.group(1)) in RETRYABLE_STATUS:
            return True
    lowered = message.lower()
    return any(text in lowered for text in RETRYABLE_TEXT)


def reason(events: list[dict[str, Any]]) -> str:
    if not events:
        return "no event stream (the CLI produced no output)"
    message = failure_message(events)
    if message:
        return message.splitlines()[0][:300]
    answer = final_answer(events)
    if answer:
        return answer.split(". ")[0].splitlines()[0][:300]
    return "failed without a message"


def main(argv: list[str]) -> int:
    if len(argv) == 5 and argv[0] == "usage":
        _, jsonl, job, model, logfile = argv
        try:
            row = usage_row(
                read_events(Path(jsonl)),
                job,
                model,
                datetime.date.today().isoformat(),
                os.environ.get("TRADER_ENV", "prod"),
            )
            if row is not None:
                log = Path(logfile)
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
        except Exception:
            pass  # usage capture must never fail the review
        return 0
    if len(argv) == 2 and argv[0] == "retryable":
        return 0 if is_retryable(read_events(Path(argv[1]))) else 1
    if len(argv) == 2 and argv[0] == "reason":
        print(reason(read_events(Path(argv[1]))))
        return 0
    print(__doc__.split("Subcommands", 1)[1] if __doc__ else "bad arguments", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
