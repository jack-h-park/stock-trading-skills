#!/usr/bin/env python3
"""How long each of today's proposals has stood, from the decision records.

The review re-proposes the same exits every session until Jack acts or the
condition clears: META, MSFT and NVDA take-profits ran from late September through
October with the message giving no sign of whether a line was news or a reminder.
The digest now says 'standing since <date>, N sessions'. Counting is done here,
not by the model: a model counting a list it was handed reads the list's first
entry as the start date, which the briefing already got wrong once.

A proposal is identified by (symbol, kind). It stands from the earliest review in
an unbroken run of reviews that all proposed it. Only days that HAVE a decision
record count; a day the review failed or did not run neither extends nor breaks
the run, because nothing was decided that day.

usage: standing.py --repo PATH --date YYYY-MM-DD [--out FILE]
Exit 0 always; with no record for --date there is nothing to write.

Python 3.9 compatible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def records(reviews: Path, upto: str) -> list[tuple[str, set[tuple[str, str]]]]:
    """(date, {(symbol, kind)}) for every decision record dated on or before `upto`."""
    out = []
    for p in sorted(reviews.glob("*.decisions.json")):
        date = p.name[:10]
        if date > upto:
            continue
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        keys = {(d.get("symbol"), d.get("kind")) for d in doc.get("decisions") or []
                if d.get("symbol") and d.get("kind")}
        out.append((date, keys))
    return out


def standing(history: list[tuple[str, set[tuple[str, str]]]], date: str) -> dict[str, Any]:
    by_date = dict(history)
    if date not in by_date:
        return {}
    dates = [d for d, _ in history]
    i = dates.index(date)
    result = {}
    for key in sorted(by_date[date]):
        j = i
        while j > 0 and key in history[j - 1][1]:
            j -= 1
        entry: dict[str, Any] = {"since": dates[j], "sessions": i - j + 1}
        if j == 0:
            # The run reaches the oldest record there is, so it may be older still.
            entry["atLeast"] = True
        result[f"{key[0]}:{key[1]}"] = entry
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    reviews = Path(args.repo) / "logs" / "reviews"
    result = standing(records(reviews, args.date), args.date)
    if not result:
        return 0
    text = json.dumps(result, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
