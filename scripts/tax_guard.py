#!/usr/bin/env python3
"""US tax facts the review must respect, computed in code: wash-sale windows and lot terms.

Two guards, both read through mcp-robinhood-proxy.py --read-only (the same server job
A uses, so nothing here can place an order):

1. WASH-SALE WINDOW. A loss is disallowed for the year when the same security is
   bought within 30 days before or after the loss sale, in ANY of the taxpayer's
   accounts — the rule does not stop at an account boundary, and a broker's 1099-B
   usually adjusts only within one account. The Agentic sleeve buys mega-caps the
   Long-term account also holds, so a loss sale there followed by a $100 dip-buy
   here would cost the deduction. And the review's own rules generate the pattern:
   a stop-loss followed by a further drop raises a BUY on the same symbol. So:
     - a BUY is blocked while any of the three Robinhood accounts sold that symbol at
       a loss in the last 30 days; it clears on day 31 after the sale;
     - a loss-realizing sell is annotated when the symbol was bought in any of them in
       the last 30 days, because that loss would be deferred.
   Accounts at other brokers are not visible here and are out of scope.

2. HOLDING PERIOD. A US gain is short-term unless the lot was held more than a year.
   For each Agentic position the open lots, their term, and the date the oldest lot
   becomes long-term (the default sale order is first-in, first-out). Display only:
   the review does not hold a proposal back because a lot is about to turn.

Korea taxes overseas gains at one rate whatever the holding period and has no
wash-sale rule; both guards are about the US return only.

usage: tax_guard.py --repo PATH --python PY --proxy PATH [--out FILE] [--date YYYY-MM-DD]
Exit 0 with no file on any failure: job A then says the tax facts are unavailable.
Python 3.9 compatible.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from signal_windows import Proxy  # noqa: E402

WINDOW_DAYS = 30
ROLES = {"<AGENTIC_ACCOUNT>": "Agentic", "<LONGTERM_ACCOUNT>": "Long-term",
         "<MIDTERM_ACCOUNT>": "Mid-term"}


def accounts(local_md: str) -> dict[str, str]:
    """{role: account number} from the gitignored config/accounts.local.md table."""
    out = {}
    for placeholder, role in ROLES.items():
        m = re.search(r"^\|\s*`?" + re.escape(placeholder) + r"`?\s*\|\s*([0-9]+)\s*\|",
                      local_md, re.M)
        if m:
            out[role] = m.group(1)
    return out


def _date(ts: str) -> str:
    return str(ts)[:10]


def buy_blocks(loss_sales: list[dict[str, Any]], today: datetime.date) -> dict[str, Any]:
    """Symbols a BUY may not be proposed for today, with the sale that blocks each.

    A buy today is inside a sale's window when the sale was at most 30 days ago. The
    most recent blocking sale decides when the block clears (day 31 after it)."""
    blocks: dict[str, Any] = {}
    for s in loss_sales:
        sold = datetime.date.fromisoformat(s["date"])
        if not (today - datetime.timedelta(days=WINDOW_DAYS) <= sold <= today):
            continue
        clears = (sold + datetime.timedelta(days=WINDOW_DAYS + 1)).isoformat()
        cur = blocks.get(s["symbol"])
        if cur is None or s["date"] > cur["date"]:
            blocks[s["symbol"]] = {**s, "clears": clears}
    return blocks


def recent_buys(buys: list[dict[str, Any]],
                today: datetime.date) -> dict[str, list[dict[str, Any]]]:
    """Buys in the last 30 days, by symbol: a loss sale of these would be deferred."""
    out: dict[str, list[dict[str, Any]]] = {}
    floor = (today - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    for b in sorted(buys, key=lambda b: b["date"]):
        if floor <= b["date"] <= today.isoformat():
            out.setdefault(b["symbol"], []).append(b)
    return out


def long_term_from(open_date: str) -> str:
    """First sale date that is long-term: held MORE than one year from acquisition."""
    d = datetime.date.fromisoformat(open_date)
    try:
        anniversary = d.replace(year=d.year + 1)
    except ValueError:  # Feb 29
        anniversary = d.replace(year=d.year + 1, day=28)
    return (anniversary + datetime.timedelta(days=1)).isoformat()


def lot_terms(lots: list[dict[str, Any]], today: datetime.date) -> dict[str, Any]:
    open_lots = [lot for lot in lots if float(lot.get("quantity") or 0) > 0]
    if not open_lots:
        return {}
    oldest = min(open_lots, key=lambda lot: lot["open_date"])
    lt = long_term_from(oldest["open_date"])
    days = (datetime.date.fromisoformat(lt) - today).days
    terms = sorted({"long" if (datetime.date.fromisoformat(long_term_from(lot["open_date"]))
                               <= today) else "short" for lot in open_lots})
    return {
        "lots": [{"openDate": lot["open_date"], "quantity": lot["quantity"]}
                 for lot in sorted(open_lots, key=lambda lot: lot["open_date"])],
        "term": "mixed" if len(terms) > 1 else terms[0] + "-term",
        "oldestLongTermFrom": lt,
        "daysToLongTerm": max(days, 0),
        "turnsLongTermSoon": 0 < days <= WINDOW_DAYS,
    }


def _pages(proxy: Proxy, tool: str, args: dict[str, Any], key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(20):
        call = dict(args, **({"cursor": cursor} if cursor else {}))
        data = proxy.call(tool, call)["data"]
        out.extend(data.get(key) or [])
        cursor = data.get("next_cursor") or data.get("next")
        if not cursor:
            break
    return out


def collect(proxy: Proxy, accts: dict[str, str], today: datetime.date) -> dict[str, Any]:
    loss_sales: list[dict[str, Any]] = []
    buys: list[dict[str, Any]] = []
    since = (today - datetime.timedelta(days=WINDOW_DAYS + 1)).isoformat()
    for role, number in accts.items():
        for t in _pages(proxy, "get_pnl_trade_history",
                        {"account_number": number, "span": "3month"}, "trades"):
            gain = float(t.get("realized_gain") or 0)
            if t.get("side") == "sell" and gain < 0:
                loss_sales.append({"symbol": t["symbol"], "account": role,
                                   "date": _date(t["timestamp"]), "loss": round(gain, 2)})
        for o in _pages(proxy, "get_equity_orders",
                        {"account_number": number, "state": "filled", "created_at_gte": since},
                        "orders"):
            if o.get("side") == "buy" and o.get("symbol"):
                buys.append({"symbol": o["symbol"], "account": role,
                             "date": _date(o.get("last_transaction_at") or o.get("created_at"))})

    lots: dict[str, Any] = {}
    agentic = accts.get("Agentic")
    if agentic:
        positions = _pages(proxy, "get_equity_positions", {"account_number": agentic}, "positions")
        for p in positions:
            sym = p.get("symbol")
            if not sym:
                continue
            data = proxy.call("get_equity_tax_lots", {"account_number": agentic, "symbol": sym})
            terms = lot_terms(data["data"].get("tax_lots") or [], today)
            if terms:
                lots[sym] = terms

    return {
        "asOf": today.isoformat(),
        "accountsCovered": sorted(accts),
        "lossSales": sorted(loss_sales, key=lambda s: (s["date"], s["symbol"])),
        "buyBlocks": buy_blocks(loss_sales, today),
        "recentBuys": recent_buys(buys, today),
        "lots": lots,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--proxy", required=True)
    ap.add_argument("--out")
    ap.add_argument("--date")
    args = ap.parse_args()
    today = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    try:
        accts = accounts((Path(args.repo) / "config" / "accounts.local.md").read_text())
        if not accts:
            raise RuntimeError("no account found in config/accounts.local.md")
        proxy = Proxy(args.python, args.proxy)
        try:
            doc = collect(proxy, accts, today)
        finally:
            proxy.close()
    except Exception as e:  # noqa: BLE001 — job A reports the facts as unavailable
        sys.stderr.write(f"tax_guard: {e} — no tax facts today\n")
        return 0
    text = json.dumps(doc, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
