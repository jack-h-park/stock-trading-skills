---
name: reconcile
description: >
  Compare Robinhood live positions against the user's manual Google Sheet holdings and report
  drift (mismatches, missing/new positions, suspected sheet typos), and compare filled Agentic
  orders against logs/trades/ to report any trade that was never logged. Read-only on every
  side — never writes to the sheet, the trade log, or trades. Use on demand or as a step in
  the scheduled review.
---

# Skill: reconcile

Detects and reports drift where two records of the same facts disagree. Reports only — Jack
makes the corrections himself (by decision). Two independent checks:

- **A. Sheet drift** — **Robinhood (source of truth)** vs the user's **manual Google Sheet**.
  Covers the Long-term and Mid-term accounts.
- **B. Trade-log check** — filled **Agentic** orders at the broker vs **`logs/trades/`**.
  Covers the Agentic account only.

They are separate on purpose: A is about positions Jack maintains by hand, B is about the
decision journal for the account the agent trades. An account can be in scope for one and not
the other, and the Agentic account is exactly that case.

## Inputs

- `config/holdings-sheet.md` — sheet fileId, account mapping, and drift thresholds.
- `config/trade-log-check.md` — scope, lookback, floor date, and matching rule for check B.
- Robinhood positions per mapped account via `get_equity_positions`.
- Filled Agentic orders via `get_equity_orders`.
- Sheet contents via the Google Drive MCP `read_file_content`.
- `logs/trades/*.md` via Glob/Read.

## Steps — A. Sheet drift

1. Read `config/holdings-sheet.md` for the fileId, sheet-section → account mapping, and
   thresholds.
2. For each mapped Robinhood account, call `get_equity_positions` (paginate if a cursor is
   returned) → {symbol: (quantity, average_buy_price)}.
3. Read the sheet; parse the rows for each mapped sheet section → {symbol: (quantity, avg)}.
4. For each account, diff Robinhood vs sheet per symbol:
   - In Robinhood, not in sheet → **MISSING FROM SHEET** (new position).
   - In sheet, not in Robinhood → **STALE IN SHEET** (sold/transferred?).
   - Both present: compare quantity and average cost against the thresholds in the config.
   - Quantity differs but average cost identical → **VERIFY SHEET ENTRY** (likely typo).
5. Classify each finding as **ALERT** or **NOISE** per the config thresholds.

## Steps — B. Trade-log check

1. Read `config/trade-log-check.md` for the scope, lookback, floor date and matching rule.
2. Call `get_equity_orders` for account `<AGENTIC_ACCOUNT>` with `state=filled` and
   `placed_agent=agentic`. Keep fills inside the lookback window and on or after the floor
   date; discard everything before the floor without comment.
3. For each remaining fill, look for `logs/trades/<fill-date>.md` and check whether it names
   that order id — or, failing an id, the same symbol and side on that date.
4. A fill with no matching entry is a **finding**. If you cannot tell, report it: the config
   fixes the tie-break toward reporting.

## Output

Write/append a section to `logs/reconcile/YYYY-MM-DD.md` (front matter per `skills/log/SKILL.md`,
plus `type: reconcile`). Structure:
- **Alerts** — table per account: symbol | sheet | Robinhood | issue.
- **Noise (low priority)** — one-line list (DRIP/rounding).
- **Clean** — one line confirming the rest matched, with counts.
- **`## Trade-log check`** — the findings from B, per the alert wording in
  `config/trade-log-check.md`. Always write this heading, including when the window is clean;
  a section that appears only on failure is one whose absence means nothing.

Lead with a one-line verdict covering both checks: `N sheet alerts across M accounts,
K unlogged Agentic trades` (or `no drift` / `all logged` for the clean halves).

When noting that the Agentic account was not sheet-reconciled, say **which** reconcile it is
out of scope for and that the trade-log check covers it. "Agentic is out of scope for this
reconcile" full stop reads as though the one account this repo trades is the one account
nothing checks.

## Hard rules

- **Read-only.** Never call any order tool; never write to the Google Sheet; never write or
  amend anything in `logs/trades/`. Reporting a missing log entry is the job. Writing one
  would fabricate a decision record — see `docs/decisions.md#15`.
- Robinhood is authoritative; the sheet is what may be stale. Describe drift as "sheet
  missing X" / "sheet shows stale Y", not as a Robinhood problem.
- Do not propose trades from a reconciliation — this is bookkeeping, not strategy.
- For check B the broker is authoritative and the journal is what is incomplete. Describe a
  finding as "no log entry for this fill", never as an unexpected or unauthorised trade.
