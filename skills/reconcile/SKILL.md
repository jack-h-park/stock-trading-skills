---
name: reconcile
description: >
  Compare Robinhood live positions against the user's manual Google Sheet holdings and report
  drift (mismatches, missing/new positions, suspected sheet typos). Read-only on both sides —
  never writes to the sheet or trades. Use on demand or as a step in the scheduled review.
---

# Skill: reconcile

Detects and reports drift between **Robinhood (source of truth)** and the user's **manual
Google Sheet**. Reports only — the user updates the sheet themselves (by decision).

## Inputs

- `config/holdings-sheet.md` — sheet fileId, account mapping, and drift thresholds.
- Robinhood positions per mapped account via `get_equity_positions`.
- Sheet contents via the Google Drive MCP `read_file_content`.

## Steps

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

## Output

Write/append a section to `logs/reconcile/YYYY-MM-DD.md` (front matter per `skills/log/SKILL.md`,
plus `type: reconcile`). Structure:
- **Alerts** — table per account: symbol | sheet | Robinhood | issue.
- **Noise (low priority)** — one-line list (DRIP/rounding).
- **Clean** — one line confirming the rest matched, with counts.

Lead with a one-line verdict: `N alerts across M accounts` (or `no drift`).

## Hard rules

- **Read-only.** Never call any order tool; never write to the Google Sheet.
- Robinhood is authoritative; the sheet is what may be stale. Describe drift as "sheet
  missing X" / "sheet shows stale Y", not as a Robinhood problem.
- Do not propose trades from a reconciliation — this is bookkeeping, not strategy.
