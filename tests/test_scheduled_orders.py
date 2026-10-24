"""scripts/scheduled_orders.py: what may be submitted for approval, and what may not.

Every test drives `run()` against a temporary repo and a fake broker client, so
nothing reaches Robinhood. The fake records each tool call; most assertions are
about which calls were NOT made — a refused order is one place_equity_order never
saw.

Account numbers are synthetic, written in the config/accounts.example.md format.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import scheduled_orders as so  # noqa: E402

AGENTIC = "900000001"
ACCOUNTS_MD = f"""| Placeholder | Real value | Role |
| --- | --- | --- |
| `<AGENTIC_ACCOUNT>` | {AGENTIC} | agent trades this one |
"""
# Thursday 2026-10-15, 10:05 ET: inside the window, the day after the review.
NOW = datetime.datetime(2026, 10, 15, 14, 5, tzinfo=datetime.timezone.utc)  # noqa: UP017
TODAY = "2026-10-15"
YESTERDAY = "2026-10-14"


def buy(sym: str, notional: float = 100, **kw: Any) -> dict[str, Any]:
    d = {"side": "buy", "symbol": sym, "kind": "buy", "shares": None,
         "notional": notional, "priceUsed": 500.0, "autoEligible": True,
         "reason": "5.8% below 20-day high"}
    d.update(kw)
    return d


class FakeBroker:
    def __init__(self, approvals: Any = True, orders: list[dict[str, Any]] | None = None,
                 review: dict[str, Any] | None = None,
                 pending: list[dict[str, Any]] | None = None,
                 placed_reply: Any = None, setting_account: str = AGENTIC) -> None:
        self.approvals = approvals
        self.orders = orders or []
        self.pending = pending or []
        self.placed_reply = placed_reply
        self.setting_account = setting_account
        self.review = review if review is not None else {
            "data": {"customer_approval_required": True, "order_checks": []}}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args))
        if name == "get_trade_approval_setting":
            return {"data": {"setting": {"account_number": self.setting_account,
                                         "human_must_approve_trades": self.approvals}}}
        if name == "get_equity_orders":
            since = args["created_at_gte"]
            return {"data": {"orders": [o for o in self.orders if o["created_at"] >= since],
                             "next": None}}
        if name == "get_trade_approvals":
            return {"data": {"approvals": self.pending, "next": None}}
        if name == "review_equity_order":
            return self.review
        if name == "place_equity_order":
            if isinstance(self.placed_reply, Exception):
                raise self.placed_reply
            if self.placed_reply is not None:
                return self.placed_reply  # type: ignore[no-any-return]
            return {"data": {"approval": {"approval_id": f"appr-{args['symbol']}"}}}
        raise AssertionError(f"unexpected tool {name}")

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def placed(self) -> list[str]:
        return [a["symbol"] for n, a in self.calls if n == "place_equity_order"]

    def close(self) -> None:
        self.closed = True


SETTLED = {"AAPL": 500.0, "MSFT": 500.0, "GOOGL": 500.0, "VOO": 500.0, "QQQM": 500.0}


def make_db(path: Path, rows: dict[str, float], date: str = YESTERDAY) -> str:
    con = sqlite3.connect(path)
    con.execute("create table if not exists historical_prices"
                " (market, ticker, price_date, close)")
    con.executemany("insert into historical_prices values ('US', ?, ?, ?)",
                    [(t, date, c) for t, c in rows.items()])
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "strategy").mkdir()
    shutil.copy(REPO / "strategy" / "policy.md", tmp_path / "strategy" / "policy.md")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.local.md").write_text(ACCOUNTS_MD)
    (tmp_path / "logs" / "reviews").mkdir(parents=True)
    make_db(tmp_path / "obs.db", SETTLED)
    return tmp_path


def write_record(repo: Path, decisions: list[dict[str, Any]], date: str = YESTERDAY,
                 target: str = TODAY, tax: dict[str, Any] | None = None,
                 **extra: Any) -> None:
    tax = {"buyBlocks": {}} if tax is None else tax
    doc = {"date": date, "targetSession": target, "priceAsOf": date,
           "decisions": decisions, "blocked": [], "departures": [], "decisionRequests": []}
    doc.update(extra)
    (repo / "logs" / "reviews" / f"{date}.decisions.json").write_text(json.dumps(doc))
    if tax is not None:
        (repo / "logs" / "reviews" / f"{date}.tax.json").write_text(json.dumps(tax))


def go_live(repo: Path) -> None:
    (repo / "config" / "execution.local.json").write_text(
        json.dumps({"scheduledPlacement": "approval-required"}))


def run(repo: Path, broker: FakeBroker, now: datetime.datetime = NOW, dry: bool = False,
        db: str | None = None) -> tuple[int, list[str]]:
    return so.run(repo, lambda: broker, now, dry, db or str(repo / "obs.db"),
                  accounts_path=repo / "config" / "accounts.local.md")


# ── mode ─────────────────────────────────────────────────────────────────────

def test_without_the_local_file_it_is_a_dry_run_and_places_nothing(repo):
    write_record(repo, [buy("VOO")])
    broker = FakeBroker()
    rc, lines = run(repo, broker)
    assert rc == 0
    assert broker.placed() == []
    assert "review_equity_order" in broker.names()  # the simulation still runs
    assert "would submit: buy VOO $100.00" in "\n".join(lines)
    assert "[dry run]" in lines[0]


@pytest.mark.parametrize("content", ['{"scheduledPlacement": "auto"}', "not json",
                                     '{"scheduledPlacement": "Approval-Required"}'])
def test_anything_but_the_exact_live_value_is_a_dry_run(repo, content):
    (repo / "config" / "execution.local.json").write_text(content)
    assert so.load_mode(repo / "config" / "execution.local.json") == "dry-run"


def test_live_submits_with_a_deterministic_ref_id_and_logs_it(repo):
    go_live(repo)
    write_record(repo, [buy("VOO"), buy("MSFT", 65)])
    broker = FakeBroker()
    rc, lines = run(repo, broker)
    assert rc == 0
    assert broker.placed() == ["VOO", "MSFT"]
    place = [a for n, a in broker.calls if n == "place_equity_order"][0]
    assert place == {"account_number": AGENTIC, "symbol": "VOO", "side": "buy",
                     "type": "market", "dollar_amount": "100.00",
                     "market_hours": "regular_hours", "time_in_force": "gfd",
                     "ref_id": so.ref_id(AGENTIC, TODAY, "VOO")}
    assert so.ref_id(AGENTIC, TODAY, "VOO") == so.ref_id(AGENTIC, TODAY, "VOO")
    assert "waiting for your approval" in lines[0]
    log = (repo / "logs" / "trades" / f"{TODAY}.md").read_text()
    assert log.count("## ") == 2 and "standing-auth" in log and AGENTIC not in log
    journal = json.loads((repo / "logs" / "executions" / f"{TODAY}.json").read_text())
    assert [o["symbol"] for o in journal["orders"]] == ["VOO", "MSFT"]
    assert broker.closed


def test_a_pending_approval_uses_a_slot_and_blocks_its_symbol(repo):
    # Run 1 used the last weekly slot on VOO. Without counting pending approvals,
    # a second run on a fresh journal would read that slot as free and send MSFT.
    go_live(repo)
    write_record(repo, [buy("VOO"), buy("MSFT")])
    week = [order(f"2026-10-1{d}T15:00:00Z") for d in (2, 3, 3, 4, 4)]
    pending = [{"approval_id": "a1", "symbol": "VOO", "dollar_amount": "100.00"}]
    broker = FakeBroker(orders=week, pending=pending)
    _, lines = run(repo, broker)
    assert broker.placed() == []
    text = "\n".join(lines)
    assert "VOO: an order or pending approval on this symbol exists today" in text
    assert "MSFT: order count cap" in text


def test_a_pending_approval_without_an_amount_stops_everything(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(pending=[{"approval_id": "a1", "symbol": "AAPL"}])
    _, lines = run(repo, broker)
    assert broker.placed() == []
    assert "daily ceiling cannot be checked" in lines[-1]


def test_a_failed_submit_is_journaled_as_uncertain_and_not_resent(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(placed_reply=RuntimeError("timed out"))
    _, lines = run(repo, broker)
    assert "outcome unknown" in "\n".join(lines)
    journal = json.loads((repo / "logs" / "executions" / f"{TODAY}.json").read_text())
    assert journal["orders"][0]["status"] == "uncertain"
    assert not (repo / "logs" / "trades" / f"{TODAY}.md").exists()
    again = FakeBroker()
    run(repo, again)
    assert again.placed() == []


def test_a_reply_with_no_id_is_not_reported_as_submitted(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(placed_reply={"data": {"message": "something else"}})
    _, lines = run(repo, broker)
    assert "submitted: buy" not in "\n".join(lines)
    assert "unknown outcome" in lines[0]


def test_an_approval_setting_for_another_account_is_an_error(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    with pytest.raises(so.BrokerShapeError):
        run(repo, FakeBroker(setting_account="900000009"))


def test_tool_errors_are_raised_not_parsed():
    from signal_windows import tool_payload
    with pytest.raises(RuntimeError, match="halted"):
        tool_payload({"result": {"isError": True,
                                 "content": [{"type": "text", "text": '{"error": "halted"}'}]}})


def test_a_rerun_the_same_day_does_not_submit_again(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    run(repo, FakeBroker())
    second = FakeBroker()
    _, lines = run(repo, second)
    assert second.placed() == []
    assert "already submitted today" in "\n".join(lines)


# ── the Level 1 precondition ─────────────────────────────────────────────────

def test_approvals_off_submits_nothing_and_never_reviews(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(approvals=False)
    rc, lines = run(repo, broker)
    assert rc == 0
    assert broker.names() == ["get_trade_approval_setting"]
    assert "approvals are OFF" in lines[0]


def test_an_unreadable_approval_setting_is_an_error_not_a_yes(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    with pytest.raises(so.BrokerShapeError):
        run(repo, FakeBroker(approvals="yes"))


@pytest.mark.parametrize("review,why", [
    ({"data": {"customer_approval_required": False, "order_checks": []}},
     "customer_approval_required"),
    ({"data": {"order_checks": []}}, "customer_approval_required"),
    ({"data": {"customer_approval_required": True,
               "order_checks": [{"type": "buying_power"}]}}, "order checks"),
])
def test_review_must_say_the_order_goes_to_the_app(repo, review, why):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(review=review)
    _, lines = run(repo, broker)
    assert broker.placed() == []
    assert why in "\n".join(lines)


# ── what the record may carry ────────────────────────────────────────────────

@pytest.mark.parametrize("decision,conflict,why", [
    (buy("TQQQ"), {}, "universe"),
    (buy("VOO", 150), {}, "over the $100 cap"),
    (buy("VOO", kind="trim"), {}, "kind"),
    (buy("VOO", shares=0.2), {}, "share quantity"),
    (buy("GOOGL"), {"departures": [{"symbol": "GOOGL"}]}, "departure"),
    (buy("GOOGL"), {"decisionRequests": [{"symbol": "GOOGL"}]}, "decision request"),
])
def test_record_level_refusals(repo, decision, conflict, why):
    go_live(repo)
    write_record(repo, [decision], **conflict)
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.placed() == []
    assert why in "\n".join(lines)


def test_a_redistribute_buy_is_a_buy(repo):
    go_live(repo)
    write_record(repo, [buy("VOO", kind="redistribute")])
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert "kind" not in "\n".join(lines)
    assert broker.placed() != []


def test_a_buy_beside_an_exit_on_the_same_symbol_is_refused(repo):
    go_live(repo)
    sell = {"side": "sell", "symbol": "GOOGL", "kind": "trim", "shares": 0.3,
            "notional": 101.85, "priceUsed": 300.0, "autoEligible": False}
    write_record(repo, [buy("GOOGL"), sell])
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.placed() == []
    assert "exit is also proposed" in "\n".join(lines)


def test_not_auto_eligible_and_sells_are_never_candidates(repo):
    go_live(repo)
    write_record(repo, [buy("VOO", autoEligible=False),
                        {"side": "sell", "symbol": "AMZN", "kind": "take-profit",
                         "autoEligible": True, "notional": 50, "priceUsed": 1}])
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "no auto-eligible buy" in lines[0]


@pytest.mark.parametrize("date,target", [(YESTERDAY, "2026-10-16"), (TODAY, TODAY)])
def test_only_an_earlier_record_targeting_today_counts(repo, date, target):
    go_live(repo)
    write_record(repo, [buy("VOO")], date=date, target=target)
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "no decision record targets today" in lines[0]


def test_an_older_record_is_not_reached_past_a_newer_one(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")], date="2026-10-13", target=TODAY)
    write_record(repo, [], date=YESTERDAY, target="2026-10-16")
    broker = FakeBroker()
    run(repo, broker)
    assert broker.calls == []


def test_a_price_the_settled_close_contradicts_is_refused(repo, tmp_path):
    db = make_db(tmp_path / "other.db", {"VOO": 520.0})
    go_live(repo)
    write_record(repo, [buy("VOO")])  # priceUsed 500 against a settled 520
    broker = FakeBroker()
    _, lines = run(repo, broker, db=db)
    assert broker.calls == []
    assert "price check: price-mismatch" in "\n".join(lines)


@pytest.mark.parametrize("db", ["missing-row", "missing-file"])
def test_no_settled_close_means_no_order(repo, tmp_path, db):
    # A frozen refresh and a missing database both look like no-settled-row.
    path = make_db(tmp_path / "x.db", {"MSFT": 500.0}) if db == "missing-row" \
        else str(tmp_path / "absent.db")
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker()
    _, lines = run(repo, broker, db=path)
    assert broker.calls == []
    assert "price check: no-settled-row" in "\n".join(lines)


def test_a_record_priced_off_another_session_is_refused(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")], priceAsOf="2026-10-09")
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "is not the review's own session" in "\n".join(lines)


def test_a_symbol_the_record_also_blocks_is_refused(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")], blocked=[{"symbol": "VOO", "rule": "committed-add cap",
                                               "value": "$200"}])
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "committed-add cap" in "\n".join(lines)


def test_a_wash_sale_block_without_a_clearing_date_still_blocks(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")], tax={"buyBlocks": {"VOO": {"date": "2026-10-01"}}})
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "no readable clearing date" in "\n".join(lines)


def test_a_symbol_in_a_wash_sale_window_is_refused(repo):
    go_live(repo)
    write_record(repo, [buy("VOO"), buy("MSFT")], tax={"buyBlocks": {
        "MSFT": {"date": "2026-10-01", "clears": "2026-11-01"},
        "VOO": {"date": "2026-09-10", "clears": "2026-10-11"}}})  # already cleared
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.placed() == ["VOO"]
    assert "wash-sale window until 2026-11-01" in "\n".join(lines)


def test_a_tax_file_whose_robinhood_half_failed_blocks_every_buy(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")], tax={"robinhood": {"status": "failed: proxy down"},
                                          "buyBlocks": {}})
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "no tax facts" in "\n".join(lines)


def test_an_observatory_block_names_the_brokerage(repo):
    go_live(repo)
    write_record(repo, [buy("VOO"), buy("MSFT")], tax={
        "robinhood": {"status": "ok"},
        "buyBlocks": {"MSFT": {"source": "observatory", "soldIn": ["Fidelity ••1234"],
                               "clears": "2026-11-01"}}})
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.placed() == ["VOO"]
    assert "wash-sale window until 2026-11-01 (loss sale at Fidelity ••1234)" in "\n".join(lines)


def test_missing_tax_facts_block_every_buy(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")], tax={})
    (repo / "logs" / "reviews" / f"{YESTERDAY}.tax.json").unlink()
    broker = FakeBroker()
    _, lines = run(repo, broker)
    assert broker.calls == []
    assert "no tax facts" in "\n".join(lines)


# ── counts and clock ─────────────────────────────────────────────────────────

def order(created: str, sym: str = "AAPL", state: str = "filled", amount: Any = "100.00"):
    return {"created_at": created, "symbol": sym, "side": "buy", "state": state,
            "dollar_based_amount": {"amount": amount} if amount is not None else None}


def test_weekly_count_leaves_one_slot(repo):
    go_live(repo)
    write_record(repo, [buy("VOO"), buy("MSFT")])
    week = [order(f"2026-10-1{d}T15:00:00Z") for d in (2, 3, 3, 4, 4)]
    week.append(order("2026-10-13T15:00:00Z", state="cancelled"))  # does not count
    broker = FakeBroker(orders=week)
    _, lines = run(repo, broker)
    assert broker.placed() == ["VOO"]
    assert "order count cap" in "\n".join(lines)


def test_an_order_from_last_week_does_not_count(repo):
    # Monday 2026-10-12 00:00 ET is 04:00Z; the query starts there.
    assert so.week_start_utc(datetime.date(2026, 10, 15)) == "2026-10-12T04:00:00Z"
    go_live(repo)
    write_record(repo, [buy("VOO")])
    last_week = [order(f"2026-10-0{d}T15:00:00Z") for d in (5, 6, 7, 8, 9)]
    last_week.append(order("2026-10-12T03:59:00Z"))  # Sunday 23:59 ET
    broker = FakeBroker(orders=last_week)
    run(repo, broker)
    assert broker.placed() == ["VOO"]


def test_an_order_already_on_the_symbol_today_blocks_a_second(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(orders=[order("2026-10-15T13:50:00Z", sym="VOO")])
    _, lines = run(repo, broker)
    assert broker.placed() == []
    assert "pending approval on this symbol exists today" in "\n".join(lines)


def test_an_unvaluable_order_today_stops_everything(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(orders=[order("2026-10-15T13:50:00Z", amount=None)])
    _, lines = run(repo, broker)
    assert broker.placed() == []
    assert "daily ceiling cannot be checked" in lines[0]


def test_a_missing_orders_list_is_an_error(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker()
    broker.call = lambda n, a: ({"data": {}} if n == "get_equity_orders" else
                                {"data": {"human_must_approve_trades": True}})
    with pytest.raises(so.BrokerShapeError):
        run(repo, broker)


@pytest.mark.parametrize("now", [
    datetime.datetime(2026, 10, 15, 13, 35, tzinfo=datetime.timezone.utc),  # 09:35 ET  # noqa: UP017
    datetime.datetime(2026, 10, 15, 19, 50, tzinfo=datetime.timezone.utc),  # 15:50 ET  # noqa: UP017
])
def test_outside_the_window_the_broker_is_never_contacted(repo, now):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker()
    _, lines = run(repo, broker, now=now)
    assert broker.calls == []
    assert "outside the regular-session window" in lines[0]


def test_no_agentic_account_is_an_error_before_any_call(repo):
    go_live(repo)
    write_record(repo, [buy("VOO")])
    (repo / "config" / "accounts.local.md").write_text("| nothing |\n")
    broker = FakeBroker()
    rc, _ = run(repo, broker)
    assert rc == 1 and broker.calls == []


# ── the numbers are the guardrails' numbers ──────────────────────────────────

def test_limits_match_config_guardrails():
    text = (REPO / "config" / "guardrails.md").read_text()

    def cell(label: str) -> str:
        m = re.search(r"^\| " + re.escape(label) + r" \| \*\*([^*]+)\*\*", text, re.M)
        assert m, label
        return m.group(1)

    assert float(cell("Max notional per order").strip("$")) == so.MAX_NOTIONAL
    assert int(cell("Max orders per day")) == so.MAX_ORDERS_PER_DAY
    assert int(cell("Max orders per week")) == so.MAX_ORDERS_PER_WEEK
    assert float(cell("Max total notional per day").strip("$").replace(",", "")) == \
        so.MAX_NOTIONAL_PER_DAY


def test_the_universe_is_read_from_policy():
    from signal_windows import universe
    syms = universe((REPO / "strategy" / "policy.md").read_text())
    assert set(syms) == {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "VOO", "QQQM"}


# ── a close older than 24h is re-checked on the live quote ───────────────────

# Monday 2026-10-19 10:05 ET, executing a Friday 2026-10-16 review: the close is ~66h old.
MONDAY = datetime.datetime(2026, 10, 19, 14, 5, tzinfo=datetime.timezone.utc)  # noqa: UP017
FRIDAY = "2026-10-16"


def friday_record(repo: Path, high: float | None = 540.0) -> None:
    make_db(repo / "obs.db", SETTLED, date=FRIDAY)
    write_record(repo, [buy("VOO")], date=FRIDAY, target="2026-10-19")
    if high is not None:
        (repo / "logs" / "reviews" / f"{FRIDAY}.signals.json").write_text(json.dumps(
            {"symbols": {"VOO": {"high": high, "price": 500.0, "drawdownPct": 7.41}}}))


def quoted(price: float | None) -> dict[str, Any]:
    data: dict[str, Any] = {"customer_approval_required": True, "order_checks": []}
    if price is not None:
        data["quote"] = {"last_trade_price": str(price)}
    return {"data": data}


def test_monday_submits_when_the_live_quote_still_shows_the_dip(repo):
    go_live(repo)
    friday_record(repo)
    broker = FakeBroker(review=quoted(505.0))  # 6.48% below 540
    _, lines = run(repo, broker, now=MONDAY)
    assert broker.placed() == ["VOO"]
    assert "live re-check 505, 6.48% below 540" in "\n".join(lines)


def test_monday_skips_when_the_dip_closed_over_the_weekend(repo):
    go_live(repo)
    friday_record(repo)
    broker = FakeBroker(review=quoted(520.0))  # 3.70% below 540
    _, lines = run(repo, broker, now=MONDAY)
    assert broker.placed() == []
    assert "live re-check: 3.70% below the 20-day high 540" in "\n".join(lines)


@pytest.mark.parametrize("price,high", [(None, 540.0), (505.0, None)])
def test_monday_without_a_quote_or_a_high_submits_nothing(repo, price, high):
    go_live(repo)
    friday_record(repo, high=high)
    broker = FakeBroker(review=quoted(price))
    _, lines = run(repo, broker, now=MONDAY)
    assert broker.placed() == []
    assert "no live quote to re-check" in "\n".join(lines)


def test_a_next_morning_close_needs_no_live_recheck(repo):
    # Thursday 10:05 ET on a Wednesday close is 18h: fresh, and no quote is needed.
    go_live(repo)
    write_record(repo, [buy("VOO")])
    broker = FakeBroker(review=quoted(None))
    run(repo, broker)
    assert broker.placed() == ["VOO"]
    assert so.close_age_hours(YESTERDAY, NOW) == pytest.approx(18.0833, abs=0.01)


def test_the_entry_threshold_is_policy_entry_rule():
    text = (REPO / "strategy" / "policy.md").read_text()
    m = re.search(r"\*\*≥ (\d+)% below its trailing 20-day high\*\*", text)
    assert m and float(m.group(1)) == so.ENTRY_DRAWDOWN_PCT
