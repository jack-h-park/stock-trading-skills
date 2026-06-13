# Trading policy

Broker-agnostic trading rules. The agent must read this before any order flow or review.
**This is a template — fill in the bracketed values before live use.**

## Objective

[e.g. long-only equity accumulation in a fixed universe; capital preservation first.]

## Universe

Only trade symbols on this list (anything else requires explicit user instruction):

- [TICKER] — [why it's in scope]
- [TICKER] — [...]

## Entry rules

- [e.g. only add on a > X% pullback from 20-day high]
- [...]

## Exit rules

- [e.g. trim when a position exceeds Y% of portfolio]
- [...]

## Prohibitions

- No shorting (also unsupported by Robinhood).
- No asset classes outside `providers/<broker>/capabilities.md`.
- No symbols outside the universe without explicit user instruction.
- No order that violates `config/guardrails.md`.

## Notes

Keep this file the single source of truth for *what* to trade. `config/guardrails.md`
governs *how much / whether to confirm*. Keep the two concerns separate.
