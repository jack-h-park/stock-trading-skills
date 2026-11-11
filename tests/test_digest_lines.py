"""digest_lines.py: both digests rendered from the day's records, the same way every day."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("digest_lines", ROOT / "scripts" / "digest_lines.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)  # type: ignore[union-attr]

DATE = "2026-10-21"


def _logs(tmp_path: Path, **files: object) -> Path:
    """Write logs/<relative path> -> content (dict/list as JSON, str as text)."""
    logs = tmp_path / "logs"
    for rel, content in files.items():
        path = logs / rel.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    return logs


DECISIONS = {
    "date": DATE, "targetSession": "2026-10-22", "priceAsOf": DATE,
    "decisions": [
        {"side": "buy", "symbol": "QQQM", "kind": "buy", "shares": None, "notional": 100},
        {"side": "sell", "symbol": "NVDA", "kind": "trim", "shares": 0.495877,
         "notional": 107.6512},
        {"side": "sell", "symbol": "MSFT", "kind": "take-profit", "shares": 0.236215,
         "notional": 125.85771415},
        {"side": "sell", "symbol": "META", "kind": "take-profit", "shares": 0.174662,
         "notional": 121.98918066},
    ],
    "blocked": [
        {"symbol": "META", "rule": "committed-add cap",
         "value": "committed $200.00 of $200; proposed $100 add would make $300.00"},
        {"symbol": "GOOGL", "rule": "committed-add cap",
         "value": "committed $200.00 of $200; adding $100 would commit $300.00"},
    ],
    "departures": [], "decisionRequests": [],
}
SIGNALS = {"symbols": {"QQQM": {"drawdownPct": 5.94}, "NVDA": {"drawdownPct": 11.05}}}
STANDING = {"MSFT:take-profit": {"since": "2026-09-01", "sessions": 34, "atLeast": True},
            "META:take-profit": {"since": "2026-09-09", "sessions": 29},
            "NVDA:trim": {"since": "2026-10-22", "sessions": 8}}
TAX = {
    "lots": {"MSFT": {"term": "short-term", "oldestLongTermFrom": "2027-06-24"},
             "META": {"term": "mixed", "turnsLongTermSoon": True, "daysToLongTerm": 12}},
    "recentBuys": {},
}
REVIEW = ("## 13:30\nSleeve: $2153.50; prior close $2,179.25; day -$25.75 (-1.18%)\n"
          "## 13:45\nSleeve: $2166.09; prior close $2159.46; day +$6.63 (+0.31%)\n")


def test_the_sleeve_line_is_formatted_in_code_from_the_last_section():
    line = dl.sleeve_line(REVIEW)
    # The thousands separator the model dropped on 10-21, from the LAST section only.
    assert line == "Sleeve **$2,166.09**, day +$6.63 (+0.31%)"
    assert dl.sleeve_line("Sleeve: $2,153.50; prior close $2,179.25; day −$25.75 (-1.18%)") == \
        "Sleeve **$2,153.50**, day −$25.75 (−1.18%)"
    assert dl.sleeve_line("no sleeve here") is None


def test_the_sleeve_line_survives_a_day_change_without_its_dollar_sign():
    # 2026-11-11: "day +4.03" instead of "day +$4.03" read as no sleeve line at all.
    line = "Sleeve: $2,075.06; prior close $2,071.03; day +4.03 (+0.19%)"
    assert dl.sleeve_line(line) == "Sleeve **$2,075.06**, day +$4.03 (+0.19%)"


def test_slots_count_agentic_buys_in_the_target_week():
    buys = {"recentBuys": {
        "QQQM": [{"account": "Agentic", "date": "2026-10-20"}],
        "AAPL": [{"account": "Long-term", "date": "2026-10-20"}],      # other account
        "NVDA": [{"account": "Agentic", "date": "2026-10-16"}],        # previous week
    }}
    assert dl.slots_clause("2026-10-22", buys) == \
        "slots for Thursday: 5 of 5 today, 5 of 6 that week"
    # A Friday review targets Monday: a fresh week whatever the last one spent.
    assert dl.slots_clause("2026-10-26", buys) == "slots for Monday: 5 of 5 today, 6 of 6 that week"
    assert "unavailable" in dl.slots_clause("2026-10-22", None)
    # A tax file whose Robinhood half failed has no recentBuys: unknown, never zero fills.
    assert "unavailable" in dl.slots_clause("2026-10-22", {"robinhood": {"status": "failed: x"},
                                                           "buyBlocks": {}})


def test_the_daily_count_never_exceeds_what_the_week_has_left():
    buys = {"recentBuys": {f"S{i}": [{"account": "Agentic", "date": "2026-10-19"}]
                           for i in range(4)}}
    assert dl.slots_clause("2026-10-22", buys) == \
        "slots for Thursday: 2 of 5 today, 2 of 6 that week"


def test_the_caps_match_the_guardrails():
    text = (ROOT / "config" / "guardrails.md").read_text()
    assert f"| Max orders per day | **{dl.DAILY_ORDER_CAP}** |" in text
    assert f"| Max orders per week | **{dl.WEEKLY_ORDER_CAP}** (Mon–Sun) |" in text


def test_proposals_render_in_a_fixed_order_with_standing_and_term():
    lines = dl.proposal_lines(DECISIONS, SIGNALS, STANDING, TAX)
    assert lines[:4] == [
        "• BUY $100 **QQQM** — 5.94% below 20d high; new",
        "• TRIM **NVDA** **0.495877sh** (~$107.65) — 11.05% below 20d high; "
        "standing since Oct 22, 8 sessions; proceeds held as cash",
        "• Take-profit **MSFT** **0.236215sh** (~$125.86) — standing since at least Sep 1, "
        "34+ sessions; short-term until Jun 24, 2027",
        "• Take-profit **META** **0.174662sh** (~$121.99) — standing since Sep 9, 29 sessions; "
        "mixed lots; oldest lot turns long-term in 12 days",
    ]
    # Two differently worded values for one rule collapse to the figure behind it.
    assert "Blocked (committed-add cap): **META**, **GOOGL** — each committed $200.00 of $200." \
        in lines
    assert "No stop-loss today." in lines


def test_a_trim_names_its_redistribute_recipients():
    rec = {"decisions": [
        {"symbol": "NVDA", "kind": "trim", "shares": 0.5, "notional": 110},
        {"symbol": "AAPL", "kind": "redistribute", "notional": 55},
        {"symbol": "AMZN", "kind": "redistribute", "notional": 55},
    ]}
    assert dl.proposal_lines(rec, None, None, None)[0] == (
        "• TRIM **NVDA** **0.5sh** (~$110.00) — drawdown n/a; new; "
        "REDISTRIBUTE → **AAPL** $55, **AMZN** $55")


def test_a_new_proposal_says_new():
    assert dl.standing_text({"since": DATE, "sessions": 1}) == "new"
    assert dl.standing_text(None) == "new"


def test_a_null_average_renders_as_na_instead_of_raising():
    # 2026-11-05: Robinhood omitted TSLA's and QLD's average cost; the record said null.
    tsla = {"account": "Long-term", "symbol": "TSLA", "kind": "drift", "sheetQty": "104",
            "sheetAvg": 352.99, "liveQty": "107.000000", "liveAvg": None, "gapUsd": 1030.53}
    assert dl.reconcile_entry(tsla) == "TSLA → 107 @ avg n/a (+3 sh)"
    qld = {"account": "Long-term", "symbol": "QLD", "kind": "add", "sheetQty": None,
           "sheetAvg": None, "liveQty": "34.200661", "liveAvg": None, "gapUsd": 2475.44}
    assert dl.reconcile_entry(qld) == "add QLD 34.200661 @ avg n/a"


def test_one_broken_section_does_not_cost_the_whole_message(tmp_path):
    logs = tmp_path / "logs"
    (logs / "reviews").mkdir(parents=True)
    (logs / "reconcile").mkdir()
    (logs / "reviews" / f"{DATE}.decisions.json").write_text(json.dumps(DECISIONS))
    # A drift alert missing the field the renderer needs.
    (logs / "reconcile" / f"{DATE}.json").write_text(json.dumps(
        {"alerts": [{"account": "Mid-term", "symbol": "XYZ", "kind": "drift"}],
         "tradeLog": {"filledInWindow": 0, "findings": []}}))
    text = dl.agentic_message(logs, DATE)
    assert "• BUY $100 **QQQM**" in text
    assert "Reconcile: could not be rendered today (KeyError)" in text


def test_reconcile_entries_take_the_copyable_form():
    drift = {"symbol": "SSO", "kind": "drift", "sheetQty": "4.261975", "sheetAvg": 48.87,
             "liveQty": "34.261975", "liveAvg": 67.90}
    assert dl.reconcile_entry(drift) == "SSO → 34.261975 @ $67.90 (+30 sh, avg +$19.03)"
    same_avg = dict(drift, sheetAvg=67.90, sheetQty="35.5")
    assert dl.reconcile_entry(same_avg) == "SSO → 34.261975 @ $67.90 (−1.238025 sh)"
    assert dl.reconcile_entry({"symbol": "BBB", "kind": "add", "liveQty": "2", "liveAvg": 51}) == \
        "add BBB 2 @ $51.00"
    assert dl.reconcile_entry({"symbol": "CCC", "kind": "remove", "sheetQty": "5"}) == \
        "remove CCC (−5 sh)"
    assert dl.reconcile_entry({"symbol": "DDD", "kind": "verify", "liveQty": "5.25",
                               "sheetQty": "5", "liveAvg": 1, "sheetAvg": 1}) == \
        "verify DDD 5.25 (+0.25 sh)"


def test_the_full_messages(tmp_path):
    recon = {"date": DATE, "alerts": [
        {"account": "Mid-term", "symbol": "PM", "kind": "drift", "sheetQty": "14.548461",
         "sheetAvg": 155.21, "liveQty": "15.548461", "liveAvg": 158.74},
        {"account": "Long-term", "symbol": "TSLA", "kind": "drift", "sheetQty": "89",
         "sheetAvg": 348.56, "liveQty": "102", "liveAvg": 352.25},
    ], "tradeLog": {"filledInWindow": 0, "findings": []}}
    logs = _logs(
        tmp_path,
        **{f"reviews__{DATE}.md": REVIEW,
           f"reviews__{DATE}.decisions.json": DECISIONS,
           f"reviews__{DATE}.signals.json": SIGNALS,
           f"reviews__{DATE}.standing.json": STANDING,
           f"reviews__{DATE}.tax.json": TAX,
           f"reconcile__{DATE}.json": recon,
           f"reconcile__{DATE}.md": "report",
           "shadow__latest.txt": "shadow-check: earlier sessions\n  = 2026-10-15: now verified",
           f"digest__{DATE}.briefing.md": "Briefing: NVDA weakness tied to rate fears.",
           f"digest__{DATE}.reference-head.md": "US accounts: $419,806.31 across 5 accounts"})
    agentic = dl.agentic_message(logs, DATE)
    blocks = agentic.strip().split("\n\n")
    assert blocks[0] == ("**Hermes Trader — Agentic**\nSleeve **$2,166.09**, day +$6.63 (+0.31%); "
                         "slots for Thursday: 5 of 5 today, 6 of 6 that week.")
    assert blocks[-1] == dl.CLOSER
    reconcile = next(b for b in blocks if b.startswith("Reconcile:")).splitlines()
    # Accounts in a fixed order, each on its own line.
    assert reconcile[2] == "Long-term: TSLA → 102 @ $352.25 (+13 sh, avg +$3.69)."
    assert reconcile[3] == "Mid-term: PM → 15.548461 @ $158.74 (+1 sh, avg +$3.53)."
    assert any(b.startswith("Shadow check:\nshadow-check:") for b in blocks)
    assert "Briefing: NVDA weakness tied to rate fears." in blocks

    reference = dl.reference_message(logs, DATE)
    assert reference == ("US accounts: $419,806.31 across 5 accounts\n\n"
                         "Trade log: no filled Agentic orders in the check window.\n")


def test_departures_and_decision_requests_have_one_shape():
    rec = {"departures": [{"symbol": "META", "threshold": "BUY >=5% below 20-day high",
                           "observed": "10.44% below high", "did": "Propose take-profit.",
                           "why": "Realize the gain."}],
           "decisionRequests": [{"symbol": "NVDA", "question": "Buy or exit?",
                                 "readings": [{"side": "buy", "detail": "8.8% below high"},
                                              {"side": "exit", "detail": "+12% from cost"}]}]}
    assert dl.departure_section(rec)[1] == (
        "• **META** (10.44% below high) — BUY >=5% below 20-day high. "
        "Did: Propose take-profit. Why: Realize the gain.")
    assert dl.decision_section(rec)[1] == (
        "• **NVDA** — Buy or exit? buy: 8.8% below high; exit: +12% from cost.")


def test_missing_records_say_so_instead_of_going_quiet(tmp_path):
    logs = _logs(tmp_path, **{f"reviews__{DATE}.md": REVIEW})
    agentic = dl.agentic_message(logs, DATE)
    assert "No decision record today" in agentic
    assert "Reconcile: did not run today." in agentic
    assert "slots unavailable" in agentic
    reference = dl.reference_message(logs, DATE)
    assert reference.startswith("US accounts: overview unavailable")


def test_a_malformed_record_still_produces_a_message(tmp_path):
    logs = _logs(tmp_path, **{f"reviews__{DATE}.decisions.json":
                              {"decisions": [{"kind": "trim", "symbol": "NVDA"}]}})
    argv, sys.argv = sys.argv, ["digest_lines.py", "--repo", str(tmp_path), "--date", DATE]
    try:
        assert dl.main() == 1
    finally:
        sys.argv = argv
    text = (logs / "digest" / f"{DATE}.agentic.md").read_text()
    # The broken section says so in place; the rest of the message still goes out.
    assert text.startswith("**Hermes Trader — Agentic**")
    assert "Proposals: could not be rendered today (KeyError)" in text


def test_departures_read_the_same_way_share_one_line():
    why = "The momentum drawdown favors reducing exposure rather than adding."
    rec = {"departures": [
        {"symbol": "META", "threshold": "BUY >=5% below 20-day high",
         "observed": "19.6% below high", "did": "Choose 50% TRIM and omit BUY", "why": why},
        {"symbol": "GOOGL", "threshold": "BUY >=5% below 20-day high",
         "observed": "10.81% below high", "did": "Choose 50% TRIM and omit BUY.", "why": why},
        {"symbol": "AAPL", "threshold": "TRIM >=10% below 20-day high", "observed": "10.2%",
         "did": "Hold", "why": "Earnings tomorrow."},
    ]}
    assert dl.departure_section(rec)[1:3] == [
        "• **META** (19.6% below high), **GOOGL** (10.81% below high) — BUY >=5% below 20-day "
        f"high. Did: Choose 50% TRIM and omit BUY. Why: {why}",
        "• **AAPL** (10.2%) — TRIM >=10% below 20-day high. Did: Hold. Why: Earnings tomorrow.",
    ]


def test_redistribute_blocks_share_one_line_whatever_the_rule_wording():
    decisions = [{"side": "sell", "symbol": "MSFT", "kind": "take-profit"},
                 {"side": "sell", "symbol": "META", "kind": "trim"}]
    blocked = [
        {"symbol": "NVDA", "rule": "committed-add cap", "value": "committed $200.00 of $200"},
        {"symbol": "MSFT", "rule": "REDISTRIBUTE one direction per review",
         "value": "take-profit sell 0.236215 shares"},
        {"symbol": "AAPL", "rule": "redistribute: one symbol, one direction; committed-add cap",
         "value": "committed $200.00 of $200; would make $253"},
        {"symbol": "META", "rule": "REDISTRIBUTE one direction and committed-add cap",
         "value": "Proposed sell on this symbol"},
    ]
    assert dl.blocked_lines(blocked, decisions) == [
        "Blocked (committed-add cap): **NVDA** — committed $200.00 of $200.",
        "No REDISTRIBUTE recipient: **MSFT** (exit proposed), "
        "**AAPL** (committed $200.00 of $200), **META** (trim proposed).",
    ]


def test_a_wash_sale_window_a_stop_loss_would_open_reads_as_one_line():
    # 2026-11-10's two records, worded as a sentence the generic form garbled.
    value = "If sold 2026-11-11 at a loss, buy block through 2026-12-11; clears 2026-12-12"
    blocked = [{"symbol": s, "rule": "wash-sale window from proposed stop-loss", "value": value}
               for s in ("GOOGL", "NVDA")]
    assert dl.blocked_lines(blocked, []) == [
        "Wash-sale hold if the stop-loss sells at a loss: **GOOGL**, **NVDA** — "
        "no buys until Dec 12, 2026."]
    # 11-06's wording, with only a 'through' date: buys resume the day after.
    other = [{"symbol": "GOOGL", "rule": "proposed stop-loss wash-sale block",
              "value": "Loss sale on 2026-11-09 starts a block through 2026-12-10"}]
    assert dl.blocked_lines(other, [])[0].endswith("no buys until Dec 11, 2026.")
    # A window an actual past sale opened keeps the generic line.
    past = [{"symbol": "AAPL", "rule": "wash-sale buy block", "value": "loss sale 2026-11-02"}]
    assert dl.blocked_lines(past, []) == [
        "Blocked (wash-sale buy block): **AAPL** — loss sale 2026-11-02."]


def test_a_position_moved_between_accounts_is_one_entry():
    # 2026-11-05: QLD left Mid-term and arrived whole in Long-term.
    recon = {"alerts": [
        {"account": "Long-term", "symbol": "QLD", "kind": "add", "liveQty": "34.200661",
         "liveAvg": None},
        {"account": "Mid-term", "symbol": "QLD", "kind": "remove", "sheetQty": "34.200661"},
        {"account": "Mid-term", "symbol": "SPY", "kind": "remove", "sheetQty": "0.026142"},
        {"account": "Long-term", "symbol": "TSLA", "kind": "add", "liveQty": "3",
         "liveAvg": None},
        {"account": "Mid-term", "symbol": "TSLA", "kind": "remove", "sheetQty": "2"},
    ]}
    lines = dl.reconcile_section(recon, True)
    assert "Moved between accounts: QLD 34.200661 sh Mid-term → Long-term." in lines
    # Different share counts are not a move; each stays in its own account.
    assert "Long-term: add TSLA 3 @ avg n/a." in lines
    assert "Mid-term: remove SPY (−0.026142 sh); remove TSLA (−2 sh)." in lines


OVERVIEW = {"date": "2026-11-10", "accounts": [
    {"name": "Agentic", "equitiesUsd": 1456.37, "cashUsd": 614.87, "cryptoUsd": None,
     "totalUsd": 2071.24},
    {"name": "Long-term", "equitiesUsd": 150000.00, "cashUsd": 4032.28, "cryptoUsd": None,
     "totalUsd": 154032.28},
    {"name": "Mid-term", "equitiesUsd": 90000.00, "cashUsd": 1888.86, "cryptoUsd": 26085.29,
     "totalUsd": 117974.15},
    {"name": "Chase", "equitiesUsd": 80000.00, "cashUsd": None, "cryptoUsd": None,
     "totalUsd": 80000.00},
    {"name": "Merrill", "equitiesUsd": 38000.00, "cashUsd": None, "cryptoUsd": None,
     "totalUsd": 38000.00}],
    "grandTotalUsd": 392077.67, "flagged": {"count": 23, "agentic": 1}}


def test_the_reference_head_is_rendered_from_the_overview_record():
    assert dl.overview_head(OVERVIEW) == (
        "US accounts: $392,077.67 across 5 accounts — $359,456.37 equities + $6,536.01 cash "
        "(Agentic $614.87, Long-term $4,032.28, Mid-term $1,888.86) + $26,085.29 crypto in "
        "Mid-term.\n"
        "Read-only context; Korean holdings are excluded.\n"
        "⚠️ 23 positions flagged across all accounts — see full review; 1 in Agentic.")


def test_an_overview_that_does_not_add_up_falls_back_to_the_model_head(tmp_path):
    bad = json.loads(json.dumps(OVERVIEW))
    bad["grandTotalUsd"] = 400000.00
    assert dl.overview_head(bad) is None
    logs = tmp_path / "logs"
    (logs / "reviews").mkdir(parents=True)
    (logs / "digest").mkdir()
    (logs / "reviews" / f"{DATE}.overview.json").write_text(json.dumps(bad))
    (logs / "digest" / f"{DATE}.reference-head.md").write_text("US accounts: from the model.")
    assert dl.reference_message(logs, DATE).startswith("US accounts: from the model.")
    (logs / "reviews" / f"{DATE}.overview.json").write_text(json.dumps(OVERVIEW))
    assert dl.reference_message(logs, DATE).startswith("US accounts: $392,077.67")
