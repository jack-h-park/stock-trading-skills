# Provider capabilities: Robinhood

Source: https://robinhood.com/us/en/support/articles/trading-with-your-agent/

## Supported

- **Long equities orders** (buy).
- **Options orders** — rolling out; may not be available on every account yet.
- Read-only: accounts, portfolio, positions, quotes, historicals, tradability, orders.
- Pre-trade simulation via `review_equity_order` (returns warnings).
- Watchlist management.
- **Fractional quantities, on both sides.** Verified 2026-07-30 against account <AGENTIC_ACCOUNT>:
  every universe symbol (AAPL, AMZN, GOOGL, META, MSFT, NVDA, QQQM, VOO) returns
  `fractional_tradability: tradable` from `get_equity_tradability`. Check that field per
  symbol rather than assuming — it is per-instrument, not a blanket account capability.

## Not supported

- **Short positions.**
- **Other asset classes**: crypto, futures, etc.
- Operating without a connected user account.

## Order spec support

| Field | Supported values |
|---|---|
| `side` | `buy` (long); `sell` to close longs |
| `asset_class` | `equity`; `option` (where enabled) |
| `order_type` | `market`, `limit` |
| `quantity` | whole or fractional shares (fractional: `market` + regular hours only) |

> Fractional and dollar-based orders place in the regular session only, whatever a symbol's
> `extended_hours_fractional_tradability` flag says. A fractional sell queued outside regular
> hours does not fill, so a trim decided after the close executes on the next session.

## Disclosed risk

The user is ultimately responsible for trades the agent places. The agent may misinterpret
instructions or act on incomplete data; Robinhood does not guarantee accuracy or
suitability. → default to confirm-before-place (`config/guardrails.md`).
