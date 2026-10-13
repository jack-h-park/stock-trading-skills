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
    proxy = tg.Proxy(sys.executable, str(fake))
    try:
        doc = tg.collect(proxy, {"Agentic": "1", "Mid-term": "2"}, D("2026-10-13"))
    finally:
        proxy.close()
    assert set(doc["buyBlocks"]) == {"FIG"}                    # the gain is not a block
    assert doc["buyBlocks"]["FIG"]["clears"] == "2026-11-09"
    assert [b["date"] for b in doc["recentBuys"]["IBM"]] == ["2026-10-06", "2026-10-06"]
    assert doc["lots"]["META"]["oldestLongTermFrom"] == "2027-06-24"
