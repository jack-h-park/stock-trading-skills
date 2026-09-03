# Trading policy

Broker-agnostic trading rules. The agent must read this before any order flow or review.

## Scope — which account

**Trading** happens **only** in the Robinhood **Agentic** cash account `<AGENTIC_ACCOUNT>`
(`agentic_allowed: true`). BUY/TRIM proposals and order execution are restricted to
this account.

**Review** covers all of Jack's accounts: Robinhood Long-term (`<MIDTERM_ACCOUNT>`),
Robinhood Mid-term (`<LONGTERM_ACCOUNT>`), and external brokerages (Chase, Fidelity, Merrill)
tracked in the holdings sheet. These accounts are read-only — the agent surfaces
informational flags (notable drawdowns or gains) but never proposes or executes
orders against them. Jack acts on those flags manually.

Starting state (2026-06-13): **$1,000 cash, no positions.** This is a small rule-based
swing/accumulation sleeve, separate from the user's main long-term portfolio.

## Objective

Rule-based swing accumulation of **high-conviction large-cap tech + core broad ETFs** that
the user already holds with conviction elsewhere. Capital preservation first; buy weakness
in quality names in $100 increments, take profits on strength, cut single-name losers.
Deliberately **excludes** the speculative / leveraged names the user trades manually — those
are unsuitable for an automated agent.

## Universe

Only trade these symbols. Anything else requires explicit user instruction.

**Mega-cap tech (single names):**
- AAPL — core holding across the user's accounts
- MSFT — core holding (currently weak → accumulation candidate)
- GOOGL — core holding, long-term conviction
- AMZN — core holding
- NVDA — core holding, top conviction name
- META — core holding (currently weak → accumulation candidate)

**Core broad ETFs (lower volatility):**
- VOO — S&P 500
- QQQM — Nasdaq 100

## Entry rules

- **Buy-the-dip accumulation.** Place a **$100 notional market buy** (regular hours) when a
  universe symbol trades **≥ 5% below its trailing 20-day high** (use `historicals` to
  compute the 20-day high). Notional orders must be `type=market` — see the order-mechanics
  note below.
- **Pace:** at most **1 add per symbol per calendar week**, where a week runs **Monday
  through Sunday** in US market time.
- **Averaging down:** at most **$200 of added capital per symbol at any one time**. Each
  $100 add commits $100; selling a fraction of a position releases that same fraction of the
  committed amount; a full exit releases all of it. Written as a running figure per symbol:

  ```
  add of $X              ->  committed += X
  sale of fraction f     ->  committed  = round(committed × (1 − f), cents)
  may add $X only while     committed + X <= 200
  ```

  **Round to cents at every step — this is part of the rule, not an implementation
  detail.** Fractional share counts do not divide evenly. The 2026-08-12 take-profit sold
  0.424941 of 0.849883 shares, which is 49.9999412%, not half; carried at full precision the
  committed figure lands at $100.00011766 and the next $100 add is refused by a hundredth of
  a cent. Rounded, it is $100.00 and the add is allowed, which is what a half-sale is
  supposed to buy back.

  This is a pacing rule in its own right — it is no longer what keeps a position under the
  20% cap in `config/guardrails.md`, since 2 × $100 stopped reaching that cap once the
  account grew.

  **A partial trim is not an exit.** It returns capital in proportion to what was sold, and
  nothing more. The rule used to read "2 total $100 adds before an exit", which never said
  which of those a half-sale was — and the review flagged its own guess about it every
  session rather than following a rule. Both of the readings it was choosing between are
  wrong in a way this one is not:

  - Treating a partial trim as an exit would reset the allowance to zero. Take-profit only
    fires at **+12% above average cost**, so the reset would arrive only when a position is
    WINNING, while the rule exists to pace a position that is LOSING. One temporary rally
    would clear the counter, and a name could absorb $200 more after every bounce.
  - Treating it as nothing would make the allowance a LIFETIME budget: two adds and that
    symbol is closed to new capital until it is fully liquidated, however much was later
    sold at a profit and however long ago.

  As a standing budget instead, the exposure bound is the same $200 at every moment, and the
  capital a profitable trim released can go back to work. In practice trims and take-profits
  are both 50%, so the rule reads: **a half-sale buys back one add.**

  It also states what a count never could. REDISTRIBUTE buys are not $100 — the current
  session proposes $65 each — and "is a $65 buy one add?" had no answer. Here it commits $65.
- Respect the daily **and weekly** order counts and the confirmation policy in
  `config/guardrails.md`.

## Exit rules

- **Take-profit:** **trim** a position when it reaches **+12%** from average cost. Size and the
  minimum position value are the same two parameters TRIM uses — see `config/trim-policy.md`,
  which owns both numbers. Not a close: half is taken, half is left to run. The floor is not
  optional here, since this trigger is measured against average cost and selling does not move
  average cost — without it the same position is halved every session at an unchanged price.
- **Stop-loss (single names only):** close when a single stock falls **−8%** from average
  cost. **ETFs (VOO, QQQM) have no hard stop** — hold through drawdowns.
- **Trim & redistribute:** if a position falls **≥ 10%** below its 20-day high, trim it by
  50% and redistribute proceeds to outperforming positions. All parameters (threshold,
  lookback, trim size, redistribute logic, suspend conditions) are in `config/trim-policy.md`.
- All exits (sells) are **always confirm-before-place** — never auto (see guardrails).

## Prioritization (deterministic — when signals exceed the order cap)

When more symbols signal than the order caps allow (`config/guardrails.md`), select
which to act on by these rules **in order, with no discretionary judgment**. The same inputs
must always produce the same ranked list:

1. **Rank by drawdown depth, deepest first** (most below the 20-day high → highest priority).
2. **Tie-break (equal drawdown to 2 decimal places): alphabetical by symbol.**
3. Take the top N where **N = min(remaining slots today, remaining slots this week)**. Both
   counts include REDISTRIBUTE buys, which are ordinary buys against the caps.
4. Symbols already at the position cap, already added this week, or otherwise blocked by a
   guardrail are removed *before* ranking (they were never eligible).

Do **not** reorder by "conviction", news, or any qualitative read. Conviction/context
belongs in the **Flags** section of the review as a *note to the user*, never as a change to
the ranked proposal. If context warrants skipping a top-ranked name, surface it as a flag and
let the user decide — the deterministic list stands as the default.

## Holding horizon

Days to a few weeks (swing). Not intraday; not indefinite buy-and-hold.

## One symbol, one direction per review

A symbol may not be bought and sold in the same review. When a BUY signal and any
exit signal (TRIM, take-profit, stop-loss) fire on the same symbol, **the review
reads the situation, acts on one side, and records the other as a departure** —
naming which threshold it read past and why, per "Departing from a threshold"
below. The item is not auto-eligible either way.

This started out as a question for Jack, keyed per symbol and stored in
`config/rulings.json`. That was the wrong shape and it was wrong for a reason
worth keeping: the overlap is not a fact about GOOGL, it is a fact about the
thresholds, so asking per symbol asks the same question once per universe name
and gets eight answers to one question. Worse, each answer wants to be a number,
and the numbers interact — raising TRIM to 30% to settle it would have killed TRIM
for every single name, because the −8% stop-loss reaches first on all of them.

Ruling still applies where the rules genuinely cannot anticipate the situation and
the agent should not decide alone: a pending acquisition, a halt, an earnings gap
that makes the 20-day high meaningless. Those are per-instance and stay in
`config/rulings.json`. A threshold overlap is not one of them.

The thresholds overlap by construction: entry fires at **≥5%** below the 20-day
high and TRIM at **≥10%** below it, so every TRIM candidate is also a BUY
candidate. On 2026-08-31 GOOGL sat at −11.75% and the review carried both readings
in one message — listed as a blocked BUY, proposed as a TRIM. Nothing resolved
that; the $200 committed-add cap happened to block the buy. With the cap empty the
review would have proposed buying $100 of GOOGL and selling $101.85 of it on the
same morning.

**Why a ruling rather than a default.** Picking one — say, the exit, because it
reduces exposure — reads as principled and would have settled this in a line. But
it is a trading decision dressed as a tie-break, and the rules genuinely do not
determine it: the entry rule sees a name worth adding to, the exit rule sees a
name worth cutting, and both are working as designed. The system should say so
rather than resolve it quietly and be right by accident. This is also the one
place in a review where a human answer carries information the rules do not
already contain, which is exactly where the attention belongs.

There is precedent: the 2026-07-28 and 07-29 reviews both proposed a fractional
sell and asked for a ruling, and the answer became the `Trim size` rule in
`config/trim-policy.md`. What follows is that, made repeatable.

### How a ruling is asked for, and why only once

**Asking every session is worse than guessing.** The fractional-sell question was
asked on two consecutive days before it was settled, and a conflict that persists
— GOOGL can sit below its 20-day high for weeks — would otherwise produce the same
request every afternoon until it resolves itself, which is how a channel stops
being read.

So a ruling is keyed, stored and reused:

- **Key:** `<symbol>:<buy-vs-exit>`. The review looks in `config/rulings.json`
  before asking. A key already answered is applied silently and named in the
  report, not re-asked.
- **Asked once**, in the Agentic message, under its own heading, stating both
  readings and their numbers. Jack answers in the interactive session; the agent
  writes the ruling to the store with its date and a one-line reason.
- **Until it is answered, nothing on that symbol executes automatically** — it is
  not auto-eligible under `config/guardrails.md`, on either side. Both readings
  still appear in the report, so a manual decision is always available.

### When a ruling should stop being a ruling

If the same conflict is answered the same way **three times across different
symbols**, the review says so and proposes the wording for a policy rule instead
of asking a fourth time. A question that always gets the same answer is a rule
nobody has written down yet, and the store is a waiting room, not a home. Rulings
that graduate move into this file or `config/trim-policy.md`; the store keeps only
what is still genuinely open.

## Departing from a threshold

Every number in this file is a **default, not a boundary** — except the hard
limits in `config/guardrails.md`, which are neither and cannot be departed from at
all. The review may read a situation differently from what a threshold says, and
place the item accordingly, **provided it says which threshold it is departing
from, what the number was, and why.** Every departure is recorded.

**Why defaults rather than fixed rules.** The thresholds have to disagree with
each other eventually, because they measure the same thing for opposite purposes:
entry fires at ≥5% below the 20-day high, TRIM at ≥10% below the same high. Each
time that surfaced, the fix on offer was another number — and the numbers interact
in ways nobody sees until an incident. Raising TRIM to 30% was reasonable on its
face and would have made TRIM dead for every single name in the universe, because
the −8% stop-loss from average cost fires first on all of them, by 5 to 18% of
price. Nobody predicted that; it took working the arithmetic out afterwards.

A number is also a poor container for the intent behind it. "In a market-wide
drawdown, a conviction name still near cost reads as an entry" is the actual
position; 30% is a lossy encoding of it that happens to be right for one symbol
and wrong for the ETFs.

### What a departure may and may not touch

**May** — the interpretation of a signal: whether a drawdown is an entry or an
exit, whether a take-profit fires now or waits, how a name ranks.

**May not** — anything in `config/guardrails.md`: order notional, orders per day
and week, the position cap, the committed-add cap, the kill switch, regular hours,
the universe. Those are not opinions about the market; they bound how wrong a
judgement can be, and a judgement that can widen its own bounds is not bounded.
A departure that would need one of them to move is not a departure — it is a
proposal to change the guardrail, and it goes to Jack as that.

### What a departure costs

A departed item is **never auto-eligible.** The standing authorization in
`config/guardrails.md` covers what the rules fully determine; a departure is by
definition not that, so it goes to Jack with its reasoning to read. Autonomy is
bought by rules being right, not by judgement being unexamined.

### The record is the point

Each departure is written to the day's `logs/reviews/<date>.decisions.json` under
`departures`, and carried into the shadow ledger. That is the whole mechanism for
learning which numbers are wrong: a threshold departed from repeatedly, in the
same direction, for the same reason, is a threshold set to the wrong value — and
the ledger will say so with dates and instances rather than from argument. A
threshold never departed from is one that is working.

Departures do not accumulate into permission. Ten departures on TRIM do not make
the eleventh automatic; they make the case for editing the number in this file,
which is a change Jack makes.

## Prohibitions

- No shorting (also unsupported by Robinhood).
- No leveraged ETFs (TQQQ, QLD, SSO, etc.) — even though the user holds them manually.
- No speculative / small-cap names (quantum, space, nuclear, meme, single-name crypto
  proxies, etc.).
- No options (initially; revisit only by explicit decision).
- No symbols outside the universe without explicit user instruction.
- No order that violates `config/guardrails.md`.

## Order mechanics — notional market orders (verified 2026-06-13)

Verified via `review_equity_order` on the Agentic account: **$100 notional buys are
supported, but only as `type=market` in regular hours** (Robinhood restricts notional /
fractional orders to market + regular session; limit orders need a whole-share quantity).
Since every universe symbol trades > $100/share, entries are placed as **$100 notional
market orders**. This is acceptable only because the universe is restricted to ultra-liquid
mega-caps/ETFs with ~0.1% bid/ask spreads (negligible slippage). Never auto-place a market
order on an illiquid symbol.

Implication: the agent can only trade during regular US market hours. A scheduled
`portfolio-review` may run after close (read-only), but any resulting buy executes next
regular session.

## Notes

This file is the single source of truth for *what* to trade. `config/guardrails.md` governs
*how much / whether to confirm*. Keep the two concerns separate.
