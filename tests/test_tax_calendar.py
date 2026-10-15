"""tax_calendar.py: merging the two producers, planned sales, and the change fingerprint.

All tickers, accounts and amounts are synthetic.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
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


tc = _load("tax_calendar")
oc = _load("observatory_cli")

D = datetime.date.fromisoformat
TODAY = D("2026-03-16")


def _rh(loss_sales=(), orders=(), earnings=None):
    return {"lossSales": list(loss_sales), "orders": list(orders), "earnings": earnings or {}}


def _obs(windows=(), drip=()):
    return {"ok": True, "schemaVersion": 1, "washSaleWindows": list(windows),
            "drip": list(drip), "coverage": {"brokerages": ["X"], "excludes": ["KR"]},
            "provenance": {"dbBuiltAt": "2026-03-16T20:00:00Z", "dbMtime": "2026-03-16T20:00:00Z"}}


OK = {"status": "ok", "reason": None}


def _ok_obs(doc):
    return {"status": "ok", "reason": None, "data": doc}


def test_one_window_per_ticker_names_every_source_that_reported_it():
    rh = _rh(loss_sales=[{"symbol": "AAAA", "account": "Mid-term", "date": "2026-03-02",
                          "loss": -50.0}])
    obs = _obs(windows=[
        {"ticker": "AAAA", "soldDate": "2026-03-03", "blockedThrough": "2026-04-02",
         "daysLeft": 17, "realizedLossUsd": -50.0, "accounts": ["Broker X"],
         "boughtSinceSale": False, "dripActive": True},
        {"ticker": "BBBB", "soldDate": "2026-03-10", "blockedThrough": "2026-04-09",
         "daysLeft": 24, "realizedLossUsd": -10.0, "accounts": ["Broker Y"],
         "boughtSinceSale": True, "dripActive": False}])
    windows = tc.merge_windows(rh, obs, TODAY)
    by = {w["ticker"]: w for w in windows}
    assert by["AAAA"]["reportedBy"] == ["observatory", "robinhood"]
    assert (by["AAAA"]["firstSale"], by["AAAA"]["blockedThrough"]) == ("2026-03-02", "2026-04-02")
    assert by["AAAA"]["dripActive"] is True
    assert by["BBBB"]["reportedBy"] == ["observatory"]          # another broker: Observatory only
    assert by["BBBB"]["daysLeft"] == 24


def test_an_expired_window_is_dropped_and_day_30_is_still_inside():
    rh = _rh(loss_sales=[{"symbol": "CCCC", "account": "Agentic", "date": "2026-02-14", "loss": -1},
                         {"symbol": "DDDD", "account": "Agentic", "date": "2026-02-13",
                          "loss": -1}])
    windows = tc.merge_windows(rh, None, TODAY)
    assert [w["ticker"] for w in windows] == ["CCCC"]          # 02-14 + 30 = 03-16 = today
    assert windows[0]["daysLeft"] == 0


def test_orders_that_start_or_extend_a_window_carry_their_effect():
    sale = {"symbol": "AAAA", "account": "Mid-term", "date": "2026-03-02", "loss": -5}
    rh = _rh(loss_sales=[sale],
             orders=[
                 {"symbol": "AAAA", "account": "Agentic", "side": "buy", "state": "queued",
                  "date": "2026-03-16"},
                 {"symbol": "AAAA", "account": "Long-term", "side": "buy", "state": "filled",
                  "date": "2026-02-20"},                     # 10 days BEFORE the sale: washes too
                 {"symbol": "EEEE", "account": "Agentic", "side": "buy", "state": "filled",
                  "date": "2026-03-05"},
                 {"symbol": "FFFF", "account": "Agentic", "side": "sell", "state": "confirmed",
                  "date": "2026-03-16"},
                 {"symbol": "GGGG", "account": "Agentic", "side": "sell", "state": "filled",
                  "date": "2026-03-05"}])                    # filled sells live in lossSales
    windows = tc.merge_windows(rh, None, TODAY)
    effects = {(e["ticker"], e["state"]): e for e in tc.order_events(rh, windows, TODAY)}
    assert effects[("AAAA", "queued")]["effect"] == "open-buy-inside-window"
    assert effects[("AAAA", "queued")]["until"] == "2026-04-01"
    assert effects[("AAAA", "filled")]["effect"] == "filled-buy-inside-window"
    assert effects[("EEEE", "filled")]["effect"] == "recent-buy"
    assert effects[("EEEE", "filled")]["until"] == "2026-04-04"
    assert effects[("FFFF", "confirmed")]["effect"] == "open-sell"
    assert ("GGGG", "filled") not in effects


def test_a_planned_loss_harvest_lists_buys_drip_and_earnings_inside_its_window():
    plans = [{"ticker": "HHHH", "account_role": "Long-term", "planned_date": "2026-03-25",
              "intent": "loss-harvest", "note": ""},
             {"ticker": "HHHH", "account_role": "Long-term", "planned_date": "2026-03-25",
              "intent": "gain", "note": ""}]
    rh = _rh(orders=[{"symbol": "HHHH", "account": "Agentic", "side": "buy", "state": "filled",
                      "date": "2026-03-01"}],
             earnings={"HHHH": {"status": "ok", "reports": [
                 {"date": "2026-01-20", "timing": "pm", "verified": True},
                 {"date": "2026-04-15", "timing": "am", "verified": False}]}})
    obs = _obs(drip=[{"ticker": "HHHH", "lastReinvestDate": "2026-03-10",
                      "accounts": ["Broker X"]}])
    events = tc.order_events(rh, [], TODAY)
    out = tc.planned_sale_checks(plans, [], events, obs, rh, TODAY)
    harvest, gain = out
    assert (harvest["windowStart"], harvest["windowEnd"]) == ("2026-02-23", "2026-04-24")
    assert sorted(c["kind"] for c in harvest["conflicts"]) == ["buy-in-window", "drip-active"]
    assert [r["date"] for r in harvest["earningsInWindow"]["dates"]] == ["2026-04-15"]
    assert gain["conflicts"] == []                               # conflicts are loss-harvest only
    assert [r["date"] for r in gain["earningsInWindow"]["dates"]] == ["2026-04-15"]


def test_planned_sales_file_is_validated_entry_by_entry(tmp_path):
    p = tmp_path / "planned-sales.local.json"
    p.write_text(json.dumps([
        {"ticker": "jjjj", "account_role": "Mid-term", "planned_date": "2026-04-01",
         "intent": "loss-harvest"},
        {"ticker": "KKKK", "planned_date": "April", "intent": "gain"},
        {"ticker": "LLLL", "planned_date": "2026-04-01", "intent": "sell"}]))
    ok, problems = tc.load_planned_sales(p)
    assert [e["ticker"] for e in ok] == ["JJJJ"]
    assert len(problems) == 2
    assert tc.load_planned_sales(tmp_path / "absent.json") == ([], [])


def test_the_example_planned_sales_file_parses():
    ok, problems = tc.load_planned_sales(ROOT / "config" / "planned-sales.example.json")
    assert problems == [] and len(ok) == 2


def _doc(rh, obs_source, plans=()):
    return tc.build(TODAY, rh, OK if rh is not None else
                    {"status": "unavailable", "reason": "x"}, obs_source, list(plans), [])


def test_fingerprint_ignores_what_moves_on_its_own_and_tracks_the_set():
    sale = {"symbol": "AAAA", "account": "Mid-term", "date": "2026-03-02", "loss": -5}
    a = _doc(_rh([sale]), _ok_obs(_obs()))
    b = tc.build(D("2026-03-17"), _rh([{**sale, "loss": -7}]), OK, _ok_obs(_obs()), [], [])
    assert a["fingerprint"] == b["fingerprint"]                 # days left and amount moved
    c = _doc(_rh([sale, {**sale, "symbol": "BBBB"}]), _ok_obs(_obs()))
    assert c["fingerprint"] != a["fingerprint"]                 # a new window


def test_routine_buys_do_not_move_the_fingerprint_but_a_buy_inside_a_window_does():
    sale = {"symbol": "AAAA", "account": "Mid-term", "date": "2026-03-02", "loss": -5}
    base = _doc(_rh([sale]), _ok_obs(_obs()))
    routine = _doc(_rh([sale], orders=[{"symbol": "ZZZZ", "account": "Agentic", "side": "buy",
                                        "state": "filled", "date": "2026-03-12"}]),
                   _ok_obs(_obs()))
    assert routine["fingerprint"] == base["fingerprint"]
    washing = _doc(_rh([sale], orders=[{"symbol": "AAAA", "account": "Agentic", "side": "buy",
                                        "state": "filled", "date": "2026-03-12"}]),
                   _ok_obs(_obs()))
    assert washing["fingerprint"] != base["fingerprint"]


def test_clean_only_when_nothing_is_open_and_every_source_answered():
    assert _doc(_rh(), _ok_obs(_obs()))["fingerprint"] == "clean"
    partial = _doc(_rh(), {"status": "unavailable", "reason": "refresh-in-progress", "data": None})
    assert partial["fingerprint"] != "clean"
    assert "PARTIAL, observatory unavailable" in tc.headline(partial)


def test_observatory_exit_3_is_a_refresh_not_a_failure():
    assert oc.classify(3, '{"ok":false,"error":"refresh-in-progress","schemaVersion":1}') == {
        "status": "unavailable", "reason": "refresh-in-progress", "data": None}
    assert oc.classify(1, '{"ok":false,"error":"boom","schemaVersion":1}')["reason"] == "rc=1: boom"
    assert oc.classify(0, "not json")["status"] == "unavailable"
    assert oc.classify(0, '{"ok":true,"schemaVersion":2}')["status"] == "unavailable"
    assert oc.classify(0, '> banner\n{"ok":true,"schemaVersion":1}')["status"] == "ok"


def test_observatory_cli_runs_pnpm_in_the_repo_and_survives_a_refresh(tmp_path):
    repo = tmp_path / "obs"
    repo.mkdir()
    (repo / "package.json").write_text("{}")
    fake = tmp_path / "pnpm"
    fake.write_text(textwrap.dedent("""\
        #!/bin/sh
        echo "$@" > args.txt
        echo '{"ok":false,"error":"refresh-in-progress","schemaVersion":1}'
        exit 3
    """))
    fake.chmod(0o755)
    res = oc.run("tax:calendar", ["--as-of", "2026-03-16"], repo=repo, pnpm=str(fake))
    assert res["reason"] == "refresh-in-progress"
    assert (repo / "args.txt").read_text().split() == [
        "--silent", "tax:calendar", "--as-of", "2026-03-16", "--json"]
    assert oc.run("x", [], repo=tmp_path / "missing")["status"] == "unavailable"


class FakeProxy:
    """In-process stand-in for the read-only proxy; records every call."""

    def __init__(self, data):
        self.data = data
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        value = self.data[name]
        if isinstance(value, Exception):
            raise value
        return {"data": value(arguments) if callable(value) else value}

    def close(self):
        pass


def test_run_end_to_end_writes_files_and_records_sources(tmp_path):
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "accounts.local.md").write_text(
        "| `<AGENTIC_ACCOUNT>` | 111 | x |\n| `<MIDTERM_ACCOUNT>` | 222 | x |\n")
    (repo / "config" / "planned-sales.local.json").write_text(json.dumps([
        {"ticker": "PPPP", "account_role": "Mid-term", "planned_date": "2026-03-20",
         "intent": "loss-harvest", "note": ""}]))
    proxy = FakeProxy({
        "get_pnl_trade_history": lambda a: {"trades": [
            {"timestamp": "2026-03-09T18:00:00Z", "symbol": "AAAA", "side": "sell",
             "realized_gain": "-12.5"}] if a["account_number"] == "222" else []},
        "get_equity_orders": lambda a: {"orders": [
            {"symbol": "PPPP", "side": "buy", "state": "filled",
             "last_transaction_at": "2026-03-04T15:00:00Z"}]
            if a["account_number"] == "111" else []},
        "get_earnings_results": {"results": [
            {"report": {"date": "2026-03-30", "timing": "pm", "verified": True}}]},
    })
    args = argparse.Namespace(repo=str(repo), python=sys.executable, proxy="unused",
                              no_robinhood=False, no_observatory=False)
    doc = tc.run(args, TODAY, proxy_factory=lambda: proxy,
                 observatory=lambda: {"status": "unavailable", "reason": "refresh-in-progress",
                                      "data": None})
    assert doc["sources"]["robinhood"]["status"] == "ok"
    assert doc["sources"]["observatory"] == {**doc["sources"]["observatory"],
                                             "status": "unavailable",
                                             "reason": "refresh-in-progress"}
    assert [w["ticker"] for w in doc["windows"]] == ["AAAA"]
    plan = doc["plannedSales"][0]
    assert [c["kind"] for c in plan["conflicts"]] == ["buy-in-window"]
    assert [r["date"] for r in plan["earningsInWindow"]["dates"]] == ["2026-03-30"]
    assert all("place" not in name and "cancel" not in name for name, _ in proxy.calls)

    # main(): files written, message printed
    out_dir = tmp_path / "out"
    os.environ["OBSERVATORY_REPO"] = str(tmp_path / "no-observatory-here")
    try:
        rc = tc.main(["--repo", str(repo), "--date", "2026-03-16", "--out-dir", str(out_dir),
                      "--no-robinhood", "--print", "none"])
    finally:
        del os.environ["OBSERVATORY_REPO"]
    assert rc == 0
    written = json.loads((out_dir / "2026-03-16.json").read_text())
    assert written["sources"]["observatory"]["status"] == "unavailable"
    assert (out_dir / "2026-03-16.md").read_text().startswith("# Tax calendar — 2026-03-16")


def test_a_failed_proxy_is_an_unavailable_source_not_a_failed_run(tmp_path):
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "accounts.local.md").write_text("| `<AGENTIC_ACCOUNT>` | 111 | x |\n")
    args = argparse.Namespace(repo=str(repo), python=sys.executable, proxy="unused",
                              no_robinhood=False, no_observatory=True)

    def boom():
        raise RuntimeError("token expired")

    doc = tc.run(args, TODAY, proxy_factory=boom)
    assert doc["sources"]["robinhood"] == {**doc["sources"]["robinhood"],
                                           "status": "unavailable", "reason": "token expired"}
    assert doc["fingerprint"] != "clean"
    msg = tc.render_message(doc, "x.md")
    assert "robinhood unavailable (token expired)" in msg
