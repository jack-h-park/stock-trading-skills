#!/usr/bin/env python3
"""Robinhood sell fills vs the Observatory's realized rows: matched, broker-only, mismatched.

The two records of one sale are never current at the same moment. Robinhood knows a
fill the second it happens; the Observatory learns of it only after the owner
downloads a statement and a refresh ingests it. So a fill that the broker has and the
Observatory lacks is the EXPECTED state for a few days after a sale, and this report
says so instead of calling it an error. The opposite — an Observatory row with no
broker fill, or the same sale with a different quantity — is a real discrepancy.

Inputs, both read-only:
  - Robinhood, through mcp-robinhood-proxy.py --read-only: filled equity orders
    (get_equity_orders) and per-trade realized P&L (get_pnl_trade_history), for every
    role in config/accounts.local.md;
  - `pnpm tax:realized --since D --json` in the Observatory (US securities only).

Matching is by (account, ticker, trade date, quantity). The account is joined by
role: each Robinhood role maps to an Observatory account label through the optional
"Observatory label" column of config/accounts.local.md (see accounts.example.md).
Both sides are summed per (account, ticker, date) first, because the broker splits
one sale across partial fills and the Observatory splits it across lots. The trade
date of a Robinhood fill is its US/Eastern date; a pair one day apart still matches
and is marked with the offset.

Gains are shown side by side, never used to decide a match: the broker's realized
figure and the Observatory's replayed or 1099-B figure differ by construction
(wash-sale adjustments, basis method).

usage: fill_reconcile.py --since YYYY-MM-DD [--until YYYY-MM-DD] [--repo PATH]
                         [--python PY] [--proxy PATH] [--lag-days N] [--format json|md]
Exit 0 whenever a report was produced (sources that failed are listed as
unavailable); exit 2 on a usage error.
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
import observatory_cli  # noqa: E402
import tax_guard as tg  # noqa: E402
from account_roles import observatory_labels, read_local  # noqa: E402
from signal_windows import Proxy  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    ET: Any = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 — tzdata missing: fall back to the UTC date
    ET = UTC

QTY_TOLERANCE = 1e-4
DEFAULT_LAG_DAYS = 7


def et_date(ts: str) -> str:
    """US/Eastern trade date of an ISO timestamp; a bare date passes through."""
    s = str(ts or "")
    if len(s) <= 10:
        return s[:10]
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET).date().isoformat()


def pnl_span(since: str, today: datetime.date) -> str:
    """The narrowest preset span of get_pnl_trade_history that reaches back to `since`."""
    age = (today - datetime.date.fromisoformat(since)).days
    if age <= 7:
        return "week"
    if age <= 30:
        return "month"
    if age <= 90:
        return "3month"
    if since >= f"{today.year}-01-01":
        return "ytd"
    return "all"


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def collect_broker(proxy: Any, accts: dict[str, str], since: str, until: str,
                   today: datetime.date) -> list[dict[str, Any]]:
    """Filled SELL orders per role, with the broker's realized gain attached where the
    P&L history has the same (ticker, date)."""
    fills: list[dict[str, Any]] = []
    for role, number in accts.items():
        gains: dict[tuple[str, str], float] = {}
        for t in tg._pages(proxy, "get_pnl_trade_history",
                           {"account_number": number, "span": pnl_span(since, today)}, "trades"):
            if t.get("side") != "sell" or not t.get("symbol"):
                continue
            key = (t["symbol"], et_date(t.get("timestamp", "")))
            gains[key] = gains.get(key, 0.0) + (_num(t.get("realized_gain")) or 0.0)
        for o in tg._pages(proxy, "get_equity_orders",
                           {"account_number": number, "state": "filled",
                            "created_at_gte": since}, "orders"):
            if o.get("side") != "sell" or not o.get("symbol"):
                continue
            day = et_date(o.get("last_transaction_at") or o.get("created_at") or "")
            if not (since <= day <= until):
                continue
            qty = _num(o.get("cumulative_quantity")) or _num(o.get("filled_quantity")) \
                or _num(o.get("quantity")) or 0.0
            fills.append({"role": role, "ticker": o["symbol"], "date": day, "quantity": qty,
                          "orderId": o.get("id"),
                          "brokerGainUsd": gains.get((o["symbol"], day))})
    return fills


def _aggregate(rows: list[dict[str, Any]], gain_key: str) -> dict[tuple[str, str, str], Any]:
    out: dict[tuple[str, str, str], Any] = {}
    for r in rows:
        k = (r["account"], r["ticker"], r["date"])
        a = out.setdefault(k, {"account": r["account"], "ticker": r["ticker"],
                               "date": r["date"], "quantity": 0.0, gain_key: None,
                               "parts": 0})
        a["quantity"] += r["quantity"]
        a["parts"] += 1
        g = r.get(gain_key)
        if g is not None:
            a[gain_key] = (a[gain_key] or 0.0) + g
    return out


def reconcile(fills: list[dict[str, Any]], obs_rows: list[dict[str, Any]],
              labels: dict[str, dict[str, Any]], today: datetime.date,
              lag_days: int = DEFAULT_LAG_DAYS) -> dict[str, Any]:
    """Pure matching. `fills` carry a role; `obs_rows` carry an Observatory label."""
    label_of = {role: str(v["label"]) for role, v in labels.items()}
    role_of = {lbl: role for role, lbl in label_of.items()}

    broker = _aggregate([{**f, "account": f["role"]} for f in fills], "brokerGainUsd")
    in_scope = []
    out_of_scope = 0
    for r in obs_rows:
        role = role_of.get(str(r.get("account")))
        if role is None:
            out_of_scope += 1
            continue
        in_scope.append({"account": role, "ticker": str(r.get("ticker", "")).upper(),
                         "date": str(r.get("soldDate", ""))[:10],
                         "quantity": _num(r.get("quantity")) or 0.0,
                         "obsGainUsd": _num(r.get("gainUsd"))})
    obs = _aggregate(in_scope, "obsGainUsd")

    matched, broker_only, mismatched = [], [], []
    used: set[tuple[str, str, str]] = set()

    def pair(k: tuple[str, str, str], ok: tuple[str, str, str], offset: int) -> None:
        b, o = broker[k], obs[ok]
        row = {"role": k[0], "observatoryLabel": label_of.get(k[0]), "ticker": k[1],
               "brokerDate": k[2], "observatoryDate": ok[2], "dateOffsetDays": offset,
               "brokerQuantity": round(b["quantity"], 6),
               "observatoryQuantity": round(o["quantity"], 6),
               "brokerGainUsd": b["brokerGainUsd"], "observatoryGainUsd": o["obsGainUsd"]}
        used.add(ok)
        if abs(b["quantity"] - o["quantity"]) <= QTY_TOLERANCE:
            matched.append(row)
        else:
            mismatched.append({**row, "issue": "quantity differs"})

    pending = []
    for k in sorted(broker):
        if k in obs:
            pair(k, k, 0)
        else:
            pending.append(k)
    for k in pending:
        role, ticker, day = k
        near = [(role, ticker, (datetime.date.fromisoformat(day)
                                + datetime.timedelta(days=d)).isoformat()) for d in (-1, 1)]
        hit = next((n for n in near if n in obs and n not in used), None)
        if hit is not None:
            pair(k, hit, (datetime.date.fromisoformat(hit[2])
                          - datetime.date.fromisoformat(day)).days)
            continue
        b = broker[k]
        age = (today - datetime.date.fromisoformat(day)).days
        broker_only.append({"role": role, "observatoryLabel": label_of.get(role),
                            "ticker": ticker, "date": day,
                            "quantity": round(b["quantity"], 6),
                            "brokerGainUsd": b["brokerGainUsd"], "ageDays": age,
                            "overdue": age > lag_days})
    for ok in sorted(obs):
        if ok in used:
            continue
        o = obs[ok]
        mismatched.append({"role": ok[0], "observatoryLabel": label_of.get(ok[0]),
                           "ticker": ok[1], "brokerDate": None, "observatoryDate": ok[2],
                           "brokerQuantity": None,
                           "observatoryQuantity": round(o["quantity"], 6),
                           "observatoryGainUsd": o["obsGainUsd"],
                           "issue": "no matching broker fill"})
    return {
        "matched": matched,
        "brokerOnly": broker_only,
        "observatoryOnlyOrMismatch": mismatched,
        "observatoryRowsOutsideRobinhood": out_of_scope,
        "lagDays": lag_days,
        "compared": True,
        "labelsAssumed": sorted(r for r, v in labels.items() if not v["configured"]),
    }


def render_md(doc: dict[str, Any]) -> str:
    r = doc["result"]
    overdue = [b for b in r["brokerOnly"] if b["overdue"]]
    lines = [f"# Fill reconcile {doc['since']}..{doc['until']}", "",
             ("NOT COMPARED — a source was unavailable (see Provenance)" if not r["compared"]
              else f"{len(r['matched'])} matched, {len(r['brokerOnly'])} broker-only "
              f"({len(overdue)} older than {r['lagDays']}d), "
              f"{len(r['observatoryOnlyOrMismatch'])} Observatory-only or mismatched"), ""]
    lines += ["Broker-only is expected for a few days after a sale: the statement has not been "
              "downloaded and refreshed into the Observatory yet. Only the last list is a "
              "discrepancy.", ""]
    for title, key, cols in (
            ("Matched", "matched", ("role", "ticker", "brokerDate", "dateOffsetDays",
                                    "brokerQuantity", "brokerGainUsd", "observatoryGainUsd")),
            ("Broker-only", "brokerOnly", ("role", "ticker", "date", "quantity", "ageDays",
                                           "overdue")),
            ("Observatory-only or mismatched", "observatoryOnlyOrMismatch",
             ("role", "ticker", "brokerDate", "observatoryDate", "brokerQuantity",
              "observatoryQuantity", "issue"))):
        lines += [f"## {title}", ""]
        if not r[key]:
            lines += ["None.", ""]
            continue
        lines += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for row in r[key]:
            lines.append("| " + " | ".join("—" if row.get(c) is None else str(row.get(c))
                                           for c in cols) + " |")
        lines.append("")
    lines += ["## Provenance", ""]
    for name, s in doc["sources"].items():
        lines.append(f"- {name}: {s['status']}" + (f" ({s['reason']})" if s.get("reason") else ""))
    prov = doc["sources"]["observatory"].get("provenance")
    if prov:
        lines.append(f"- observatory DB built {prov.get('dbBuiltAt')} "
                     f"(mtime {prov.get('dbMtime')})")
    if r["labelsAssumed"]:
        lines.append(f"- Observatory label assumed (not configured) for: "
                     f"{', '.join(r['labelsAssumed'])}")
    lines.append(f"- Observatory rows outside the Robinhood roles: "
                 f"{r['observatoryRowsOutsideRobinhood']} (not compared)")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace, today: datetime.date,
        proxy_factory: Callable[[], Any] | None = None,
        observatory: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    local = read_local(Path(args.repo))
    accts = tg.accounts(local)
    labels = observatory_labels(local)
    until = args.until or today.isoformat()

    fills: list[dict[str, Any]] = []
    rh: dict[str, Any]
    if not accts:
        rh = {"status": "unavailable", "reason": "no account found in config/accounts.local.md"}
    else:
        try:
            proxy = (proxy_factory or (lambda: Proxy(args.python, args.proxy)))()
            try:
                fills = collect_broker(proxy, accts, args.since, until, today)
            finally:
                proxy.close()
            rh = {"status": "ok", "reason": None}
        except Exception as e:  # noqa: BLE001 — recorded, never fatal
            rh = {"status": "unavailable", "reason": str(e)[:200]}

    cli_args = ["--since", args.since] + (["--until", args.until] if args.until else [])
    obs = (observatory or (lambda: observatory_cli.run("tax:realized", cli_args)))()
    rows = ((obs.get("data") or {}).get("rows") or []) if obs["status"] == "ok" else []
    # A comparison needs both sides. Against a source that did not answer, every row of
    # the other would read as a discrepancy (or every fill as "lag"), so nothing is
    # compared and the report says which side was missing.
    compared = rh["status"] == "ok" and obs["status"] == "ok"
    result = reconcile(fills if compared else [], rows if compared else [], labels, today,
                       args.lag_days)
    result["compared"] = compared
    return {
        "schemaVersion": 1,
        "since": args.since,
        "until": until,
        "generatedAt": datetime.datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": {"robinhood": rh,
                    "observatory": {"status": obs["status"], "reason": obs.get("reason"),
                                    "provenance": (obs.get("data") or {}).get("provenance")}},
        "result": result,
    }


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--repo", default=str(here.parent))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--proxy", default=str(here / "mcp-robinhood-proxy.py"))
    ap.add_argument("--lag-days", type=int, default=DEFAULT_LAG_DAYS)
    ap.add_argument("--format", choices=("json", "md"), default="md")
    ap.add_argument("--date", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        for d in (args.since, args.until):
            if d:
                datetime.date.fromisoformat(d)
    except ValueError:
        ap.error("--since/--until must be YYYY-MM-DD")
    today = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    doc = run(args, today)
    print(json.dumps(doc, indent=1, sort_keys=True) if args.format == "json" else render_md(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
