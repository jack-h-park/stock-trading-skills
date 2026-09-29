#!/usr/bin/env python3
"""Compute each universe symbol's 20-day high and drawdown in code, for job A.

Why: the review used to ask the model to "compute the trailing 20-trading-day high"
from the bars it fetched, and the model counted the window differently on
consecutive days. At 13:30 PT the day's own daily bar is not published yet, so the
model has the previous sessions' bars plus a live quote. On 2026-09-28 it took 19
prior bars and counted today as the 20th (window 08-31..09-28, AMZN 6.85% below
264.36); on 09-29 it took the 20 prior bars (08-31..09-28, AMZN 6.68%). The 08-28
high of 267.56 fell out on the 28th a day early, so AMZN read 6.85% where the
09-29 rule gives 7.96%. Near the 5% BUY and 10% TRIM lines a one-session drift
flips a signal, and nothing downstream re-derives it.

The definition, fixed here and nowhere else:

    the 20 most recent COMPLETED regular sessions before the session of the price
    used (the regular-session last trade, as PRICE_BASIS requires); the high is the
    highest intraday high among those 20 daily bars.

Before, not including: that is the only form available at run time, when today's
bar does not exist yet, and a bar that does exist later in the day is left out too,
so the answer does not depend on when the job ran.

Robinhood's bars are read through mcp-robinhood-proxy.py --read-only — the same
server, and the same write refusal, that job A uses. Nothing here can place an
order even by mistake, because the proxy refuses every non-read tool.

usage: signal_windows.py --repo PATH --python PY --proxy PATH [--out FILE]
Exit 0 with no file on any failure: job A then computes the window itself under
the same written definition and says so. This is an input, never a gate.

Python 3.9 compatible (the ops host's system python).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

WINDOW = 20
LOOKBACK_DAYS = 45  # calendar days fetched; 20 sessions plus holidays with margin


def universe(policy_text: str) -> list[str]:
    """Symbols listed under '## Universe' in strategy/policy.md ('- SYM — ...')."""
    m = re.search(r"^## Universe\n(.*?)(?=^## )", policy_text, re.S | re.M)
    if not m:
        return []
    return re.findall(r"^- ([A-Z]{1,6}) —", m.group(1), re.M)


def window(bars: list[dict[str, Any]], session: str,
           size: int = WINDOW) -> dict[str, Any] | None:
    """The `size` most recent real bars dated before `session`, and their high."""
    real = sorted(
        (b for b in bars
         if not b.get("interpolated") and str(b.get("begins_at", ""))[:10] < session),
        key=lambda b: b["begins_at"],
    )[-size:]
    if len(real) < size:
        return None
    top = max(real, key=lambda b: float(b["high_price"]))
    return {
        "windowStart": real[0]["begins_at"][:10],
        "windowEnd": real[-1]["begins_at"][:10],
        "sessions": len(real),
        "high": float(top["high_price"]),
        "highDate": top["begins_at"][:10],
    }


def signal(price: float, w: dict[str, Any]) -> dict[str, Any]:
    drawdown = (w["high"] - price) / w["high"] * 100
    return {**w, "price": price, "drawdownPct": round(drawdown, 2)}


def tool_payload(response: dict[str, Any]) -> dict[str, Any]:
    """The JSON a Robinhood tool returned, from a JSON-RPC tools/call response."""
    if "error" in response:
        raise RuntimeError(response["error"].get("message", "tool error"))
    result = response.get("result") or {}
    if result.get("structuredContent"):
        return result["structuredContent"]
    for item in result.get("content") or []:
        if item.get("type") == "text":
            return json.loads(item["text"])
    raise RuntimeError("tool returned no content")


class Proxy:
    """A minimal stdio JSON-RPC client for mcp-robinhood-proxy.py --read-only."""

    def __init__(self, python: str, proxy: str) -> None:
        self.proc = subprocess.Popen(
            [python, proxy, "--read-only"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        self.next_id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "signal-windows", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == self.next_id:
                return msg
        raise RuntimeError(f"proxy closed before answering {method}")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return tool_payload(self.request("tools/call", {"name": name, "arguments": arguments}))

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


def compute(proxy: Proxy, symbols: list[str], today: datetime.date) -> dict[str, Any]:
    quotes = proxy.call("get_equity_quotes", {"symbols": symbols})["data"]["results"]
    start = (today - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat() + "T00:00:00Z"
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(symbols), 10):  # the tool takes up to 10 symbols per call
        chunk = symbols[i:i + 10]
        data = proxy.call("get_equity_historicals", {"symbols": chunk, "start_time": start,
                                                     "interval": "day", "bounds": "regular"})
        for r in data["data"]["results"]:
            bars_by_symbol[r["symbol"]] = r.get("bars") or []

    out: dict[str, Any] = {}
    for q in quotes:
        quote = q["quote"]
        sym = quote["symbol"]
        # PRICE_BASIS: the regular-session last trade, dated by its own venue time.
        price = float(quote["last_trade_price"])
        session = str(quote.get("venue_last_trade_time", ""))[:10]
        w = window(bars_by_symbol.get(sym, []), session)
        out[sym] = {"priceDate": session, **signal(price, w)} if w else {
            "priceDate": session, "price": price, "error": f"fewer than {WINDOW} bars"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--proxy", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    symbols = universe((Path(args.repo) / "strategy" / "policy.md").read_text())
    if not symbols:
        sys.stderr.write("signal_windows: no universe found in strategy/policy.md\n")
        return 0
    try:
        proxy = Proxy(args.python, args.proxy)
        try:
            result = compute(proxy, symbols, datetime.date.today())
        finally:
            proxy.close()
    except Exception as e:  # noqa: BLE001 — an input, never a gate
        sys.stderr.write(f"signal_windows: {e} — job A computes the window itself\n")
        return 0
    doc = {
        "definition": (f"the {WINDOW} most recent completed regular sessions before the "
                       "session of the price used; high = highest intraday high among those bars"),
        "symbols": result,
    }
    text = json.dumps(doc, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
