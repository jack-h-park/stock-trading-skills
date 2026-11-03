"""The trade-log check must select agent orders by agent_initiated.

placed_agent names whoever finally submitted an order. An order the agent
submits for approval is finally submitted by the approval in the app, so it
reads placed_agent=user. Every scripts/scheduled_orders.py buy is one of those,
and under a placed_agent=agentic filter the 2026-10-30 and 2026-11-03 QQQM
fills were invisible to the check, which reported "0 in window" both days.

Run: python3 -m pytest tests/test_trade_log_filter.py
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCES = {
    "config": REPO / "config" / "trade-log-check.md",
    "skill": REPO / "skills" / "reconcile" / "SKILL.md",
    "prompt": REPO / "scripts" / "run-review.sh",
}


def test_every_source_filters_on_agent_initiated():
    for name, path in SOURCES.items():
        assert "agent_initiated=true" in path.read_text(), name


def test_no_source_uses_placed_agent_as_the_filter():
    # Naming the old filter to warn against it is fine; using it is not.
    for name, path in SOURCES.items():
        text = re.sub(r"\s+", " ", path.read_text())
        for m in re.finditer(r"placed_agent=agentic", text):
            before = text[max(0, m.start() - 12):m.start()]
            assert "not " in before or "old " in before, (name, text[m.start() - 40:m.end()])


def test_config_table_row_names_the_filter():
    text = SOURCES["config"].read_text()
    assert "| Order filter | `state=filled`, `agent_initiated=true` |" in text


def test_matching_accepts_the_ref_id_the_journal_records():
    for name in ("config", "skill", "prompt"):
        assert "ref_id" in SOURCES[name].read_text(), name
