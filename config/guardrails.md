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
  - order_type = `market` with `dollar_amount` (notional) — see note below
  - within regular US session hours (notional/fractional reject outside regular hours)
  - symbol is an ultra-liquid universe name (tight spread) per `strategy/policy.md`
  - kill-switch not active
  - Sells, trims, and any exit are **always confirm** (never auto).

> **Why market, not limit:** Robinhood notional ($-amount) and fractional orders are only
> accepted as `type=market` in regular hours; limit orders require a whole/known share
> quantity. Every universe symbol trades > $100/share, so a $100 order *must* be a notional
> market order. This is safe only because the universe is restricted to ultra-liquid
> mega-caps/ETFs with ~0.1% spreads. Do NOT auto-place market orders on anything illiquid.

## Hard limits

| Limit | Value |
|---|---|
| Max notional per order | **$100** |
| Max orders per day | **5** |
| Max total notional per day | **$1,000** |
| Max position size (% of portfolio) | **20%** |
| Allowed order types | notional market (auto); limit allowed only with confirm |
| Allowed trading hours | regular US session only (notional/fractional require it) |

> Note: per-order cap $100 × 5 orders = $500 max actual daily spend; the $1,000 daily total
> is a hard ceiling that also covers any larger confirm-required orders.
>
> This count is also the ceiling on how many orders the standing authorization above can
> place without per-order confirmation, so raising it raises unconfirmed daily exposure by
> $100 per added slot.

## Kill switch

If the portfolio is down **> 5% intraday**, halt all new orders and notify the user.
Do not resume without explicit instruction.

## Always

- Run `review_order` before `place_order`, every time.
- Append every order/cancel to `logs/trades/` (see `skills/log/SKILL.md`).
- Never trade outside `strategy/policy.md`.
