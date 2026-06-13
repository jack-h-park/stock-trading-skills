# Provider adapter: Robinhood

Maps the [provider contract](../_contract.md) onto the Robinhood Agent MCP server.

MCP server id in this environment: `b98713b4-79ed-46c5-8271-87c6e82fe2f9`
(tools surface as `mcp__b98713b4-...__<tool>`).

## Mapping

| Contract op | Robinhood MCP tool |
|---|---|
| `accounts()` | `get_accounts` |
| `portfolio()` | `get_portfolio` |
| `positions()` | `get_equity_positions` |
| `quote(symbol)` | `get_equity_quotes` |
| `historicals(symbol, span)` | `get_equity_historicals` |
| `tradability(symbol)` | `get_equity_tradability` |
| `orders()` | `get_equity_orders` |
| `search(query)` | `search` |
| `review_order(spec)` | `review_equity_order` |
| `place_order(spec)` | `place_equity_order` |
| `cancel_order(id)` | `cancel_equity_order` |

Watchlist tools (`add_to_watchlist`, `create_watchlist`, `get_watchlists`, …) exist but
are not part of the trading flow; use directly when needed.

## Notes

- Always call `review_equity_order` first and surface its pre-trade warnings to the user
  before `place_equity_order`.
- Resolve ambiguous symbols via `search` before quoting/ordering.
- Confirm the target account from `get_accounts` if more than one exists.
