#!/usr/bin/env python3
"""Account roles from the gitignored config/accounts.local.md, and their Observatory labels.

The tracked repo names accounts only by role (Agentic, Long-term, Mid-term). The real
numbers live in config/accounts.local.md, which tax_guard.accounts() already parses.
The Observatory names the same accounts by a free-text label (an alias from its own
gitignored account map), so matching a broker fill to an Observatory row needs a
role -> label map. That map is the optional fourth column of the same table:

  | Placeholder | Real value | Role | Observatory label |
  | `<AGENTIC_ACCOUNT>` | 123... | ... | Robinhood Agentic |

The column is optional so existing three-column files keep working; a role with no
label falls back to `Robinhood <role>`, and the report says which labels were
assumed rather than configured.

Python 3.9 compatible.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tax_guard import ROLES, accounts  # noqa: E402,F401 — re-exported for callers

UNSET = {"", "(fill in)", "(optional)", "-", "—"}


def observatory_labels(local_md: str) -> dict[str, dict[str, object]]:
    """{role: {"label": str, "configured": bool}} for every role in ROLES."""
    out: dict[str, dict[str, object]] = {}
    for placeholder, role in ROLES.items():
        label = None
        for line in local_md.splitlines():
            cells = [c.strip().strip("`").strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] == placeholder:
                if len(cells) >= 4 and cells[3] not in UNSET:
                    label = cells[3]
                break
        out[role] = ({"label": label, "configured": True} if label
                     else {"label": f"Robinhood {role}", "configured": False})
    return out


def read_local(repo: Path) -> str:
    """The local accounts file, or "" when it is absent (callers then report no accounts)."""
    p = repo / "config" / "accounts.local.md"
    try:
        return p.read_text()
    except OSError:
        return ""

