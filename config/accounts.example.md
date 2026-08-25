# Accounts (example)

The tracked docs, skills and adapters refer to accounts by placeholder. The real
identifiers live in `config/accounts.local.md`, which is gitignored — this repo
is public, and a full brokerage account number beside the broker's name and the
owner's identity is not something a git history should carry.

Copy this file to `config/accounts.local.md`, fill in the values, and `chmod 600` it.

| Placeholder | Real value | Role |
| --- | --- | --- |
| `<AGENTIC_ACCOUNT>` | (fill in) | The only account the agent trades (`agentic_allowed: true`) |
| `<LONGTERM_ACCOUNT>` | (fill in) | Reconciled against the holdings sheet, never traded |
| `<MIDTERM_ACCOUNT>` | (fill in) | Reconciled against the holdings sheet, never traded |
| `<US_HOLDINGS_SHEET_ID>` | (fill in) | Google Drive fileId of the US holdings master sheet |

Resolve a placeholder here before calling a broker tool. A skill that cannot read
the local file must stop and say so rather than guessing an account — guessing
one would place a real order against the wrong account.
