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
    "workspace/data/financial-records/outputs/stock-portfolio-observatory"
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


# How long an unverified session is carried before it is closed as unverifiable.
#
# A decision checked the morning after can find no settled close yet: the refresh
# that stores it runs on its own clock. The check used to run once per record, and
# latest.txt is written only when something is wrong, so a session that could not
# be verified said so once and then left the message the next day — the reader,
# seeing the warning stop, took it as resolved. On 2026-09-23 the whole 09-22
# session was "no-settled-row", and nothing ever said whether it later checked
# out. Carrying it means a later run re-checks it and says which way it went.
# Ten days covers a long weekend plus a refresh outage of a week; past that the
# row is not coming and saying so once is the honest end.
MAX_CARRY_DAYS = 10


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


# Human-facing rendering. Every line this script prints reaches the trader's
# daily message (digest_lines.py pastes latest.txt verbatim), and those messages
# are in Korean. The verdict values themselves stay English: scheduled_orders.py
# and the ledger read them, so only the printed label is translated.
VERDICT_LABEL = {
    "price-mismatch": "가격 불일치",
    "no-settled-row": "확정 종가 없음",
    "incomplete": "결정 기록 불완전",
}
EARLIER_HEADER = "이전 거래일 재점검:"
SIDE_LABEL = {"buy": "매수", "sell": "매도", "trim": "일부 매도"}


def kdate(value: Any) -> str:
    """'2026-11-23' -> '11월 23일'; anything unparseable is printed as it came."""
    try:
        d = datetime.date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{d.month}월 {d.day}일"


def num(value: Any) -> str:
    """A price without float noise: 349.0799865722656 -> 349.08, 0.1234 kept."""
    if isinstance(value, (int, float)):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def finding_line(f: dict[str, Any], price_date: Any, origin: str | None = None) -> str:
    """One '!' line for a finding that is not ok."""
    verdict = f.get("verdict")
    label = VERDICT_LABEL.get(verdict, verdict)
    where = f" ({kdate(origin)} 기록)" if origin else ""
    if verdict == "no-settled-row":
        detail = f"{kdate(price_date)} 종가가 아직 저장되지 않음"
    elif verdict == "incomplete":
        detail = "symbol, priceUsed 또는 priceAsOf 누락"
    elif "priceUsed" in f:
        detail = "사용 가격 {}, 확정 종가 {} (차이 {})".format(
            num(f.get("priceUsed")), num(f.get("settledClose")), num(f.get("delta")))
    else:
        detail = f.get("detail") or ""
    return f"  ! {f.get('symbol')}{where} {label}: {detail}"


def read_ledger(ledger: Path) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    out = []
    for line in ledger.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def unverified_dates(entries: list[dict[str, Any]]) -> list[str]:
    """Review dates whose most recent ledger entry still has a no-settled-row and
    has not been closed as unverifiable. Latest entry wins, so a recheck that
    resolved a date takes it off the list."""
    latest: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.get("date"):
            latest[e["date"]] = e
    return sorted(
        d for d, e in latest.items()
        if not e.get("abandoned")
        and any(f.get("verdict") == "no-settled-row" for f in e.get("findings") or [])
    )


def ledger_entry(record: dict[str, Any], findings: list[dict[str, Any]],
                 **extra: Any) -> dict[str, Any]:
    auto = [d for d in (record.get("decisions") or []) if d.get("autoEligible")]
    entry = {
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
    }
    entry.update(extra)
    return entry


def recheck(reviews: Path, entries: list[dict[str, Any]], db_path: str,
            skip: str | None, today: datetime.date) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-check every carried unverified session. Returns new ledger entries and
    the lines to print — one per carried session, every run, until it closes."""
    new_entries: list[dict[str, Any]] = []
    lines: list[str] = []
    for date in unverified_dates(entries):
        if date == skip:
            continue  # the newest record is checked by the main path this run
        path = reviews / f"{date}.decisions.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        findings = check(record, db_path)
        missing = [f["symbol"] for f in findings if f["verdict"] == "no-settled-row"]
        mismatched = [f for f in findings if f["verdict"] not in ("ok", "no-settled-row")]
        try:
            age = (today - datetime.date.fromisoformat(date)).days
        except ValueError:
            age = 0
        abandoned = bool(missing) and age > MAX_CARRY_DAYS
        new_entries.append(ledger_entry(record, findings, recheck=True,
                                        **({"abandoned": True} if abandoned else {})))
        if mismatched:
            for f in mismatched:
                lines.append(finding_line(f, record.get("priceAsOf"), origin=date))
        if abandoned:
            lines.append(f"  x {kdate(date)}: 끝내 검증 못 함 — {age}일이 지나도록 "
                         f"{', '.join(missing)} 확정 종가 없음, 더 이상 추적하지 않음")
        elif missing:
            lines.append(f"  ~ {kdate(date)}: 아직 미검증 — {', '.join(missing)} "
                         f"확정 종가 아직 없음 ({age}일 경과)")
        elif not mismatched:
            lines.append(f"  = {kdate(date)}: 이제 검증됨 — 가격 {len(findings)}건 "
                         "확정 종가와 일치")
    return new_entries, lines


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
        # The newest record dated BEFORE today. A same-day record cannot have a
        # settled close yet, so checking it can only say "no-settled-row" — on
        # 2026-10-07 two manual runs before the 13:30 one left a same-day record,
        # and the scheduled check reported all three of its prices as unverified.
        today = datetime.date.today().isoformat()
        found = sorted(p for p in reviews.glob("*.decisions.json")
                       if p.name[:10] < today)
        path = found[-1] if found else None

    if path is None or not path.exists():
        # Not an error. Until the review has run once with the record step, there
        # is nothing to check, and saying so loudly every session teaches nobody.
        if not args.quiet:
            print("%s결정 기록이 아직 없습니다" %
                  ("" if not args.date else f"{kdate(args.date)} "))
        return 0

    record = json.loads(path.read_text())
    findings = check(record, args.db)
    auto = [d for d in (record.get("decisions") or []) if d.get("autoEligible")]
    bad = [f for f in findings if f["verdict"] not in ("ok",)]

    ledger = Path(args.repo) / "logs" / "shadow" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    prior = read_ledger(ledger)
    carried, carried_lines = recheck(reviews, prior, args.db, record.get("date"),
                                     datetime.date.today())
    with ledger.open("a") as fh:
        for e in carried:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        fh.write(json.dumps(ledger_entry(record, findings), ensure_ascii=False) + "\n")

    # Departures are recorded, not alarmed on: they are the expected output of a
    # judgement call, and printing them every session would bury the price
    # mismatches this check exists for. Read them from the ledger.
    if args.quiet and not bad and not carried_lines:
        return 0
    if args.quiet and not bad:
        print(EARLIER_HEADER)
        print("\n".join(carried_lines))
        return 0

    departures = record.get("departures") or []
    # threshold / observed / did / why and reason are model-written text from
    # the review; they are placed as they come, never translated here.
    print(
        f"{kdate(record.get('date'))} 결정 기록 ({kdate(record.get('targetSession'))} 실행 예정): "
        f"자동 실행 대상 {len(auto)}건, 점검 {len(findings)}건, "
        f"기준을 넘어 판단 {len(departures)}건"
    )
    for d in departures:
        print("  기준을 넘어 판단: {} {} ({}) → {} — {}".format(
            d.get("symbol"), d.get("threshold"), d.get("observed"), d.get("did"), d.get("why", "")))
    for d in auto:
        side = d.get("side")
        print("  실행 예정: {} {} ${} — {}".format(
            d.get("symbol"), SIDE_LABEL.get(str(side).lower(), side), d.get("notional"),
            d.get("reason", "")))
    for f in bad:
        print(finding_line(f, record.get("priceAsOf")))
    if carried_lines:
        print(EARLIER_HEADER)
        print("\n".join(carried_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
