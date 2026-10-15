#!/usr/bin/env python3
"""Analyst ratings, price targets and earnings surprises as plain facts, per ticker.

For each ticker: buy/hold/sell counts, low/mean/high price target, the regular-session
last price, the mean target vs that price in percent, the last N reported quarters as
beat/miss/inline against the estimate, the next report date, and a few fundamentals
fields. Every value carries the timestamp of the source it came from.

What this script does NOT do, by design: score, rank by outlook, or say buy, sell or
hold. It sorts only by keys the owner names, from the fixed list in SORT_KEYS; any
other key is a usage error. A composite score would be a verdict with the arithmetic
hidden, and the tax desk and analyst-facts skill report facts, not advice.

Reads through mcp-robinhood-proxy.py --read-only (nothing here can place an order):
get_equity_analyst_ratings, get_equity_quotes, get_earnings_results,
get_equity_fundamentals.

usage: analyst_facts.py TICKER [TICKER ...] [--quarters N] [--sort KEY[:asc|desc],...]
                        [--format json|md] [--python PY] [--proxy PATH]
Exit 2 on a usage error (an unknown sort key included), 1 when the proxy cannot be
reached, 0 otherwise.
Python 3.9 compatible.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# datetime.UTC is 3.11+; the ops host runs 3.9.
UTC = datetime.timezone.utc  # noqa: UP017

sys.path.insert(0, str(Path(__file__).resolve().parent))
from signal_windows import Proxy  # noqa: E402

# The only keys a table may be sorted by. Each is one fact as reported, or one plain
# ratio of two reported facts; none combines several into a score.
SORT_KEYS = (
    "ticker",
    "last_price",
    "mean_target",
    "low_target",
    "high_target",
    "target_vs_price_pct",
    "buy_count",
    "hold_count",
    "sell_count",
    "analyst_count",
    "beats",
    "misses",
    "next_report_date",
    "pe_ratio",
    "market_cap",
    "dividend_yield",
)
FUNDAMENTAL_FIELDS = ("pe_ratio", "market_cap", "dividend_yield", "high_52_weeks",
                      "low_52_weeks", "market_date")
DEFAULT_QUARTERS = 4


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def parse_sort(spec: str | None) -> list[tuple[str, bool]]:
    """[(key, descending)] from "key[:asc|desc],..."; raises ValueError on anything else."""
    if not spec:
        return []
    out = []
    for part in spec.split(","):
        key, _, direction = part.strip().partition(":")
        if key not in SORT_KEYS:
            raise ValueError(f"unknown sort key {key!r}; allowed: {', '.join(SORT_KEYS)}")
        if direction not in ("", "asc", "desc"):
            raise ValueError(f"sort direction must be asc or desc, not {direction!r}")
        out.append((key, direction == "desc"))
    return out


def sort_rows(rows: list[dict[str, Any]], keys: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    """Stable multi-key sort; a missing value sorts last in either direction."""
    out = list(rows)
    for key, desc in reversed(keys):
        present = [r for r in out if r.get(key) is not None]
        missing = [r for r in out if r.get(key) is None]
        out = sorted(present, key=lambda r: r[key], reverse=desc) + missing
    return out


def earnings_facts(rows: list[dict[str, Any]], quarters: int) -> dict[str, Any]:
    reported = [r for r in rows if (r.get("eps") or {}).get("actual") is not None]
    upcoming = [r for r in rows if (r.get("eps") or {}).get("actual") is None
                and (r.get("report") or {}).get("date")]
    last = []
    for r in reported[-quarters:]:
        est, act = _num(r["eps"].get("estimate")), _num(r["eps"].get("actual"))
        outcome = None
        if est is not None and act is not None:
            outcome = "beat" if act > est else "miss" if act < est else "inline"
        last.append({"fiscal": f"{r.get('year')}Q{r.get('quarter')}",
                     "reportDate": (r.get("report") or {}).get("date"),
                     "epsEstimate": est, "epsActual": act, "outcome": outcome})
    nxt = upcoming[0] if upcoming else None
    return {
        "quarters": last,
        "beats": sum(1 for q in last if q["outcome"] == "beat"),
        "misses": sum(1 for q in last if q["outcome"] == "miss"),
        "inline": sum(1 for q in last if q["outcome"] == "inline"),
        "nextReport": None if nxt is None else {
            "date": nxt["report"]["date"], "timing": nxt["report"].get("timing"),
            "verified": bool(nxt["report"].get("verified"))},
    }


def build_row(ticker: str, rating: dict[str, Any] | None, quote: dict[str, Any] | None,
              earnings: list[dict[str, Any]] | None, fundamentals: dict[str, Any] | None,
              quarters: int) -> dict[str, Any]:
    r = rating or {}
    q = quote or {}
    price = _num(q.get("last_trade_price"))
    mean = _num(r.get("mean_price_target"))
    counts = [r.get("num_buy_ratings"), r.get("num_hold_ratings"), r.get("num_sell_ratings")]
    e = earnings_facts(earnings or [], quarters) if earnings is not None else None
    f = fundamentals or {}
    return {
        "ticker": ticker,
        "coverage": "ok" if rating else "no analyst coverage",
        "buy_count": counts[0],
        "hold_count": counts[1],
        "sell_count": counts[2],
        "analyst_count": sum(c for c in counts if c is not None) if rating else None,
        "low_target": _num(r.get("low_price_target")),
        "mean_target": mean,
        "high_target": _num(r.get("high_price_target")),
        "last_price": price,
        "target_vs_price_pct": (round((mean - price) / price * 100, 2)
                                if mean is not None and price else None),
        "beats": None if e is None else e["beats"],
        "misses": None if e is None else e["misses"],
        "next_report_date": None if e is None or e["nextReport"] is None
        else e["nextReport"]["date"],
        "earnings": e,
        "pe_ratio": _num(f.get("pe_ratio")),
        "market_cap": _num(f.get("market_cap")),
        "dividend_yield": _num(f.get("dividend_yield")),
        "fundamentals": {k: f.get(k) for k in FUNDAMENTAL_FIELDS} if fundamentals else None,
        "sources": {
            "ratingsUpdatedAt": r.get("updated_at"),
            "priceAt": q.get("venue_last_trade_time"),
            "priceBasis": "regular-session last trade",
            "fundamentalsMarketDate": f.get("market_date"),
            "earnings": "get_earnings_results" if e is not None else "unavailable",
        },
    }


def collect(proxy: Any, tickers: list[str], quarters: int) -> list[dict[str, Any]]:
    ratings: dict[str, Any] = {}
    data = proxy.call("get_equity_analyst_ratings", {"symbols": tickers})["data"]
    # Positional per the tool's own guide; a result that names its symbol is trusted
    # over its position.
    for sym, res in zip(tickers, data.get("results") or []):
        ratings[str((res or {}).get("symbol") or sym).upper()] = (res or {}).get("ratings")
    quotes: dict[str, Any] = {}
    for i in range(0, len(tickers), 20):
        qd = proxy.call("get_equity_quotes", {"symbols": tickers[i:i + 20]})["data"]
        for res in qd.get("results") or []:
            q = res.get("quote") or {}
            if q.get("symbol"):
                quotes[q["symbol"]] = q
    fundamentals: dict[str, Any] = {}
    for i in range(0, len(tickers), 10):
        try:
            fd = proxy.call("get_equity_fundamentals", {"symbols": tickers[i:i + 10]})["data"]
        except Exception:  # noqa: BLE001 — optional fields; the row says they are missing
            continue
        for res in fd.get("results") or []:
            if res.get("symbol"):
                fundamentals[res["symbol"]] = res
    rows = []
    for sym in tickers:
        try:
            earn = proxy.call("get_earnings_results", {"symbol": sym})["data"].get("results") or []
        except Exception:  # noqa: BLE001 — recorded as unavailable on the row
            earn = None
        rows.append(build_row(sym, ratings.get(sym), quotes.get(sym), earn,
                              fundamentals.get(sym), quarters))
    return rows


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def render_md(doc: dict[str, Any]) -> str:
    cols = ("ticker", "buy_count", "hold_count", "sell_count", "low_target", "mean_target",
            "high_target", "last_price", "target_vs_price_pct", "beats", "misses",
            "next_report_date")
    lines = [f"Analyst facts for {len(doc['rows'])} ticker(s)"
             + (f", sorted by {doc['sortedBy']}" if doc["sortedBy"] else ", in the order given"),
             "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in doc["rows"]:
        lines.append("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    lines += ["", f"Beats/misses: last {doc['quarters']} reported quarters, actual EPS vs "
              "estimate. Target vs price: mean target against the regular-session last trade."]
    for r in doc["rows"]:
        s = r["sources"]
        lines.append(f"- {r['ticker']}: ratings as of {s['ratingsUpdatedAt'] or 'n/a'}; "
                     f"price at {s['priceAt'] or 'n/a'}; earnings {s['earnings']}"
                     + ("" if r["coverage"] == "ok" else f"; {r['coverage']}"))
    lines.append(f"Source: Robinhood (read-only), fetched {doc['generatedAt']}. "
                 "Facts only; no rating or recommendation is implied.")
    return "\n".join(lines) + "\n"


def run(tickers: list[str], quarters: int, sort: list[tuple[str, bool]],
        proxy_factory: Callable[[], Any]) -> dict[str, Any]:
    proxy = proxy_factory()
    try:
        rows = collect(proxy, tickers, quarters)
    finally:
        proxy.close()
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now(UTC).isoformat(timespec="seconds"),
        "quarters": quarters,
        "sortedBy": ",".join(f"{k}:{'desc' if d else 'asc'}" for k, d in sort) or None,
        "rows": sort_rows(rows, sort),
    }


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--quarters", type=int, default=DEFAULT_QUARTERS)
    ap.add_argument("--sort", help="comma-separated KEY[:asc|desc]; keys: " + ", ".join(SORT_KEYS))
    ap.add_argument("--format", choices=("json", "md"), default="md")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--proxy", default=str(here / "mcp-robinhood-proxy.py"))
    args = ap.parse_args(argv)
    try:
        sort = parse_sort(args.sort)
    except ValueError as e:
        ap.error(str(e))
    if not 1 <= args.quarters <= 8:
        ap.error("--quarters must be between 1 and 8 (the tool returns up to 8)")
    tickers = list(dict.fromkeys(t.strip().upper() for t in args.tickers if t.strip()))
    if len(tickers) > 75:
        ap.error("at most 75 tickers per run")
    try:
        doc = run(tickers, args.quarters, sort, lambda: Proxy(args.python, args.proxy))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"analyst_facts: {e}\n")
        return 1
    print(json.dumps(doc, indent=1) if args.format == "json" else render_md(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
