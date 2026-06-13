# Trading policy

Broker-agnostic trading rules. The agent must read this before any order flow or review.

## Scope — which account

Trading happens **only** in the Robinhood **Agentic** cash account `<AGENTIC_ACCOUNT>`
(`agentic_allowed: true`). The user's other accounts (Mid-term `<MIDTERM_ACCOUNT>`,
Long-term `<LONGTERM_ACCOUNT>`, and external brokerages tracked in the holdings sheet) are
**reference only** — the agent must never attempt to trade them.

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
- **Pace:** at most **1 add per symbol per calendar week**.
- **Averaging down:** at most **2 total $100 adds per symbol** before an exit — this also
  keeps each position within the 20% (≈$200) cap in `config/guardrails.md`.
- Respect all daily limits and the confirmation policy in `config/guardrails.md`.

## Exit rules

- **Take-profit:** trim/close a position when it reaches **+12%** from average cost.
- **Stop-loss (single names only):** close when a single stock falls **−8%** from average
  cost. **ETFs (VOO, QQQM) have no hard stop** — hold through drawdowns.
- All exits (sells) are **always confirm-before-place** — never auto (see guardrails).

## Holding horizon

Days to a few weeks (swing). Not intraday; not indefinite buy-and-hold.

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
