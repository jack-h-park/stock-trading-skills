# Observatory context — whole-portfolio reference

The Stock Portfolio Observatory ingests the brokers' own exports for **every** account and
brokerage the user holds — Robinhood Long-term, Mid-term and Agentic, plus Chase, Fidelity,
Merrill, and the Korean holdings — and converts them into one base currency with an explicit
FX snapshot. At the end of each refresh it publishes a small summary file.

This agent trades a ~$1,000 sleeve. That sleeve is a rounding error against the whole
portfolio, and the agent has never been able to see the rest of it. This file is how it does:
enough to notice that a name it is about to add to is already large somewhere else, or that
trimming one has a tax consequence the sleeve's own P/L cannot show.

**Reference only, in the strongest sense.** Nothing here is a rule, a gate, or an input to
any ranked list. See "How to use it" below — that boundary is the whole point of this file.

## Source

- Path: `$STOCK_BRIEFING_SUMMARY_PATH`, default
  `~/workspace/data/stock-management/outputs/stock-portfolio-observatory/briefing-summary.json`
- **Those two forms are for a human reading this file.** `run-review.sh` resolves the path in
  the shell and hands the prompt a literal absolute one, because the scheduled job has `Read`
  but not `Bash` — an agent given `$VAR` or a leading `~` has no way to open the file except to
  shell out, and that call is denied. Do not reintroduce either form into a prompt.
- Written by the Observatory's `pnpm refresh` (weekdays 14:00), and by `pnpm summary` on demand.
- **Read-only, and read as a file.** Never call the Observatory app. It is a LAN service that
  may be down; the review must not depend on it.
- Absent or unreadable is a normal state, not an error. Say so in Flags and carry on.

## Schema contract

The document carries a `schemaVersion`. **Refuse a document whose `schemaVersion` is greater
than 1** — report it in Flags rather than guessing at fields whose meaning may have changed.
An older version may be read as-is.

## Fields worth reading

| Field | What it is |
|---|---|
| `portfolio.marketValue` / `unrealizedGl` / `unrealizedGlPct` | Whole portfolio, in `portfolio.baseCurrency` (KRW) |
| `portfolio.pricedPositionCount` / `unpricedPositionCount` | How much of the portfolio those figures actually cover |
| `byMarket[]` | Per-market totals, native and base, each with its `currency` |
| `positions.concentration.top1Share` / `top5Share` / `top10Share` | Whole-portfolio concentration, % |
| `positions.concentration.overCap[]` | Positions past the Observatory's own per-position cap, with `currentPct` |
| `positions.taxSensitive[]` | Positions where selling has a tax consequence, with `reason` |
| `positions.topGainers[]` / `topLosers[]` | Biggest movers across all accounts |
| `health.issues[]` | Anything wrong with the data behind all of the above |
| `ingestedAt` | When these figures were built — typically the previous afternoon |

## ⚠️ The percentages are not the guardrail percentages

`positions.concentration.*` and `overCap[].currentPct` are shares of the **entire portfolio
across every account and brokerage, denominated in KRW**.

`config/guardrails.md`'s "Max position size (% of portfolio) — 20%" is a share of the
**Agentic account alone** (~$1,000, so ~$200).

These have completely different denominators and are never comparable. A name at 12% of the
whole portfolio says nothing about whether it is near the 20% Agentic cap, and vice versa.
Never substitute one for the other, and never let an Observatory percentage satisfy or
trigger a guardrail check.

Note also that the Observatory's totals **include** the Agentic sleeve. Its figures are
already whole-portfolio; do not add the sleeve to them.

## How to use it

Read it, filter it to what is relevant, and put it in **Flags** as a note to the user.

Relevant means: it concerns a symbol in the universe in `strategy/policy.md`, or a symbol
with an open position in the Agentic account. The whole portfolio holds names this agent
will never trade; surfacing those is noise that trains the reader to skip the section.

**It must never:**

- change the ranked BUY list, or the order of it — `strategy/policy.md` is explicit that
  the deterministic ranking takes no qualitative input, and this is qualitative input
- suppress, skip, or veto a signal
- become a `do-not-add` list, or any other rule expressed as data
- satisfy, replace, or trigger any check in `config/guardrails.md`

If the context genuinely argues against a top-ranked name, that is exactly the case
`strategy/policy.md` already covers: surface it as a flag and let the user decide. The
ranked list stands as the default.

Two or three observations is plenty. This is context for a person, not a second strategy.
