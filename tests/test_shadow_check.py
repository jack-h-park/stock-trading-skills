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


def test_an_unverified_session_is_reported_until_its_close_arrives_then_once_as_verified(tmp_path):
    db = tmp_path / "prices.db"
    _db(db, [])
    old, new = _day(-3), _day(-1)
    _record(tmp_path, old, old, [("MSFT", 500.0)])
    first = _run(tmp_path, db)
    assert "MSFT no-settled-row" in first

    # Next session: a newer record checks clean, the old one still has no close.
    _db(db, [("MSFT", new, 510.0)])
    _record(tmp_path, new, new, [("MSFT", 510.0)])
    second = _run(tmp_path, db)
    assert f"~ {old}: still unverified" in second

    # The refresh stores the old close: reported once as verified ...
    _db(db, [("MSFT", old, 500.02)])
    third = _run(tmp_path, db)
    assert f"= {old}: now verified" in third

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
    assert f"MSFT (from {old}) price-mismatch" in out
    assert "now verified" not in out


def test_a_session_that_never_gets_a_close_is_closed_once_after_the_carry_window(tmp_path):
    db = tmp_path / "prices.db"
    _db(db, [])
    old, new = _day(-30), _day(-1)
    _record(tmp_path, old, old, [("NVDA", 200.0)])
    _run(tmp_path, db)

    _db(db, [("MSFT", new, 510.0)])
    _record(tmp_path, new, new, [("MSFT", 510.0)])
    out = _run(tmp_path, db)
    assert f"x {old}: never verified" in out
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
