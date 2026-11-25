#!/usr/bin/env python3
"""Submit yesterday's auto-eligible buys for approval in the Robinhood app.

Level 1 of the autonomy plan (docs/decisions.md §21). The afternoon review already
writes logs/reviews/<date>.decisions.json, marking an order autoEligible when every
condition of the standing authorization in config/guardrails.md holds. Until now
nothing acted on that flag: the review is read-only by construction, so every
proposal waited for Jack to open an interactive session, including the ones no
judgement went into.

This script is the missing execution path, and it is deliberately not an agent.
No model holds an order tool here. It is plain code that reads the record the
review wrote, re-checks what can be checked mechanically, and submits through the
same proxy the interactive order server uses (--agentic-orders), so the proxy's
Agentic-account check sits underneath it too.

THE LEVEL 1 PRECONDITION: Robinhood's own trade approval must be ON. With it on,
place_equity_order does not place anything; it sends the order to the Robinhood
app, where Jack approves or declines it. So the human gate moves from "open a
session and say yes" to "tap approve", and it lives outside this repo's code. The
script reads the setting before anything else and refuses to submit while it is
off. Level 2 (no approval) is a separate decision and a separate change.

What is re-checked here, in code, per order (anything that fails is skipped and
named in the report):

  * the record targets today's session, was written before today, and the order
    is autoEligible, a buy, kind buy or redistribute, notional <= $100, no share
    quantity
  * the symbol is in the universe parsed from strategy/policy.md
  * the symbol carries no departure, no decision request and no sell decision
    in the same record (one symbol, one direction)
  * the review's drawdown for the symbol is under the TRIM threshold in
    config/trim-policy.md. At or past it the momentum reading says cut, even when
    the trim itself was skipped by the $150 floor or the 3-day suspension, so the
    buy is a direction choice and waits for confirmation (strategy/policy.md, one
    symbol, one direction). A missing signals file or an unreadable threshold
    blocks every buy
  * the symbol is outside every wash-sale window in the review's <date>.tax.json
    (§20); a missing tax file, a failed Robinhood half or no usable Observatory
    summary blocks every buy, and a stale Observatory adds a warning to the report
  * the record's priceAsOf is its own session, and the price the decision used
    matches the settled close (scripts/shadow-check.py's own comparison, not a
    second implementation); no settled close yet means no order
  * the daily and weekly order counts and the daily notional ceiling, counted
    from the broker's order list plus every approval still pending in the app
  * the regular session is open
  * when the close the review priced from is over 24h old (a Monday after a
    Friday review), the entry still holds on the live quote review_equity_order
    returns: at least 5% below the review's 20-day high from <date>.signals.json,
    and still under the TRIM threshold
  * review_equity_order reports customer_approval_required and no order_checks

What is NOT re-derived here, and is trusted from the review: the committed-add
cap and the 20% position cap. Both need the per-symbol fill history replayed,
which is the review's job and should not have a second implementation. At Level 1
the approval tap covers that trust. Level 2 cannot be switched on until they are
recomputed in code. The same holds for the intraday kill switch (portfolio down
more than 5%): at 10:05 ET it has had half an hour to fire, and nothing here
reads it. An approval Jack declines is the kill switch at Level 1.

Mode is read from the gitignored config/execution.local.json:

  {"scheduledPlacement": "approval-required"}   submit for approval
  anything else, or no file                      dry run: report, submit nothing

Dry run still calls the broker's reads and review_equity_order (a simulation), so
the report says exactly what a live run would have submitted.

Idempotency: a journal under logs/executions/ records every submit, including one
whose outcome is unknown, and a re-run the same day skips those symbols; so does
an order or pending approval on the symbol at the broker. Each order's ref_id is
also derived from (account, session, symbol), so a retry sends the same key.

usage: scheduled_orders.py [--repo PATH] [--python PY] [--dry-run] [--now ISO]
Prints a short Korean report on stdout (the cron delivers it verbatim to Telegram;
every message from the trader agent is in Korean). The trade log and the journal
stay English. Exit 1 when the
broker could not be read or answered in a shape this script does not understand;
exit 0 otherwise, including when nothing was eligible.

Python 3.9 compatible (the ops host's system python).
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accounts_local import account_for  # noqa: E402
from signal_windows import Proxy, universe  # noqa: E402

# datetime.UTC is 3.11+; the ops host runs 3.9.
UTC = datetime.timezone.utc  # noqa: UP017
try:
    from zoneinfo import ZoneInfo
    ET: Any = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 — without tzdata there is no safe session clock
    ET = None

# config/guardrails.md § Hard limits. Restated as numbers because this is the code
# that enforces them; tests/test_scheduled_orders.py holds them to that table.
MAX_NOTIONAL = 100.0
# A REDISTRIBUTE buy is an ordinary buy against the caps (strategy/policy.md); the review
# records it as its own kind so the digest can name it beside the TRIM that funds it.
BUY_KINDS = ("buy", "redistribute")
MAX_ORDERS_PER_DAY = 5
MAX_ORDERS_PER_WEEK = 6
MAX_NOTIONAL_PER_DAY = 1000.0

# Regular session is 09:30-16:00 ET. Stay clear of the open's wide spreads and of
# the close (and of a 13:00 early close, which the window below would still hit,
# so early-close days rely on the cron's 10:05 ET start).
SESSION_OPEN = datetime.time(9, 45)
SESSION_CLOSE = datetime.time(15, 45)

# Orders in these states never became a trade and do not use up a slot.
DEAD_STATES = frozenset({"cancelled", "rejected", "failed", "voided"})

# strategy/policy.md entry rule, and the guardrail's freshness window. A close
# older than FRESH_HOURS at submit time (Monday, after a Friday review) does not
# satisfy "a settled close under 24h old", so the entry is re-confirmed on the
# live quote review_equity_order returns: the guardrail's own "or a live quote".
ENTRY_DRAWDOWN_PCT = 5.0
FRESH_HOURS = 24.0

LIVE_MODE = "approval-required"
REF_NAMESPACE = uuid.UUID("6f1c2a6e-3b7d-4f43-9d55-2f6b8c1e0a19")
MAX_ORDER_PAGES = 5


class BrokerShapeError(Exception):
    """The broker answered, but not in a shape this script can rely on."""


# shadow-check's verdict values, as the report shows them. The values themselves stay
# English (other code compares them); an unknown one is shown raw.
VERDICT_KO = {
    "price-mismatch": "가격 불일치",
    "no-settled-row": "확정 종가 없음",
    "incomplete": "정보 부족",
}


def ko_date(iso: Any) -> str:
    """'2026-11-01' -> '2026년 11월 1일'; anything unreadable is returned as text."""
    try:
        d = datetime.date.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{d.year}년 {d.month}월 {d.day}일"


# ── inputs ────────────────────────────────────────────────────────────────────

def load_mode(path: Path) -> str:
    """LIVE_MODE only when the local file says so exactly; otherwise dry run."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return "dry-run"
    if isinstance(doc, dict) and doc.get("scheduledPlacement") == LIVE_MODE:
        return LIVE_MODE
    return "dry-run"


def find_record(reviews: Path, today: str) -> tuple[Path, dict[str, Any]] | None:
    """The newest decision record written before today that targets today."""
    for p in sorted(reviews.glob("*.decisions.json"), reverse=True):
        if p.name[:10] >= today:
            continue
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("targetSession") == today:
            return p, doc
        # Only the newest earlier record can target today; an older one that
        # does not is stale, and reaching further back would act on it.
        return None
    return None


def _number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    if isinstance(v, dict):
        return _number(v.get("amount"))
    return None


def select(record: dict[str, Any], symbols: set[str]) -> tuple[list[dict[str, Any]],
                                                              list[tuple[str, str]]]:
    """Split the record's decisions into candidates and (symbol, reason) skips."""
    decisions = [d for d in record.get("decisions") or [] if isinstance(d, dict)]
    sells = {d.get("symbol") for d in decisions if d.get("side") != "buy"}
    departed = {d.get("symbol") for d in record.get("departures") or [] if isinstance(d, dict)}
    asked = {d.get("symbol") for d in record.get("decisionRequests") or []
             if isinstance(d, dict)}
    blocked = {d.get("symbol"): d.get("rule") for d in record.get("blocked") or []
               if isinstance(d, dict)}

    out: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in decisions:
        sym = str(d.get("symbol") or "?")
        if d.get("side") != "buy":
            continue  # exits are always confirm; not this script's business
        if d.get("autoEligible") is not True:
            continue  # the review already says why in its own message
        notional = _number(d.get("notional"))
        reason = None
        if d.get("kind") not in BUY_KINDS:
            reason = f"주문 종류가 {d.get('kind')!r}이라 매수가 아님"
        elif sym not in symbols:
            reason = "strategy/policy.md 종목 범위에 없음"
        elif d.get("shares") is not None:
            reason = "주식 수량이 지정됨. 자동 실행은 금액 지정 매수만 가능"
        elif notional is None or notional <= 0:
            reason = "사용할 수 있는 금액이 없음"
        elif notional > MAX_NOTIONAL:
            reason = f"금액 ${notional:.2f}이 ${MAX_NOTIONAL:.0f} 한도를 넘음"
        elif sym in sells:
            reason = "같은 종목에 매도(청산)도 제안됨"
        elif sym in departed:
            reason = "기준을 넘어 판단한 기록이 있음"
        elif sym in asked:
            reason = "열린 결정 요청이 있음"
        elif sym in blocked:
            reason = f"리뷰에서 보류로도 분류됨 ({blocked[sym]})"
        elif sym in seen:
            reason = "한 결정 기록에 같은 종목 매수가 두 번 있음"
        if reason:
            skipped.append((sym, reason))
            continue
        seen.add(sym)
        out.append({"symbol": sym, "notional": round(float(notional or 0), 2),
                    "priceUsed": d.get("priceUsed"), "reason": d.get("reason", "")})
    return out, skipped


def _shadow_module(here: Path) -> Any:
    spec = importlib.util.spec_from_file_location("shadow_check", here / "shadow-check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def price_verdicts(record: dict[str, Any], db: str) -> dict[str, str]:
    """{symbol: verdict} from shadow-check's comparison against the settled close."""
    mod = _shadow_module(Path(__file__).resolve().parent)
    return {str(f.get("symbol")): str(f.get("verdict")) for f in mod.check(record, db)}


def wash_sale_blocks(record_path: Path, today: str) -> dict[str, str] | None:
    """{symbol: reason} from the review's <date>.tax.json; None when it is missing
    or its Robinhood half failed.

    docs/decisions.md §20: a buy is blocked while any Robinhood account sold the
    symbol at a loss in the last 30 days, and while the Observatory shows a loss sale
    of it at any other brokerage. The review applies that when it sets
    autoEligible; this repeats the check against the same file, so a record written
    while the guard was down cannot slip a blocked buy through. Missing facts block
    everything, because a lost deduction does not announce itself — and so does a
    file whose Robinhood half failed, which holds only the other brokerages' windows.
    """
    path = record_path.with_name(record_path.name.replace(".decisions.json", ".tax.json"))
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    # Files from before robinhood.status existed were complete or not written at all.
    rh = doc.get("robinhood") if isinstance(doc, dict) else None
    if isinstance(rh, dict) and rh.get("status") != "ok":
        return None
    out: dict[str, str] = {}
    for sym, b in (doc.get("buyBlocks") or {}).items():
        clears = str(b.get("clears", "")) if isinstance(b, dict) else ""
        try:
            datetime.date.fromisoformat(clears)
        except ValueError:
            out[str(sym)] = "wash-sale 보류인데 해제일을 읽을 수 없음"
            continue
        if clears > today:
            sale = (f"{', '.join(b.get('soldIn') or []) or '다른 증권사'}에서 손실 매도"
                    if b.get("source") == "observatory"
                    else f"{ko_date(b.get('date'))} 손실 매도")
            out[str(sym)] = f"wash-sale 기간, {ko_date(clears)} 매수 재개 ({sale})"
    return out


def observatory_coverage(record_path: Path) -> tuple[str | None, str | None, str | None]:
    """(block reason, log warning, report warning) for the other-brokerage half of the
    tax facts. The block reason and the report warning are Korean (they reach the
    delivered report); the log warning is the same warning in English, for the trade
    log.

    The Observatory supplies wash-sale windows for every brokerage but Robinhood,
    from statements that are filed by hand, so it routinely trails by a few days.
    Two cases are treated differently on purpose (docs/decisions.md §21):

      * no usable Observatory summary at all (absent, missing, unreadable,
        refused): every buy is blocked, the same as a missing tax file;
      * a usable summary that is stale: buys proceed, and the report says through
        which date other brokerages were checked. Blocking on staleness would
        block most days, since filing lags by design; at Level 1 the person
        approving in the app is the check for the days the summary cannot see.

    A missing tax file is wash_sale_blocks' to report, so it returns (None, None, None).
    """
    path = record_path.with_name(record_path.name.replace(".decisions.json", ".tax.json"))
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, None, None
    obs = doc.get("observatory") if isinstance(doc, dict) else None
    if not isinstance(obs, dict):
        return "세금 정보에 Observatory 상태가 없음 (다른 증권사 미확인)", None, None
    if obs.get("status") != "ok":
        return (f"다른 증권사 미확인: Observatory 상태 {str(obs.get('status'))[:80]}",
                None, None)
    if obs.get("stale") is not False:
        through = obs.get("transactionsThrough")
        gap = obs.get("missingWeekdays")
        gap_text = f", {gap} weekday(s) not yet filed" if isinstance(gap, int) else ""
        gap_ko = f"(평일 {gap}일분 아직 미제출)" if isinstance(gap, int) else ""
        log = (f"other brokerages' sales are checked only through "
               f"{through or 'an unknown date'}{gap_text}. Before approving, make sure "
               "none of these was sold at a loss at another brokerage since then")
        report = (f"다른 증권사 매도 내역은 {ko_date(through) if through else '알 수 없는 날짜'}"
                  f"까지만 확인했습니다{gap_ko}. 승인하기 전에 그 이후 다른 증권사에서 이 "
                  "종목들을 손실 매도한 적이 없는지 확인해 주십시오")
        return None, log, report
    return None, None, None


def trim_threshold(text: str) -> float | None:
    """The TRIM drawdown threshold (percent) from config/trim-policy.md's table, or None."""
    m = re.search(r"^\|\s*Drawdown threshold\s*\|\s*\*\*([0-9.]+)%\*\*", text, re.M)
    return float(m.group(1)) if m else None


def load_drawdowns(record_path: Path) -> dict[str, float] | None:
    """{symbol: drawdownPct} from the review's <date>.signals.json; None when unreadable."""
    path = record_path.with_name(record_path.name.replace(".decisions.json", ".signals.json"))
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    out: dict[str, float] = {}
    for sym, v in (doc.get("symbols") or {}).items():
        dd = _number(v.get("drawdownPct")) if isinstance(v, dict) else None
        if dd is not None:
            out[str(sym)] = dd
    return out


def trim_zone_reason(sym: str, drawdowns: dict[str, float] | None,
                     threshold: float | None) -> str | None:
    """Why a buy on `sym` must wait for confirmation because of the TRIM zone, or None.

    On 2026-11-02 QQQM sat 10.70% below its high. Its TRIM was skipped by the $150
    floor, so the record carried no exit and the $100 buy went out on its own; the buy
    lifted the position past the floor and the TRIM came due four sessions later.
    """
    if threshold is None:
        return "config/trim-policy.md의 일부 매도 기준을 읽을 수 없음"
    if drawdowns is None:
        return "리뷰의 signals 파일이 없어 일부 매도 구간을 확인할 수 없음"
    dd = drawdowns.get(sym)
    if dd is None:
        return "리뷰 signals에 이 종목의 낙폭이 없음"
    if dd >= threshold:
        return (f"20일 고점 대비 −{dd:.2f}%로 일부 매도 기준 {threshold:g}% 이상. "
                "이 구간의 매수는 방향 선택이라 확인을 기다림")
    return None


def load_highs(record_path: Path) -> dict[str, float]:
    """{symbol: 20-day high} from the review's <date>.signals.json, or empty."""
    path = record_path.with_name(record_path.name.replace(".decisions.json", ".signals.json"))
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, float] = {}
    for sym, v in (doc.get("symbols") or {}).items():
        high = _number(v.get("high")) if isinstance(v, dict) else None
        if high and high > 0:
            out[str(sym)] = high
    return out


def close_age_hours(price_as_of: str, now: datetime.datetime) -> float | None:
    """Hours since the 16:00 ET close of `price_as_of`, or None if unreadable."""
    try:
        day = datetime.date.fromisoformat(str(price_as_of))
    except ValueError:
        return None
    close = datetime.datetime.combine(day, datetime.time(16, 0), tzinfo=ET)
    return (now - close).total_seconds() / 3600


def live_price(review: Any) -> float | None:
    """The current price review_equity_order returned with its simulation."""
    for key in ("last_trade_price", "ask_price", "price"):
        n = _number(_find(review, key))
        if n is not None and n > 0:
            return n
    return None


def live_recheck(review: Any, high: float | None,
                 trim_at: float | None = None) -> tuple[str | None, str]:
    """(skip reason or None, note) for an entry decided on a close that is stale now."""
    live = live_price(review)
    if live is None or not high:
        return ("종가가 24시간보다 오래됐는데 진입 조건을 다시 확인할 실시간 시세가 없음", "")
    dd = (high - live) / high * 100
    if dd < ENTRY_DRAWDOWN_PCT:
        return (f"실시간 재확인: 20일 고점 {high:g} 대비 −{dd:.2f}%로 진입 기준 "
                f"{ENTRY_DRAWDOWN_PCT:g}%에 못 미침", "")
    if trim_at is not None and dd >= trim_at:
        return (f"실시간 재확인: 20일 고점 {high:g} 대비 −{dd:.2f}%로 일부 매도 기준 "
                f"{trim_at:g}% 이상. 확인을 기다림", "")
    return None, f"실시간 재확인 {live:g}, {high:g} 대비 −{dd:.2f}%"


# ── broker reads ──────────────────────────────────────────────────────────────

def week_start_utc(today: datetime.date) -> str:
    """Monday 00:00 US/Eastern of today's week, as an ISO UTC timestamp."""
    monday = today - datetime.timedelta(days=today.weekday())
    start = datetime.datetime.combine(monday, datetime.time(0, 0), tzinfo=ET)
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def et_date(ts: str) -> str:
    s = str(ts or "")
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET).date().isoformat()


def order_notional(o: dict[str, Any]) -> float | None:
    for key in ("dollar_based_amount", "total_notional", "executed_notional"):
        n = _number(o.get(key))
        if n is not None:
            return n
    price, qty = _number(o.get("price")), _number(o.get("quantity"))
    if price is not None and qty is not None:
        return price * qty
    return None


def fetch_orders(client: Any, account: str, since: str) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    args: dict[str, Any] = {"account_number": account, "created_at_gte": since}
    for _ in range(MAX_ORDER_PAGES):
        payload = client.call("get_equity_orders", args)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("orders"), list):
            raise BrokerShapeError("get_equity_orders returned no orders list")
        orders.extend(o for o in data["orders"] if isinstance(o, dict))
        cursor = data.get("next")
        if not cursor:
            return orders
        args = {**args, "cursor": cursor}
    raise BrokerShapeError(f"get_equity_orders had more than {MAX_ORDER_PAGES} pages this week")


def fetch_pending(client: Any, account: str) -> list[dict[str, Any]]:
    """Equity approvals still waiting on Jack in the Robinhood app.

    With trade approval on, place_equity_order creates an approval, not an order,
    so get_equity_orders does not see it until Jack approves. Without this, a
    re-run would read a slot this script already used as free.
    """
    out: list[dict[str, Any]] = []
    args: dict[str, Any] = {"account_number": account, "asset_class": "ASSET_CLASS_EQUITY"}
    for _ in range(MAX_ORDER_PAGES):
        payload = client.call("get_trade_approvals", args)
        items = _find(payload, "approvals")
        if items is None:
            items = _find(payload, "results")
        if not isinstance(items, list):
            raise BrokerShapeError("get_trade_approvals returned no approvals list")
        out.extend(a for a in items if isinstance(a, dict))
        cursor = _find(payload, "next")
        if not cursor:
            return out
        args = {**args, "cursor": cursor}
    raise BrokerShapeError(f"get_trade_approvals had more than {MAX_ORDER_PAGES} pages")


def approval_notional(a: dict[str, Any]) -> float | None:
    for key in ("dollar_amount", "notional", "total_notional", "amount"):
        n = _number(_find(a, key))
        if n is not None:
            return n
    return None


def usage(orders: list[dict[str, Any]], pending: list[dict[str, Any]],
          today: str) -> dict[str, Any]:
    """Slots used this week and today, and today's notional (None if unknown).

    A pending approval counts against today AND this week whatever its date: it
    may still become an order, and the conservative reading costs at most a slot
    until Jack acts on it.
    """
    live = [o for o in orders if str(o.get("state", "")).lower() not in DEAD_STATES]
    todays = [o for o in live if et_date(str(o.get("created_at", ""))) == today]
    total: float | None = 0.0
    for n in [order_notional(o) for o in todays] + [approval_notional(a) for a in pending]:
        if n is None:
            total = None
            break
        total = (total or 0.0) + n
    symbols = {str(o.get("symbol", "")).upper() for o in todays
               if str(o.get("side", "")).lower() == "buy"}
    # An approval's side is not relied on: any pending one blocks its symbol.
    symbols |= {str(_find(a, "symbol") or "").upper() for a in pending}
    return {"week": len(live) + len(pending), "day": len(todays) + len(pending),
            "dayNotional": total, "todaySymbols": symbols}


def _find(obj: Any, key: str) -> Any:
    """First value under `key` anywhere in a nested payload, or None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = _find(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, key)
            if hit is not None:
                return hit
    return None


def approvals_on(client: Any, account: str) -> bool:
    payload = client.call("get_trade_approval_setting", {"account_number": account})
    setting = _find(payload, "setting")
    if isinstance(setting, dict) and setting.get("account_number") not in (None, account):
        raise BrokerShapeError("get_trade_approval_setting answered for a different account")
    value = _find(payload, "human_must_approve_trades")
    if not isinstance(value, bool):
        raise BrokerShapeError("get_trade_approval_setting carried no human_must_approve_trades")
    return value


def review_gate(payload: Any) -> str | None:
    """None when review_equity_order allows a submit-for-approval; else why not."""
    if _find(payload, "customer_approval_required") is not True:
        return "주문 검토가 customer_approval_required를 보고하지 않음"
    checks = _find(payload, "order_checks")
    if checks:
        return "주문 검토에서 order_checks 경고가 나옴: " + json.dumps(checks)[:200]
    return None


# ── the run ───────────────────────────────────────────────────────────────────

def in_session(now: datetime.datetime) -> bool:
    local = now.astimezone(ET)
    return local.weekday() < 5 and SESSION_OPEN <= local.time() <= SESSION_CLOSE


def ref_id(account: str, session: str, symbol: str) -> str:
    return str(uuid.uuid5(REF_NAMESPACE, f"{account}:{session}:{symbol}:buy"))


def order_args(account: str, c: dict[str, Any]) -> dict[str, Any]:
    return {"account_number": account, "symbol": c["symbol"], "side": "buy",
            "type": "market", "dollar_amount": f"{c['notional']:.2f}",
            "market_hours": "regular_hours", "time_in_force": "gfd"}


def read_journal(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"orders": []}
    return doc if isinstance(doc, dict) else {"orders": []}


def write_trade_log(repo: Path, today: str, now: datetime.datetime, c: dict[str, Any],
                    review: str, result: str) -> None:
    """Append one entry per skills/log/SKILL.md. The account is left as the role."""
    path = repo / "logs" / "trades" / f"{today}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else (
        f"---\ndate: {today}\nbroker: robinhood\naccount: <AGENTIC_ACCOUNT>\n---\n")
    entry = (
        f"\n## {now.astimezone(ET).strftime('%H:%M:%S')} — BUY ${c['notional']:.2f} "
        f"{c['symbol']} @ market (notional)\n\n"
        "- broker: robinhood\n- account: <AGENTIC_ACCOUNT>\n"
        "- side: buy | asset_class: equity | tif: gfd\n"
        f"- review: {review}\n"
        "- confirmation: standing-auth (rule: config/guardrails.md Standing authorization), "
        "submitted by scripts/scheduled_orders.py for approval in the Robinhood app\n"
        f"- result: {result}\n"
        f"- policy ref: strategy/policy.md#entry-rules — {c.get('reason') or 'dip-buy'}\n"
    )
    with path.open("a") as fh:
        fh.write(head + entry)


def run(repo: Path, client_factory: Any, now: datetime.datetime, dry_run: bool,
        db: str, accounts_path: Path | None = None) -> tuple[int, list[str]]:
    if ET is None:
        return 1, ["⚠️ agentic-execute: 미국 동부 시간대 데이터가 없어 아무것도 제출하지 "
                   "않았습니다."]
    today = now.astimezone(ET).date().isoformat()
    mode = "dry-run" if dry_run else load_mode(repo / "config" / "execution.local.json")
    live = mode == LIVE_MODE
    tag = "agentic-execute" if live else "agentic-execute [시험 실행]"

    found = find_record(repo / "logs" / "reviews", today)
    if found is None:
        return 0, [f"{tag} {today}: 오늘 거래일을 대상으로 한 결정 기록이 없어 할 일이 "
                   "없습니다."]
    record_path, record = found

    symbols = set(universe((repo / "strategy" / "policy.md").read_text()))
    candidates, skipped = select(record, symbols)
    try:
        trim_at = trim_threshold((repo / "config" / "trim-policy.md").read_text())
    except OSError:
        trim_at = None
    if candidates:
        drawdowns = load_drawdowns(record_path)
        kept = []
        for c in candidates:
            why = trim_zone_reason(c["symbol"], drawdowns, trim_at)
            if why:
                skipped.append((c["symbol"], why))
            else:
                kept.append(c)
        candidates = kept
    coverage_warning: str | None = None  # English, for the trade log
    coverage_report: str | None = None  # Korean, for the delivered report
    if candidates:
        block, coverage_warning, coverage_report = observatory_coverage(record_path)
        if block:
            skipped += [(c["symbol"], block) for c in candidates]
            candidates = []
    if candidates:
        blocks = wash_sale_blocks(record_path, today)
        kept = []
        for c in candidates:
            why = "리뷰의 세금 정보가 없음 (wash-sale 기간 확인 불가)" if blocks is None \
                else blocks.get(c["symbol"])
            if why:
                skipped.append((c["symbol"], why))
            else:
                kept.append(c)
        candidates = kept
    if candidates and record.get("priceAsOf") != record.get("date"):
        # The review prices from its own session's close. Any other date is a
        # stale or mislabelled input, which is the fault the freshness rule names.
        skipped += [(c["symbol"], f"가격 기준일(priceAsOf {record.get('priceAsOf')})이 "
                                  f"리뷰 거래일({record.get('date')})과 다름")
                    for c in candidates]
        candidates = []
    if candidates:
        # Only "ok" passes. no-settled-row is what a frozen refresh or a missing
        # database also looks like (2026-08-26 held a figure three days old), and
        # the guardrail asks for a settled close, not for the absence of a mismatch.
        verdicts = price_verdicts(record, db)
        kept = []
        for c in candidates:
            v = verdicts.get(c["symbol"], "incomplete")
            if v == "ok":
                kept.append(c)
            else:
                skipped.append((c["symbol"], f"가격 점검 미통과: {VERDICT_KO.get(v, v)}"))
        candidates = kept

    lines: list[str] = []
    if not candidates:
        lines.append(f"{tag} {today}: {record_path.name}에 자동 실행 대상 매수가 없습니다.")
        lines += [f"  건너뜀 {s}: {r}" for s, r in skipped]
        return 0, lines
    if not in_session(now):
        lines.append(f"⚠️ {tag} {today}: 정규장 실행 시간대가 아니어서 아무것도 제출하지 "
                     f"않았습니다 (대상 {len(candidates)}건).")
        return 0, lines

    account = account_for("Agentic", accounts_path)
    if not account:
        return 1, [f"⚠️ {tag} {today}: config/accounts.local.md에서 Agentic 계좌를 찾지 "
                   "못해 아무것도 제출하지 않았습니다."]

    age = close_age_hours(str(record.get("priceAsOf")), now)
    stale = age is None or age > FRESH_HOURS
    highs = load_highs(record_path) if stale else {}

    journal_path = repo / "logs" / "executions" / f"{today}.json"
    journal = read_journal(journal_path)
    done = {o.get("symbol") for o in journal.get("orders") or []
            if o.get("status") in ("submitted", "uncertain")}

    submitted = 0
    client = client_factory()
    try:
        if not approvals_on(client, account):
            msg = (f"⛔ {tag} {today}: Robinhood 앱의 거래 승인이 꺼져 있습니다. Level 1은 "
                   "모든 주문이 앱에서 승인을 기다릴 때만 제출합니다."
                   + (f" 아무것도 제출하지 않았습니다 (대상 {len(candidates)}건)." if live else
                      " 실제 실행이었다면 아무것도 제출하지 않았을 것입니다.")
                   + " 다시 시작하려면 Agentic 계좌 설정에서 승인을 켜 주십시오.")
            if live:
                return 0, [msg]
            lines.append(msg)
        use = usage(fetch_orders(client, account, week_start_utc(now.astimezone(ET).date())),
                    fetch_pending(client, account), today)
        if use["dayNotional"] is None:
            return 0, lines + [f"⚠️ {tag} {today}: 오늘 주문 또는 승인 대기 중 금액을 읽을 "
                               "수 없는 건이 있어 일일 한도를 확인할 수 없습니다. 아무것도 "
                               "제출하지 않았습니다."]

        slots = min(MAX_ORDERS_PER_DAY - use["day"], MAX_ORDERS_PER_WEEK - use["week"])
        room = MAX_NOTIONAL_PER_DAY - float(use["dayNotional"])
        results: list[str] = []
        for c in candidates:
            sym = c["symbol"]
            if sym in done:
                skipped.append((sym, "이 스크립트가 오늘 이미 제출함"))
                continue
            if sym in use["todaySymbols"]:
                skipped.append((sym, "오늘 이 종목에 주문 또는 승인 대기가 이미 있음"))
                continue
            if slots <= 0:
                skipped.append((sym, f"주문 건수 한도 (오늘 {use['day']}/{MAX_ORDERS_PER_DAY}, "
                                     f"이번 주 {use['week']}/{MAX_ORDERS_PER_WEEK})"))
                continue
            if c["notional"] > room:
                skipped.append((sym, f"일일 금액 한도 (남은 금액 ${room:.2f})"))
                continue
            args = order_args(account, c)
            try:
                review = client.call("review_equity_order", args)
            except (RuntimeError, ValueError) as e:
                skipped.append((sym, f"review_equity_order 실패: {str(e)[:160]}"))
                continue
            why = review_gate(review)
            if why:
                skipped.append((sym, why))
                continue
            note = ""
            if stale:
                why, note = live_recheck(review, highs.get(sym), trim_at)
                if why:
                    skipped.append((sym, why))
                    continue
                note = f" ({note})"
            if not live:
                results.append(f"  제출 예정: 매수 {sym} ${c['notional']:.2f}{note}")
                slots -= 1
                room -= c["notional"]
                continue
            slots -= 1
            room -= c["notional"]
            ident: Any = None
            error = ""  # English: it is journaled
            shown = ""  # what the report says about it
            try:
                placed = client.call("place_equity_order",
                                     {**args, "ref_id": ref_id(account, today, sym)})
                ident = _find(placed, "approval_id") or _find(placed, "order_id")
                if not ident:
                    error = "the response named no approval or order id"
                    shown = "응답에 승인 또는 주문 id가 없음"
            except (RuntimeError, ValueError) as e:
                error = shown = str(e)[:160]
            # An error after the request left is uncertain, not refused: the submit may
            # have landed. Journal it so a re-run skips the symbol instead of relying on
            # Robinhood deduplicating the ref_id, which nobody has confirmed for approvals.
            status = "submitted" if not error else "uncertain"
            journal.setdefault("orders", []).append({
                "symbol": sym, "notional": c["notional"], "status": status,
                "id": str(ident or ""), "error": error, "at": now.isoformat(),
                "record": record_path.name})
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(json.dumps(journal, indent=1) + "\n")
            if error:
                results.append(f"  ⚠️ {sym}: 결과 불명 ({shown}). Robinhood 앱을 확인해 "
                               "주십시오. 오늘 다시 실행해도 다시 제출하지 않습니다")
                continue
            write_trade_log(repo, today, now, c, "customer_approval_required; no order checks"
                            + (f"; {coverage_warning}" if coverage_warning else ""),
                            f"submitted for approval, id {ident}")
            submitted += 1
            results.append(f"  승인 대기로 제출: 매수 {sym} ${c['notional']:.2f} "
                           f"(id {ident}){note}")
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    if live and submitted:
        lines.append(f"🤖 {tag} {today}: 주문 {submitted}건이 Robinhood 앱(Agentic)에서 "
                     "승인을 기다리고 있습니다. 승인하기 전에는 체결되지 않습니다.")
    elif live and results:
        lines.append(f"⚠️ {tag} {today}: 제출 {len(results)}건, 결과 불명.")
    elif live:
        lines.append(f"{tag} {today}: 아무것도 제출하지 않았습니다.")
    else:
        lines.append(f"{tag} {today}: 주문 {len(results)}건을 승인 대기로 제출할 예정입니다.")
    lines += results
    if results and coverage_report:
        lines.append(f"  ⚠️ {coverage_report}.")
    lines += [f"  건너뜀 {s}: {r}" for s, r in skipped]
    return 0, lines


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(here.parent))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--proxy", default=str(here / "mcp-robinhood-proxy.py"))
    ap.add_argument("--db", default=None, help="observatory db (default: shadow-check's)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", help="ISO timestamp, for testing the session window")
    args = ap.parse_args()

    now = (datetime.datetime.fromisoformat(args.now) if args.now
           else datetime.datetime.now(UTC))
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    db = args.db or str(_shadow_module(here).DEFAULT_DB)

    def factory() -> Proxy:
        return Proxy(args.python, args.proxy, mode="--agentic-orders", client="scheduled-orders")

    try:
        rc, lines = run(Path(args.repo), factory, now, args.dry_run, db)
    except Exception as e:  # noqa: BLE001 — report on the channel, fail the cron
        print(f"⚠️ agentic-execute: 끝까지 실행하지 못하고 멈췄습니다 — {e}. 이미 제출된 "
              "주문이 있는지 Robinhood 앱을 확인해 주십시오.")
        return 1
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
