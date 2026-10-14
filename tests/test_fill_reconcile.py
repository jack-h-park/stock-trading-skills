"""fill_reconcile.py: broker fills vs Observatory realized rows, matched by role.

All tickers, accounts, labels and amounts are synthetic.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


fr = _load("fill_reconcile")
ar = _load("account_roles")

D = datetime.date.fromisoformat
TODAY = D("2026-03-20")
LABELS = {"Agentic": {"label": "RH Sleeve", "configured": True},
          "Long-term": {"label": "RH Core", "configured": True},
          "Mid-term": {"label": "Robinhood Mid-term", "configured": False}}


def _fill(role, ticker, date, qty, gain=None):
    return {"role": role, "ticker": ticker, "date": date, "quantity": qty, "brokerGainUsd": gain}


def _row(account, ticker, date, qty, gain=0.0):
    return {"account": account, "brokerage": "Robinhood", "ticker": ticker, "soldDate": date,
            "quantity": qty, "proceedsUsd": 0, "gainUsd": gain, "term": "short", "basis": "replay"}


def test_lots_and_partial_fills_are_summed_before_matching():
    fills = [_fill("Long-term", "AAAA", "2026-03-10", 2.0, 5.0),
             _fill("Long-term", "AAAA", "2026-03-10", 1.0, 1.0)]
    rows = [_row("RH Core", "AAAA", "2026-03-10", 1.5, 2.0),
            _row("RH Core", "AAAA", "2026-03-10", 1.5, 3.5)]
    r = fr.reconcile(fills, rows, LABELS, TODAY)
    assert len(r["matched"]) == 1 and r["brokerOnly"] == [] and r["observatoryOnlyOrMismatch"] == []
    m = r["matched"][0]
    assert (m["brokerQuantity"], m["observatoryGainUsd"], m["brokerGainUsd"]) == (3.0, 5.5, 6.0)


def test_broker_only_is_lag_until_it_is_overdue():
    fills = [_fill("Agentic", "BBBB", "2026-03-18", 1.0),
             _fill("Agentic", "CCCC", "2026-03-02", 1.0)]
    r = fr.reconcile(fills, [], LABELS, TODAY, lag_days=7)
    by = {b["ticker"]: b for b in r["brokerOnly"]}
    assert (by["BBBB"]["ageDays"], by["BBBB"]["overdue"]) == (2, False)
    assert (by["CCCC"]["ageDays"], by["CCCC"]["overdue"]) == (18, True)


def test_quantity_difference_and_observatory_only_rows_are_discrepancies():
    fills = [_fill("Agentic", "DDDD", "2026-03-11", 2.0)]
    rows = [_row("RH Sleeve", "DDDD", "2026-03-11", 1.0),
            _row("RH Sleeve", "EEEE", "2026-03-12", 4.0)]
    issues = {m["ticker"]: m["issue"]
              for m in fr.reconcile(fills, rows, LABELS, TODAY)["observatoryOnlyOrMismatch"]}
    assert issues == {"DDDD": "quantity differs", "EEEE": "no matching broker fill"}


def test_a_one_day_offset_still_matches_and_says_so():
    r = fr.reconcile([_fill("Agentic", "FFFF", "2026-03-11", 1.0)],
                     [_row("RH Sleeve", "FFFF", "2026-03-12", 1.0)], LABELS, TODAY)
    assert r["matched"][0]["dateOffsetDays"] == 1


def test_rows_of_other_brokers_are_counted_not_compared_and_assumed_labels_are_named():
    rows = [_row("Some Other Broker", "GGGG", "2026-03-11", 1.0),
            _row("Robinhood Mid-term", "HHHH", "2026-03-11", 1.0)]
    r = fr.reconcile([_fill("Mid-term", "HHHH", "2026-03-11", 1.0)], rows, LABELS, TODAY)
    assert r["observatoryRowsOutsideRobinhood"] == 1
    assert len(r["matched"]) == 1
    assert r["labelsAssumed"] == ["Mid-term"]


def test_eastern_trade_date_and_pnl_span():
    assert fr.et_date("2026-03-11T01:30:00Z") == "2026-03-10"     # evening ET, next day UTC
    assert fr.et_date("2026-03-11T15:00:00Z") == "2026-03-11"
    assert fr.et_date("2026-03-11") == "2026-03-11"
    assert fr.pnl_span("2026-03-15", TODAY) == "week"
    assert fr.pnl_span("2026-02-01", TODAY) == "3month"
    assert fr.pnl_span("2025-06-01", TODAY) == "all"


def test_observatory_label_column_is_optional():
    md = textwrap.dedent("""
        | Placeholder | Real value | Role | Observatory label |
        | --- | --- | --- | --- |
        | `<AGENTIC_ACCOUNT>` | 111 | trades | RH Sleeve |
        | `<LONGTERM_ACCOUNT>` | 222 | never traded | (optional) |
        | `<MIDTERM_ACCOUNT>` | 333 | never traded |
    """)
    labels = ar.observatory_labels(md)
    assert labels["Agentic"] == {"label": "RH Sleeve", "configured": True}
    assert labels["Long-term"] == {"label": "Robinhood Long-term", "configured": False}
    assert labels["Mid-term"]["configured"] is False
    assert ar.accounts(md) == {"Agentic": "111", "Long-term": "222", "Mid-term": "333"}


class FakeProxy:
    def __init__(self, data):
        self.data = data

    def call(self, name, arguments):
        return {"data": self.data[name](arguments)}

    def close(self):
        pass


def _args(repo):
    return argparse.Namespace(repo=str(repo), python=sys.executable, proxy="unused",
                              since="2026-03-01", until=None, lag_days=7)


def test_run_matches_through_the_proxy_and_the_cli(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.local.md").write_text(
        "| `<AGENTIC_ACCOUNT>` | 111 | x | RH Sleeve |\n")
    proxy = FakeProxy({
        "get_pnl_trade_history": lambda a: {"trades": [
            {"symbol": "JJJJ", "side": "sell", "timestamp": "2026-03-11T15:00:00Z",
             "realized_gain": "-3.25"}]},
        "get_equity_orders": lambda a: {"orders": [
            {"symbol": "JJJJ", "side": "sell", "state": "filled", "cumulative_quantity": "2",
             "last_transaction_at": "2026-03-11T15:00:00Z", "id": "o-1"},
            {"symbol": "KKKK", "side": "buy", "state": "filled", "cumulative_quantity": "1",
             "last_transaction_at": "2026-03-11T15:00:00Z"}]},
    })
    obs = {"status": "ok", "reason": None,
           "data": {"ok": True, "schemaVersion": 1, "provenance": {"dbBuiltAt": "t"},
                    "rows": [_row("RH Sleeve", "JJJJ", "2026-03-11", 2.0, -3.0)]}}
    doc = fr.run(_args(tmp_path), TODAY, proxy_factory=lambda: proxy, observatory=lambda: obs)
    r = doc["result"]
    assert r["compared"] is True
    assert [(m["ticker"], m["brokerGainUsd"]) for m in r["matched"]] == [("JJJJ", -3.25)]
    assert "1 matched, 0 broker-only" in fr.render_md(doc)


def test_nothing_is_compared_when_the_observatory_is_unavailable(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.local.md").write_text("| `<AGENTIC_ACCOUNT>` | 111 | x |\n")
    proxy = FakeProxy({
        "get_pnl_trade_history": lambda a: {"trades": []},
        "get_equity_orders": lambda a: {"orders": [
            {"symbol": "JJJJ", "side": "sell", "state": "filled", "quantity": "2",
             "last_transaction_at": "2026-03-11T15:00:00Z"}]},
    })
    doc = fr.run(_args(tmp_path), TODAY, proxy_factory=lambda: proxy,
                 observatory=lambda: {"status": "unavailable", "reason": "refresh-in-progress",
                                      "data": None})
    assert doc["result"]["compared"] is False
    assert doc["result"]["brokerOnly"] == []
    assert "NOT COMPARED" in fr.render_md(doc)
