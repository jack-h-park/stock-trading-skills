"""robinhood_snapshot.py writes a pull only when it is whole, and keeps the old file otherwise.

The broker is faked at the tool-call boundary, so these run without a token.

Run: python3 -m pytest tests/test_robinhood_snapshot.py
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import robinhood_snapshot as rs  # noqa: E402

NOW = datetime.datetime(2026, 11, 3, 21, 0, tzinfo=rs.UTC)
ACCOUNTS_MD = ("| Placeholder | Number |\n|---|---|\n"
               "| `<MIDTERM_ACCOUNT>` | 111 |\n| `<LONGTERM_ACCOUNT>` | 222 |\n"
               "| `<AGENTIC_ACCOUNT>` | 333 |\n")


class Broker:
    """Positions and lots per account number, answered the way the MCP pages them."""

    def __init__(self, book, lot_page=None, fail_lots=(), orders_never_end=False):
        self.book = book
        self.lot_page = lot_page
        self.fail_lots = set(fail_lots)
        self.orders_never_end = orders_never_end
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, args))
        acct = self.book[args["account_number"]]
        if tool == "get_equity_positions":
            return {"data": {"positions": [{"symbol": s, "quantity": str(q),
                                            "average_buy_price": "10", "type": "long",
                                            "extra": "dropped"}
                                           for s, q in acct.items()], "next": None}}
        if tool == "get_equity_tax_lots":
            sym = args["symbol"]
            if sym in self.fail_lots:
                raise RuntimeError("rate limited")
            lots = [{"quantity": str(acct[sym] / 2)}, {"quantity": str(acct[sym] / 2)}]
            if self.lot_page:
                lots = lots[:self.lot_page]
            return {"data": {"tax_lots": lots, "next": None}}
        if tool == "get_equity_orders":
            assert "symbol" not in args and "state" not in args
            if self.orders_never_end:
                return {"data": {"orders": [], "next": "more"}}
            return {"data": {"orders": [{"id": "o1"}], "next": None}}
        raise AssertionError(tool)


BOOK = {"111": {"NOW": 17.0, "INTC": 66.0}, "222": {"AVGO": 3.0}, "333": {"QQQM": 1.2}}


def run(tmp_path, broker, *extra):
    md = tmp_path / "accounts.local.md"
    md.write_text(ACCOUNTS_MD)
    out = tmp_path / "robinhood-snapshot.json"
    rc = rs.main(["--out", str(out), "--accounts", str(md), *extra], call=broker, now=NOW)
    return rc, out


def seed(out, n_positions=1, since="2026-07-31"):
    out.write_text(json.dumps({"fetchedAt": "old", "accounts": [
        {"nickname": role, "positions": [{"symbol": "X"}] * n_positions,
         "orders": {"createdAtGte": since}} for role in rs.ROLES]}))


def test_a_whole_pull_is_written_in_the_bridge_shape(tmp_path, capsys):
    out = tmp_path / "robinhood-snapshot.json"
    seed(out)
    rc, out = run(tmp_path, Broker(BOOK))
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["fetchedAt"] == "2026-11-03T21:00:00.000Z" and doc["source"] == "robinhood_mcp"
    mid = doc["accounts"][0]
    assert mid["nickname"] == "Mid-term" and mid["accountNumber"] == "111"
    assert set(mid["positions"][0]) == set(rs.POSITION_FIELDS)
    assert mid["orders"] == {"createdAtGte": "2026-07-31", "complete": True,
                             "items": [{"id": "o1"}]}
    # The previous file is kept beside it, and the new one is private.
    prev = json.loads((tmp_path / "robinhood-snapshot.json.prev").read_text())
    assert prev["fetchedAt"] == "old"
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert "wrote" in capsys.readouterr().out


def test_the_order_window_never_moves_on_its_own(tmp_path):
    out = tmp_path / "robinhood-snapshot.json"
    seed(out, since="2026-07-31")
    broker = Broker(BOOK)
    run(tmp_path, broker)
    sinces = {a["created_at_gte"] for t, a in broker.calls if t == "get_equity_orders"}
    assert sinces == {"2026-07-31"}


def test_no_window_and_no_previous_file_is_a_pull_error(tmp_path):
    rc, out = run(tmp_path, Broker(BOOK))
    assert rc == 2 and not out.exists()


def refused(tmp_path, broker, capsys, n_positions=1):
    out = tmp_path / "robinhood-snapshot.json"
    seed(out, n_positions=n_positions)
    before = out.read_text()
    rc, _ = run(tmp_path, broker)
    assert rc == 1
    assert out.read_text() == before          # the old file is untouched
    return capsys.readouterr().out


def test_a_lots_page_cut_short_is_refused_by_the_tally(tmp_path, capsys):
    text = refused(tmp_path, Broker(BOOK, lot_page=1), capsys)
    assert "NOW lots add up to 8.5, position is 17.0" in text


def test_a_symbol_whose_lots_failed_is_refused(tmp_path, capsys):
    text = refused(tmp_path, Broker(BOOK, fail_lots={"AVGO"}), capsys)
    assert "Long-term: lots unavailable for AVGO (rate limited)" in text


def test_orders_that_never_finish_paging_are_refused(tmp_path, capsys):
    text = refused(tmp_path, Broker(BOOK, orders_never_end=True), capsys)
    assert "orders did not finish paging" in text


def test_an_account_that_suddenly_holds_nothing_is_refused(tmp_path, capsys):
    book = dict(BOOK, **{"222": {}})
    text = refused(tmp_path, Broker(book), capsys)
    assert "Long-term: no positions, previous snapshot had 1" in text


def test_check_mode_writes_nothing(tmp_path):
    out = tmp_path / "robinhood-snapshot.json"
    seed(out)
    before = out.read_text()
    rc, _ = run(tmp_path, Broker(BOOK), "--check")
    assert rc == 0 and out.read_text() == before


def test_a_pull_that_raises_keeps_the_old_file(tmp_path, capsys):
    out = tmp_path / "robinhood-snapshot.json"
    seed(out)
    before = out.read_text()

    def broken(tool, args):
        raise OSError("proxy exited")

    rc, _ = run(tmp_path, broken)
    assert rc == 2 and out.read_text() == before
    assert "pull failed" in capsys.readouterr().out


def test_the_snapshot_reads_only_through_the_read_only_proxy():
    text = (rs.HERE / "robinhood_snapshot.py").read_text()
    assert 'Proxy(sys.executable, str(HERE / "mcp-robinhood-proxy.py")' in text
    assert "--agentic-orders" not in text.split('"""', 2)[2]
