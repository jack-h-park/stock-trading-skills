# Trim & Redistribute Policy

Runtime parameters for the TRIM and REDISTRIBUTE signals computed in the daily
portfolio review. Edit this file to change thresholds — the next cron run picks
up the new values automatically. No code changes required.

## TRIM signal

| Parameter | Value | Notes |
|-----------|-------|-------|
| Drawdown threshold | **10%** below recent high | If current price ≤ (recent high × 0.90), flag TRIM |
| Recent-high lookback | **20 trading days** | Same window as BUY signal; use `historicals interval=day` |
| Trim size | **50%** of current shares held | Round down to whole shares |
| Minimum position value | **$150** | Skip TRIM if current market value < this (not worth the friction) |
| Applies to accounts | **Agentic only** (`<AGENTIC_ACCOUNT>`) | Same scope as all other automated signals |
| Applies to asset classes | Single names + ETFs (VOO, QQQM) | ETFs included — high drawdown here is unusual enough to warrant trimming |

### Relationship to existing exit rules (policy.md)

The TRIM signal is **momentum-based** (price vs recent high), distinct from the
existing exits which are **cost-basis-based** (price vs average cost):

- Stop-loss (single names, -8% from avg cost): close the whole position → still applies, unchanged
- Take-profit (+12% from avg cost): trim/close → still applies, unchanged
- TRIM (this policy, -10% from recent high): trim 50% → new, may trigger before or after the above

When multiple exit signals fire on the same symbol in the same review, apply the
**most aggressive** one (i.e. stop-loss or take-profit override the 50% trim).

## REDISTRIBUTE signal

Proceeds from TRIM orders are redistributed to outperforming positions the same
session (or next regular session if after hours).

| Parameter | Value | Notes |
|-----------|-------|-------|
| Outperformance metric | Total return since purchase vs portfolio average | `(current price − avg cost) / avg cost` for each position; compare to mean |
| Eligible recipients | Positions with return > portfolio average AND within the universe | Must be a symbol in `strategy/policy.md` universe |
| Allocation method | Equal split among eligible recipients | Round each allocation to whole dollars; remainder held as cash |
| Minimum allocation per recipient | **$50** | If split < $50, reduce recipient count (drop lowest outperformer first) |
| Fallback — no outperformers | Hold proceeds as cash | Do not force-allocate; surface in digest as "proceeds held as cash" |
| Fallback — no positions yet | Apply to top BUY signal candidates | Use the same ranked BUY list from the main review |

## Guardrail interaction

- TRIM orders are **always confirm-before-place** — same as all other sells (per `config/guardrails.md`)
- TRIM orders are **exempt from the $100/order cap** — a 50% trim of a position will exceed $100; the cap applies to buys only
- TRIM orders are **not counted against the 5 orders/day buy limit** — they are a separate exit operation
- REDISTRIBUTE buys **are** subject to normal guardrails ($100/order cap, daily order count, position cap)
- If REDISTRIBUTE allocation per symbol > $100, place as a confirm-required order (not auto)

## Suspend conditions

Do not compute or propose any TRIM signal if:

- Kill switch is active (portfolio down > 5% intraday)
- The position was added within the last **3 calendar days** (avoid whipsawing a fresh entry)
- The symbol already has a stop-loss or take-profit proposal in the same review (use the stronger signal)
