# Accounts (example)

The tracked docs, skills and adapters refer to accounts by placeholder. The real
identifiers live in `config/accounts.local.md`, which is gitignored — this repo
is public, and a full brokerage account number beside the broker's name and the
owner's identity is not something a git history should carry.

Copy this file to `config/accounts.local.md`, fill in the values, and `chmod 600` it.

| Placeholder | Real value | Role | Observatory label |
| --- | --- | --- | --- |
| `<AGENTIC_ACCOUNT>` | (fill in) | The only account the agent trades (`agentic_allowed: true`) | (optional) |
| `<LONGTERM_ACCOUNT>` | (fill in) | Reconciled against the holdings sheet, never traded | (optional) |
| `<MIDTERM_ACCOUNT>` | (fill in) | Reconciled against the holdings sheet, never traded | (optional) |
| `<US_HOLDINGS_SHEET_ID>` | (fill in) | Google Drive fileId of the US holdings master sheet | |

**Observatory label** (optional) is the name the Stock Portfolio Observatory gives the same
account — the value of its database's `account` column, an alias from the Observatory's own
gitignored account map. `scripts/fill_reconcile.py` uses it to match a Robinhood fill to the
Observatory's realized row. Left empty, it falls back to `Robinhood <role>` (for example
`Robinhood Long-term`) and the report says the label was assumed. A three-column table still
works; the column is read only when present.

Resolve a placeholder here before calling a broker tool. A skill that cannot read
the local file must stop and say so rather than guessing an account — guessing
one would place a real order against the wrong account.
