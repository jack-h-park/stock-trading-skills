#!/usr/bin/env python3
"""Check yesterday's decision record against what the market actually settled at.

Phase 2 of the autonomy plan (docs/decisions.md#16). Nothing here places, cancels
or proposes anything. It answers one question, in writing, every session:

    if the standing authorization had been wired to the scheduled review,
    what would it have placed — and was the price it decided on real?

That second half is the point. The review reads prices from a table written by a
scheduled refresh and re-checked by nothing, and within one week that table held
a figure frozen three days by a failing refresh AND an intraday quote stored as a
settled close (MSFT 509.71 against 513.53 on 2026-08-28). Confirm-before-place
survives both because a person reads the number. Auto-place would not. So before
anything is switched on, the record has to show the inputs were sound — not
because it sounds prudent, but because both faults are from the same fortnight.

Deliberately NOT a second implementation of the signal rules. It reads the
decisions the review already wrote and compares them with settled closes from the
observatory database. Re-deriving the signals here would create exactly the
dual-implementation drift this repo has been bitten by before, and a checker that
can disagree with the thing it checks proves nothing about either.

Python 3.9 compatible: the iMac runs system python with no venv.
`from __future__ import annotations` makes every annotation a lazy string, so
PEP 604 `X | Y` syntax below never actually executes on 3.9.

usage: shadow-check.py [--date YYYY-MM-DD] [--repo PATH] [--db PATH] [--quiet]
exit 0 always — this is a reporting path, never a gate.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

DEFAULT_DB = Path.home() / (
    "workspace/data/stock-management/outputs/stock-portfolio-observatory"
    "/stock-portfolio-observatory.db"
)
# What counts as the same price.
#
# The review prices from the broker's last regular trade; this table stores the
# consolidated settled close. Those are two definitions of "the close" and they
# differ by a few cents as a matter of course — MSFT on 2026-09-01 was 501.15
# against 501.02, and a flat $0.02 tolerance called that a mismatch. Left alone it
# would have put a finding in the ledger nearly every day, and a check that cries
# wolf is one nobody reads, which is the failure this whole line of work keeps
# hitting.
#
# Relative, because the gap scales with price. 0.15% clears the vendor difference
# (0.026% on that MSFT pair) by roughly six times, and the fault this exists to
# catch — the 08-28 intraday quote stored as a close — was 0.74% out, roughly five
# times the other way. The absolute floor keeps a cheap symbol from tripping on
# rounding alone.
TOLERANCE_PCT = 0.0015
TOLERANCE_MIN = 0.05


def tolerance_for(price: float) -> float:
    return max(TOLERANCE_MIN, abs(float(price)) * TOLERANCE_PCT)


def settled_close(db_path: str, symbol: str, price_date: str) -> float | None:
    """The settled US close for a symbol on a date, or None if we have no row."""
    if not Path(db_path).exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select close from historical_prices"
            " where market='US' and ticker=? and price_date=?",
            (symbol, price_date),
        ).fetchone()
    finally:
        con.close()
    return None if row is None else float(row[0])


def check(record: dict[str, Any], db_path: str) -> list[dict[str, Any]]:
    """Compare each decision's price against the settled close for the date it used."""
    findings: list[dict[str, Any]] = []
    price_date = record.get("priceAsOf")
    for d in record.get("decisions") or []:
        symbol = d.get("symbol")
        used = d.get("priceUsed")
        if not symbol or used is None or not price_date:
            findings.append({"symbol": symbol, "verdict": "incomplete",
                             "detail": "record is missing symbol, priceUsed or priceAsOf"})
            continue
        actual = settled_close(db_path, symbol, price_date)
        if actual is None:
            findings.append({"symbol": symbol, "verdict": "no-settled-row",
                             "detail": f"no US close stored for {symbol} on {price_date}"})
            continue
        delta = float(used) - actual
        findings.append({
            "symbol": symbol,
            "verdict": "ok" if abs(delta) <= tolerance_for(actual) else "price-mismatch",
            "priceUsed": float(used), "settledClose": actual, "delta": round(delta, 4),
            "autoEligible": bool(d.get("autoEligible")),
        })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="review date to check (default: the newest record)")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--quiet", action="store_true", help="print nothing when everything checks out")
    args = ap.parse_args()

    reviews = Path(args.repo) / "logs" / "reviews"
    path: Path | None
    if args.date:
        path = reviews / (f"{args.date}.decisions.json")
    else:
        found = sorted(reviews.glob("*.decisions.json"))
        path = found[-1] if found else None

    if path is None or not path.exists():
        # Not an error. Until the review has run once with the record step, there
        # is nothing to check, and saying so loudly every session teaches nobody.
        if not args.quiet:
            print("shadow-check: no decision record yet%s" %
                  ("" if not args.date else f" for {args.date}"))
        return 0

    record = json.loads(path.read_text())
    findings = check(record, args.db)
    auto = [d for d in (record.get("decisions") or []) if d.get("autoEligible")]
    bad = [f for f in findings if f["verdict"] not in ("ok",)]

    ledger = Path(args.repo) / "logs" / "shadow" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as fh:
        fh.write(json.dumps({
            "checkedAt": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "date": record.get("date"),
            "targetSession": record.get("targetSession"),
            "priceAsOf": record.get("priceAsOf"),
            "autoEligibleCount": len(auto),
            # Carried so the ledger can answer "which threshold keeps being read
            # past, in which direction, and why" from a record instead of from
            # memory. A threshold departed from repeatedly and consistently is set
            # to the wrong number; one never departed from is working.
            "departures": record.get("departures") or [],
            "autoEligible": [{"side": d.get("side"), "symbol": d.get("symbol"),
                              "notional": d.get("notional")} for d in auto],
            "findings": findings,
        }, ensure_ascii=False) + "\n")

    # Departures are recorded, not alarmed on: they are the expected output of a
    # judgement call, and printing them every session would bury the price
    # mismatches this check exists for. Read them from the ledger.
    if args.quiet and not bad:
        return 0

    departures = record.get("departures") or []
    print(
        f"shadow-check {record.get('date')} (would execute {record.get('targetSession')}): "
        f"{len(auto)} auto-eligible, {len(findings)} checked, {len(departures)} departure(s)"
    )
    for d in departures:
        print("  read past: {} {} ({}) -> {} — {}".format(
            d.get("symbol"), d.get("threshold"), d.get("observed"), d.get("did"), d.get("why", "")))
    for d in auto:
        print("  would place: {} {} ${} — {}".format(
            d.get("side"), d.get("symbol"), d.get("notional"), d.get("reason", "")))
    for f in bad:
        detail = f.get("detail") or (
            "used {}, settled {} (delta {})".format(
                f.get("priceUsed"), f.get("settledClose"), f.get("delta")
            )
        )
        print("  ! {} {}: {}".format(f.get("symbol"), f["verdict"], detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
