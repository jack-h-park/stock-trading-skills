# Holdings sheet — reconciliation source

The user maintains a manual master holdings sheet in Google Sheets. The agent reconciles
**Robinhood live positions** against this sheet and reports drift. The agent does **not**
write to the sheet (manual updates only, by decision).

## Sheet

- Title: `미국 주식 보유 현황 및 수익률`
- Google Drive fileId: `<US_HOLDINGS_SHEET_ID>`
- Tab: **`미실현수익 정리 (자동)`** — the one the observatory's `publish:us-sheet`
  step rewrites each refresh. The workbook's FIRST tab is `[양식] 미실현수익 정리`,
  a blank form, so a range with no tab name reads two rows of `#DIV/0!` and
  `#N/A` and looks like a sheet with no positions in it.
- Read via the Google Drive MCP (`read_file_content`). Read-only.

## Account mapping (sheet section → Robinhood account)

| Sheet "Account" value | Robinhood account_number | nickname |
|---|---|---|
| `Robinhood - Long-term` | <LONGTERM_ACCOUNT> | Long-term |
| `Robinhood - Mid-term` | <MIDTERM_ACCOUNT> | Mid-term |
| (not in sheet yet) | <AGENTIC_ACCOUNT> | Agentic (new, agentic trading) |

The sheet also tracks external brokerages (Chase, Fidelity, Merrill) — those have no API
and are out of scope for reconciliation (manual only).

The **Agentic** account is deliberately not sheet-reconciled: its sheet section is
agent-managed and expected to diverge, so diffing it would produce drift every day and a
warning that is always on is one that stops being read. It is not therefore unchecked — it is
the sole account covered by the trade-log check in `config/trade-log-check.md`, which holds
its filled orders against `logs/trades/`. Say both halves whenever you report the exclusion.

## Drift thresholds (what counts as an alert)

**ALERT (surface prominently):**
- A position present in Robinhood but missing from the sheet (or vice versa).
- Share quantity differs by **≥ 1 whole share** or **≥ 1%**, whichever is smaller.
- Average cost differs by **> 1%**.
- Quantity differs while average cost is **identical** → likely a sheet data-entry error;
  flag as "verify sheet entry".

**NOISE (list separately as low-priority, or omit):**
- Sub-share fractional quantity drift with ~unchanged average cost (dividend reinvestment).
- Average cost difference < ~$0.20 (rounding).

## Notes

- Robinhood `average_buy_price` already reflects partial sells — use it as the user's avg cost.
- Use `quantity` for holdings reconciliation (not `shares_available_for_sells`).
- The Agentic account is the only one the agent trades; its activity will naturally diverge
  from the sheet until the user adds an "Robinhood - Agentic" section (optional).
