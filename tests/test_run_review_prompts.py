"""The prompt assignments in scripts/run-review.sh must survive `set -u`.

The prompts are double-quoted bash strings, so every literal dollar sign has to
be written `\\$`. One that is not is a parameter expansion: `$249.36` in an
example line reads as `$2` followed by "49.36", and under `set -u` the
assignment aborts the whole script. That happened on 2026-10-05 and 10-06 — the
reconcile example added to PROMPT_D carried `$249.36`, `$347.39` and `$366.54`,
and every run died between the merge and the digest, after jobs A/B/C had done
their work, with nothing delivered.

So this evaluates each assignment in bash, with `set -u` on and only the
variables the prompts are meant to expand defined. A new unescaped `$` fails
here instead of on the host at 13:30.

Run: python3 -m pytest tests/test_run_review_prompts.py
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run-review.sh"

# The variables the prompts are written to expand. Anything else is a mistake.
EXPANDED = {
    "TODAY": "2026-10-07",
    "SIGNALS_FILE": "logs/reviews/2026-10-07.signals.json",
    "OBSERVATORY_SUMMARY": "/tmp/briefing-summary.json",
    "BRIEFING_JSON": "/tmp/briefing.json",
    "STOCK_DATA_DIR": "/tmp/stock-management",
}

START = re.compile(r"^(PRICE_BASIS|CRITICAL_RO|PROMPT_[A-Z])=\"")


def assignments(text: str) -> dict[str, str]:
    """Each prompt-like assignment, from its opening line to its closing quote."""
    found: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = START.match(lines[i])
        if not m:
            i += 1
            continue
        block = [lines[i]]
        # A one-line assignment closes on its own line.
        j = i
        closed = len(lines[i]) > len(m.group(0)) and lines[i].endswith('"') and not lines[i].endswith('\\"')
        while not closed:
            j += 1
            block.append(lines[j])
            closed = lines[j].endswith('"') and not lines[j].endswith('\\"')
        found[m.group(1)] = "\n".join(block)
        i = j + 1
    return found


def test_every_prompt_is_found():
    names = set(assignments(SCRIPT.read_text()))
    assert {"PRICE_BASIS", "CRITICAL_RO", "PROMPT_A", "PROMPT_B", "PROMPT_C", "PROMPT_D"} <= names


def test_prompt_assignments_survive_set_u():
    blocks = assignments(SCRIPT.read_text())
    prelude = "set -u\n" + "".join(f"{k}={v!r}\n" for k, v in EXPANDED.items())
    # PRICE_BASIS and CRITICAL_RO are expanded inside the later prompts, so they
    # are evaluated first, in file order.
    body = "\n".join(blocks.values())
    proc = subprocess.run(
        ["bash", "-c", prelude + body + "\necho evaluated"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0 and "evaluated" in proc.stdout, proc.stderr


def test_the_reconcile_example_keeps_its_dollar_amounts():
    blocks = assignments(SCRIPT.read_text())
    prelude = "set -u\n" + "".join(f"{k}={v!r}\n" for k, v in EXPANDED.items())
    proc = subprocess.run(
        ["bash", "-c", prelude + "\n".join(blocks.values()) + '\nprintf "%s" "$PROMPT_D"'],
        capture_output=True,
        text=True,
    )
    assert "@ $249.36 (+1 sh, avg +$3.21)" in proc.stdout
