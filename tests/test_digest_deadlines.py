#!/usr/bin/env python3
"""Does the checker catch the message that actually went out, and only that kind?"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "c", Path(__file__).resolve().parent.parent / "scripts" / "check-digest-deadlines.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

# Verbatim from logs/digest/2026-09-01.agentic.md, delivered 13:41 PT — 41 minutes
# after the 13:00 bell it told Jack to decide before.
SHIPPED = ("PANW is up 93% in mid-term and reports Q4 after tonight's close — "
           "trim partial or hold through the print, decide before the bell.")

EXPIRED = [
    SHIPPED,
    "Another item — decide today.",
    "A third — act by the close.",
    "Decide on the position ahead of the bell.",
    "Resolve this before today's close.",
]

# Answerable when the message lands, so not a finding.
FINE = [
    "Trim NVDA or hold through tomorrow's close — decide before tomorrow's bell.",
    "Hold into Friday's Jackson Hole; decide before Friday's open.",
    "PANW reports after the Sept 2 close — decide before the Sept 2 bell.",
    "Revisit at the next open.",
    "Nothing here needs a decision.",
    # "decide" is not December, "may" is not May, "march" is not March. The first
    # draft matched month PREFIXES and cleared the shipped message on the word
    # "decide" itself.
    "You may decide to march on; there is no deadline.",
]


def main():
    bad = 0
    for text in EXPIRED:
        if not c.findings(text):
            print("MISS (should flag): %s" % text); bad += 1
    for text in FINE:
        hits = c.findings(text)
        if hits:
            print("FALSE POSITIVE: %s -> %r" % (text, [h[1] for h in hits])); bad += 1
    print("expired flagged: %d/%d | clean passed: %d/%d"
          % (sum(1 for t in EXPIRED if c.findings(t)), len(EXPIRED),
             sum(1 for t in FINE if not c.findings(t)), len(FINE)))
    print("FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
