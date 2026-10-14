---
name: tax-desk
description: >
  Answer portfolio-tax questions across every brokerage from computed figures: wash-sale
  windows and what an order or planned sale would do to them, tax scenarios (sell the
  long-term lots now, wait for long-term, sell everything, harvest losses, US-only vs
  US+KR filing, shift the sale date), realized gains this year, whether a fill reached
  the Observatory, whether a broker download arrived, and the tax policy vs the computed
  year-to-date figures. Use for "wash sale", "tax lot", "short-term vs long-term",
  "harvest", "realized YTD", "planned sale", "세금", "양도세", "워시세일". Facts and
  scenario arithmetic only — never a buy, sell or hold verdict.
---

# Skill: tax-desk

**Code computes, you narrate.** Every number you report comes from the JSON of one of
the commands below, and the reply quotes that output's provenance lines. Never derive a
tax figure, a window or a term in your own reasoning, and never rebuild the engine from
the Observatory's TypeScript: that is the step most likely to produce a confident wrong
number.

Paths (resolve `$HOME` at run time):
- `REPO=$HOME/workspace/ai-assets/jackhpark-stock-trading-skills`
- `OBS=$HOME/workspace/code/core/jackhpark-stock-observatory`
- Observatory CLIs: `python3 $REPO/scripts/observatory_cli.py <name> [args]` (adds
  `--json` and the toolchain PATH; exit 3 = a refresh is rebuilding the DB — say so and
  retry later, do not read the DB meanwhile).

## Which command answers which question

| Question | Command |
|---|---|
| What wash-sale windows are open? Can I buy/sell X without washing a loss? | Read the newest `$REPO/logs/tax-calendar/<date>.json` (`.md` for the table). If it is not today's, or the owner asks for a fresh one: `python3 $REPO/scripts/tax_calendar.py --print message` |
| "I plan to sell X on D" | Write the intent to `$REPO/config/planned-sales.local.json` (shape: `config/planned-sales.example.json`), then rerun `tax_calendar.py`. Only on the owner's explicit statement |
| Tax if I sell now / wait for long-term / sell all / harvest / US-only vs US+KR / on another date | `observatory_cli.py tax:scenario --scenario lt-now\|wait-lt\|sell-all\|harvest\|filing-compare\|date-shift [...]` |
| Same, at today's live prices | Fetch `get_equity_quotes`, write `{"asOf": ..., "quotes": {TICKER: price}}` to a temp file, pass `--prices <file>` |
| What did I realize this year? | `observatory_cli.py tax:realized --since <YYYY-01-01>` |
| Did my sale show up in the Observatory? | `python3 $REPO/scripts/fill_reconcile.py --since <date>` |
| Did my statement upload arrive? | `observatory_cli.py sources:status` |
| Policy YTD vs computed YTD | Read-only: `$OBS/data/tax-policy.json` and the DB `meta` keys in `references/db-glossary.md` |
| Lot-level detail no CLI covers | Read-only SQL per `references/db-glossary.md` |
| Refresh the data | Only when the owner asks — see Refusals |

Arguments, output fields and exit codes: `references/cli-catalogue.md`.

## Reply shape

1. **Verdict line** — what changed, or the scenario's headline difference, in one sentence.
2. **Compact table** — the few rows that answer the question. Link the full JSON/MD file
   instead of pasting every lot.
3. **Provenance lines**, quoted from the output: DB build time, policy hash and mtime,
   price source and as-of, FX dates, credit mode, which sources were unavailable, and
   "Estimate under the stated inputs; not tax advice."
4. A `<<<KO>>>` block with a Korean copy of the verdict line and the table's labels only.
   Numbers, tickers and dates are not restated there.

Detail and an example: `references/presentation.md`. Something the owner must decide (a
sale plan, a policy difference to apply) goes to Discord; awareness goes to the trader
Telegram topic. Answer on the surface the owner asked on.

## Refusals

- **No verdicts.** No buy, sell or hold call, no outlook ranking, no "best" plan. A
  scenario's lowest estimate is "the lowest estimated tax under the stated inputs", with
  the inputs. Strategy proposals for the Agentic sleeve belong to `portfolio-review`, not
  here.
- **Never write `data/tax-policy.json`** or anything else in the Observatory. Report the
  policy value, the computed value and the difference, and point to the Observatory's
  `/tax-settings` page; the owner applies it there.
- **Refresh only when the owner asks**, with `$OBS/deploy/hermes/refresh-now.sh`. Say
  first that it rebuilds the database and republishes the Google Sheets. Exit 3 means one
  is already running. Scheduled refreshes stay on the cron.
- **Inbox filing stays on the laptop.** Do not file, move or push broker downloads; answer
  "did it arrive?" with `sources:status`.
- **Read-only Robinhood only.** Use the `robinhood` MCP server (read-only). Never call an
  order tool from this skill; an order goes through the `trade` skill and its
  review → confirm → place → log flow.
- If a source is unavailable, say which and what the answer therefore cannot see. Never
  fill the gap from memory or by recomputing.
