#!/usr/bin/env python3
"""Assemble the review digest from the day's records, so its fixed lines are fixed.

The digest model used to write every line of both messages from a format spelled out in
the prompt, and the format did not hold: across three consecutive sessions the sleeve
value lost its thousands separator, one message ran its reconcile items together into a
paragraph with no blank lines, the TRIM and take-profit lines swapped places, and the
same departure was worded three different ways. Each of those lines is a copy of a
number already in a file, so here they are rendered from the file instead.

What is still written by a model is only what needs judgement: the briefing note (which
items matter and whether a deadline has passed). The digest step writes it as a fragment;
this script places it.

There used to be a second, read-only message for Telegram: the cross-account US total,
a flag count pointing at a review the reader could not open, and the two clean-check
lines. It was dropped on 2026-11-18 (docs/decisions.md §24). The checks now close the
Agentic message in one line when they pass, and appear as sections there when they fail.

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

Output: digest/<date>.agentic.md (Discord).

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

# The '$' on the day change is optional. On 2026-11-11 the model wrote "day +4.03"
# for "day +$4.03", and the strict form turned a correct sleeve line into "Sleeve
# unavailable". The figures are what the digest copies; the currency sign is not.
SLEEVE = re.compile(
    r"Sleeve: \$(?P<value>[\d,]+(?:\.\d+)?); prior close \$[\d,]+(?:\.\d+)?; "
    r"day (?P<dsign>[+\-−]?)\$?(?P<change>[\d,]+(?:\.\d+)?) \((?P<psign>[+\-−]?)(?P<pct>[\d.]+)%\)"
)

ACCOUNT_ORDER = ["Long-term", "Mid-term"]
EXIT_KINDS = ("take-profit", "stop-loss")
CLOSER = "제안일 뿐입니다. Jack이 대화 세션에서 확인하기 전에는 아무것도 실행되지 않습니다."
WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")
# How a kind or a reading side is named in the message. The record keeps the English
# value, which scheduled_orders.py and the tests read.
KIND_KO = {"buy": "매수", "redistribute": "재배분", "trim": "일부 매도",
           "take-profit": "익절", "stop-loss": "손절"}
SIDE_KO = {"buy": "매수", "exit": "매도", "sell": "매도"}


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
    return f"{d.year}년 {d.month}월 {d.day}일"


def short_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.month}월 {d.day}일"


def sentence(text: object) -> str:
    """The text as one sentence: its own ? or ! kept, otherwise exactly one full stop."""
    t = str(text or "").strip().rstrip(".")
    return t if t.endswith(("?", "!")) else f"{t}."


def shares(value: float | Decimal | str) -> str:
    return f"{qty(value)}주"


def sleeve_line(review: str) -> str | None:
    """'Agentic 계좌 **$X**, 당일 ±$Y (±Z%)' from the review's LAST 'Sleeve:' line.

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
    return f"Agentic 계좌 **{value}**, 당일 {dsign}{change} ({psign}{m['pct']}%)"


def slots_clause(target: str | None, tax: dict[str, Any] | None) -> str:
    """Order slots for the session the proposals would execute in, not the one that closed.

    Counts Agentic buy fills from the tax file's recentBuys, which covers every buy —
    REDISTRIBUTE included — in the last 30 days; the week runs Monday to Sunday.
    """
    if not target:
        return "주문 여유 알 수 없음(결정 기록에 실행 세션이 없음)"
    day = dt.date.fromisoformat(target)
    weekday = WEEKDAYS[day.weekday()]
    # A tax file whose Robinhood half failed carries no recentBuys at all: unknown, not zero.
    if tax is None or "recentBuys" not in tax:
        return f"{weekday} 주문 여유 알 수 없음(오늘 체결 데이터 없음)"
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
    return (f"{weekday} 주문 여유: 당일 {left_today}/{DAILY_ORDER_CAP}건, "
            f"그 주 {left_week}/{WEEKLY_ORDER_CAP}건")


def standing_text(entry: dict[str, Any] | None) -> str:
    if not entry or entry.get("sessions", 1) <= 1:
        return "신규"
    since = short_date(entry["since"])
    if entry.get("atLeast"):
        return f"최소 {since}부터 {entry['sessions']}회 이상 연속"
    return f"{since}부터 {entry['sessions']}회 연속"


def term_text(lot: dict[str, Any] | None) -> str | None:
    if not lot:
        return None
    term = lot.get("term")
    if term == "short-term" and lot.get("oldestLongTermFrom"):
        text = f"{long_date(lot['oldestLongTermFrom'])} 전까지 단기 보유"
    elif term == "long-term":
        text = "장기 보유"
    elif term == "mixed":
        text = "단기·장기 lot 혼재"
    else:
        return None
    if lot.get("turnsLongTermSoon"):
        text += f"; 가장 오래된 lot은 {lot.get('daysToLongTerm')}일 뒤 장기 전환"
    return text


def drawdown(signals: dict[str, Any] | None, symbol: str) -> str:
    sym = ((signals or {}).get("symbols") or {}).get(symbol) or {}
    pct = sym.get("drawdownPct")
    return f"20일 고점 대비 −{pct:.2f}%" if isinstance(pct, (int, float)) else "하락폭 없음"


def proposal_lines(rec: dict[str, Any], signals: dict[str, Any] | None,
                   standing: dict[str, Any] | None, tax: dict[str, Any] | None) -> list[str]:
    decisions = rec.get("decisions") or []
    lots = (tax or {}).get("lots") or {}
    buys = [d for d in decisions if d.get("kind") == "buy"]
    redistribute = [d for d in decisions if d.get("kind") == "redistribute"]
    trims = [d for d in decisions if d.get("kind") == "trim"]
    exits = [d for d in decisions if d.get("kind") in EXIT_KINDS]

    def stood(d: dict[str, Any]) -> str:
        # Every kind, not just exits: a trim re-sized to half the current position
        # each day reads as fresh news unless the line says how long it has stood.
        return standing_text((standing or {}).get(f"{d['symbol']}:{d['kind']}"))

    out: list[str] = []
    for d in buys:
        out.append(f"• 매수 {dollars(d['notional'])} **{d['symbol']}** — "
                   f"{drawdown(signals, d['symbol'])}; {stood(d)}")
    for d in trims:
        dest = ", ".join(f"**{r['symbol']}** {dollars(r['notional'])}" for r in redistribute)
        tail = f"재배분 → {dest}" if dest else "매도 대금은 현금으로 보유"
        out.append(f"• 일부 매도 **{d['symbol']}** **{shares(d['shares'])}** "
                   f"(~{money(d['notional'])}) — {drawdown(signals, d['symbol'])}; "
                   f"{stood(d)}; {tail}")
    for d in exits:
        label = KIND_KO[d["kind"]]
        line = (f"• {label} **{d['symbol']}** **{shares(d['shares'])}** "
                f"(~{money(d['notional'])}) — {stood(d)}")
        term = term_text(lots.get(d["symbol"]))
        out.append(f"{line}; {term}" if term else line)
    if not out:
        out.append("오늘 제안 없음.")

    out += blocked_lines(rec.get("blocked") or [], decisions, rec.get("targetSession"))
    none = [name for name, present in (("일부 매도", trims), ("손절",
            [d for d in exits if d["kind"] == "stop-loss"])) if not present]
    if none:
        out.append("오늘 " + "·".join(none) + " 없음.")
    return out


# A stop-loss filled on the target session opens a 30-day wash-sale window: buys are
# blocked for 30 days and resume on day 31 (config/guardrails.md § Wash-sale window).
WASH_CLEAR_DAYS = 31
_AMOUNT = r"\$\d+(?:,\d{3})*(?:\.\d+)?"  # not [\d,]+: "$200," on 11-18 ended a clause
COMMITTED = re.compile(rf"committed ({_AMOUNT}) of ({_AMOUNT})")

# The review names rules in English, and scheduled_orders.py reads them that way; the
# message names the ones it knows in Korean and shows any other as the review wrote it.
RULE_KO = {
    "committed-add cap": "추가 매수 한도",
    "one add per symbol per calendar week": "종목당 주 1회 추가 매수",
    "one add per symbol per week": "종목당 주 1회 추가 매수",
    "20% position cap": "종목 비중 20% 한도",
    "position cap": "종목 비중 한도",
    "daily order cap": "일일 주문 한도",
    "weekly order cap": "주간 주문 한도",
    "wash-sale window": "wash-sale 기간",
    "wash-sale block": "wash-sale 기간",
    "wash-sale buy block": "wash-sale 기간",
}


def rule_label(rule: str) -> str:
    """The rule name as one spelling, so one rule makes one line.

    The review writes the same rule differently from day to day: 'committed-add cap'
    on 11-19, 'Committed-add cap' on 11-20, 'Committed-add capital cap' on 11-18. Each
    spelling used to be its own group.
    """
    label = " ".join(rule.split()).lower()
    return label.replace("committed-add capital cap", "committed-add cap")


def rule_ko(label: str) -> str:
    return RULE_KO.get(label, label)


def figure_ko(figure: str) -> str:
    """'committed $X of $Y' as Korean; any other figure as the review wrote it.

    The review keeps that one phrase in English because block_figure reads it.
    """
    m = COMMITTED.fullmatch(figure)
    return f"약정 {m.group(1)} / 한도 {m.group(2)}" if m else figure


def block_figure(value: str) -> str:
    """The number behind a block: 'committed $X of $Y' wherever the value states it.

    Otherwise the value up to its first ';' or '·'. What follows is the model restating
    the arithmetic, worded differently daily: on 11-23 each value also carried the
    symbol's drawdown after a '·', which made every symbol its own line.
    """
    m = COMMITTED.search(value)
    if m:
        return m.group(0)
    return re.split(r"\s*[;·]\s*", value, maxsplit=1)[0].strip()


def blocked_lines(blocked: list[dict[str, Any]], decisions: list[dict[str, Any]],
                  target_session: str | None = None) -> list[str]:
    """One line per (rule, figure), naming every symbol it blocked and the number behind it.

    The rule is normalised by rule_label and the figure taken by block_figure, so the
    grouping does not depend on how the review happened to word either.

    REDISTRIBUTE candidates are the exception: the review names their rule in free text
    ('REDISTRIBUTE one direction per review', 'redistribute: one symbol, one direction;
    committed-add cap'), a new wording most days. They share one line, each symbol
    given the reason that applies to it: a sell proposed on it, or its own figure. A
    trim is named as a trim: policy says a partial trim is not an exit, and on 11-04
    "exit proposed" beside META read as if the position were being closed.

    A wash-sale window that a proposed loss sale WOULD open is the other exception. The
    review words it as a sentence ('If sold 2026-11-11 at a loss, buy block through
    2026-12-11'), which read as "each If sold ..." in the generic form. Those share one
    line per clearing date. The window a proposed stop-loss opens is also computed here
    from the decision record, because the review wrote it on 11-19 and not on 11-20 or
    11-23, with the same two stop-losses standing all three days.
    """
    sell_kind = {d.get("symbol"): d.get("kind") for d in decisions if d.get("side") == "sell"}
    groups: dict[tuple[str, str], list[str]] = {}
    no_recipient: list[str] = []
    wash: dict[str, list[str]] = {}
    for b in blocked:
        raw = str(b.get("value") or "n/a")
        figure = block_figure(raw)
        rule = str(b.get("rule") or "blocked")
        if "redistribute" in rule.lower():
            kind = sell_kind.get(b["symbol"])
            why = figure_ko(figure) if kind is None else (
                "일부 매도 제안" if kind == "trim" else "매도 제안")
            no_recipient.append(f"**{b['symbol']}** ({why})")
            continue
        clears = conditional_wash_clear(rule, raw)
        if clears:
            wash.setdefault(clears, []).append(b["symbol"])
            continue
        groups.setdefault((rule_label(rule), block_figure(raw)), []).append(b["symbol"])
    if target_session:
        clears = (dt.date.fromisoformat(target_session)
                  + dt.timedelta(days=WASH_CLEAR_DAYS)).isoformat()
        for d in decisions:
            if d.get("kind") != "stop-loss" or not d.get("symbol"):
                continue
            # Computed from the record, so it replaces whatever date the review wrote
            # for the same symbol rather than listing the symbol twice.
            for symbols in wash.values():
                if d["symbol"] in symbols:
                    symbols.remove(d["symbol"])
            wash.setdefault(clears, []).append(d["symbol"])
        wash = {k: v for k, v in wash.items() if v}
    out = []
    for (rule, figure), symbols in groups.items():
        names = ", ".join(f"**{s}**" for s in symbols)
        each = "각각 " if len(symbols) > 1 else ""
        out.append(f"보류({rule_ko(rule)}): {names} — {each}{figure_ko(figure)}.")
    if no_recipient:
        out.append("재배분 대상 없음: " + ", ".join(no_recipient) + ".")
    for clears, symbols in wash.items():
        names = ", ".join(f"**{s}**" for s in symbols)
        out.append(f"손절이 손실로 체결되면 wash-sale 보류: {names} — "
                   f"{long_date(clears)}부터 매수 재개.")
    return out


def conditional_wash_clear(rule: str, value: str) -> str | None:
    """The date buys may resume, for a wash-sale window a PROPOSED loss sale would open.

    None for anything else, including a window an actual past sale already opened
    (that one is a plain block and keeps the generic line). Takes 'clears YYYY-MM-DD'
    when the review states it, else the day after 'through YYYY-MM-DD'.
    """
    text = f"{rule} {value}".lower()
    if "wash" not in text or not ("proposed" in text or "if sold" in text):
        return None
    m = re.search(r"clears (\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m = re.search(r"through (\d{4}-\d{2}-\d{2})", text)
    if m:
        day = dt.date.fromisoformat(m.group(1)) + dt.timedelta(days=1)
        return day.isoformat()
    return None


def decision_section(rec: dict[str, Any]) -> list[str]:
    reqs = rec.get("decisionRequests") or []
    if not reqs:
        return []
    out = ["결정 필요:"]
    for r in reqs:
        readings = "; ".join(f"{SIDE_KO.get(str(x.get('side')), x.get('side'))}: {x.get('detail')}"
                             for x in r.get("readings") or [])
        out.append(f"• **{r['symbol']}** — {sentence(r.get('question'))} {sentence(readings)}")
    out.append("Jack이 정하기 전에는 어느 쪽이든 이 종목은 실행되지 않습니다. "
               "답은 기록되고 다시 묻지 않습니다.")
    return out


def departure_section(rec: dict[str, Any]) -> list[str]:
    """One line per distinct reading, naming every symbol it was applied to.

    The review writes one departure per symbol, and when it reads several symbols the
    same way it repeats the same threshold, action and reason for each — on three
    symbols that was most of a message, and pushed it past Discord's 2,000 characters.
    """
    deps = rec.get("departures") or []
    if not deps:
        return []
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for d in deps:
        key = tuple(sentence(d.get(k)).lower() for k in ("threshold", "did", "why"))
        groups.setdefault(key, []).append(d)  # type: ignore[arg-type]
    out = ["기준을 넘어 판단한 항목:"]
    for members in groups.values():
        first = members[0]
        names = ", ".join(f"**{d['symbol']}** ({str(d.get('observed') or 'n/a').rstrip('.')})"
                          for d in members)
        out.append(f"• {names} — {sentence(first.get('threshold'))} "
                   f"조치: {sentence(first.get('did'))} 이유: {sentence(first.get('why'))}")
    out.append("자동 실행 대상이 아닙니다. 어느 쪽이든 Jack이 확인합니다.")
    return out


def avg_text(value: Any) -> str:
    # Robinhood omits the average cost on some positions (transferred-in lots,
    # long-held ones); the record carries null, and 'n/a' is the honest rendering.
    return "평단 없음" if value is None else money(value)


def reconcile_entry(a: dict[str, Any]) -> str:
    sym, kind = a["symbol"], a.get("kind", "drift")
    if kind == "add":
        return f"추가 {sym} {shares(a['liveQty'])} @ {avg_text(a.get('liveAvg'))}"
    if kind == "remove":
        return f"삭제 {sym} (−{shares(a['sheetQty'])})"
    delta = Decimal(str(a["liveQty"])) - Decimal(str(a["sheetQty"]))
    moved = signed(Decimal(qty(abs(delta))) * (1 if delta >= 0 else -1)) + "주"
    if kind == "verify":
        return f"확인 {sym} {shares(a['liveQty'])} ({moved})"
    text = f"{sym} → {shares(a['liveQty'])} @ {avg_text(a.get('liveAvg'))} ({moved}"
    if a.get("liveAvg") is not None and a.get("sheetAvg") is not None:
        avg = (Decimal(str(a["liveAvg"])) - Decimal(str(a["sheetAvg"]))).quantize(Decimal("0.01"))
        if avg != 0:
            text += f", 평단 {signed(avg, '$')}"
    return text + ")"


def reconcile_section(recon: dict[str, Any] | None, recon_md: bool) -> list[str]:
    if recon is None:
        if recon_md:
            return ["시트 대조: 오늘 구조화된 결과가 없습니다. logs/reconcile/을 직접 보세요."]
        return ["시트 대조: 오늘 실행되지 않았습니다."]
    alerts = recon.get("alerts") or []
    if not alerts:
        return []
    moves, rest = pair_transfers(alerts)
    by_account: dict[str, list[str]] = {}
    for a in sorted(rest, key=lambda a: a["symbol"]):
        by_account.setdefault(a.get("account", "계좌 미상"), []).append(reconcile_entry(a))
    order = ACCOUNT_ORDER + sorted(k for k in by_account if k not in ACCOUNT_ORDER)
    out = ["시트 대조:",
           "Agentic 밖의 계좌입니다. 시트는 Observatory의 Robinhood 스냅샷을 따르므로 "
           "행을 고치지 말고 스냅샷을 새로 고치세요."]
    if moves:
        out.append("계좌 간 이동: " + "; ".join(moves) + ".")
    out += [f"{acct}: " + "; ".join(by_account[acct]) + "." for acct in order if acct in by_account]
    return out


def pair_transfers(alerts: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Split out positions that left one account and arrived whole in another.

    On 11-05 five positions moved from Mid-term to Long-term, and the reconcile listed
    each twice, as an 'add' in one account and a 'remove' in the other: ten entries for
    five facts. An add and a remove of the same symbol and the same share count, in
    different accounts, are one move.
    """
    removes = [a for a in alerts if a.get("kind") == "remove" and a.get("sheetQty") is not None]
    used: set[int] = set()
    moves: list[str] = []
    rest: list[dict[str, Any]] = []
    for a in sorted(alerts, key=lambda a: a["symbol"]):
        if a.get("kind") == "add" and a.get("liveQty") is not None:
            match = next((r for r in removes if id(r) not in used
                          and r["symbol"] == a["symbol"] and r.get("account") != a.get("account")
                          and Decimal(str(r["sheetQty"])) == Decimal(str(a["liveQty"]))), None)
            if match is not None:
                used.add(id(match))
                moves.append(f"{a['symbol']} {shares(a['liveQty'])} "
                             f"{match.get('account')} → {a.get('account')}")
                continue
        rest.append(a)
    return moves, [a for a in rest if id(a) not in used]


def unlogged_section(recon: dict[str, Any] | None) -> list[str]:
    findings = ((recon or {}).get("tradeLog") or {}).get("findings") or []
    if not findings:
        return []
    out = ["기록되지 않은 거래:"]
    for f in findings:
        side = str(f.get("side", "")).lower()
        out.append(f"• {short_date(f['date'])} {SIDE_KO.get(side, side)} **{f['symbol']}** "
                   f"{shares(f['shares'])} @ {money(f['price'])}")
    out.append("skills/log/SKILL.md에 따라 빠진 기록을 logs/trades/에 쓰고, 주문한 이유를 "
               "복원해 적으세요. 새로 주문하거나 취소할 것은 없습니다.")
    return out


RENDER_ERRORS = (KeyError, TypeError, AttributeError, InvalidOperation, ValueError)
# Sections that fell back to a note in this run; main() exits 1 when any did, so the
# run log still records a failure even though a message went out.
failed_sections: list[str] = []


def guarded(title: str, build: Any, *args: Any) -> list[str]:
    """One section, or a one-line note in its place when its record has an unexpected shape.

    On 2026-11-05 one reconcile alert with a null average cost raised in the reconcile
    section, and the whole Agentic message was replaced by a one-line failure notice:
    the proposals, which were fine, never reached Discord. A section now fails alone.
    """
    try:
        return build(*args)
    except RENDER_ERRORS as exc:
        print(f"digest_lines: {title} section: {exc!r}", file=sys.stderr)
        failed_sections.append(title)
        return [f"{title}: 오늘은 표시하지 못했습니다({type(exc).__name__}). "
                f"전체 리뷰는 logs/에 있습니다."]


def agentic_message(logs: Path, date: str) -> str:
    reviews = logs / "reviews"
    rec = _load(reviews / f"{date}.decisions.json")
    signals = _load(reviews / f"{date}.signals.json")
    standing = _load(reviews / f"{date}.standing.json")
    tax = _load(reviews / f"{date}.tax.json")
    recon = _load(logs / "reconcile" / f"{date}.json")

    sleeve = sleeve_line(_text(reviews / f"{date}.md"))
    target = (rec or {}).get("targetSession")
    head = sleeve or "Agentic 계좌 금액 없음(오늘 리뷰에 'Sleeve:' 줄이 없음)"
    opening = ["**Hermes Trader — Agentic**", f"{head}; {slots_clause(target, tax)}."]
    blocks: list[list[str]] = [opening]

    if rec is None:
        blocks.append(["오늘 결정 기록이 없어 제안을 나열할 수 없습니다. "
                       "logs/reviews/를 직접 보세요."])
    else:
        blocks += [b for b in (guarded("결정 요청", decision_section, rec),
                               guarded("기준을 넘어 판단한 항목", departure_section, rec)) if b]
        blocks.append(guarded("제안", proposal_lines, rec, signals, standing, tax))
    for section in (guarded("거래 기록", unlogged_section, recon),
                    guarded("시트 대조", reconcile_section, recon,
                            (logs / "reconcile" / f"{date}.md").exists())):
        if section:
            blocks.append(section)
    checks = guarded("점검", checks_section, recon)
    if checks:
        blocks.append(checks)
    shadow = _text(logs / "shadow" / "latest.txt")
    if shadow:
        blocks.append(["Shadow 점검:", shadow])
    briefing = _text(logs / "digest" / f"{date}.briefing.md")
    if briefing:
        blocks.append([briefing])
    blocks.append([CLOSER])
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


def checks_line(recon: dict[str, Any] | None) -> str | None:
    """One line naming the checks that passed, or None when neither did (or neither ran).

    A failing check is its own section above (Reconcile, Unlogged trades), because it
    asks Jack to change something. A passing one only needs to say that it ran: before
    2026-11-18 that took two lines of a separate Telegram message.
    """
    if recon is None:
        return None
    parts = []
    if not recon.get("alerts"):
        parts.append("시트가 Robinhood와 일치")
    trade_log = recon.get("tradeLog") or {}
    if not trade_log.get("findings"):
        n = trade_log.get("filledInWindow", 0)
        parts.append("거래 기록 점검 기간에 체결된 Agentic 주문 없음" if not n
                     else f"체결된 Agentic 주문 {n}건 모두 기록됨")
    return "점검: " + "; ".join(parts) + "." if parts else None


def checks_section(recon: dict[str, Any] | None) -> list[str]:
    line = checks_line(recon)
    return [line] if line else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    logs = args.repo / "logs"
    digest = logs / "digest"
    digest.mkdir(parents=True, exist_ok=True)
    rc = 0
    failed_sections.clear()
    for name, build in ((f"{args.date}.agentic.md", agentic_message),):
        try:
            text = build(logs, args.date)
        except RENDER_ERRORS as exc:
            # A record with a shape this script did not expect must not cost the day's
            # message: say what happened and where the full review is.
            print(f"digest_lines: {name}: {exc!r}", file=sys.stderr)
            text = (f"⚠️ {args.date} digest를 만들지 못했습니다({type(exc).__name__}). "
                    f"전체 리뷰는 logs/reviews/{args.date}.md에 있습니다.\n")
            rc = 1
        (digest / name).write_text(text)
    return 1 if failed_sections else rc


if __name__ == "__main__":
    sys.exit(main())
