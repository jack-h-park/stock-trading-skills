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
    is autoEligible, a buy, kind buy, notional <= $100, no share quantity
  * the symbol is in the universe parsed from strategy/policy.md
  * the symbol carries no departure, no decision request and no sell decision
    in the same record (one symbol, one direction)
  * the symbol is outside every wash-sale window in the review's <date>.tax.json
    (§20); a missing tax file blocks every buy
  * the record's priceAsOf is its own session, and the price the decision used
    matches the settled close (scripts/shadow-check.py's own comparison, not a
    second implementation); no settled close yet means no order
  * the daily and weekly order counts and the daily notional ceiling, counted
    from the broker's order list plus every approval still pending in the app
  * the regular session is open
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
Prints a short English report on stdout (the cron delivers it). Exit 1 when the
broker could not be read or answered in a shape this script does not understand;
exit 0 otherwise, including when nothing was eligible.

Python 3.9 compatible (the ops host's system python).
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
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

LIVE_MODE = "approval-required"
REF_NAMESPACE = uuid.UUID("6f1c2a6e-3b7d-4f43-9d55-2f6b8c1e0a19")
MAX_ORDER_PAGES = 5


class BrokerShapeError(Exception):
    """The broker answered, but not in a shape this script can rely on."""


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
        if d.get("kind") != "buy":
            reason = f"kind is {d.get('kind')!r}, not buy"
        elif sym not in symbols:
            reason = "not in the strategy/policy.md universe"
        elif d.get("shares") is not None:
            reason = "carries a share quantity; only notional buys are auto-placed"
        elif notional is None or notional <= 0:
            reason = "no usable notional"
        elif notional > MAX_NOTIONAL:
            reason = f"notional ${notional:.2f} is over the ${MAX_NOTIONAL:.0f} cap"
        elif sym in sells:
            reason = "an exit is also proposed on this symbol"
        elif sym in departed:
            reason = "carries a departure from a threshold"
        elif sym in asked:
            reason = "has an open decision request"
        elif sym in blocked:
            reason = f"the review also lists it as blocked ({blocked[sym]})"
        elif sym in seen:
            reason = "a second buy on the same symbol in one record"
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
    """{symbol: reason} from the review's <date>.tax.json; None when it is missing.

    docs/decisions.md §20: a buy is blocked while any Robinhood account sold the
    symbol at a loss in the last 30 days. The review applies that when it sets
    autoEligible; this repeats the check against the same file, so a record written
    while the guard was down cannot slip a blocked buy through. Missing facts block
    everything, because a lost deduction does not announce itself.
    """
    path = record_path.with_name(record_path.name.replace(".decisions.json", ".tax.json"))
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    out: dict[str, str] = {}
    for sym, b in (doc.get("buyBlocks") or {}).items():
        clears = str(b.get("clears", "")) if isinstance(b, dict) else ""
        try:
            datetime.date.fromisoformat(clears)
        except ValueError:
            out[str(sym)] = "wash-sale block with no readable clearing date"
            continue
        if clears > today:
            out[str(sym)] = f"wash-sale window until {clears} (loss sale {b.get('date')})"
    return out


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
        return "review did not report customer_approval_required"
    checks = _find(payload, "order_checks")
    if checks:
        return "review raised order checks: " + json.dumps(checks)[:200]
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
        return 1, ["⚠️ agentic-execute: no US/Eastern timezone data; nothing submitted."]
    today = now.astimezone(ET).date().isoformat()
    mode = "dry-run" if dry_run else load_mode(repo / "config" / "execution.local.json")
    live = mode == LIVE_MODE
    tag = "agentic-execute" if live else "agentic-execute [dry run]"

    found = find_record(repo / "logs" / "reviews", today)
    if found is None:
        return 0, [f"{tag} {today}: no decision record targets today's session; nothing to do."]
    record_path, record = found

    symbols = set(universe((repo / "strategy" / "policy.md").read_text()))
    candidates, skipped = select(record, symbols)
    if candidates:
        blocks = wash_sale_blocks(record_path, today)
        kept = []
        for c in candidates:
            why = "no tax facts from the review (wash-sale window unknown)" if blocks is None \
                else blocks.get(c["symbol"])
            if why:
                skipped.append((c["symbol"], why))
            else:
                kept.append(c)
        candidates = kept
    if candidates and record.get("priceAsOf") != record.get("date"):
        # The review prices from its own session's close. Any other date is a
        # stale or mislabelled input, which is the fault the freshness rule names.
        skipped += [(c["symbol"], f"priceAsOf {record.get('priceAsOf')} is not the review's "
                                  f"own session {record.get('date')}") for c in candidates]
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
                skipped.append((c["symbol"], f"price check: {v}"))
        candidates = kept

    lines: list[str] = []
    if not candidates:
        lines.append(f"{tag} {today}: no auto-eligible buy in {record_path.name}.")
        lines += [f"  skipped {s}: {r}" for s, r in skipped]
        return 0, lines
    if not in_session(now):
        lines.append(f"⚠️ {tag} {today}: outside the regular-session window; "
                     f"nothing submitted ({len(candidates)} eligible).")
        return 0, lines

    account = account_for("Agentic", accounts_path)
    if not account:
        return 1, [f"⚠️ {tag} {today}: the Agentic account could not be resolved from "
                   "config/accounts.local.md; nothing submitted."]

    journal_path = repo / "logs" / "executions" / f"{today}.json"
    journal = read_journal(journal_path)
    done = {o.get("symbol") for o in journal.get("orders") or []
            if o.get("status") in ("submitted", "uncertain")}

    submitted = 0
    client = client_factory()
    try:
        if not approvals_on(client, account):
            msg = (f"⛔ {tag} {today}: trade approvals are OFF in the Robinhood app. Level 1 "
                   "submits only while every order waits for your approval there"
                   + (f"; nothing submitted ({len(candidates)} eligible)." if live else
                      "; a live run would submit nothing.")
                   + " Turn approvals on in the Agentic account settings to resume.")
            if live:
                return 0, [msg]
            lines.append(msg)
        use = usage(fetch_orders(client, account, week_start_utc(now.astimezone(ET).date())),
                    fetch_pending(client, account), today)
        if use["dayNotional"] is None:
            return 0, lines + [f"⚠️ {tag} {today}: an order or pending approval today has no "
                               "readable notional, so the daily ceiling cannot be checked; "
                               "nothing submitted."]

        slots = min(MAX_ORDERS_PER_DAY - use["day"], MAX_ORDERS_PER_WEEK - use["week"])
        room = MAX_NOTIONAL_PER_DAY - float(use["dayNotional"])
        results: list[str] = []
        for c in candidates:
            sym = c["symbol"]
            if sym in done:
                skipped.append((sym, "already submitted today by this script"))
                continue
            if sym in use["todaySymbols"]:
                skipped.append((sym, "an order or pending approval on this symbol exists today"))
                continue
            if slots <= 0:
                skipped.append((sym, f"order count cap (day {use['day']}/{MAX_ORDERS_PER_DAY}, "
                                     f"week {use['week']}/{MAX_ORDERS_PER_WEEK})"))
                continue
            if c["notional"] > room:
                skipped.append((sym, f"daily notional ceiling (${room:.2f} left)"))
                continue
            args = order_args(account, c)
            try:
                review = client.call("review_equity_order", args)
            except (RuntimeError, ValueError) as e:
                skipped.append((sym, f"review_equity_order failed: {str(e)[:160]}"))
                continue
            why = review_gate(review)
            if why:
                skipped.append((sym, why))
                continue
            if not live:
                results.append(f"  would submit: buy {sym} ${c['notional']:.2f}")
                slots -= 1
                room -= c["notional"]
                continue
            slots -= 1
            room -= c["notional"]
            ident: Any = None
            error = ""
            try:
                placed = client.call("place_equity_order",
                                     {**args, "ref_id": ref_id(account, today, sym)})
                ident = _find(placed, "approval_id") or _find(placed, "order_id")
                if not ident:
                    error = "the response named no approval or order id"
            except (RuntimeError, ValueError) as e:
                error = str(e)[:160]
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
                results.append(f"  ⚠️ {sym}: outcome unknown ({error}); check the Robinhood "
                               "app — a re-run today will not resubmit it")
                continue
            write_trade_log(repo, today, now, c, "customer_approval_required; no order checks",
                            f"submitted for approval, id {ident}")
            submitted += 1
            results.append(f"  submitted: buy {sym} ${c['notional']:.2f} (id {ident})")
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    if live and submitted:
        lines.append(f"🤖 {tag} {today}: {submitted} order(s) waiting for your approval in the "
                     "Robinhood app (Agentic). Nothing fills until you approve.")
    elif live and results:
        lines.append(f"⚠️ {tag} {today}: {len(results)} submit(s) with an unknown outcome.")
    elif live:
        lines.append(f"{tag} {today}: nothing submitted.")
    else:
        lines.append(f"{tag} {today}: {len(results)} order(s) would be submitted for approval.")
    lines += results
    lines += [f"  skipped {s}: {r}" for s, r in skipped]
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
        print(f"⚠️ agentic-execute: stopped before finishing — {e}. Check the Robinhood app "
              "for anything already submitted.")
        return 1
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
