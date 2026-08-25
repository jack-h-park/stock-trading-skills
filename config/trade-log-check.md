# Trade-log check — parameters

The scheduled review compares **filled Agentic orders at the broker** against
**`logs/trades/`** and reports any fill with no log entry. Read-only on both
sides; it writes a finding, never a log entry.

This exists because the two paths that touch this account have very different
guarantees. `scripts/run-review.sh` always logs, because the script makes it a
step. Orders are placed in interactive sessions, where the
"review → confirm → place → log" contract in `skills/trade/SKILL.md` is a written
instruction and nothing enforces it — and between 2026-06-23 and 2026-08-13
eleven filled orders produced no entry at all (the backlog note kept with the logs (`logs/trades/README.md`, untracked)). A
contract nobody can skip is better than a contract nobody remembers, and the
only place it can be made unskippable is after the fact.

## Scope

| Parameter | Value |
|---|---|
| Account | Robinhood Agentic `<AGENTIC_ACCOUNT>` **only** |
| Order filter | `state=filled`, `placed_agent=agentic` |
| Lookback | 30 calendar days from the run date |
| Floor date | **2026-08-24** — fills before this are never alerted on |
| Log location | `logs/trades/YYYY-MM-DD.md`, matched on the order's fill date |

Only the Agentic account is in scope, because it is the only account this repo
trades (`docs/decisions.md#3`). The other Robinhood accounts are Jack's own
manual activity and have no log contract to violate.

## The floor date

The floor is the day the check was installed. The eleven pre-floor fills are real
and unlogged, and no log entry for them is coming — they are documented once in
the backlog note kept with the logs (`logs/trades/README.md`, untracked) instead. Without a floor the check would report the same
eleven every afternoon forever, and a warning that is always on is a warning that
stops being read. Do not move the floor forward to silence a *new* miss; the
correct fix for a new miss is to write the entry.

## Matching rule

A fill is **covered** when `logs/trades/<fill-date>.md` exists and names the
order id **in full**, or names the same symbol and side on that date. Prefer the
order id — symbol+side is the fallback for an entry written before the id was
known, and it is genuinely weaker: four of the eleven orders in
the backlog note kept with the logs (`logs/trades/README.md`, untracked) were placed in one session, two of them sharing the id
prefix `6a7d3761`, so neither an abbreviated id nor symbol+side separates every
real case. Never match on a prefix.

Ambiguity resolves toward reporting: if the check cannot tell whether a fill is
covered, it reports it. A false alert costs Jack ten seconds; a missed one
restores the silence this check exists to break.

## Alert wording

Report under a `## Trade-log check` heading in `logs/reconcile/YYYY-MM-DD.md`:

- Lead with the verdict: `N filled Agentic orders with no log entry` or
  `all filled Agentic orders logged (M in window)`.
- Per finding: fill date, symbol, side, quantity, price, order id.
- Say what the fix is — **write the missing entry per `skills/log/SKILL.md`**,
  reconstructing the rationale from the session where the order was placed. Do
  not describe it as a broker problem or a position discrepancy; the broker is
  right and the journal is incomplete.
- When the window is clean, one line. It belongs in the read-only digest, not the
  actionable one.
