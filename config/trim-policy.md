# Trim & Redistribute Policy

Runtime parameters for the TRIM and REDISTRIBUTE signals computed in the daily
portfolio review. Edit this file to change thresholds — the next cron run picks
up the new values automatically. No code changes required.

## TRIM signal

| Parameter | Value | Notes |
|-----------|-------|-------|
| Drawdown threshold | **10%** below recent high | If current price ≤ (recent high × 0.90), flag TRIM |
| Recent-high lookback | **20 trading days** | Same window as BUY signal; use `historicals interval=day` |
| Trim size | **50%** of current shares held | Fractional allowed — round down to **6 decimal places** |
| Minimum position value | **$150** | Skip TRIM if current market value < this (not worth the friction) |
| Applies to accounts | **Agentic only** (`<AGENTIC_ACCOUNT>`) | Same scope as all other automated signals |
| Applies to asset classes | Single names + ETFs (VOO, QQQM) | ETFs included — high drawdown here is unusual enough to warrant trimming |

> **Why fractional, not whole shares.** Entries are $100 notional and every universe symbol
> trades above $100/share, so positions are fractional by construction. A whole-share trim
> needs 2 shares held, which the 20% position cap forbids for 7 of the 8 universe symbols —
> at a $401 cap only NVDA (~$190/sh) can reach 2 shares at all. Rounding to whole shares
> therefore yielded 0 executable shares on essentially every real position, which is why the
> 2026-07-28 and 07-29 reviews both proposed a fractional sell and asked for a ruling.
>
> Robinhood accepts fractional quantities on all eight symbols (see
> `providers/robinhood/capabilities.md`), but only as `market` orders in the regular session.
> The post-close review therefore always produces a trim that executes on the next session.

### Relationship to existing exit rules (policy.md)

The TRIM signal is **momentum-based** (price vs recent high), distinct from the
existing exits which are **cost-basis-based** (price vs average cost):

- Stop-loss (single names, -8% from avg cost): close the whole position → still applies, unchanged
- Take-profit (+12% from avg cost): **trim, using the `Trim size` and `Minimum position value`
  rows of the table above** — 50% of shares held, skipped below $150
- TRIM (this policy, -10% from recent high): trim 50% → new, may trigger before or after the above

When multiple exit signals fire on the same symbol in the same review, apply the
**most aggressive** one — stop-loss closes the position and so overrides everything. A
take-profit and a TRIM are now the same 50%, so they never stack: sell that 50% once.

> **Why take-profit borrows this table's floor.** Its trigger is measured against **average
> cost**, and selling never changes average cost — so a position at +15% is still at +15%
> after a half-sale, and the signal fires again the next session, and the one after that.
> AMZN at \$230.81 would have gone \$115 → \$58 → \$29 → \$14, halving on an unchanged price.
> The \$150 floor is what ends it: one trim, then the remainder is too small to qualify.
> TRIM does not need this because its trigger is a **moving** 20-day high that resolves itself.
> Stop-loss does not need it because it closes the whole position, leaving nothing to re-fire.

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
- TRIM orders are **not counted against the buy limits** (5/day, 6/week) — they are a separate exit operation
- REDISTRIBUTE buys **are** subject to normal guardrails ($100/order cap, **daily and weekly** order counts, position cap) — they are ordinary buys and consume the same slots as a ranked BUY
- If REDISTRIBUTE allocation per symbol > $100, place as a confirm-required order (not auto)

## Suspend conditions

Do not compute or propose any TRIM signal if:

- Kill switch is active (portfolio down > 5% intraday)
- The position was added within the last **3 calendar days** (avoid whipsawing a fresh entry)
- The symbol already has a stop-loss or take-profit proposal in the same review (use the stronger signal)
