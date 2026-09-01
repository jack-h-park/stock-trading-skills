#!/usr/bin/env python3
"""Flag a delivered deadline that had already expired when it was written.

The review runs post-close. The morning briefing does not: it publishes at 08:00 PT,
mid-session, so "decide before the close" is honest when the briefing says it and
expired by the time the afternoon review repeats it. On 2026-09-01 the PANW act-now
went out at 13:41 PT reading "decide before the bell" — 41 minutes after the 13:00
bell, on a session the stock fell 5.2% during. Jack was asked to make a decision
whose window had shut before the message arrived.

That is fixed in PROMPT_D, which now rewrites a stale deadline instead of carrying
it. This exists because it is the second fix for the same fault — flagged on 08-31
as a wording inconsistency, delivered again on 09-01 as an impossible instruction —
and a prompt rule cannot tell you whether it held. This can: it reads the message
that was actually written and says whether the fix took.

It does not gate delivery. A message with a stale deadline is still worth sending;
what must not happen is nobody noticing it went out.

usage: check-digest-deadlines.py <digest.md> [...]
exit 0 always.
"""
import re
import sys
from pathlib import Path

# Deadlines that mean "the session happening right now" and so cannot survive a
# post-close message. Bare by design — a qualifier is what makes them legitimate.
SAME_SESSION = re.compile(
    r"\b(?:before|by|ahead of)\s+(?:the\s+)?(?:closing\s+)?"
    r"(?:bell|close|open)\b|\bdecide\s+today\b|\bbefore\s+today'?s\s+\w+",
    re.IGNORECASE,
)

# A future session named just before the phrase makes it answerable: "before
# tomorrow's close", "before Wednesday's bell", "before the Sept 2 open".
#
# Every alternative is anchored, and months require a day number. The first draft
# used `(jan|...|dec)\w*` and matched the "dec" in **decide** — so the sentence
# "decide before the bell" qualified itself as a December deadline and the checker
# reported the 2026-09-01 message clean. A prefix list without boundaries will
# always find a word it did not mean; "may" and "march" are ordinary English too,
# which is why a bare month name is not enough on its own.
FUTURE_QUALIFIER = re.compile(
    r"\btomorrow\b"
    r"|\bnext\b"
    r"|\bfollowing\s+(?:session|day|open|close|week)\b"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?\s+\d{1,2}\b",
    re.IGNORECASE,
)
LOOKBEHIND = 60  # characters; enough for "before Wednesday's" and its lead-in


def findings(text):
    out = []
    for m in SAME_SESSION.finditer(text):
        window = text[max(0, m.start() - LOOKBEHIND):m.start()]
        if FUTURE_QUALIFIER.search(window):
            continue
        line = text[:m.start()].count("\n") + 1
        snippet = text[max(0, m.start() - 70):m.end() + 20].replace("\n", " ").strip()
        out.append((line, m.group(0).strip(), snippet))
    return out


def main(argv):
    hits = 0
    for arg in argv:
        path = Path(arg)
        if not path.exists():
            continue
        for line, phrase, snippet in findings(path.read_text(encoding="utf-8")):
            hits += 1
            print("%s:%d expired deadline %r — this message is written after the "
                  "close, so a same-session deadline had already passed: ...%s..."
                  % (path.name, line, phrase, snippet))
    if hits:
        print("PROMPT_D is supposed to rewrite these (see 'ACT-NOW OVERRIDE' in "
              "scripts/run-review.sh). A hit here means the prompt fix did not hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
