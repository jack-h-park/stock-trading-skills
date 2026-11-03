#!/usr/bin/env python3
"""Regenerate the Observatory's data/robinhood-snapshot.json from the broker.

The Observatory reads Robinhood positions, tax lots and post-CSV orders from this
one file, and its holdings sheet tab is built from it. Until now an interactive
session wrote it by hand, so it went stale between sessions: after the 2026-10-22
regeneration, every trade Jack made by hand showed up in the 13:30 reconcile as
sheet drift until someone regenerated it again. This script makes the pull
schedulable. The Observatory's refresh wrapper runs it before `pnpm refresh`.

It reads through mcp-robinhood-proxy.py --read-only, the floor the scheduled
review already uses, so no order tool is reachable from here. Account numbers come
from the gitignored config/accounts.local.md; nothing here names one.

The shape is the one docs/robinhood-orders-bridge.md in the Observatory defines:
per account, positions, per-symbol lots, and the order history from
`createdAtGte` with no symbol or state filter, paged to the end.

A pull that looks wrong never replaces a good file. The new snapshot is refused,
and the old one kept, when:

  * an account in accounts.local.md is missing from the pull
  * positions or orders did not finish paging (`complete` would be false)
  * any symbol's lots could not be read
  * a symbol's lots do not add up to its position quantity (a lots page cut short
    looks answered; only the tally sees it)
  * an account that held positions in the previous snapshot now reads none

A stale file is reported by the Observatory's `robinhood_snapshot_fresh`; a wrong
one is not reported by anything, so refusing is the safe side.

usage: robinhood_snapshot.py --out PATH [--created-at-gte YYYY-MM-DD] [--check]
  --created-at-gte  defaults to the previous snapshot's value. Fetching from too
                    early costs nothing (the bridge filters on execution date);
                    too late loses fills silently, so it never moves on its own.
  --check           pull and validate, print the summary, write nothing.

Exit 0 written (or --check passed), 1 refused (old file kept), 2 could not pull.
stdout is one summary line, then one line per refusal reason.

Python 3.9 compatible: the ops host runs it under /usr/bin/python3.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from accounts_local import accounts  # noqa: E402

ROLES = ("Mid-term", "Long-term", "Agentic")
PAGE_LIMIT = 200
TALLY_TOLERANCE = 1e-6
POSITION_FIELDS = ("symbol", "quantity", "average_buy_price", "type")
UTC = datetime.timezone.utc  # noqa: UP017 — datetime.UTC is 3.11+; the ops host runs 3.9

Call = Callable[[str, dict], dict]


def pages(call: Call, tool: str, args: dict[str, Any], key: str) -> tuple[list[Any], bool]:
    """Every item under `key`, following the cursor; (items, finished)."""
    out: list[Any] = []
    cursor = None
    for _ in range(PAGE_LIMIT):
        data = call(tool, dict(args, **({"cursor": cursor} if cursor else {})))["data"]
        out.extend(data.get(key) or [])
        cursor = data.get("next_cursor") or data.get("next")
        if not cursor:
            return out, True
    return out, False


def pull(call: Call, numbers: dict[str, str], since: str, fetched_at: str) -> dict[str, Any]:
    doc: dict[str, Any] = {"fetchedAt": fetched_at, "source": "robinhood_mcp", "accounts": []}
    for role in ROLES:
        number = numbers.get(role)
        if not number:
            continue
        positions, positions_done = pages(call, "get_equity_positions",
                                          {"account_number": number}, "positions")
        lots, unavailable = [], []
        for p in positions:
            sym = p["symbol"]
            try:
                got, done = pages(call, "get_equity_tax_lots",
                                  {"account_number": number, "symbol": sym}, "tax_lots")
            except Exception as e:  # noqa: BLE001 — one symbol's failure is a refusal, not a crash
                unavailable.append({"symbol": sym, "reason": str(e)[:200]})
                continue
            if not done:
                unavailable.append({"symbol": sym, "reason": "pagination did not finish"})
            lots.append({"symbol": sym, "lots": got})
        orders, orders_done = pages(call, "get_equity_orders",
                                    {"account_number": number, "created_at_gte": since}, "orders")
        doc["accounts"].append({
            "accountNumber": number,
            "nickname": role,
            "positions": [{k: p.get(k) for k in POSITION_FIELDS} for p in positions],
            "positionsComplete": positions_done,
            "lots": lots,
            "lotsUnavailable": unavailable,
            "orders": {"createdAtGte": since, "complete": orders_done, "items": orders},
        })
    return doc


def problems(new: dict[str, Any], old: dict[str, Any] | None, roles: list[str]) -> list[str]:
    """Why `new` must not replace `old`; empty when it may."""
    out = []
    by_role = {a["nickname"]: a for a in new.get("accounts") or []}
    old_by_role = {a.get("nickname"): a for a in (old or {}).get("accounts") or []}
    for role in roles:
        acct = by_role.get(role)
        if acct is None:
            out.append(f"{role}: missing from the pull")
            continue
        if not acct.get("positionsComplete", True):
            out.append(f"{role}: positions did not finish paging")
        if not acct["orders"]["complete"]:
            out.append(f"{role}: orders did not finish paging")
        for u in acct["lotsUnavailable"]:
            out.append(f"{role}: lots unavailable for {u['symbol']} ({u['reason']})")
        tally = {x["symbol"]: sum(float(lot.get("quantity") or 0) for lot in x["lots"])
                 for x in acct["lots"]}
        unavailable = {u["symbol"] for u in acct["lotsUnavailable"]}
        for p in acct["positions"]:
            sym = p["symbol"]
            if sym in unavailable:
                continue
            if abs(float(p["quantity"] or 0) - tally.get(sym, 0.0)) > TALLY_TOLERANCE:
                out.append(f"{role}: {sym} lots add up to {tally.get(sym, 0.0):g}, "
                           f"position is {p['quantity']}")
        before = old_by_role.get(role)
        if before and before.get("positions") and not acct["positions"]:
            out.append(f"{role}: no positions, previous snapshot had "
                       f"{len(before['positions'])}")
    return out


def summary(doc: dict[str, Any]) -> str:
    parts = []
    for a in doc["accounts"]:
        n_lots = sum(len(x["lots"]) for x in a["lots"])
        parts.append(f"{a['nickname']} {len(a['positions'])} positions/{n_lots} lots/"
                     f"{len(a['orders']['items'])} orders")
    return "; ".join(parts)


def write_atomically(path: Path, doc: dict[str, Any]) -> None:
    """Keep the previous file as <name>.prev, then swap the new one in."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.chmod(0o600)
    if path.exists():
        prev = path.with_name(path.name + ".prev")
        os.replace(path, prev)
    os.replace(tmp, path)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def default_since(old: dict[str, Any] | None) -> str | None:
    values = [a.get("orders", {}).get("createdAtGte") for a in (old or {}).get("accounts") or []]
    values = [v for v in values if v]
    return min(values) if values else None


def main(argv: list[str] | None = None, call: Call | None = None,
         now: datetime.datetime | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--created-at-gte")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--accounts", type=Path, default=REPO / "config" / "accounts.local.md")
    args = ap.parse_args(argv)

    old = _load(args.out)
    since = args.created_at_gte or default_since(old)
    if not since:
        print("robinhood snapshot: no --created-at-gte and no previous snapshot to take it from")
        return 2
    try:
        numbers = accounts(args.accounts.read_text())
    except OSError:
        numbers = {}
    roles = [r for r in ROLES if r in numbers]
    if not roles:
        print(f"robinhood snapshot: no accounts in {args.accounts.name}")
        return 2

    fetched_at = (now or datetime.datetime.now(UTC)) \
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")
    proxy = None
    try:
        if call is None:
            from signal_windows import Proxy
            proxy = Proxy(sys.executable, str(HERE / "mcp-robinhood-proxy.py"),
                          client="robinhood-snapshot")
            call = proxy.call
        doc = pull(call, numbers, since, fetched_at)
    except Exception as e:  # noqa: BLE001 — any pull failure keeps the old file
        print(f"robinhood snapshot: pull failed, kept the previous file — {str(e)[:200]}")
        return 2
    finally:
        if proxy is not None:
            proxy.close()

    issues = problems(doc, old, roles)
    if issues:
        print(f"robinhood snapshot: refused, kept the previous file — {len(issues)} problem(s)")
        for line in issues:
            print(f"  {line}")
        return 1
    if args.check:
        print(f"robinhood snapshot: check passed, nothing written — {summary(doc)}")
        return 0
    write_atomically(args.out, doc)
    print(f"robinhood snapshot: wrote {fetched_at} — {summary(doc)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
