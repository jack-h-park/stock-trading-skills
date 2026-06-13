# Guardrails

Hard limits and confirmation policy. The `trade` skill must enforce every line here.
**Template — set the bracketed values before live use.**

## Confirmation policy

- **Default: confirm before every live `place_order`.** Show the user the `review_order`
  result (price, est. cost, warnings) and wait for explicit approval.
- **Standing authorization (opt-in only):** the agent may place without per-order
  confirmation **only** when ALL of these hold:
  - [e.g. side = buy, asset_class = equity]
  - [e.g. symbol ∈ universe in strategy/policy.md]
  - [e.g. order notional ≤ $X]
  - [e.g. within daily limits below]
  Anything outside this set always falls back to confirm-before-place.

## Hard limits

| Limit | Value |
|---|---|
| Max notional per order | [$X] |
| Max orders per day | [N] |
| Max total notional per day | [$X] |
| Max position size (% of portfolio) | [Y%] |
| Allowed order types | market, limit |
| Allowed trading hours | [e.g. regular session only] |

## Kill switch

If [condition — e.g. portfolio down > Z% intraday], halt all new orders and notify the
user. Do not resume without explicit instruction.

## Always

- Run `review_order` before `place_order`, every time.
- Append every order/cancel to `logs/trades/` (see `skills/log/SKILL.md`).
- Never trade outside `strategy/policy.md`.
