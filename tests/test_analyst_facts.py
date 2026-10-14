"""analyst_facts.py: facts only, sorted only by owner-named keys from a fixed list.

All tickers and figures are synthetic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("analyst_facts",
                                              ROOT / "scripts" / "analyst_facts.py")
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)  # type: ignore[union-attr]


def test_sort_accepts_only_listed_keys():
    assert af.parse_sort("target_vs_price_pct:desc,ticker") == [
        ("target_vs_price_pct", True), ("ticker", False)]
    assert af.parse_sort(None) == []
    for bad in ("score", "consensus", "outlook:desc", "buy_count:down", "ticker,rank"):
        with pytest.raises(ValueError):
            af.parse_sort(bad)


def test_no_sort_key_is_a_composite():
    # Each allowed key is a reported fact or a plain ratio of two; nothing that reads as
    # a verdict or a score may be added to the list.
    forbidden = ("score", "rank", "rating", "consensus", "outlook", "upside", "signal",
                 "recommend", "strength", "verdict")
    assert not [k for k in af.SORT_KEYS if any(f in k for f in forbidden)]


def test_unknown_sort_key_exits_2_before_any_broker_call(capsys):
    with pytest.raises(SystemExit) as e:
        af.main(["AAAA", "--sort", "score", "--proxy", "/nonexistent"])
    assert e.value.code == 2
    assert "unknown sort key" in capsys.readouterr().err


def test_missing_values_sort_last_in_either_direction():
    rows = [{"ticker": "A", "mean_target": None}, {"ticker": "B", "mean_target": 10.0},
            {"ticker": "C", "mean_target": 20.0}]
    assert [r["ticker"] for r in af.sort_rows(rows, [("mean_target", True)])] == ["C", "B", "A"]
    assert [r["ticker"] for r in af.sort_rows(rows, [("mean_target", False)])] == ["B", "C", "A"]


def _earn(year, q, est, act, date, verified=True):
    return {"year": year, "quarter": q, "eps": {"estimate": est, "actual": act},
            "report": {"date": date, "timing": "pm", "verified": verified}}


def test_row_facts_beats_and_target_vs_price():
    rating = {"num_buy_ratings": 6, "num_hold_ratings": 3, "num_sell_ratings": 1,
              "low_price_target": "80", "mean_price_target": "110", "high_price_target": "150",
              "updated_at": "2026-03-01T00:00:00Z"}
    quote = {"last_trade_price": "100", "venue_last_trade_time": "2026-03-02T21:00:00Z"}
    earnings = [_earn(2025, 1, "1.0", "1.1", "2025-04-01"),
                _earn(2025, 2, "1.0", "0.9", "2025-07-01"),
                _earn(2025, 3, "1.0", "1.0", "2025-10-01"),
                _earn(2025, 4, "1.0", "1.2", "2026-01-01"),
                _earn(2026, 1, "1.1", None, "2026-04-01", verified=False)]
    row = af.build_row("AAAA", rating, quote, earnings, {"pe_ratio": "20.5"}, quarters=3)
    assert (row["analyst_count"], row["target_vs_price_pct"]) == (10, 10.0)
    assert [q["outcome"] for q in row["earnings"]["quarters"]] == ["miss", "inline", "beat"]
    assert (row["beats"], row["misses"]) == (1, 1)
    assert row["earnings"]["nextReport"] == {"date": "2026-04-01", "timing": "pm",
                                             "verified": False}
    assert row["sources"]["ratingsUpdatedAt"] == "2026-03-01T00:00:00Z"
    assert row["pe_ratio"] == 20.5


def test_no_coverage_is_said_not_zeroed():
    row = af.build_row("BBBB", None, {"last_trade_price": "5"}, [], None, quarters=4)
    assert row["coverage"] == "no analyst coverage"
    assert row["buy_count"] is None and row["analyst_count"] is None


class FakeProxy:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append(name)
        if name == "get_equity_analyst_ratings":
            ratings = {"num_buy_ratings": 1, "num_hold_ratings": 0, "num_sell_ratings": 0,
                       "mean_price_target": "12"}
            return {"data": {"results": [{"symbol": "AAAA", "ratings": ratings},
                                         {"symbol": "BBBB", "ratings": None}]}}
        if name == "get_equity_quotes":
            return {"data": {"results": [{"quote": {"symbol": s, "last_trade_price": "10"}}
                                         for s in arguments["symbols"]]}}
        if name == "get_equity_fundamentals":
            raise RuntimeError("unavailable")
        if name == "get_earnings_results":
            return {"data": {"results": []}}
        raise AssertionError(f"unexpected tool {name}")

    def close(self):
        pass


def test_run_reads_only_and_keeps_the_given_order_without_a_sort_key():
    proxy = FakeProxy()
    doc = af.run(["BBBB", "AAAA"], 4, [], lambda: proxy)
    assert [r["ticker"] for r in doc["rows"]] == ["BBBB", "AAAA"]
    assert all(c.startswith("get_") for c in proxy.calls)
    md = af.render_md(doc)
    assert "in the order given" in md and "no analyst coverage" in md
    for word in ("buy now", "should", "recommend", "outperform"):
        assert word not in md.lower().replace("no rating or recommendation is implied", "")
