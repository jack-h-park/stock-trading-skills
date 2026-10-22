#!/usr/bin/env python3
"""Assemble the two review digests from the day's records, so their fixed lines are fixed.

The digest model used to write every line of both messages from a format spelled out in
the prompt, and the format did not hold: across three consecutive sessions the sleeve
value lost its thousands separator, one message ran its reconcile items together into a
paragraph with no blank lines, the TRIM and take-profit lines swapped places, and the
same departure was worded three different ways. Each of those lines is a copy of a
number already in a file, so here they are rendered from the file instead.

What is still written by a model is only what needs judgement: the briefing note (which
items matter and whether a deadline has passed) and the head of the reference message
(the cross-account total, whose source is the overview prose). The digest step writes
those as two fragments; this script places them.

Inputs, all under logs/ and all optional — a missing one turns its section into a line
saying so, never into silence:
  reviews/<date>.md               last 'Sleeve:' line (job A)
  reviews/<date>.decisions.json   proposals, blocks, departures, decision requests (job A)
  reviews/<date>.signals.json     drawdown per symbol (code)
  reviews/<date>.standing.json    how long each proposal has stood (code)
  reviews/<date>.tax.json         lot terms and the fills that count toward slots (code)
  reconcile/<date>.json           sheet drift and the trade-log check (job B)
  shadow/latest.txt               shadow-check lines (code)
  digest/<date>.briefing.md       briefing note (digest model)
  digest/<date>.reference-head.md US-accounts head of the reference message (digest model)

Outputs: digest/<date>.agentic.md (Discord) and digest/<date>.md (Telegram).

usage: digest_lines.py --repo PATH --date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# config/guardrails.md: max orders per day and per week (Mon-Sun).
DAILY_ORDER_CAP = 5
WEEKLY_ORDER_CAP = 6

SLEEVE = re.compile(
    r"Sleeve: \$(?P<value>[\d,]+(?:\.\d+)?); prior close \$[\d,]+(?:\.\d+)?; "
    r"day (?P<dsign>[+\-−]?)\$(?P<change>[\d,]+(?:\.\d+)?) \((?P<psign>[+\-−]?)(?P<pct>[\d.]+)%\)"
)

ACCOUNT_ORDER = ["Long-term", "Mid-term"]
EXIT_KINDS = ("take-profit", "stop-loss")
CLOSER = "Proposals only; nothing executes until Jack confirms in the interactive session."


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def money(value: float | Decimal | str) -> str:
    return f"${Decimal(str(value)).quantize(Decimal('0.01')):,}"


def dollars(value: float | Decimal | str) -> str:
    """An order notional: '$100', or '$62.50' when it is not whole dollars."""
    d = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"${d:,.0f}" if d == d.to_integral_value() else f"${d:,}"


def qty(value: float | Decimal | str) -> str:
    """A share count as the broker states it: up to 6 decimals, no trailing zeros."""
    d = Decimal(str(value)).quantize(Decimal("0.000001"))
    s = f"{d:f}".rstrip("0").rstrip(".")
    return s or "0"


def signed(value: Decimal, unit: str = "") -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}{unit}{abs(value)}"


def long_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def short_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def sentence(text: object) -> str:
    """The text as one sentence: its own ? or ! kept, otherwise exactly one full stop."""
    t = str(text or "").strip().rstrip(".")
    return t if t.endswith(("?", "!")) else f"{t}."


def sleeve_line(review: str) -> str | None:
    """'Sleeve **$X**, day ±$Y (±Z%)' from the review's LAST 'Sleeve:' line.

    The last one, because a manual run earlier the same day appends its own section
    computed on prices that were not the close.
    """
    matches = list(SLEEVE.finditer(review))
    if not matches:
        return None
    m = matches[-1]
    dsign = "−" if m["dsign"] in ("-", "−") else "+"
    psign = "−" if m["psign"] in ("-", "−") else "+"
    value = money(m["value"].replace(",", ""))
    change = money(m["change"].replace(",", ""))
    return f"Sleeve **{value}**, day {dsign}{change} ({psign}{m['pct']}%)"


def slots_clause(target: str | None, tax: dict[str, Any] | None) -> str:
    """Order slots for the session the proposals would execute in, not the one that closed.

    Counts Agentic buy fills from the tax file's recentBuys, which covers every buy —
    REDISTRIBUTE included — in the last 30 days; the week runs Monday to Sunday.
    """
    if not target:
        return "slots unavailable (no target session in the decision record)"
    day = dt.date.fromisoformat(target)
    weekday = f"{day:%A}"
    if tax is None:
        return f"slots for {weekday} unavailable (no fill data today)"
    monday = day - dt.timedelta(days=day.weekday())
    week = today = 0
    for buys in (tax.get("recentBuys") or {}).values():
        for b in buys:
            if b.get("account") != "Agentic" or not b.get("date"):
                continue
            filled = dt.date.fromisoformat(b["date"][:10])
            if monday <= filled <= day:
                week += 1
            if filled == day:
                today += 1
    left_today = max(0, min(DAILY_ORDER_CAP - today, WEEKLY_ORDER_CAP - week))
    left_week = max(0, WEEKLY_ORDER_CAP - week)
    return (f"slots for {weekday}: {left_today} of {DAILY_ORDER_CAP} today, "
            f"{left_week} of {WEEKLY_ORDER_CAP} that week")


def standing_text(entry: dict[str, Any] | None) -> str:
    if not entry or entry.get("sessions", 1) <= 1:
        return "new"
    since = short_date(entry["since"])
    if entry.get("atLeast"):
        return f"standing since at least {since}, {entry['sessions']}+ sessions"
    return f"standing since {since}, {entry['sessions']} sessions"


def term_text(lot: dict[str, Any] | None) -> str | None:
    if not lot:
        return None
    term = lot.get("term")
    if term == "short-term" and lot.get("oldestLongTermFrom"):
        text = f"short-term until {long_date(lot['oldestLongTermFrom'])}"
    elif term == "long-term":
        text = "long-term"
    elif term == "mixed":
        text = "mixed lots"
    else:
        return None
    if lot.get("turnsLongTermSoon"):
        text += f"; oldest lot turns long-term in {lot.get('daysToLongTerm')} days"
    return text


def drawdown(signals: dict[str, Any] | None, symbol: str) -> str:
    sym = ((signals or {}).get("symbols") or {}).get(symbol) or {}
    pct = sym.get("drawdownPct")
    return f"{pct:.2f}% below 20d high" if isinstance(pct, (int, float)) else "drawdown n/a"


def proposal_lines(rec: dict[str, Any], signals: dict[str, Any] | None,
                   standing: dict[str, Any] | None, tax: dict[str, Any] | None) -> list[str]:
    decisions = rec.get("decisions") or []
    lots = (tax or {}).get("lots") or {}
    buys = [d for d in decisions if d.get("kind") == "buy"]
    redistribute = [d for d in decisions if d.get("kind") == "redistribute"]
    trims = [d for d in decisions if d.get("kind") == "trim"]
    exits = [d for d in decisions if d.get("kind") in EXIT_KINDS]

    out: list[str] = []
    for d in buys:
        out.append(f"• BUY {dollars(d['notional'])} **{d['symbol']}** — "
                   f"{drawdown(signals, d['symbol'])}")
    for d in trims:
        dest = ", ".join(f"**{r['symbol']}** {dollars(r['notional'])}" for r in redistribute)
        tail = f"REDISTRIBUTE → {dest}" if dest else "proceeds held as cash"
        out.append(f"• TRIM **{d['symbol']}** **{qty(d['shares'])}sh** (~{money(d['notional'])})"
                   f" — {drawdown(signals, d['symbol'])}; {tail}")
    for d in exits:
        label = "Take-profit" if d["kind"] == "take-profit" else "Stop-loss"
        key = f"{d['symbol']}:{d['kind']}"
        line = (f"• {label} **{d['symbol']}** **{qty(d['shares'])}sh** (~{money(d['notional'])})"
                f" — {standing_text((standing or {}).get(key))}")
        term = term_text(lots.get(d["symbol"]))
        out.append(f"{line}; {term}" if term else line)
    if not out:
        out.append("No proposals today.")

    out += blocked_lines(rec.get("blocked") or [])
    none = [name for name, present in (("TRIM", trims), ("stop-loss",
            [d for d in exits if d["kind"] == "stop-loss"])) if not present]
    if none:
        out.append("No " + " or ".join(none) + " today.")
    return out


def blocked_lines(blocked: list[dict[str, Any]]) -> list[str]:
    """One line per (rule, figure), naming every symbol it blocked and the number behind it.

    The figure is the record's value up to its first ';' — 'committed $200.00 of $200' —
    because what follows is the model restating the arithmetic, worded differently daily.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for b in blocked:
        figure = str(b.get("value") or "n/a").split(";")[0].strip()
        groups.setdefault((str(b.get("rule") or "blocked"), figure), []).append(b["symbol"])
    out = []
    for (rule, figure), symbols in groups.items():
        names = ", ".join(f"**{s}**" for s in symbols)
        each = "each " if len(symbols) > 1 else ""
        out.append(f"Blocked ({rule}): {names} — {each}{figure}.")
    return out


def decision_section(rec: dict[str, Any]) -> list[str]:
    reqs = rec.get("decisionRequests") or []
    if not reqs:
        return []
    out = ["Decision needed:"]
    for r in reqs:
        readings = "; ".join(f"{x.get('side')}: {x.get('detail')}" for x in r.get("readings") or [])
        out.append(f"• **{r['symbol']}** — {sentence(r.get('question'))} {sentence(readings)}")
    out.append("Nothing on these symbols executes either way until Jack rules; "
               "the answer is recorded and not asked again.")
    return out


def departure_section(rec: dict[str, Any]) -> list[str]:
    deps = rec.get("departures") or []
    if not deps:
        return []
    out = ["Read past a threshold:"]
    for d in deps:
        out.append(f"• **{d['symbol']}** — {d.get('threshold')}; observed "
                   f"{sentence(d.get('observed'))} Did: {sentence(d.get('did'))} "
                   f"Why: {sentence(d.get('why'))}")
    out.append("Not auto-eligible; Jack confirms either way.")
    return out


def reconcile_entry(a: dict[str, Any]) -> str:
    sym, kind = a["symbol"], a.get("kind", "drift")
    if kind == "add":
        return f"add {sym} {qty(a['liveQty'])} @ {money(a['liveAvg'])}"
    if kind == "remove":
        return f"remove {sym} (−{qty(a['sheetQty'])} sh)"
    delta = Decimal(str(a["liveQty"])) - Decimal(str(a["sheetQty"]))
    shares = signed(Decimal(qty(abs(delta))) * (1 if delta >= 0 else -1)) + " sh"
    if kind == "verify":
        return f"verify {sym} {qty(a['liveQty'])} ({shares})"
    text = f"{sym} → {qty(a['liveQty'])} @ {money(a['liveAvg'])} ({shares}"
    avg = (Decimal(str(a["liveAvg"])) - Decimal(str(a["sheetAvg"]))).quantize(Decimal("0.01"))
    if avg != 0:
        text += f", avg {signed(avg, '$')}"
    return text + ")"


def reconcile_section(recon: dict[str, Any] | None, recon_md: bool) -> list[str]:
    if recon is None:
        if recon_md:
            return ["Reconcile: structured result missing today; read logs/reconcile/ directly."]
        return ["Reconcile: did not run today."]
    alerts = recon.get("alerts") or []
    if not alerts:
        return []
    by_account: dict[str, list[str]] = {}
    for a in sorted(alerts, key=lambda a: a["symbol"]):
        by_account.setdefault(a.get("account", "Unknown"), []).append(reconcile_entry(a))
    order = ACCOUNT_ORDER + sorted(k for k in by_account if k not in ACCOUNT_ORDER)
    out = ["Reconcile:",
           "Outside Agentic. The sheet tab is regenerated from the Observatory's Robinhood "
           "snapshot, so refresh the snapshot rather than editing rows; nothing to place."]
    out += [f"{acct}: " + "; ".join(by_account[acct]) + "." for acct in order if acct in by_account]
    return out


def unlogged_section(recon: dict[str, Any] | None) -> list[str]:
    findings = ((recon or {}).get("tradeLog") or {}).get("findings") or []
    if not findings:
        return []
    out = ["Unlogged trades:"]
    for f in findings:
        out.append(f"• {short_date(f['date'])} {str(f.get('side', '')).upper()} **{f['symbol']}** "
                   f"{qty(f['shares'])}sh @ {money(f['price'])}")
    out.append("Write each missing entry in logs/trades/ per skills/log/SKILL.md, reconstructing "
               "why the order was placed; nothing to place or cancel.")
    return out


def agentic_message(logs: Path, date: str) -> str:
    reviews = logs / "reviews"
    rec = _load(reviews / f"{date}.decisions.json")
    signals = _load(reviews / f"{date}.signals.json")
    standing = _load(reviews / f"{date}.standing.json")
    tax = _load(reviews / f"{date}.tax.json")
    recon = _load(logs / "reconcile" / f"{date}.json")

    sleeve = sleeve_line(_text(reviews / f"{date}.md"))
    target = (rec or {}).get("targetSession")
    head = sleeve or "Sleeve unavailable (no 'Sleeve:' line in today's review)"
    opening = ["**Hermes Trader — Agentic**", f"{head}; {slots_clause(target, tax)}."]
    blocks: list[list[str]] = [opening]

    if rec is None:
        blocks.append(["No decision record today, so no proposals can be listed; "
                       "read logs/reviews/ directly."])
    else:
        blocks += [b for b in (decision_section(rec), departure_section(rec)) if b]
        blocks.append(proposal_lines(rec, signals, standing, tax))
    for section in (unlogged_section(recon),
                    reconcile_section(recon, (logs / "reconcile" / f"{date}.md").exists())):
        if section:
            blocks.append(section)
    shadow = _text(logs / "shadow" / "latest.txt")
    if shadow:
        blocks.append(["Shadow check:", shadow])
    briefing = _text(logs / "digest" / f"{date}.briefing.md")
    if briefing:
        blocks.append([briefing])
    blocks.append([CLOSER])
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


def reference_message(logs: Path, date: str) -> str:
    head = _text(logs / "digest" / f"{date}.reference-head.md")
    blocks = [head or "US accounts: overview unavailable in today's digest — read-only context; "
                      "see logs/reviews/ for the full review."]
    recon = _load(logs / "reconcile" / f"{date}.json")
    if recon is not None:
        lines = []
        if not recon.get("alerts"):
            lines.append("Reconcile: the sheet matches Robinhood.")
        trade_log = recon.get("tradeLog") or {}
        if not trade_log.get("findings"):
            n = trade_log.get("filledInWindow", 0)
            lines.append("Trade log: no filled Agentic orders in the check window."
                         if not n else f"Trade log: all {n} filled Agentic orders logged.")
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    logs = args.repo / "logs"
    digest = logs / "digest"
    digest.mkdir(parents=True, exist_ok=True)
    rc = 0
    for name, build in ((f"{args.date}.agentic.md", agentic_message),
                        (f"{args.date}.md", reference_message)):
        try:
            text = build(logs, args.date)
        except (KeyError, TypeError, AttributeError, InvalidOperation, ValueError) as exc:
            # A record with a shape this script did not expect must not cost the day's
            # message: say what happened and where the full review is.
            print(f"digest_lines: {name}: {exc!r}", file=sys.stderr)
            text = (f"⚠️ The {args.date} digest could not be assembled ({type(exc).__name__}); "
                    f"the full review is in logs/reviews/{args.date}.md.\n")
            rc = 1
        (digest / name).write_text(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
