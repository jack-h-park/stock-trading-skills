---
name: analyst-facts
description: >
  Report analyst ratings, price targets, last price and recent earnings beats/misses for
  named tickers as plain facts with source timestamps, sorted only by keys the owner
  names. Use for "what do analysts say about X", "price targets for A, B, C", "did X beat
  earnings", "sort these by target vs price", "애널리스트", "목표가". Never a buy, sell
  or hold verdict, a score, or an outlook ranking.
---

# Skill: analyst-facts

**Code computes, you narrate.** Run the script and report what it returns:

```
python3 $HOME/workspace/ai-assets/jackhpark-stock-trading-skills/scripts/analyst_facts.py \
  TICKER [TICKER ...] [--quarters 4] [--sort KEY[:asc|desc],...] [--format md|json]
```

Per ticker: buy/hold/sell counts, low/mean/high target, regular-session last price, mean
target vs price (%), the last N reported quarters as beat/miss/inline on EPS, the next
report date (tentative when unverified), P/E, market cap, dividend yield, and the
timestamp of each source. It reads Robinhood through the read-only proxy.

## Sorting

Only by what the owner names, from this fixed list (anything else is rejected with exit 2):
`ticker`, `last_price`, `mean_target`, `low_target`, `high_target`,
`target_vs_price_pct`, `buy_count`, `hold_count`, `sell_count`, `analyst_count`, `beats`,
`misses`, `next_report_date`, `pe_ratio`, `market_cap`, `dividend_yield`.

If the owner names no key, keep the order given. Do not invent a ranking, a weighted
score or a "consensus strength" — a composite is a verdict with the arithmetic hidden.

## Reply shape

In Korean (docs/decisions.md §25): a verdict line stating what was fetched (not what to
do), the script's compact table with Korean labels, then the source lines (ratings as-of,
price time). Numbers, tickers and dates stay as they are. Say "애널리스트 커버리지 없음" or "목표가 정보 없음" where the script reports none;
never as zero.

## Refusals

- No buy, sell or hold call, no outlook ranking, no "best" ticker, including when asked
  directly. Offer the facts sorted by a key the owner picks instead.
- This skill uses only the read-only `robinhood` server. Orders go through the `trade`
  skill. Strategy proposals for the Agentic sleeve belong to `portfolio-review`.
