# Guardrails

Hard limits and confirmation policy. The `trade` skill must enforce every line here.

## Confirmation policy

- **Default: confirm before every live `place_order`.** Show the user the `review_order`
  result (price, est. cost, warnings) and wait for explicit approval.
- **Standing authorization (narrow auto-place):** the agent may place WITHOUT per-order
  confirmation **only** when ALL of these hold. Anything outside falls back to confirm:
  - side = `buy` (opening/adding a long)
  - asset_class = `equity`
  - symbol ∈ universe in `strategy/policy.md`
  - order notional ≤ **$100** (per-order cap below)
  - within the daily limits below (count and total)
  - order_type = `limit` (no auto market orders)
  - within allowed trading hours (regular session)
  - kill-switch not active
  - Sells, trims, and any exit are **always confirm** (never auto).

## Hard limits

| Limit | Value |
|---|---|
| Max notional per order | **$100** |
| Max orders per day | **3** |
| Max total notional per day | **$1,000** |
| Max position size (% of portfolio) | **20%** |
| Allowed order types | market, limit (auto-place: limit only) |
| Allowed trading hours | regular US session only (no extended hours) |

> Note: per-order cap $100 × 3 orders = $300 max actual daily spend; the $1,000 daily total
> is a hard ceiling that also covers any larger confirm-required orders.

## Kill switch

If the portfolio is down **> 5% intraday**, halt all new orders and notify the user.
Do not resume without explicit instruction.

## Always

- Run `review_order` before `place_order`, every time.
- Append every order/cancel to `logs/trades/` (see `skills/log/SKILL.md`).
- Never trade outside `strategy/policy.md`.
