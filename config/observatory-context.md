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
  the shell and hands the prompt a literal absolute one. Under `claude -p` that was forced —
  the job had `Read` but not `Bash`, so a `$VAR` or leading `~` could not be opened at all.
  The review now runs `codex exec`, which could expand them, but a resolved path still pins
  which file the prompt means. Do not reintroduce either form into a prompt.
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
| `washSaleWindows.windows[]` | US tickers sold at a loss in the last 30 days, in any account at any brokerage — see below |
| `health.issues[]` | Anything wrong with the data behind all of the above |
| `ingestedAt` | When these figures were built — typically the previous afternoon |

## `washSaleWindows` — a loss a BUY here would disallow

Added to schema version 1 as an additive field; an older summary simply lacks it, which is
not an error. Shape:

- `measuredOn` — the date the windows were measured against.
- `transactionsThrough` — the last date the Observatory has transactions for. A sale after it
  is not in the list, so an empty list means "none as of that date", not "none".
- `line` — one sentence summarising the open windows, or `null` when there are none.
- `windows[]`, one per ticker:
  - `ticker`, `blockedThrough` (last day a buy still falls inside a window),
    `clearFrom` (first day a buy is outside every window), `daysLeft` (0 on the last day)
  - `realizedLoss` — the loss in the open windows, USD, **negative**
  - `possiblyDisallowed` — the part of it already matched by a buy, USD, negative or 0;
    an estimate
  - `boughtSinceSale` — a buy has already landed inside the window after the sale
  - `soldIn` — the accounts the sale was in, masked
  - `buysInWindow[]` — the buys inside the window, before or after the sale

These are tickers sold at a loss in the last 30 days **across every account the owner
holds**, not just the three Robinhood accounts. A buy of one of them in ANY account —
including the Agentic account this agent trades — disallows that loss under IRC §1091.

How this relates to the wash-sale guardrail: these windows **do** block a BUY, but not
through this file. `scripts/tax_guard.py` reads the same summary in code, takes every window
still open today, and merges it into `buyBlocks` in `logs/reviews/<date>.tax.json` beside the
Robinhood accounts' own loss sales, tagged `source: "observatory"`. Job A blocks a BUY on any
symbol there before ranking (step 4a). That is a guardrail computed in code, the same as the
add cap. Nothing in this file becomes a gate, and the review never builds a block from what
it reads here.

**What the review must do with it:** if a BUY candidate on the ranked list appears in
`washSaleWindows.windows`, say so in **Flags**, naming the ticker, the `blockedThrough`
date and the `realizedLoss` at stake (and `possiblyDisallowed` when it is non-zero), e.g.
`XYZ: in a wash-sale window through 2026-11-08 — buying it disallows a −$140 loss realized
elsewhere`. This flag is required, and it does **not** count toward the two-or-three
observation limit below: it is the one item here that names money the BUY itself would
cost.

It is still a flag, not a gate. It does not remove the candidate, move it down the list, or
block it — "How to use it" below applies to this field exactly as to the others. In the
normal case 4a has already blocked the symbol and it never reaches the ranked list; the flag
is what is left when the tax file is missing or did not carry that window.

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
