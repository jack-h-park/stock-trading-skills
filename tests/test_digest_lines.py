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
    assert line == "Agentic 계좌 **$2,166.09**, 당일 +$6.63 (+0.31%)"
    assert dl.sleeve_line("Sleeve: $2,153.50; prior close $2,179.25; day −$25.75 (-1.18%)") == \
        "Agentic 계좌 **$2,153.50**, 당일 −$25.75 (−1.18%)"
    assert dl.sleeve_line("no sleeve here") is None


def test_the_sleeve_line_survives_a_day_change_without_its_dollar_sign():
    # 2026-11-11: "day +4.03" instead of "day +$4.03" read as no sleeve line at all.
    line = "Sleeve: $2,075.06; prior close $2,071.03; day +4.03 (+0.19%)"
    assert dl.sleeve_line(line) == "Agentic 계좌 **$2,075.06**, 당일 +$4.03 (+0.19%)"


def test_slots_count_agentic_buys_in_the_target_week():
    buys = {"recentBuys": {
        "QQQM": [{"account": "Agentic", "date": "2026-10-20"}],
        "AAPL": [{"account": "Long-term", "date": "2026-10-20"}],      # other account
        "NVDA": [{"account": "Agentic", "date": "2026-10-16"}],        # previous week
    }}
    assert dl.slots_clause("2026-10-22", buys) == \
        "목요일 주문 여유: 당일 5/5건, 그 주 5/6건"
    # A Friday review targets Monday: a fresh week whatever the last one spent.
    assert dl.slots_clause("2026-10-26", buys) == "월요일 주문 여유: 당일 5/5건, 그 주 6/6건"
    assert "알 수 없음" in dl.slots_clause("2026-10-22", None)
    # A tax file whose Robinhood half failed has no recentBuys: unknown, never zero fills.
    assert "알 수 없음" in dl.slots_clause("2026-10-22", {"robinhood": {"status": "failed: x"},
                                                           "buyBlocks": {}})


def test_the_daily_count_never_exceeds_what_the_week_has_left():
    buys = {"recentBuys": {f"S{i}": [{"account": "Agentic", "date": "2026-10-19"}]
                           for i in range(4)}}
    assert dl.slots_clause("2026-10-22", buys) == \
        "목요일 주문 여유: 당일 2/5건, 그 주 2/6건"


def test_the_caps_match_the_guardrails():
    text = (ROOT / "config" / "guardrails.md").read_text()
    assert f"| Max orders per day | **{dl.DAILY_ORDER_CAP}** |" in text
    assert f"| Max orders per week | **{dl.WEEKLY_ORDER_CAP}** (Mon–Sun) |" in text


def test_proposals_render_in_a_fixed_order_with_standing_and_term():
    lines = dl.proposal_lines(DECISIONS, SIGNALS, STANDING, TAX)
    assert lines[:4] == [
        "• 매수 $100 **QQQM** — 20일 고점 대비 −5.94%; 신규",
        "• 일부 매도 **NVDA** **0.495877주** (~$107.65) — 20일 고점 대비 −11.05%; "
        "10월 22일부터 8회 연속; 매도 대금은 현금으로 보유",
        "• 익절 **MSFT** **0.236215주** (~$125.86) — 최소 9월 1일부터 34회 이상 연속; "
        "2027년 6월 24일 전까지 단기 보유",
        "• 익절 **META** **0.174662주** (~$121.99) — 9월 9일부터 29회 연속; "
        "단기·장기 lot 혼재; 가장 오래된 lot은 12일 뒤 장기 전환",
    ]
    # Two differently worded values for one rule collapse to the figure behind it.
    assert "보류(추가 매수 한도): **META**, **GOOGL** — 각각 약정 $200.00 / 한도 $200." in lines
    assert "오늘 손절 없음." in lines


def test_a_trim_names_its_redistribute_recipients():
    rec = {"decisions": [
        {"symbol": "NVDA", "kind": "trim", "shares": 0.5, "notional": 110},
        {"symbol": "AAPL", "kind": "redistribute", "notional": 55},
        {"symbol": "AMZN", "kind": "redistribute", "notional": 55},
    ]}
    assert dl.proposal_lines(rec, None, None, None)[0] == (
        "• 일부 매도 **NVDA** **0.5주** (~$110.00) — 하락폭 없음; 신규; "
        "재배분 → **AAPL** $55, **AMZN** $55")


def test_a_new_proposal_says_new():
    assert dl.standing_text({"since": DATE, "sessions": 1}) == "신규"
    assert dl.standing_text(None) == "신규"


def test_a_null_average_renders_as_na_instead_of_raising():
    # 2026-11-05: Robinhood omitted TSLA's and QLD's average cost; the record said null.
    tsla = {"account": "Long-term", "symbol": "TSLA", "kind": "drift", "sheetQty": "104",
            "sheetAvg": 352.99, "liveQty": "107.000000", "liveAvg": None, "gapUsd": 1030.53}
    assert dl.reconcile_entry(tsla) == "TSLA → 107주 @ 평단 없음 (+3주)"
    qld = {"account": "Long-term", "symbol": "QLD", "kind": "add", "sheetQty": None,
           "sheetAvg": None, "liveQty": "34.200661", "liveAvg": None, "gapUsd": 2475.44}
    assert dl.reconcile_entry(qld) == "추가 QLD 34.200661주 @ 평단 없음"


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
    assert "• 매수 $100 **QQQM**" in text
    assert "시트 대조: 오늘은 표시하지 못했습니다(KeyError)" in text


def test_reconcile_entries_take_the_copyable_form():
    drift = {"symbol": "SSO", "kind": "drift", "sheetQty": "4.261975", "sheetAvg": 48.87,
             "liveQty": "34.261975", "liveAvg": 67.90}
    assert dl.reconcile_entry(drift) == "SSO → 34.261975주 @ $67.90 (+30주, 평단 +$19.03)"
    same_avg = dict(drift, sheetAvg=67.90, sheetQty="35.5")
    assert dl.reconcile_entry(same_avg) == "SSO → 34.261975주 @ $67.90 (−1.238025주)"
    assert dl.reconcile_entry({"symbol": "BBB", "kind": "add", "liveQty": "2", "liveAvg": 51}) == \
        "추가 BBB 2주 @ $51.00"
    assert dl.reconcile_entry({"symbol": "CCC", "kind": "remove", "sheetQty": "5"}) == \
        "삭제 CCC (−5주)"
    assert dl.reconcile_entry({"symbol": "DDD", "kind": "verify", "liveQty": "5.25",
                               "sheetQty": "5", "liveAvg": 1, "sheetAvg": 1}) == \
        "확인 DDD 5.25주 (+0.25주)"


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
           "shadow__latest.txt": ("이전 거래일 재점검:\n"
                                  "  = 10월 15일: 이제 검증됨 — 가격 1건 확정 종가와 일치"),
           f"digest__{DATE}.briefing.md": "브리핑: 금리 우려에 따른 NVDA 약세."})
    agentic = dl.agentic_message(logs, DATE)
    blocks = agentic.strip().split("\n\n")
    assert blocks[0] == ("**Hermes Trader — Agentic**\nAgentic 계좌 **$2,166.09**, "
                         "당일 +$6.63 (+0.31%); 목요일 주문 여유: 당일 5/5건, 그 주 6/6건.")
    assert blocks[-1] == dl.CLOSER
    reconcile = next(b for b in blocks if b.startswith("시트 대조:")).splitlines()
    # Accounts in a fixed order, each on its own line.
    assert reconcile[2] == "Long-term: TSLA → 102주 @ $352.25 (+13주, 평단 +$3.69)."
    assert reconcile[3] == "Mid-term: PM → 15.548461주 @ $158.74 (+1주, 평단 +$3.53)."
    assert any(b.startswith("Shadow 점검:\n이전 거래일 재점검:") for b in blocks)
    assert "브리핑: 금리 우려에 따른 NVDA 약세." in blocks

    # Reconcile drifted, so only the trade-log check is reported as passing.
    assert blocks[-2] == "브리핑: 금리 우려에 따른 NVDA 약세."
    assert "점검: 거래 기록 점검 기간에 체결된 Agentic 주문 없음." in blocks


def test_departures_and_decision_requests_have_one_shape():
    rec = {"departures": [{"symbol": "META", "threshold": "BUY >=5% below 20-day high",
                           "observed": "10.44% below high", "did": "Propose take-profit.",
                           "why": "Realize the gain."}],
           "decisionRequests": [{"symbol": "NVDA", "question": "Buy or exit?",
                                 "readings": [{"side": "buy", "detail": "8.8% below high"},
                                              {"side": "exit", "detail": "+12% from cost"}]}]}
    assert dl.departure_section(rec)[1] == (
        "• **META** (10.44% below high) — BUY >=5% below 20-day high. "
        "조치: Propose take-profit. 이유: Realize the gain.")
    assert dl.decision_section(rec)[1] == (
        "• **NVDA** — Buy or exit? 매수: 8.8% below high; 매도: +12% from cost.")


def test_missing_records_say_so_instead_of_going_quiet(tmp_path):
    logs = _logs(tmp_path, **{f"reviews__{DATE}.md": REVIEW})
    agentic = dl.agentic_message(logs, DATE)
    assert "오늘 결정 기록이 없어" in agentic
    assert "시트 대조: 오늘 실행되지 않았습니다." in agentic
    assert "주문 여유 알 수 없음" in agentic
    # No reconcile record: the checks did not run, so nothing may say they passed.
    assert "점검:" not in agentic


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
    assert "제안: 오늘은 표시하지 못했습니다(KeyError)" in text


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
        f"high. 조치: Choose 50% TRIM and omit BUY. 이유: {why}",
        "• **AAPL** (10.2%) — TRIM >=10% below 20-day high. 조치: Hold. 이유: Earnings tomorrow.",
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
        "보류(추가 매수 한도): **NVDA** — 약정 $200.00 / 한도 $200.",
        "재배분 대상 없음: **MSFT** (매도 제안), "
        "**AAPL** (약정 $200.00 / 한도 $200), **META** (일부 매도 제안).",
    ]


def test_a_wash_sale_window_a_stop_loss_would_open_reads_as_one_line():
    # 2026-11-10's two records, worded as a sentence the generic form garbled.
    value = "If sold 2026-11-11 at a loss, buy block through 2026-12-11; clears 2026-12-12"
    blocked = [{"symbol": s, "rule": "wash-sale window from proposed stop-loss", "value": value}
               for s in ("GOOGL", "NVDA")]
    assert dl.blocked_lines(blocked, []) == [
        "손절이 손실로 체결되면 wash-sale 보류: **GOOGL**, **NVDA** — "
        "2026년 12월 12일부터 매수 재개."]
    # 11-06's wording, with only a 'through' date: buys resume the day after.
    other = [{"symbol": "GOOGL", "rule": "proposed stop-loss wash-sale block",
              "value": "Loss sale on 2026-11-09 starts a block through 2026-12-10"}]
    assert dl.blocked_lines(other, [])[0].endswith("2026년 12월 11일부터 매수 재개.")
    # A window an actual past sale opened keeps the generic line.
    past = [{"symbol": "AAPL", "rule": "wash-sale buy block", "value": "loss sale 2026-11-02"}]
    assert dl.blocked_lines(past, []) == [
        "보류(wash-sale 기간): **AAPL** — loss sale 2026-11-02."]


def test_one_rule_is_one_line_whatever_its_spelling_or_extra_figures():
    # The same block as worded on 11-18, 11-20 and 11-23.
    blocked = [
        {"symbol": "NVDA", "rule": "Committed-add capital cap",
         "value": "32.10% drawdown, committed $200.00 of $200, proposed $100 add would bring "
                  "committed capital to $300.00"},
        {"symbol": "GOOGL", "rule": "Committed-add cap",
         "value": "committed $200.00 of $200; $300.00 after proposed $100 add"},
        {"symbol": "MSFT", "rule": "Committed-add cap",
         "value": "committed $200.00 of $200 · $300.00 after proposed $100 add · 11.84% drawdown"},
        {"symbol": "VOO", "rule": "One add per symbol per calendar week",
         "value": "committed $100.00 of $200; filled 2026-11-18 ($100.00)"},
    ]
    assert dl.blocked_lines(blocked, []) == [
        "보류(추가 매수 한도): **NVDA**, **GOOGL**, **MSFT** — 각각 약정 $200.00 / 한도 $200.",
        "보류(종목당 주 1회 추가 매수): **VOO** — 약정 $100.00 / 한도 $200.",
    ]


def test_a_proposed_stop_loss_always_carries_its_wash_sale_hold():
    # 11-20 and 11-23 had GOOGL and NVDA stop-losses standing and no wash-sale block
    # in the record; 11-19 had the same two with one. The line must not depend on that.
    decisions = [{"side": "sell", "symbol": "NVDA", "kind": "stop-loss"},
                 {"side": "sell", "symbol": "GOOGL", "kind": "stop-loss"},
                 {"side": "sell", "symbol": "MSFT", "kind": "take-profit"}]
    assert dl.blocked_lines([], decisions, "2026-11-24") == [
        "손절이 손실로 체결되면 wash-sale 보류: **NVDA**, **GOOGL** — "
        "2026년 12월 25일부터 매수 재개."]
    # A date the review also wrote for the same symbol is replaced, not repeated.
    stale = [{"symbol": "NVDA", "rule": "proposed stop-loss starts wash-sale buy block",
              "value": "If filled 2026-11-20 at the price used, loss sale blocks buys through "
                       "2026-12-20; clears 2026-12-21"}]
    assert dl.blocked_lines(stale, decisions, "2026-11-24") == [
        "손절이 손실로 체결되면 wash-sale 보류: **NVDA**, **GOOGL** — "
        "2026년 12월 25일부터 매수 재개."]
    # 11-19's record: the computed date matches what the review wrote.
    assert dl.blocked_lines(stale, decisions[:1], "2026-11-20") == [
        "손절이 손실로 체결되면 wash-sale 보류: **NVDA** — 2026년 12월 21일부터 매수 재개."]


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
    assert "계좌 간 이동: QLD 34.200661주 Mid-term → Long-term." in lines
    # Different share counts are not a move; each stays in its own account.
    assert "Long-term: 추가 TSLA 3주 @ 평단 없음." in lines
    assert "Mid-term: 삭제 SPY (−0.026142주); 삭제 TSLA (−2주)." in lines


def test_passing_checks_close_the_agentic_message_in_one_line():
    clean = {"alerts": [], "tradeLog": {"filledInWindow": 2, "findings": []}}
    assert dl.checks_line(clean) == (
        "점검: 시트가 Robinhood와 일치; 체결된 Agentic 주문 2건 모두 기록됨.")
    both_failed = {"alerts": [{"symbol": "PM"}], "tradeLog": {"findings": [{"symbol": "VOO"}]}}
    assert dl.checks_line(both_failed) is None
    assert dl.checks_line(None) is None


def test_only_the_agentic_message_is_written(tmp_path):
    # The read-only Telegram digest was dropped on 2026-11-18 (decisions §24); the cron
    # wrapper delivers <date>.md to Telegram whenever it exists, so it must not appear.
    logs = _logs(tmp_path, **{f"reviews__{DATE}.md": REVIEW,
                              f"reconcile__{DATE}.json": {"alerts": [], "tradeLog": {}}})
    argv, sys.argv = sys.argv, ["digest_lines.py", "--repo", str(tmp_path), "--date", DATE]
    try:
        dl.main()
    finally:
        sys.argv = argv
    written = sorted(p.name for p in (logs / "digest").iterdir())
    assert written == [f"{DATE}.agentic.md"]
    text = (logs / "digest" / f"{DATE}.agentic.md").read_text()
    assert "점검: 시트가 Robinhood와 일치; 거래 기록 점검 기간에 체결된 Agentic 주문 없음" in text
