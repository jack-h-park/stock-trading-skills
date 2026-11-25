"""shadow-check carries a session it could not verify until it resolves.

The defect this pins: a decision checked before its settled close was stored was
reported once as no-settled-row, then fell out of the message, and the reader took
the silence as resolved. These run the real script end to end with --quiet, the
way run-review.sh calls it, against a temporary repo and price database.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "shadow-check.py"


def _db(path: Path, rows: list[tuple[str, str, float]]) -> None:
    con = sqlite3.connect(path)
    con.execute("create table if not exists historical_prices"
                " (market text, ticker text, price_date text, close real)")
    con.executemany("insert into historical_prices values ('US', ?, ?, ?)", rows)
    con.commit()
    con.close()


def _record(repo: Path, date: str, price_as_of: str, decisions: list[tuple[str, float]]) -> None:
    reviews = repo / "logs" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"{date}.decisions.json").write_text(json.dumps({
        "date": date, "targetSession": date, "priceAsOf": price_as_of,
        "decisions": [{"symbol": s, "priceUsed": p, "autoEligible": False} for s, p in decisions],
    }))


def _run(repo: Path, db: Path) -> str:
    cmd = [sys.executable, str(SCRIPT), "--repo", str(repo), "--db", str(db), "--quiet"]
    out = subprocess.run(cmd,
                         capture_output=True, text=True, check=True)
    return out.stdout


def _day(offset: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


def _k(iso: str) -> str:
    """The date as the message prints it: '2026-11-23' -> '11월 23일'."""
    d = datetime.date.fromisoformat(iso)
    return f"{d.month}월 {d.day}일"


def test_an_unverified_session_is_reported_until_its_close_arrives_then_once_as_verified(tmp_path):
    db = tmp_path / "prices.db"
    _db(db, [])
    old, new = _day(-3), _day(-1)
    _record(tmp_path, old, old, [("MSFT", 500.0)])
    first = _run(tmp_path, db)
    assert f"! MSFT 확정 종가 없음: {_k(old)} 종가가 아직 저장되지 않음" in first

    # Next session: a newer record checks clean, the old one still has no close.
    _db(db, [("MSFT", new, 510.0)])
    _record(tmp_path, new, new, [("MSFT", 510.0)])
    second = _run(tmp_path, db)
    assert f"~ {_k(old)}: 아직 미검증 — MSFT 확정 종가 아직 없음" in second

    # The refresh stores the old close: reported once as verified ...
    _db(db, [("MSFT", old, 500.02)])
    third = _run(tmp_path, db)
    assert f"= {_k(old)}: 이제 검증됨 — 가격 1건 확정 종가와 일치" in third

    # ... and then the session leaves the message for good.
    assert _run(tmp_path, db) == ""


def test_a_carried_session_that_resolves_to_a_mismatch_says_so(tmp_path):
    db = tmp_path / "prices.db"
    _db(db, [])
    old, new = _day(-3), _day(-1)
    _record(tmp_path, old, old, [("MSFT", 509.71)])
    _run(tmp_path, db)

    _db(db, [("MSFT", new, 510.0), ("MSFT", old, 513.53)])
    _record(tmp_path, new, new, [("MSFT", 510.0)])
    out = _run(tmp_path, db)
    assert (f"! MSFT ({_k(old)} 기록) 가격 불일치: "
            "사용 가격 509.71, 확정 종가 513.53 (차이 -3.82)") in out
    assert "이제 검증됨" not in out


def test_a_session_that_never_gets_a_close_is_closed_once_after_the_carry_window(tmp_path):
    db = tmp_path / "prices.db"
    _db(db, [])
    old, new = _day(-30), _day(-1)
    _record(tmp_path, old, old, [("NVDA", 200.0)])
    _run(tmp_path, db)

    _db(db, [("MSFT", new, 510.0)])
    _record(tmp_path, new, new, [("MSFT", 510.0)])
    out = _run(tmp_path, db)
    assert f"x {_k(old)}: 끝내 검증 못 함" in out
    assert _run(tmp_path, db) == ""

    ledger = [json.loads(line) for line in
              (tmp_path / "logs" / "shadow" / "ledger.jsonl").read_text().splitlines()]
    assert any(e.get("abandoned") and e["date"] == old for e in ledger)


def test_a_clean_history_prints_nothing(tmp_path):
    db = tmp_path / "prices.db"
    day = _day(-1)
    _db(db, [("META", day, 751.66)])
    _record(tmp_path, day, day, [("META", 751.90)])
    assert _run(tmp_path, db) == ""


def test_a_record_from_earlier_today_is_never_the_one_checked(tmp_path):
    db = tmp_path / "prices.db"
    yesterday, today = _day(-1), _day(0)
    _db(db, [("MSFT", yesterday, 510.0)])
    _record(tmp_path, yesterday, yesterday, [("MSFT", 510.0)])
    _record(tmp_path, today, today, [("MSFT", 520.0)])  # a manual run before the scheduled one
    assert _run(tmp_path, db) == ""
    ledger = (tmp_path / "logs" / "shadow" / "ledger.jsonl").read_text().splitlines()
    assert [json.loads(line)["date"] for line in ledger] == [yesterday]


def test_the_full_report_is_korean_and_prices_print_without_float_noise(tmp_path):
    db = tmp_path / "prices.db"
    day, target = _day(-2), _day(-1)
    _db(db, [("AAPL", day, 349.0799865722656), ("VOO", day, 600.0)])
    reviews = tmp_path / "logs" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / f"{day}.decisions.json").write_text(json.dumps({
        "date": day, "targetSession": target, "priceAsOf": day,
        "decisions": [
            {"symbol": "AAPL", "priceUsed": 349.62, "autoEligible": False},
            {"symbol": "VOO", "priceUsed": 600.0, "autoEligible": True, "side": "buy",
             "notional": 100, "reason": "20일 고점 대비 −6.56%"},
        ],
        "departures": [{"symbol": "MSFT", "threshold": "기준 문구", "observed": "관측 문구",
                        "did": "한 일", "why": "이유"}],
    }))
    out = _run(tmp_path, db).splitlines()
    assert out[0] == (f"{_k(day)} 결정 기록 ({_k(target)} 실행 예정): "
                      "자동 실행 대상 1건, 점검 2건, 기준을 넘어 판단 1건")
    # Model-written departure text and reason are placed as they come.
    assert "  기준을 넘어 판단: MSFT 기준 문구 (관측 문구) → 한 일 — 이유" in out
    assert "  실행 예정: VOO 매수 $100 — 20일 고점 대비 −6.56%" in out
    assert "  ! AAPL 가격 불일치: 사용 가격 349.62, 확정 종가 349.08 (차이 0.54)" in out
    # The ledger keeps the machine values: English verdicts and the raw close.
    entry = json.loads((tmp_path / "logs" / "shadow" / "ledger.jsonl").read_text())
    aapl = next(f for f in entry["findings"] if f["symbol"] == "AAPL")
    assert aapl["verdict"] == "price-mismatch"
    assert aapl["settledClose"] == 349.0799865722656
