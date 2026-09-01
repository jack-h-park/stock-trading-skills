---
name: trade
description: >
  Place, review, or cancel a brokerage order through the active provider. Use whenever the
  user wants to buy/sell a security, simulate an order, or cancel an open order in the
  stock-trading-skills repo. Enforces the review→confirm→place→log flow and the guardrails.
---

# Skill: trade

Broker-agnostic order flow. Resolves the active provider via `providers/<broker>/adapter.md`
and calls only the abstract ops in `providers/_contract.md`.

## Before anything

1. Read `strategy/policy.md` (what is allowed) and `config/guardrails.md` (limits + confirm
   policy). Read the active provider's `capabilities.md`.
2. Default provider: **robinhood** unless the user names another.

## Recording a ruling

When Jack answers a **Decision needed** item from a review — a buy-vs-exit conflict
the rules do not resolve — write it to `config/rulings.json` before doing anything
else, under the key the review used (`<SYMBOL>:buy-vs-exit`), with `ruling`,
`ruledOn` (today) and `reason` in his words where you have them.

Write it even when he also tells you to place the order in the same breath, and
especially then: the order is one session, the ruling is every session after it.
The scheduled review reads this file before it asks, so a ruling that was given but
not written is a question he gets asked again, and the fractional-sell question was
asked on two consecutive days for exactly that reason.

Do not invent a ruling from a proposal he approved, or from silence. Approving a
trim is not a ruling on the conflict behind it — the ruling is what he says when
asked which reading wins. If you are not sure he was answering the question, leave
the store alone and say so.

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
   gone (the untracked backlog note in `logs/trades/`). The next scheduled review will hold the broker's filled
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
