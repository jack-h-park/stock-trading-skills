# Provider contract

Every brokerage provider must expose these abstract operations. The common skills
(`trade`, `portfolio-review`) call only these abstractions; a provider's `adapter.md`
maps them onto that broker's concrete tools/API.

Keep this thin. It currently reflects one provider (Robinhood). Refine the contract only
when a second provider actually lands and reveals a real difference.

## Read operations

| Abstract op | Purpose |
|---|---|
| `accounts()` | List tradable accounts |
| `portfolio()` | Portfolio snapshot (equity, buying power, cash) |
| `positions()` | Current equity (and option) positions |
| `quote(symbol)` | Real-time quote |
| `historicals(symbol, span)` | OHLCV bars |
| `tradability(symbol)` | Whether a symbol is currently tradable |
| `orders()` | Order history / open orders |
| `search(query)` | Resolve a name/ticker to an instrument |

## Write operations

| Abstract op | Purpose | Safety |
|---|---|---|
| `review_order(spec)` | **Dry-run / simulate** an order, return pre-trade warnings | always first |
| `place_order(spec)` | Submit a live order | requires confirmation (see guardrails) |
| `cancel_order(id)` | Cancel an open order | log immediately |

## Order spec (common shape)

```
{ broker, account, symbol, side (buy|sell), quantity, order_type (market|limit),
  limit_price?, time_in_force, asset_class (equity|option) }
```

A provider's `capabilities.md` declares which `side`, `order_type`, and `asset_class`
values it actually supports. Anything outside that is rejected before `review_order`.

## Invariant

`place_order` is **never** called without a preceding successful `review_order` in the
same flow, and never without satisfying `config/guardrails.md`.
