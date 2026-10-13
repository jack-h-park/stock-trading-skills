"""standing.py: since when, and for how many reviews, each proposal has stood."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("standing", ROOT / "scripts" / "standing.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)  # type: ignore[union-attr]


def _write(reviews: Path, date: str, items: list[tuple[str, str]]) -> None:
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"{date}.decisions.json").write_text(json.dumps(
        {"date": date, "decisions": [{"symbol": s, "kind": k} for s, k in items]}))


def test_a_run_is_counted_across_days_without_a_record(tmp_path):
    reviews = tmp_path / "logs" / "reviews"
    _write(reviews, "2026-10-01", [("META", "take-profit")])
    _write(reviews, "2026-10-02", [("META", "take-profit"), ("NVDA", "take-profit")])
    # 10-05 and 10-06: the review failed, no record — neither extends nor breaks the run
    _write(reviews, "2026-10-07", [("META", "take-profit"), ("NVDA", "take-profit")])
    out = st.standing(st.records(reviews, "2026-10-07"), "2026-10-07")
    assert out == {"META:take-profit": {"since": "2026-10-01", "sessions": 3, "atLeast": True},
                   "NVDA:take-profit": {"since": "2026-10-02", "sessions": 2}}


def test_a_review_without_the_proposal_breaks_the_run(tmp_path):
    reviews = tmp_path / "logs" / "reviews"
    _write(reviews, "2026-10-07", [("NVDA", "take-profit")])
    _write(reviews, "2026-10-08", [])
    _write(reviews, "2026-10-09", [("NVDA", "take-profit")])
    assert st.standing(st.records(reviews, "2026-10-09"), "2026-10-09") == {
        "NVDA:take-profit": {"since": "2026-10-09", "sessions": 1}}


def test_a_different_kind_is_a_different_proposal_and_later_records_are_ignored(tmp_path):
    reviews = tmp_path / "logs" / "reviews"
    _write(reviews, "2026-10-08", [("NVDA", "buy")])
    _write(reviews, "2026-10-09", [("NVDA", "take-profit")])
    _write(reviews, "2026-10-12", [("NVDA", "take-profit")])
    assert st.standing(st.records(reviews, "2026-10-09"), "2026-10-09") == {
        "NVDA:take-profit": {"since": "2026-10-09", "sessions": 1}}


def test_no_record_for_the_date_writes_nothing(tmp_path):
    reviews = tmp_path / "logs" / "reviews"
    _write(reviews, "2026-10-08", [("NVDA", "take-profit")])
    assert st.standing(st.records(reviews, "2026-10-09"), "2026-10-09") == {}
