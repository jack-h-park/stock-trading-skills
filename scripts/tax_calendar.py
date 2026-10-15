#!/usr/bin/env python3
"""One readable file of US tax-timing constraints: wash-sale windows, the orders that
start or extend them, and earnings dates inside a planned sale's window.

Two producers can each see half of the picture, and this script joins them:

  - Robinhood, live, through mcp-robinhood-proxy.py --read-only (the same server
    tax_guard.py and the daily review use, so nothing here can place an order):
    loss sales and filled buys in the three Robinhood accounts, today's open orders,
    and per-ticker earnings dates. It cannot see any other broker. Earnings come from
    get_earnings_results (one ticker, trailing and upcoming reports) rather than the
    market-wide get_earnings_calendar, whose 31-day limit is shorter than a planned
    sale's 61-day window and which returns every company to filter down to a few.
  - The Observatory's `pnpm tax:calendar --json`: windows derived from the database
    built from every broker's exports, including dividend reinvestments. It cannot
    see today's orders or anything not yet downloaded and refreshed.

Plus the owner's declared intent: planned sales in the gitignored
config/planned-sales.local.json (schema: config/planned-sales.example.json).

Every window records which source reported it and which sources were unavailable,
so a short list is never mistaken for a complete one. A source that fails — the
Observatory exits 3 while a refresh rebuilds its database — is recorded as
unavailable; the run still writes its file.

Writes logs/tax-calendar/<date>.json and <date>.md and prints a short message
(the Telegram text for scripts/hermes/tax-calendar-cron.sh). The JSON carries a
`fingerprint` of the window set that excludes anything that moves on its own
(days left, amounts, timestamps), so the cron speaks only when the set changes.

Interactive sessions read the latest file; they never recompute the windows.

usage: tax_calendar.py [--repo PATH] [--python PY] [--proxy PATH] [--date YYYY-MM-DD]
                       [--out-dir DIR] [--no-robinhood] [--no-observatory]
                       [--print message|json|none]
Python 3.9 compatible.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# datetime.UTC is 3.11+; the ops host runs 3.9.
UTC = datetime.timezone.utc  # noqa: UP017

sys.path.insert(0, str(Path(__file__).resolve().parent))
import observatory_cli  # noqa: E402
import tax_guard as tg  # noqa: E402
from account_roles import read_local  # noqa: E402
from signal_windows import Proxy  # noqa: E402

WINDOW_DAYS = tg.WINDOW_DAYS
OPEN_STATES = {"new", "queued", "confirmed", "unconfirmed", "partially_filled"}
INTENTS = ("loss-harvest", "gain", "other")
SOURCES = ("robinhood", "observatory", "earnings")
FINGERPRINT_EFFECTS = ("open-buy-inside-window", "filled-buy-inside-window")
COVERAGE = {
    "robinhood": "Robinhood Agentic, Long-term and Mid-term accounts, live",
    "observatory": "every broker in the Observatory database, as of its last refresh "
                   "(see observatory.coverage for what it excludes)",
    "earnings": "Robinhood earnings results per planned-sale ticker",
}

D = datetime.date.fromisoformat


def _plus(day: str, n: int) -> str:
    return (D(day) + datetime.timedelta(days=n)).isoformat()


# --- inputs -------------------------------------------------------------------------

def load_planned_sales(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """(valid entries, problems). An absent file is no plans, not an error."""
    if not path.is_file():
        return [], []
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return [], [f"{path.name}: unreadable ({e})"]
    if not isinstance(raw, list):
        return [], [f"{path.name}: expected a JSON list"]
    ok: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, entry in enumerate(raw):
        where = f"{path.name}[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: not an object")
            continue
        ticker = str(entry.get("ticker") or "").strip().upper()
        intent = entry.get("intent")
        try:
            planned = D(str(entry.get("planned_date"))).isoformat()
        except ValueError:
            problems.append(f"{where}: planned_date is not YYYY-MM-DD")
            continue
        if not ticker:
            problems.append(f"{where}: ticker missing")
            continue
        if intent not in INTENTS:
            problems.append(f"{where}: intent must be one of {', '.join(INTENTS)}")
            continue
        ok.append({"ticker": ticker, "account_role": str(entry.get("account_role") or ""),
                   "planned_date": planned, "intent": intent, "note": str(entry.get("note") or "")})
    return ok, problems


def _order_date(o: dict[str, Any]) -> str:
    return str(o.get("last_transaction_at") or o.get("created_at") or "")[:10]


def collect_robinhood(proxy: Any, accts: dict[str, str], today: datetime.date,
                      tickers_for_earnings: list[str]) -> dict[str, Any]:
    """Raw Robinhood facts. Raises when the proxy itself fails; per-ticker earnings
    failures are recorded instead, because one bad symbol should not hide the rest."""
    since = (today - datetime.timedelta(days=WINDOW_DAYS + 1)).isoformat()
    loss_sales: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    for role, number in accts.items():
        for t in tg._pages(proxy, "get_pnl_trade_history",
                           {"account_number": number, "span": "3month"}, "trades"):
            gain = float(t.get("realized_gain") or 0)
            if t.get("side") == "sell" and gain < 0 and t.get("symbol"):
                loss_sales.append({"symbol": t["symbol"], "account": role,
                                   "date": str(t.get("timestamp", ""))[:10],
                                   "loss": round(gain, 2)})
        for o in tg._pages(proxy, "get_equity_orders",
                           {"account_number": number, "created_at_gte": since}, "orders"):
            if not o.get("symbol") or o.get("side") not in ("buy", "sell"):
                continue
            state = str(o.get("state") or "")
            if state != "filled" and state not in OPEN_STATES:
                continue
            orders.append({"symbol": o["symbol"], "account": role, "side": o["side"],
                           "state": state, "date": _order_date(o)})
    earnings: dict[str, Any] = {}
    for sym in sorted(set(tickers_for_earnings)):
        try:
            rows = proxy.call("get_earnings_results", {"symbol": sym})["data"].get("results") or []
            earnings[sym] = {"status": "ok", "reports": [
                {"date": (r.get("report") or {}).get("date"),
                 "timing": (r.get("report") or {}).get("timing"),
                 "verified": bool((r.get("report") or {}).get("verified"))}
                for r in rows if (r.get("report") or {}).get("date")]}
        except Exception as e:  # noqa: BLE001 — recorded per ticker
            earnings[sym] = {"status": "unavailable", "reason": str(e), "reports": []}
    return {"lossSales": loss_sales, "orders": orders, "earnings": earnings}


# --- merge (pure) -------------------------------------------------------------------

def merge_windows(rh: dict[str, Any] | None, obs: dict[str, Any] | None,
                  today: datetime.date) -> list[dict[str, Any]]:
    """Active wash-sale windows, one per ticker, each saying which source reported it.

    A window runs from a loss sale through day 30 after it; a buy of the same security
    inside it (in any account) defers the loss. Robinhood's and the Observatory's
    records of one sale can differ by a day (UTC timestamp vs statement date), so the
    merge is per ticker, keeping every sale with its source, and the window runs to
    the latest `blockedThrough`."""
    by: dict[str, dict[str, Any]] = {}

    def slot(ticker: str) -> dict[str, Any]:
        return by.setdefault(ticker, {"ticker": ticker, "sales": [], "reportedBy": set(),
                                      "dripActive": False, "boughtSinceSale": False})

    for s in (rh or {}).get("lossSales", []):
        through = _plus(s["date"], WINDOW_DAYS)
        if through < today.isoformat() or s["date"] > today.isoformat():
            continue
        w = slot(s["symbol"])
        w["sales"].append({"date": s["date"], "blockedThrough": through,
                           "accounts": [s["account"]], "lossUsd": s.get("loss"),
                           "source": "robinhood"})
        w["reportedBy"].add("robinhood")
    for o in (obs or {}).get("washSaleWindows", []) or []:
        through = str(o.get("blockedThrough") or _plus(str(o["soldDate"]), WINDOW_DAYS))
        if through < today.isoformat():
            continue
        w = slot(str(o["ticker"]).upper())
        w["sales"].append({"date": str(o["soldDate"]), "blockedThrough": through,
                           "accounts": list(o.get("accounts") or []),
                           "lossUsd": o.get("realizedLossUsd"), "source": "observatory"})
        w["reportedBy"].add("observatory")
        w["dripActive"] = w["dripActive"] or bool(o.get("dripActive"))
        w["boughtSinceSale"] = w["boughtSinceSale"] or bool(o.get("boughtSinceSale"))

    out = []
    for ticker in sorted(by):
        w = by[ticker]
        sales = sorted(w["sales"], key=lambda s: (s["date"], s["source"]))
        through = max(s["blockedThrough"] for s in sales)
        out.append({
            "ticker": ticker,
            "firstSale": sales[0]["date"],
            "lastSale": sales[-1]["date"],
            "blockedThrough": through,
            "daysLeft": (D(through) - today).days,
            "sales": sales,
            "reportedBy": sorted(w["reportedBy"]),
            "dripActive": w["dripActive"],
            "boughtSinceSale": w["boughtSinceSale"],
        })
    return out


def order_events(rh: dict[str, Any] | None, windows: list[dict[str, Any]],
                 today: datetime.date) -> list[dict[str, Any]]:
    """Orders that start or extend a window, each with its effect in plain words."""
    if not rh:
        return []
    by_ticker = {w["ticker"]: w for w in windows}
    floor = (today - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    events = []
    for o in sorted(rh.get("orders", []), key=lambda o: (o["date"], o["symbol"], o["side"])):
        w = by_ticker.get(o["symbol"])
        # The rule reaches 30 days either side of the loss sale, so a buy shortly
        # BEFORE the sale defers it as surely as one after.
        inside = w is not None and _plus(w["firstSale"], -WINDOW_DAYS) \
            <= (o["date"] or today.isoformat()) <= w["blockedThrough"]
        ev = {"ticker": o["symbol"], "account": o["account"], "side": o["side"],
              "state": o["state"], "date": o["date"]}
        if o["state"] in OPEN_STATES:
            if o["side"] == "buy" and w is not None:
                ev.update(effect="open-buy-inside-window", until=w["blockedThrough"],
                          note="if it fills, the loss in the open window is deferred")
            elif o["side"] == "buy":
                ev.update(effect="open-buy", until=None,
                          note="once filled, a loss sale within 30 days of the fill is deferred")
            else:
                ev.update(effect="open-sell", until=None,
                          note="starts a 30-day window if it fills at a loss")
        elif o["date"] >= floor and o["side"] == "buy":
            if inside and w is not None:
                ev.update(effect="filled-buy-inside-window", until=w["blockedThrough"],
                          note="a buy within 30 days of a loss sale: that loss is deferred")
            else:
                ev.update(effect="recent-buy", until=_plus(o["date"], WINDOW_DAYS),
                          note="a loss sale before this date is deferred")
        else:
            continue
        events.append(ev)
    return events


def planned_sale_checks(plans: list[dict[str, Any]], windows: list[dict[str, Any]],
                        events: list[dict[str, Any]], obs: dict[str, Any] | None,
                        rh: dict[str, Any] | None, today: datetime.date) -> list[dict[str, Any]]:
    """Each planned sale with its window and what falls inside it.

    Conflicts (the things that would defer a harvested loss) apply only to
    `loss-harvest`; earnings dates and an already-open window are listed for every
    intent, as facts."""
    by_ticker = {w["ticker"]: w for w in windows}
    drip = {str(d.get("ticker", "")).upper(): d for d in (obs or {}).get("drip", []) or []}
    earnings = (rh or {}).get("earnings", {})
    out = []
    for p in plans:
        t, day = p["ticker"], p["planned_date"]
        start, end = _plus(day, -WINDOW_DAYS), _plus(day, WINDOW_DAYS)
        conflicts: list[dict[str, Any]] = []
        notes: list[str] = []
        if day < today.isoformat():
            notes.append("planned date has passed")
        w = by_ticker.get(t)
        if w is not None:
            notes.append(f"an open wash-sale window on {t} runs through {w['blockedThrough']}")
        if p["intent"] == "loss-harvest":
            for ev in events:
                if ev["ticker"] != t or ev["side"] != "buy":
                    continue
                if ev["state"] in OPEN_STATES or start <= ev["date"] <= end:
                    conflicts.append({"kind": "buy-in-window", "date": ev["date"],
                                      "account": ev["account"], "state": ev["state"],
                                      "source": "robinhood"})
            d = drip.get(t)
            if d is not None:
                conflicts.append({"kind": "drip-active",
                                  "date": d.get("lastReinvestDate"),
                                  "account": ", ".join(d.get("accounts") or []),
                                  "state": "reinvesting", "source": "observatory"})
        e = earnings.get(t)
        if e is None:
            earn: dict[str, Any] = {"status": "not-checked", "dates": []}
        else:
            earn = {"status": e["status"], "dates": [
                r for r in e["reports"] if r["date"] and start <= r["date"] <= end]}
            if e["status"] != "ok":
                earn["reason"] = e.get("reason")
        out.append({**p, "windowStart": start, "windowEnd": end, "conflicts": conflicts,
                    "earningsInWindow": earn, "notes": notes})
    return out


def fingerprint(doc: dict[str, Any]) -> str:
    """Identity of the window set: stable while nothing changes, no timestamps or amounts.

    `clean` when there is nothing to report and every source answered, so the cron can
    say "back to clean" once and then stay quiet."""
    key = {
        "windows": [[w["ticker"], w["lastSale"], w["blockedThrough"], w["reportedBy"],
                     w["dripActive"]] for w in doc["windows"]],
        # Only the orders that touch an open window. Routine buys age in and out of
        # the 30-day lookback every day; they matter only against a planned loss
        # sale, and there they are already counted as that plan's conflict.
        "orders": [[e["ticker"], e["side"], e["effect"], e["date"]]
                   for e in doc["orderEvents"] if e["effect"] in FINGERPRINT_EFFECTS],
        "planned": [[p["ticker"], p["planned_date"], p["intent"],
                     sorted(c["kind"] for c in p["conflicts"]),
                     [r["date"] for r in p["earningsInWindow"]["dates"]]]
                    for p in doc["plannedSales"]],
        "plannedProblems": doc["plannedSalesProblems"],
        "unavailable": sorted(k for k, v in doc["sources"].items()
                              if v["status"] == "unavailable"),
    }
    if not any(key.values()):
        return "clean"
    blob = json.dumps(key, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build(today: datetime.date, rh: dict[str, Any] | None, rh_source: dict[str, Any],
          obs_source: dict[str, Any], plans: list[dict[str, Any]],
          plan_problems: list[str]) -> dict[str, Any]:
    obs = obs_source.get("data") if obs_source["status"] == "ok" else None
    windows = merge_windows(rh, obs, today)
    events = order_events(rh, windows, today)
    planned = planned_sale_checks(plans, windows, events, obs, rh, today)
    earn_status = "not-needed" if not plans else (
        "unavailable" if rh is None else
        ("partial" if any(e["status"] != "ok" for e in rh["earnings"].values()) else "ok"))
    sources = {
        "robinhood": {"status": rh_source["status"], "reason": rh_source.get("reason"),
                      "covers": COVERAGE["robinhood"]},
        "observatory": {"status": obs_source["status"], "reason": obs_source.get("reason"),
                        "covers": COVERAGE["observatory"],
                        "coverage": (obs or {}).get("coverage"),
                        "provenance": (obs or {}).get("provenance"),
                        "settlement": (obs or {}).get("settlement")},
        "earnings": {"status": "unavailable" if earn_status == "unavailable" else "ok",
                     "detail": earn_status, "covers": COVERAGE["earnings"]},
    }
    doc = {
        "schemaVersion": 1,
        "asOf": today.isoformat(),
        "generatedAt": datetime.datetime.now(UTC).isoformat(timespec="seconds"),
        "windowDays": WINDOW_DAYS,
        "sources": sources,
        "windows": windows,
        "orderEvents": events,
        "plannedSales": planned,
        "plannedSalesProblems": plan_problems,
        "disclaimer": "Timing facts under the stated sources; not tax advice.",
    }
    doc["fingerprint"] = fingerprint(doc)
    return doc


# --- rendering ----------------------------------------------------------------------

def _source_line(doc: dict[str, Any]) -> str:
    parts = []
    for name in SOURCES:
        s = doc["sources"][name]
        if name == "earnings" and s.get("detail") == "not-needed":
            continue
        parts.append(f"{name} {s['status']}" + (f" ({s['reason']})" if s.get("reason") else ""))
    return "Sources: " + " · ".join(parts)


def headline(doc: dict[str, Any]) -> str:
    n_conf = sum(1 for p in doc["plannedSales"] if p["conflicts"])
    unavailable = [k for k, v in doc["sources"].items() if v["status"] == "unavailable"]
    touching = sum(1 for e in doc["orderEvents"] if e["effect"] in FINGERPRINT_EFFECTS)
    head = (f"Tax calendar {doc['asOf']}: {len(doc['windows'])} wash-sale window(s) open, "
            f"{touching} buy(s) inside a window, "
            f"{n_conf} planned-sale conflict(s)")
    if unavailable:
        head += f" — PARTIAL, {', '.join(unavailable)} unavailable"
    return head


def render_message(doc: dict[str, Any], md_path: str) -> str:
    lines = [headline(doc)]
    for w in doc["windows"]:
        lines.append(f"- {w['ticker']} blocked through {w['blockedThrough']} "
                     f"({w['daysLeft']}d) · last loss sale {w['lastSale']} · reported by "
                     f"{', '.join(w['reportedBy'])}" + (" · DRIP on" if w["dripActive"] else ""))
    for e in doc["orderEvents"]:
        if e["effect"] in ("open-buy-inside-window", "filled-buy-inside-window", "open-sell"):
            lines.append(f"- {e['ticker']} {e['side']} ({e['account']}, {e['state']}): {e['note']}")
    for p in doc["plannedSales"]:
        bits = [c["kind"] for c in p["conflicts"]]
        bits += [f"earnings {r['date']}" + ("" if r["verified"] else " (tentative)")
                 for r in p["earningsInWindow"]["dates"]]
        bits += p["notes"]
        lines.append(f"- planned {p['intent']} {p['ticker']} on {p['planned_date']}: "
                     + ("; ".join(bits) if bits else "nothing in its window"))
    for prob in doc["plannedSalesProblems"]:
        lines.append(f"- planned-sales file: {prob}")
    lines.append(_source_line(doc))
    lines.append(f"Full table: {md_path}")
    return "\n".join(lines)


def render_md(doc: dict[str, Any]) -> str:
    out = [f"# Tax calendar — {doc['asOf']}", "", headline(doc), ""]
    out += ["## Wash-sale windows", ""]
    if doc["windows"]:
        out += ["| Ticker | Last loss sale | Blocked through | Days left | Reported by | DRIP on "
                "| Bought since sale (Observatory) |", "|---|---|---|---|---|---|---|"]
        for w in doc["windows"]:
            out.append(f"| {w['ticker']} | {w['lastSale']} | {w['blockedThrough']} | "
                       f"{w['daysLeft']} | {', '.join(w['reportedBy'])} | "
                       f"{'yes' if w['dripActive'] else 'no'} | "
                       f"{'yes' if w['boughtSinceSale'] else 'no'} |")
        out += ["", "Sales behind each window:", ""]
        for w in doc["windows"]:
            for s in w["sales"]:
                loss = "" if s["lossUsd"] is None else f", loss {s['lossUsd']} USD"
                accts = ", ".join(s["accounts"]) or "account n/a"
                out.append(f"- {w['ticker']} {s['date']} ({accts}"
                           f"{loss}) — {s['source']}")
    else:
        out.append("None open.")
    out += ["", "## Orders that start or extend a window", ""]
    if doc["orderEvents"]:
        out += ["| Ticker | Side | Account | State | Date | Effect | Until |",
                "|---|---|---|---|---|---|---|"]
        for e in doc["orderEvents"]:
            out.append(f"| {e['ticker']} | {e['side']} | {e['account']} | {e['state']} | "
                       f"{e['date']} | {e['note']} | {e['until'] or '—'} |")
    else:
        out.append("None.")
    out += ["", "## Planned sales", ""]
    if doc["plannedSales"]:
        out += ["| Ticker | Account | Date | Intent | Window | Conflicts | Earnings in window "
                "| Notes |", "|---|---|---|---|---|---|---|---|"]
        for p in doc["plannedSales"]:
            conf = ", ".join(f"{c['kind']} {c['date'] or ''}".strip() for c in p["conflicts"])
            ew = p["earningsInWindow"]
            earn = ", ".join(r["date"] + ("" if r["verified"] else "?") for r in ew["dates"]) \
                if ew["status"] == "ok" else ew["status"]
            out.append(f"| {p['ticker']} | {p['account_role'] or '—'} | {p['planned_date']} | "
                       f"{p['intent']} | {p['windowStart']}..{p['windowEnd']} | "
                       f"{conf or 'none'} | {earn or 'none'} | {'; '.join(p['notes']) or '—'} |")
    else:
        out.append("None declared (config/planned-sales.local.json).")
    for prob in doc["plannedSalesProblems"]:
        out.append(f"- problem: {prob}")
    out += ["", "## Provenance", "", _source_line(doc)]
    for name in SOURCES:
        out.append(f"- {name}: covers {doc['sources'][name]['covers']}")
    prov = doc["sources"]["observatory"].get("provenance")
    if prov:
        out.append(f"- observatory DB built {prov.get('dbBuiltAt')} (mtime {prov.get('dbMtime')}), "
                   f"generated by tax:calendar")
    cov = doc["sources"]["observatory"].get("coverage")
    if cov:
        excludes = ", ".join(cov.get("excludes") or []) or "nothing stated"
        out.append(f"- observatory excludes: {excludes}")
    out += [f"- fingerprint {doc['fingerprint']}", "", f"_{doc['disclaimer']}_", ""]
    return "\n".join(out)


# --- main ---------------------------------------------------------------------------

def run(args: argparse.Namespace, today: datetime.date,
        proxy_factory: Callable[[], Any] | None = None,
        observatory: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    repo = Path(args.repo)
    plans, problems = load_planned_sales(repo / "config" / "planned-sales.local.json")

    rh: dict[str, Any] | None = None
    rh_source: dict[str, Any]
    if args.no_robinhood:
        rh_source = {"status": "unavailable", "reason": "skipped (--no-robinhood)"}
    else:
        accts = tg.accounts(read_local(repo))
        if not accts:
            rh_source = {"status": "unavailable",
                         "reason": "no account found in config/accounts.local.md"}
        else:
            try:
                proxy = (proxy_factory or (lambda: Proxy(args.python, args.proxy)))()
                try:
                    rh = collect_robinhood(proxy, accts, today, [p["ticker"] for p in plans])
                finally:
                    proxy.close()
                rh_source = {"status": "ok", "reason": None}
            except Exception as e:  # noqa: BLE001 — recorded, never fatal
                rh, rh_source = None, {"status": "unavailable", "reason": str(e)[:200]}

    if args.no_observatory:
        obs_source: dict[str, Any] = {"status": "unavailable",
                                      "reason": "skipped (--no-observatory)", "data": None}
    else:
        obs_source = (observatory or (lambda: observatory_cli.run(
            "tax:calendar", ["--as-of", today.isoformat()])))()
    return build(today, rh, rh_source, obs_source, plans, problems)


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", default=str(here.parent))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--proxy", default=str(here / "mcp-robinhood-proxy.py"))
    ap.add_argument("--date")
    ap.add_argument("--out-dir")
    ap.add_argument("--no-robinhood", action="store_true")
    ap.add_argument("--no-observatory", action="store_true")
    ap.add_argument("--print", dest="show", choices=("message", "json", "none"),
                    default="message")
    args = ap.parse_args(argv)
    today = D(args.date) if args.date else datetime.date.today()
    doc = run(args, today)

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.repo) / "logs" / "tax-calendar"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{doc['asOf']}.json"
    md_path = out_dir / f"{doc['asOf']}.md"
    json_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    md_path.write_text(render_md(doc))
    if args.show == "message":
        print(render_message(doc, str(md_path)))
    elif args.show == "json":
        print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
