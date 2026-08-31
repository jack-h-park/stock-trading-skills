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
  - **no unresolved buy-vs-exit conflict on the symbol.** When both fire, neither
    side is auto-eligible until Jack has ruled, and the ruling is stored in
    `config/rulings.json` so it is asked once rather than every session —
    see "One symbol, one direction per review" in `strategy/policy.md`
  - **the price the decision used is a settled close under 24h old, or a live quote.**
    Prices are read from a table that is written by a scheduled refresh and re-checked
    by nothing. On 2026-08-28 it held an intraday quote labelled as that day's close
    (MSFT 509.71 against a settled 513.53), and on 2026-08-26 it held a figure frozen
    three days by a refresh that kept failing. Both were visible only in hindsight.
    Confirm-before-place tolerates a stale input because a person sees the number;
    auto-place does not, so the freshness that was implicit becomes a precondition.
  - Sells, trims, and any exit are **always confirm** (never auto).

> **Where this authorization does and does not reach.** It is written for the
> interactive agent, which holds `place_equity_order`. The scheduled review
> (`scripts/run-review.sh`) does not: its `--allowedTools` whitelist grants read
> tools only, deliberately, so a cron job physically cannot trade. So the standing
> authorization has never fired from the scheduled path — every proposal it produces
> waits for Jack to open a session, including the ones that are wholly determined by
> the rules above. That gap is the subject of `docs/decisions.md` §16; nothing here
> grants the scheduled path anything.

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
| Max orders per week | **6** (Mon–Sun) |
| Max total notional per day | **$1,000** |
| Max position size (% of portfolio) | **20%** |
| Allowed order types | notional market (auto); limit allowed only with confirm |
| Allowed trading hours | regular US session only (notional/fractional require it) |

> Note: per-order cap $100 × 5 orders = $500 max actual daily spend, and $600 in a week;
> the $1,000 daily total is a hard ceiling that also covers any larger confirm-required
> orders.
>
> These counts are also the ceiling on how many orders the standing authorization above can
> place without per-order confirmation, so raising either raises unconfirmed exposure by
> $100 per added slot.
>
> **Why a weekly count at all.** `strategy/policy.md` allows 1 add per symbol per week over
> an 8-symbol universe, so the week was already bounded at 8 — but only implicitly, and every
> week of real trading so far put its entire allowance on a single day (3 on 06-23, 2 on
> 07-07, 2 on 07-23). At 5/day one heavy morning can take most of the week's room and leave
> nothing for a deeper drop on Thursday. 6 sits above every week observed so far, so it
> bounds the runaway case without touching normal operation.

## Kill switch

If the portfolio is down **> 5% intraday**, halt all new orders and notify the user.
Do not resume without explicit instruction.

## Always

- Run `review_order` before `place_order`, every time.
- Append every order/cancel to `logs/trades/` (see `skills/log/SKILL.md`).
- Never trade outside `strategy/policy.md`.
