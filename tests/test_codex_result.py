"""scripts/codex_result.py against event streams shaped like real `codex exec --json`.

Every fixture below is a transcription of a stream captured on 2026-09-28 (codex
0.147 on the MacBook, 0.156 on the ops host), with paths and request ids removed.
Two of them exist because the obvious reading is wrong:

  * SUCCESS carries an item.completed of type "error" (a model-metadata warning)
    and still completed. Treating it as a failure would fail every run.
  * NOT_LOGGED_IN carries five "Reconnecting... N/5" top-level errors before the
    final one. The reason must be the final one, and a 401 must not be retried.

Run: python3 -m pytest tests/test_codex_result.py
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import codex_result as cr  # noqa: E402


def stream(*events):
    return [dict(e) for e in events]


SUCCESS = stream(
    {"type": "thread.started", "thread_id": "t"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "error",
            "message": "Model metadata for `gpt-6-sol` not found. Defaulting to fallback metadata.",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "mcp_tool_call",
            "server": "robinhood",
            "tool": "get_accounts",
            "arguments": {},
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {"id": "item_3", "type": "agent_message", "text": "Wrote the review. 3 signals."},
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 170305,
            "cached_input_tokens": 145664,
            "cache_write_input_tokens": 0,
            "output_tokens": 1006,
            "reasoning_output_tokens": 185,
        },
    },
)

NOT_LOGGED_IN = stream(
    {"type": "thread.started", "thread_id": "t"},
    {"type": "turn.started"},
    *[
        {
            "type": "error",
            "message": f"Reconnecting... {n}/5 (unexpected status 401 Unauthorized: "
            "Missing bearer or basic authentication in header, url: https://api.openai.com/v1/responses)",
        }
        for n in range(1, 6)
    ],
    {
        "type": "error",
        "message": "unexpected status 401 Unauthorized: Missing bearer or basic "
        "authentication in header, url: https://api.openai.com/v1/responses",
    },
    {
        "type": "turn.failed",
        "error": {
            "message": "unexpected status 401 Unauthorized: Missing bearer "
            "or basic authentication in header, url: https://api.openai.com/v1/responses"
        },
    },
)

REFUSAL = "The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account."

MODEL_REFUSED = stream(
    {"type": "thread.started", "thread_id": "t"},
    {
        "type": "error",
        "message": json.dumps(
            {
                "type": "error",
                "status": 400,
                "error": {
                    "type": "invalid_request_error",
                    "message": REFUSAL,
                },
            }
        ),
    },
    {
        "type": "turn.failed",
        "error": {
            "message": json.dumps(
                {
                    "type": "error",
                    "status": 400,
                    "error": {
                        "type": "invalid_request_error",
                        "message": REFUSAL,
                    },
                }
            )
        },
    },
)


def capacity(status):
    return stream(
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.failed", "error": {"message": f"unexpected status {status}: upstream busy"}},
    )


# ── usage ────────────────────────────────────────────────────────────────────


def test_usage_row_splits_cached_input_out_of_input():
    row = cr.usage_row(SUCCESS, "trading-review", "gpt-6-sol", "2026-09-28", "prod")
    assert row == {
        "date": "2026-09-28",
        "job": "trading-review",
        "provider": "openai-codex",
        "model": "gpt-6-sol",
        "input_tokens": 170305 - 145664,
        "output_tokens": 1006,
        "cache_read_tokens": 145664,
        "reasoning_output_tokens": 185,
        "env": "prod",
    }


def test_usage_row_carries_no_cost():
    """A guessed cost would be indistinguishable from a measured one."""
    row = cr.usage_row(SUCCESS, "trading-review", "gpt-6-sol", "2026-09-28", "prod")
    assert "cost_usd" not in row


def test_usage_sums_every_completed_turn():
    two = SUCCESS + [SUCCESS[-1]]
    assert cr.usage_totals(two)["output"] == 2 * 1006


def test_no_completed_turn_writes_no_row():
    assert cr.usage_row(NOT_LOGGED_IN, "trading-review", "gpt-6-sol", "2026-09-28", "prod") is None


def test_usage_cli_appends_one_line_and_never_fails(tmp_path):
    events = tmp_path / "e.jsonl"
    events.write_text("\n".join(json.dumps(e) for e in SUCCESS) + "\nnot json\n")
    log = tmp_path / "logs" / "trader-usage.jsonl"
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "codex_result.py"),
        "usage",
        str(events),
        "trading-review",
        "gpt-6-sol",
        str(log),
    ]
    assert subprocess.run(cmd).returncode == 0
    assert subprocess.run(cmd).returncode == 0
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["provider"] == "openai-codex"
    missing = [
        sys.executable,
        str(REPO / "scripts" / "codex_result.py"),
        "usage",
        str(tmp_path / "nope"),
        "j",
        "m",
        str(log),
    ]
    assert subprocess.run(missing).returncode == 0


# ── failure reading ──────────────────────────────────────────────────────────


def test_warning_items_are_not_failures():
    assert cr.failure_message(SUCCESS) is None
    assert cr.is_retryable(SUCCESS) is False


def test_reason_is_the_final_error_not_a_reconnect():
    reason = cr.reason(NOT_LOGGED_IN)
    assert reason.startswith("unexpected status 401 Unauthorized")
    assert "Reconnecting" not in reason


def test_not_logged_in_is_not_retried():
    """codex already reconnected five times; a sixth attempt meets the same 401."""
    assert cr.is_retryable(NOT_LOGGED_IN) is False


def test_nested_server_error_is_unwrapped():
    assert cr.reason(MODEL_REFUSED) == (
        "HTTP 400: The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account."
    )
    assert cr.is_retryable(MODEL_REFUSED) is False


def test_capacity_answers_are_retried():
    for status in (429, 500, 502, 503, 529):
        assert cr.is_retryable(capacity(status)), status
    assert not cr.is_retryable(capacity(404))


def test_no_stream_at_all_is_retried():
    assert cr.is_retryable([]) is True
    assert cr.reason([]).startswith("no event stream")


def test_retryable_cli_exit_codes(tmp_path):
    def run(events):
        f = tmp_path / "e.jsonl"
        f.write_text("\n".join(json.dumps(e) for e in events))
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "codex_result.py"), "retryable", str(f)]
        ).returncode

    assert run(capacity(529)) == 0
    assert run(NOT_LOGGED_IN) == 1


def test_reason_falls_back_to_the_answer():
    answered = [e for e in SUCCESS if e["type"] != "turn.completed"]
    assert cr.reason(answered) == "Wrote the review"
