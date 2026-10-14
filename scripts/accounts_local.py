#!/usr/bin/env python3
"""Resolve account roles to real account numbers from config/accounts.local.md.

The tracked repo names accounts only by role (Agentic, Long-term, Mid-term) or by
placeholder (`<AGENTIC_ACCOUNT>`, ...). The real numbers live in the gitignored
config/accounts.local.md, whose table format is config/accounts.example.md. This
module is the one parser of that table: tax_guard.py reads every role from it, and
mcp-robinhood-proxy.py --agentic-orders reads the Agentic number to decide whether
an order call may pass.

Callers that guard an order must fail closed: a missing file or a missing row
returns nothing, never a guess.

Python 3.9 compatible (runs under the ops host's system python3 with the proxy).
"""

from __future__ import annotations

import re
from pathlib import Path

ROLES = {"<AGENTIC_ACCOUNT>": "Agentic", "<LONGTERM_ACCOUNT>": "Long-term",
         "<MIDTERM_ACCOUNT>": "Mid-term"}

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "accounts.local.md"


def accounts(local_md: str) -> dict[str, str]:
    """{role: account number} from the config/accounts.local.md table."""
    out = {}
    for placeholder, role in ROLES.items():
        m = re.search(r"^\|\s*`?" + re.escape(placeholder) + r"`?\s*\|\s*([0-9]+)\s*\|",
                      local_md, re.M)
        if m:
            out[role] = m.group(1)
    return out


def account_for(role: str, path: Path | None = None) -> str | None:
    """The account number for one role, or None when the file or its row is missing."""
    try:
        text = (path or DEFAULT_PATH).read_text()
    except OSError:
        return None
    return accounts(text).get(role)
