"""tax_guard.py: the wash-sale window and lot terms the review must respect.

Fixtures follow the live case the guard was built on: four loss sales in the
Mid-term account on 2026-10-09, and Agentic lots opened 2026-06-23 and 2026-08-13.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("tax_guard", ROOT / "scripts" / "tax_guard.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)  # type: ignore[union-attr]

D = datetime.date.fromisoformat


def _sale(sym, date, loss=-100.0, account="Mid-term"):
    return {"symbol": sym, "account": account, "date": date, "loss": loss}


def test_a_buy_is_blocked_for_thirty_days_after_a_loss_sale_and_clears_on_day_31():
    sales = [_sale("FIG", "2026-10-09", -3907.44)]
    assert tg.buy_blocks(sales, D("2026-10-13"))["FIG"]["clears"] == "2026-11-09"
    assert "FIG" in tg.buy_blocks(sales, D("2026-11-08"))      # day 30: still inside
    assert tg.buy_blocks(sales, D("2026-11-09")) == {}           # day 31: clear


def test_the_most_recent_loss_sale_decides_when_a_block_clears():
    sales = [_sale("META", "2026-10-01", account="Long-term"),
             _sale("META", "2026-10-10", account="Agentic")]
    block = tg.buy_blocks(sales, D("2026-10-13"))["META"]
    assert (block["account"], block["clears"]) == ("Agentic", "2026-11-10")


def test_recent_buys_cover_the_last_thirty_days_in_order():
    buys = [{"symbol": "IBM", "account": "Long-term", "date": d}
            for d in ("2026-10-06", "2026-09-10", "2026-09-16")]
    out = tg.recent_buys(buys, D("2026-10-13"))
    assert [b["date"] for b in out["IBM"]] == ["2026-09-16", "2026-10-06"]


def test_long_term_starts_the_day_after_the_first_anniversary():
    assert tg.long_term_from("2026-06-23") == "2027-06-24"
    assert tg.long_term_from("2028-02-29") == "2029-03-01"


def test_lot_terms_name_the_oldest_lot_and_flag_one_about_to_turn():
    lots = [{"open_date": "2026-08-13", "quantity": "0.1723"},
            {"open_date": "2026-06-23", "quantity": "0.177025"},
            {"open_date": "2026-01-02", "quantity": "0"}]          # closed lot ignored
    far = tg.lot_terms(lots, D("2026-10-13"))
    assert (far["term"], far["oldestLongTermFrom"], far["turnsLongTermSoon"]) == (
        "short-term", "2027-06-24", False)
    near = tg.lot_terms(lots, D("2027-06-10"))
    assert (near["daysToLongTerm"], near["turnsLongTermSoon"]) == (14, True)
    assert tg.lot_terms(lots, D("2027-07-01"))["term"] == "mixed"


def test_accounts_are_read_from_the_local_table_and_never_guessed():
    md = textwrap.dedent("""
        | Placeholder | Real value | Role |
        | --- | --- | --- |
        | `<AGENTIC_ACCOUNT>` | 111111111 | trades |
        | `<LONGTERM_ACCOUNT>` | 222222222 | never traded |
        | `<MIDTERM_ACCOUNT>` | (fill in) | never traded |
    """)
    assert tg.accounts(md) == {"Agentic": "111111111", "Long-term": "222222222"}


def test_end_to_end_through_a_read_only_proxy(tmp_path):
    data = {
        "get_pnl_trade_history": {"trades": [
            {"timestamp": "2026-10-09T18:23:23Z", "symbol": "FIG", "side": "sell",
             "realized_gain": "-3907.44"},
            {"timestamp": "2026-10-02T18:00:00Z", "symbol": "MSFT", "side": "sell",
             "realized_gain": "120.10"}], "next_cursor": ""},
        "get_equity_orders": {"orders": [
            {"symbol": "IBM", "side": "buy", "last_transaction_at": "2026-10-06T15:00:00Z"}]},
        "get_equity_positions": {"positions": [{"symbol": "META"}]},
        "get_equity_tax_lots": {"tax_lots": [{"open_date": "2026-06-23", "quantity": "0.177025"}]},
    }
    fake = tmp_path / "proxy.py"
    fake.write_text(textwrap.dedent(f"""
        import json, sys
        assert "--read-only" in sys.argv[1:]
        DATA = {json.dumps(data)}
        for line in sys.stdin:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            if msg["method"] == "initialize":
                result = {{}}
            else:
                payload = {{"data": DATA[msg["params"]["name"]]}}
                result = {{"content": [{{"type": "text", "text": json.dumps(payload)}}]}}
            reply = {{"jsonrpc": "2.0", "id": msg["id"], "result": result}}
            sys.stdout.write(json.dumps(reply) + "\\n")
            sys.stdout.flush()
    """))
    summary = tmp_path / "briefing-summary.json"
    summary.write_text(json.dumps(_summary([_window("XYZ", "2026-11-08", "2026-11-09")])))
    proxy = tg.Proxy(sys.executable, str(fake))
    try:
        doc = tg.collect(proxy, {"Agentic": "1", "Mid-term": "2"}, D("2026-10-13"),
                         str(summary))
    finally:
        proxy.close()
    assert set(doc["buyBlocks"]) == {"FIG", "XYZ"}             # the gain is not a block
    assert doc["buyBlocks"]["FIG"]["source"] == "robinhood"
    assert doc["buyBlocks"]["XYZ"]["source"] == "observatory"
    assert doc["observatory"]["status"] == "ok"
    assert doc["buyBlocks"]["FIG"]["clears"] == "2026-11-09"
    assert [b["date"] for b in doc["recentBuys"]["IBM"]] == ["2026-10-06", "2026-10-06"]
    assert doc["lots"]["META"]["oldestLongTermFrom"] == "2027-06-24"


def _summary(windows, through="2026-10-12", version=1):
    return {"schemaVersion": version,
            "washSaleWindows": {"measuredOn": "2026-10-12", "transactionsThrough": through,
                                "line": None, "windows": windows}}


def _window(sym, through, clears, loss=-140.0, sold_in=("Fidelity ••1234",)):
    return {"ticker": sym, "blockedThrough": through, "clearFrom": clears, "daysLeft": 0,
            "realizedLoss": loss, "possiblyDisallowed": 0, "boughtSinceSale": False,
            "soldIn": list(sold_in), "buysInWindow": []}


def test_an_open_observatory_window_blocks_and_a_closed_one_does_not():
    summary = _summary([_window("XYZ", "2026-11-08", "2026-11-09"),
                        _window("OLD", "2026-10-12", "2026-10-13")])
    blocks = tg.observatory_blocks(summary, D("2026-10-13"))
    assert set(blocks) == {"XYZ"}
    assert blocks["XYZ"]["source"] == "observatory"
    assert (blocks["XYZ"]["clears"], blocks["XYZ"]["loss"]) == ("2026-11-09", -140.0)
    assert "XYZ" in tg.observatory_blocks(summary, D("2026-11-08"))   # last blocked day


def test_merge_keeps_the_block_that_clears_later_and_robinhood_on_a_tie():
    rh = tg.buy_blocks([_sale("META", "2026-10-09")], D("2026-10-13"))    # clears 11-09
    same = {"META": {**_window("META", "2026-11-08", "2026-11-09"),
                     "source": "observatory", "clears": "2026-11-09"}}
    assert tg.merge_blocks(rh, same)["META"]["source"] == "robinhood"
    later = {"META": {"source": "observatory", "clears": "2026-11-12"}}
    assert tg.merge_blocks(rh, later)["META"]["clears"] == "2026-11-12"


def test_an_absent_or_newer_summary_adds_nothing_and_says_why(tmp_path):
    assert tg.read_observatory(None) == (None, "not configured")
    assert tg.read_observatory(str(tmp_path / "nope.json")) == (None, "missing")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert tg.read_observatory(str(bad)) == (None, "unreadable")
    newer = tmp_path / "v2.json"
    newer.write_text(json.dumps(_summary([], version=2)))
    assert tg.read_observatory(str(newer)) == (None, "refused: schemaVersion 2")
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schemaVersion": 1}))
    assert tg.read_observatory(str(old)) == (None, "no washSaleWindows")


def test_a_lagging_summary_is_marked_stale():
    # Friday's transactions read on Tuesday: only Monday is missing — the weekend is not.
    fresh = tg.observatory_status(_summary([], through="2026-10-09"), "ok", D("2026-10-13"))
    assert (fresh["missingWeekdays"], fresh["stale"]) == (1, False)
    stale = tg.observatory_status(_summary([], through="2026-10-05"), "ok", D("2026-10-13"))
    assert (stale["missingWeekdays"], stale["stale"]) == (5, True)
    assert tg.observatory_status(_summary([], through=None), "ok", D("2026-10-13"))["stale"]
    assert tg.observatory_status(None, "missing", D("2026-10-13")) == {"status": "missing"}
