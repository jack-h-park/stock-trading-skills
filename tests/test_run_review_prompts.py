"""The prompt assignments in scripts/run-review.sh must survive `set -u`.

The prompts are double-quoted bash strings, so every literal dollar sign has to
be written `\\$`. One that is not is a parameter expansion: `$100.00` in an
example line reads as `$1` followed by "00.00", and under `set -u` the
assignment aborts the whole script. That happened on 2026-10-05 and 10-06 — the
reconcile example added to PROMPT_D carried three unescaped dollar amounts,
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
    "STANDING_FILE": "logs/reviews/2026-10-07.standing.json",
    "TAX_FILE": "logs/reviews/2026-10-07.tax.json",
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


def _expanded(name: str) -> str:
    blocks = assignments(SCRIPT.read_text())
    prelude = "set -u\n" + "".join(f"{k}={v!r}\n" for k, v in EXPANDED.items())
    proc = subprocess.run(
        ["bash", "-c", prelude + "\n".join(blocks.values()) + f'\nprintf "%s" "${name}"'],
        capture_output=True,
        text=True,
    )
    return proc.stdout


def test_the_digest_prompt_keeps_its_dollar_sign_and_writes_fragments_only():
    prompt = _expanded("PROMPT_D")
    assert "'US accounts: $X across N accounts'" in prompt
    assert "logs/digest/2026-10-07.briefing.md" in prompt
    assert "logs/digest/2026-10-07.reference-head.md" in prompt
    assert "Do NOT write logs/digest/2026-10-07.agentic.md or logs/digest/2026-10-07.md" in prompt


def test_the_reconcile_prompt_asks_for_the_record_the_digest_reads():
    prompt = _expanded("PROMPT_B")
    assert "logs/reconcile/2026-10-07.json" in prompt
    for key in ('"alerts"', '"liveQty"', '"sheetAvg"', '"tradeLog"', '"filledInWindow"'):
        assert key in prompt


def test_wash_sale_windows_are_flagged_not_gated():
    """Job A must flag a ranked BUY inside an Observatory wash-sale window, and must
    not turn that window into a block: only the tax file under 4a blocks a BUY.
    """
    blocks = assignments(SCRIPT.read_text())
    prelude = "set -u\n" + "".join(f"{k}={v!r}\n" for k, v in EXPANDED.items())
    proc = subprocess.run(
        ["bash", "-c", prelude + "\n".join(blocks.values()) + '\nprintf "%s" "$PROMPT_A"'],
        capture_output=True,
        text=True,
    )
    prompt = proc.stdout
    assert "washSaleWindows.windows" in prompt
    for field in ("blockedThrough", "realizedLoss"):
        assert field in prompt
    assert "does not count toward the 3" in prompt
    assert "does not remove, re-rank or block the candidate" in prompt
    assert "only logs/reviews/2026-10-07.tax.json under 4a can block" in prompt
    # The standing rule this guidance sits under is unchanged.
    assert "must NOT change the ranked BUY list or its order" in prompt

    context = (REPO / "config" / "observatory-context.md").read_text()
    assert "`washSaleWindows.windows[]`" in context
    assert "It is still a flag, not a gate." in context


def test_job_a_blocks_on_observatory_windows_through_the_tax_file():
    """The cross-brokerage block reaches job A only through the tax file, and the
    script hands tax_guard.py the same summary path the prompts read."""
    text = SCRIPT.read_text()
    assert '--observatory "$OBSERVATORY_SUMMARY"' in text
    blocks = assignments(text)
    prelude = "set -u\n" + "".join(f"{k}={v!r}\n" for k, v in EXPANDED.items())
    proc = subprocess.run(
        ["bash", "-c", prelude + "\n".join(blocks.values()) + '\nprintf "%s" "$PROMPT_A"'],
        capture_output=True,
        text=True,
    )
    prompt = proc.stdout
    assert "one with source 'observatory' names the soldIn accounts" in prompt
    assert "observatory.status is not 'ok'" in prompt
    assert "observatory.stale is true" in prompt
    assert "(−$140); clears Nov 9" in prompt
