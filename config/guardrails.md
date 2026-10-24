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

> **Where this authorization does and does not reach.** Two paths act on it.
> The interactive agent, which holds `place_equity_order`, and
> `scripts/scheduled_orders.py` (the `agentic-execute` cron), which submits the
> review's `autoEligible` buys the next morning. The scheduled review itself still
> holds no order tool (`scripts/run-review.sh`). The scheduled path submits
> **only while Robinhood's trade approval is on**, so every order it sends waits
> for approval in the Robinhood app: it removes the session, not the approval. It
> is dry until `config/execution.local.json` switches it on. Its re-checks, and
> the two caps it trusts the review for, are in `docs/decisions.md` §21.
>
> **When the session-hours and price-freshness lines are judged.** The review
> runs after the close, so both are judged for the session the order will
> execute in, and `scripts/scheduled_orders.py` checks both again when it
> submits. When the close is over 24h old by then (a Monday after a Friday
> review), the submitter takes the line's other branch, "or a live quote": the
> live price from `review_equity_order` must still sit at least 5% below the
> review's 20-day high, or the order is skipped. Until 2026-10-22 the review read them as unmet at review time and
> marked no buy auto-eligible for ten sessions; that is why this says so.

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

## Wash-sale window (US tax)

A **BUY is blocked** while any of the three Robinhood accounts (Agentic, Long-term,
Mid-term) sold that symbol **at a loss in the last 30 days**; it clears on day 31 after
the sale. The review's own stop-losses start the same block. A proposed sell that would
realize a loss is **annotated** when the symbol was bought in any of those accounts in the
last 30 days, because that loss would be deferred. `scripts/tax_guard.py` computes both;
the review never derives them itself.

This is a guardrail, not a threshold: a departure cannot read past it.

> **Why.** A loss is disallowed for the year when the same security is bought within 30
> days before or after the loss sale in **any** of the taxpayer's accounts, and a broker's
> 1099-B usually adjusts only within one account. The Agentic sleeve buys mega-caps the
> Long-term account also holds, and its own rules make the pattern on their own: a
> stop-loss followed by a further drop raises a BUY on the same symbol. The cost of the
> block is at most 30 days of $100 dip-buys on one symbol. Accounts at other brokers are
> not visible to the review and are out of scope.
>
> Each exit also shows its lots' US holding period (short-term until a date, long-term,
> or mixed) — display only; no proposal waits for a lot to turn long-term. Korea taxes
> overseas gains at one rate whatever the holding period and has no wash-sale rule.

## Kill switch

If the portfolio is down **> 5% intraday**, halt all new orders and notify the user.
Do not resume without explicit instruction.

## Always

- Run `review_order` before `place_order`, every time.
- Append every order/cancel to `logs/trades/` (see `skills/log/SKILL.md`).
- Never trade outside `strategy/policy.md`.
