"""The 20-day window is counted in code, the same way every day.

Fixtures are AMZN's real regular-session daily highs. At 13:30 PT today's daily bar
does not exist yet; on 2026-09-28 the model counted today as the 20th session
(08-31..09-28, 6.85%) and on 09-29 it used the 20 prior sessions (08-31..09-28,
6.68%). The rule is the second: 20 completed sessions before the price's session.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "signal_windows.py"
spec = importlib.util.spec_from_file_location("signal_windows", SCRIPT)
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)  # type: ignore[union-attr]

AMZN_HIGHS = [
    ("2026-08-28", 267.56), ("2026-08-31", 264.36), ("2026-09-01", 255.82),
    ("2026-09-02", 256.24), ("2026-09-03", 259.50), ("2026-09-04", 261.12),
    ("2026-09-08", 257.99), ("2026-09-09", 254.69), ("2026-09-10", 253.15),
    ("2026-09-11", 257.59), ("2026-09-14", 255.95), ("2026-09-15", 253.27),
    ("2026-09-16", 249.26), ("2026-09-17", 252.73), ("2026-09-18", 255.43),
    ("2026-09-21", 259.49), ("2026-09-22", 259.00), ("2026-09-23", 253.99),
    ("2026-09-24", 250.43), ("2026-09-25", 250.8775), ("2026-09-28", 250.00),
    ("2026-09-29", 248.10),
]


def _bars(upto: str, interpolated_extra: bool = False):
    bars = [{"begins_at": d + "T00:00:00Z", "high_price": str(h)}
            for d, h in AMZN_HIGHS if d <= upto]
    if interpolated_extra:  # a gap-fill bar carries a stale high and must not count
        bars.append({"begins_at": "2026-09-26T00:00:00Z", "high_price": "999",
                     "interpolated": True})
    return bars


def test_the_window_is_the_twenty_completed_sessions_before_the_price_session():
    w = sw.window(_bars("2026-09-28"), "2026-09-29")
    assert (w["windowStart"], w["windowEnd"], w["sessions"]) == ("2026-08-31", "2026-09-28", 20)
    assert (w["high"], w["highDate"]) == (264.36, "2026-08-31")
    assert sw.signal(246.695, w)["drawdownPct"] == 6.68


def test_the_day_before_counts_the_same_way():
    # 09-28's job had bars through 09-25 (plus a gap-fill bar that must not count).
    w = sw.window(_bars("2026-09-25", interpolated_extra=True), "2026-09-28")
    assert (w["windowStart"], w["windowEnd"]) == ("2026-08-28", "2026-09-25")
    assert w["high"] == 267.56
    assert sw.signal(246.26, w)["drawdownPct"] == 7.96


def test_the_answer_does_not_depend_on_whether_todays_bar_exists_yet():
    early = sw.window(_bars("2026-09-28"), "2026-09-29")
    late = sw.window(_bars("2026-09-29"), "2026-09-29")
    assert early == late
    assert sw.window(_bars("2026-09-29")[:5], "2026-09-29") is None


def test_the_universe_is_read_from_the_policy():
    assert sw.universe((ROOT / "strategy" / "policy.md").read_text()) == [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "VOO", "QQQM"]


def test_a_tool_error_is_raised_not_read_as_data():
    ok = {"result": {"content": [{"type": "text", "text": '{"data": 1}'}]}}
    assert sw.tool_payload(ok) == {"data": 1}
    try:
        sw.tool_payload({"error": {"message": "refused"}})
    except RuntimeError as e:
        assert "refused" in str(e)
    else:
        raise AssertionError("an error response was read as data")


def test_end_to_end_through_a_proxy_that_must_be_read_only(tmp_path):
    bars = _bars("2026-09-28")
    fake = tmp_path / "proxy.py"
    fake.write_text(textwrap.dedent(f"""
        import json, sys
        assert "--read-only" in sys.argv[1:], "signal_windows must use the read-only proxy"
        BARS = {json.dumps(bars)}
        for line in sys.stdin:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            if msg["method"] == "initialize":
                result = {{"protocolVersion": "2025-06-18"}}
            else:
                name = msg["params"]["name"]
                if name == "get_equity_quotes":
                    data = {{"data": {{"results": [{{"quote": {{"symbol": "AMZN",
                        "last_trade_price": "246.695",
                        "venue_last_trade_time": "2026-09-29T19:59:59Z"}}}}]}}}}
                else:
                    data = {{"data": {{"results": [{{"symbol": "AMZN", "bars": BARS}}]}}}}
                result = {{"content": [{{"type": "text", "text": json.dumps(data)}}]}}
            reply = {{"jsonrpc": "2.0", "id": msg["id"], "result": result}}
            sys.stdout.write(json.dumps(reply) + "\\n")
            sys.stdout.flush()
    """))
    proxy = sw.Proxy(sys.executable, str(fake))
    try:
        out = sw.compute(proxy, ["AMZN"], __import__("datetime").date(2026, 9, 29))
    finally:
        proxy.close()
    assert out["AMZN"]["drawdownPct"] == 6.68
    assert out["AMZN"]["windowStart"] == "2026-08-31"
    assert out["AMZN"]["priceDate"] == "2026-09-29"
