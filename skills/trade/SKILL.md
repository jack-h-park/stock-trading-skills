---
name: trade
description: >
  Place, review, or cancel a brokerage order through the active provider. Use whenever the
  user wants to buy/sell a security, simulate an order, or cancel an open order in the
  trading-agent repo. Enforces the review→confirm→place→log flow and the guardrails.
---

# Skill: trade

Broker-agnostic order flow. Resolves the active provider via `providers/<broker>/adapter.md`
and calls only the abstract ops in `providers/_contract.md`.

## Before anything

1. Read `strategy/policy.md` (what is allowed) and `config/guardrails.md` (limits + confirm
   policy). Read the active provider's `capabilities.md`.
2. Default provider: **robinhood** unless the user names another.

## Place / sell flow (must follow in order)

1. **Resolve.** If the symbol is ambiguous, `search`. Confirm the account via `accounts()`
   if more than one.
2. **Validate against policy.** Reject if the symbol is outside the universe, the side/asset
   class is unsupported, or any guardrail (notional, daily count, hours) would be violated.
   Tell the user *which* rule blocked it.
3. **Review (mandatory).** Call `review_order(spec)`. Surface price, estimated cost, and all
   pre-trade warnings.
4. **Confirm.** Unless the order falls fully inside the standing-authorization set in
   `config/guardrails.md`, ask the user for explicit approval. Money moving is irreversible.
5. **Place.** Call `place_order(spec)`.
6. **Log.** Immediately append to `logs/trades/YYYY-MM-DD.md` per `skills/log/SKILL.md`,
   including the review warnings and the resulting order id. Do it in this session, before
   you move on: the fill is recoverable from the broker forever, the *reason* only exists
   here and only right now. Eleven trades were once placed without it and their rationale is
   gone (`logs/trades/README.md`). The next scheduled review will hold the broker's filled
   orders against this directory and report anything missing
   (`config/trade-log-check.md`) — so a skipped log surfaces within a day, but it surfaces
   as a gap that can no longer be filled honestly.

## Cancel flow

1. `orders()` to find the open order id.
2. `cancel_order(id)`.
3. Log the cancel.

## Never

- `place_order` without a successful `review_order` in the same flow.
- Trade outside `strategy/policy.md` or beyond `config/guardrails.md`.
- Short, or trade an asset class the provider's `capabilities.md` excludes.
