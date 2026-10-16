"""--agentic-orders enforcement in scripts/mcp-robinhood-proxy.py.

The trader profile's order server runs the proxy in this mode. It relays eight
tools, and of those it lets review/place/cancel through only when
`arguments.account_number` is the Agentic account read from the gitignored
config/accounts.local.md. These tests never reach Robinhood: `_relay` is stubbed,
and in most of them the point is that it is never called.

Account numbers here are synthetic. The accounts file is written to tmp_path in
the config/accounts.example.md table format, and ACCOUNTS_LOCAL points at it.

Run: python3 -m pytest tests/test_robinhood_proxy_agentic_orders.py
"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "mcp_robinhood_proxy_agentic", REPO / "scripts" / "mcp-robinhood-proxy.py"
)
assert _spec and _spec.loader
proxy = importlib.util.module_from_spec(_spec)
sys.modules["mcp_robinhood_proxy_agentic"] = proxy
_spec.loader.exec_module(proxy)

AGENTIC = "900000001"
LONGTERM = "900000002"
MIDTERM = "900000003"

ACCOUNTS_MD = f"""# Accounts (local)

| Placeholder | Real value | Role |
| --- | --- | --- |
| `<AGENTIC_ACCOUNT>` | {AGENTIC} | agent trades this one |
| `<LONGTERM_ACCOUNT>` | {LONGTERM} | never traded |
| `<MIDTERM_ACCOUNT>` | {MIDTERM} | never traded |
"""

ALLOWED = [
    "get_accounts",
    "get_portfolio",
    "get_equity_orders",
    "get_trade_approval_setting",
    "get_trade_approvals",
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
]
CHECKED = ["review_equity_order", "place_equity_order", "cancel_equity_order"]

# Write tools that exist on the Robinhood MCP and must never pass in this mode,
# plus reads the order server has no business relaying.
REFUSED = [
    "place_option_order",
    "review_option_order",
    "cancel_option_order",
    "place_crypto_order",
    "preview_crypto_order",
    "cancel_crypto_order",
    "exercise_option",
    "cancel_option_exercise",
    "create_alert",
    "update_alert",
    "delete_alert",
    "create_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "create_scan",
    "run_scan",
    "decline_trade_approval",
    "get_equity_positions",
    "get_equity_tax_lots",
    "search",
    "place_futures_order",
    "",
]


@pytest.fixture
def accounts_file(tmp_path, monkeypatch):
    path = tmp_path / "accounts.local.md"
    path.write_text(ACCOUNTS_MD)
    monkeypatch.setattr(proxy, "ACCOUNTS_LOCAL", path)
    return path


def run_main(monkeypatch, lines, flags=("--agentic-orders",)):
    """Drive main() over stdin lines. Returns (stdout payloads, relayed msgs)."""
    relayed = []

    def fake_relay(msg, token, transform=None):
        relayed.append(msg)

    monkeypatch.setattr(sys, "argv", ["mcp-robinhood-proxy.py", *flags])
    monkeypatch.setattr(proxy, "_get_token", lambda: "tok")
    monkeypatch.setattr(proxy, "_relay", fake_relay)
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(x + "\n" for x in lines)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    proxy.main()
    payloads = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    return payloads, relayed


def call(name, arguments=None, msg_id=1):
    return json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
         "params": {"name": name, "arguments": arguments if arguments is not None else {}}}
    )


def order_args(account):
    args = {"symbol": "XYZ", "side": "buy", "type": "market", "dollar_amount": "10.00"}
    if account is not None:
        args["account_number"] = account
    return args


def assert_no_account_number(payload):
    text = json.dumps(payload)
    for number in (AGENTIC, LONGTERM, MIDTERM):
        assert number not in text, "a refusal echoed an account number"


# ── the eight allowed tools ─────────────────────────────────────────────────────

def test_order_tools_on_the_agentic_account_are_relayed(monkeypatch, accounts_file):
    lines = [call(n, order_args(AGENTIC), msg_id=i) for i, n in enumerate(CHECKED)]
    payloads, relayed = run_main(monkeypatch, lines)
    assert payloads == []
    assert [m["params"]["name"] for m in relayed] == CHECKED


def test_cancel_on_the_agentic_account_is_relayed(monkeypatch, accounts_file):
    args = {"account_number": AGENTIC, "order_id": "00000000-0000-0000-0000-000000000001"}
    payloads, relayed = run_main(monkeypatch, [call("cancel_equity_order", args)])
    assert payloads == []
    assert len(relayed) == 1


def test_unchecked_reads_relay_without_an_account_check(monkeypatch, tmp_path):
    """get_accounts/get_portfolio/get_equity_orders are relayed even with no accounts file.

    get_accounts is how the protocol confirms the target account in the first place.
    """
    monkeypatch.setattr(proxy, "ACCOUNTS_LOCAL", tmp_path / "missing.md")
    lines = [call("get_accounts"),
             call("get_portfolio", {"account_number": LONGTERM}),
             call("get_equity_orders", {"account_number": AGENTIC})]
    payloads, relayed = run_main(monkeypatch, lines)
    assert payloads == []
    assert len(relayed) == 3


# ── the account check ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", CHECKED)
@pytest.mark.parametrize("account", [LONGTERM, MIDTERM, "123", AGENTIC + "0", " " + AGENTIC])
def test_order_tool_on_another_account_is_refused(monkeypatch, accounts_file, name, account):
    payloads, relayed = run_main(monkeypatch, [call(name, order_args(account), msg_id=7)])
    assert relayed == [], f"{name} on a non-Agentic account was forwarded"
    assert len(payloads) == 1
    err = payloads[0]["error"]
    assert payloads[0]["id"] == 7
    assert err["code"] == -32602
    assert "not the Agentic account" in err["message"]
    assert_no_account_number(payloads[0])


@pytest.mark.parametrize("name", CHECKED)
@pytest.mark.parametrize("arguments", [
    {},
    {"account_number": ""},
    {"account_number": None},
    {"account_number": int(AGENTIC)},
    "not-a-dict",
])
def test_order_tool_without_a_usable_account_argument_is_refused(
    monkeypatch, accounts_file, name, arguments
):
    payloads, relayed = run_main(monkeypatch, [call(name, arguments)])
    assert relayed == []
    assert payloads[0]["error"]["code"] == -32602
    assert "account_number argument is missing" in payloads[0]["error"]["message"]
    assert_no_account_number(payloads[0])


def test_order_call_with_no_arguments_key_is_refused(monkeypatch, accounts_file):
    line = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "place_equity_order"}})
    payloads, relayed = run_main(monkeypatch, [line])
    assert relayed == []
    assert payloads[0]["error"]["code"] == -32602


@pytest.mark.parametrize("name", CHECKED)
def test_missing_accounts_file_fails_closed(monkeypatch, tmp_path, name):
    monkeypatch.setattr(proxy, "ACCOUNTS_LOCAL", tmp_path / "does-not-exist.md")
    payloads, relayed = run_main(monkeypatch, [call(name, order_args(AGENTIC))])
    assert relayed == [], "an order passed with no accounts file to check it against"
    assert "could not be resolved" in payloads[0]["error"]["message"]
    assert_no_account_number(payloads[0])


@pytest.mark.parametrize("name", CHECKED)
def test_missing_agentic_row_fails_closed(monkeypatch, tmp_path, name):
    path = tmp_path / "accounts.local.md"
    path.write_text("\n".join(
        line for line in ACCOUNTS_MD.splitlines() if "<AGENTIC_ACCOUNT>" not in line
    ))
    monkeypatch.setattr(proxy, "ACCOUNTS_LOCAL", path)
    payloads, relayed = run_main(monkeypatch, [call(name, order_args(LONGTERM))])
    assert relayed == []
    assert "could not be resolved" in payloads[0]["error"]["message"]
    assert_no_account_number(payloads[0])


def test_placeholder_left_unfilled_fails_closed(monkeypatch, tmp_path):
    """The example file copied without filling in the number must not pass anything."""
    path = tmp_path / "accounts.local.md"
    path.write_text((REPO / "config" / "accounts.example.md").read_text())
    monkeypatch.setattr(proxy, "ACCOUNTS_LOCAL", path)
    payloads, relayed = run_main(
        monkeypatch, [call("place_equity_order", order_args("(fill in)"))]
    )
    assert relayed == []
    assert payloads[0]["error"]["code"] == -32602


def test_accounts_file_is_read_per_call(monkeypatch, accounts_file):
    """Removing the file takes effect on the next call, without a respawn."""
    lines = [call("review_equity_order", order_args(AGENTIC), msg_id=1)]
    _, relayed = run_main(monkeypatch, lines)
    assert len(relayed) == 1
    accounts_file.unlink()
    payloads, relayed = run_main(monkeypatch, lines)
    assert relayed == []
    assert payloads[0]["error"]["code"] == -32602


# ── everything else is refused outright ───────────────────────────────────────

@pytest.mark.parametrize("name", REFUSED)
def test_tools_outside_the_eight_are_refused_and_never_relayed(monkeypatch, accounts_file, name):
    payloads, relayed = run_main(monkeypatch, [call(name, order_args(AGENTIC), msg_id=5)])
    assert relayed == [], f"{name!r} was forwarded in agentic-orders mode"
    assert payloads[0]["id"] == 5
    assert payloads[0]["error"]["code"] == -32601
    assert "agentic-orders" in payloads[0]["error"]["message"]
    assert_no_account_number(payloads[0])


def test_non_tool_call_methods_still_relay(monkeypatch, accounts_file):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 9, "method": "initialize"}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})]
    _, relayed = run_main(monkeypatch, lines)
    assert len(relayed) == 2


def test_a_json_rpc_batch_is_not_relayed(monkeypatch, accounts_file):
    """A batch array would carry tools/call past the per-message check; drop it."""
    batch = json.dumps([json.loads(call("place_option_order"))])
    payloads, relayed = run_main(monkeypatch, [batch])
    assert relayed == [] and payloads == []


# ── the listing hides what the mode refuses ──────────────────────────────────

def test_tools_list_keeps_exactly_the_eight():
    names = ALLOWED + REFUSED[:-1] + ["get_equity_quotes"]
    payload = {"jsonrpc": "2.0", "id": 2,
               "result": {"tools": [{"name": n} for n in names]}}
    kept = [t["name"] for t in proxy._strip_to_agentic_orders(payload)["result"]["tools"]]
    assert kept == ALLOWED


def test_tools_list_transform_is_applied_in_agentic_orders_mode(monkeypatch):
    seen = {}

    def fake_relay(msg, token, transform=None):
        seen[msg["method"]] = transform

    monkeypatch.setattr(sys, "argv", ["proxy", "--agentic-orders"])
    monkeypatch.setattr(proxy, "_get_token", lambda: "tok")
    monkeypatch.setattr(proxy, "_relay", fake_relay)
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize"})]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(x + "\n" for x in lines)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    proxy.main()
    assert seen["tools/list"] is proxy._strip_to_agentic_orders
    assert seen["initialize"] is None


def test_the_allow_list_is_the_eight_the_design_names():
    assert sorted(proxy.AGENTIC_ORDER_TOOLS) == sorted(ALLOWED)
    assert sorted(proxy.ACCOUNT_CHECKED_TOOLS) == sorted(CHECKED)


# ── the two filtering modes cannot be combined ───────────────────────────────

def test_read_only_and_agentic_orders_together_exit_before_relaying(monkeypatch, capsys):
    relayed = []
    monkeypatch.setattr(sys, "argv", ["proxy", "--read-only", "--agentic-orders"])
    monkeypatch.setattr(proxy, "_get_token", lambda: pytest.fail("token fetched"))
    monkeypatch.setattr(proxy, "_relay", lambda *a, **k: relayed.append(a))
    monkeypatch.setattr(sys, "stdin", io.StringIO(call("get_accounts") + "\n"))
    with pytest.raises(SystemExit) as exc:
        proxy.main()
    assert exc.value.code == 2
    assert relayed == []
    assert "mutually exclusive" in capsys.readouterr().err


def test_read_only_mode_is_unchanged_by_the_new_mode(monkeypatch, accounts_file):
    """--read-only still refuses place_equity_order even on the Agentic account."""
    payloads, relayed = run_main(
        monkeypatch, [call("place_equity_order", order_args(AGENTIC))], flags=("--read-only",)
    )
    assert relayed == []
    assert payloads[0]["error"]["code"] == -32601
