"""Unit tests for the V2.28 Risk:Reward manager (pure maths + engine hooks).

Run from the repository root (needs: pip install -r app/requirements.txt):
    python tests/test_risk_reward.py
No Deriv connection is used; orders go to a fake client. All files are written
to a temporary folder.
"""
import asyncio
import csv
import importlib
import os
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

APP_DIR = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
TMP = Path(tempfile.mkdtemp(prefix="rr_test_"))
os.environ["DR_EXPLOIT_TRADES_PATH"] = str(TMP / "trades_v2.csv")
os.environ["DR_EXPLOIT_DIAGNOSTIC_LOG_PATH"] = str(TMP / "entry_diagnostics.jsonl")
os.environ["DR_EXPLOIT_ENSEMBLE_LOG_PATH"] = str(TMP / "ensemble_decisions.csv")
os.environ["DR_EXPLOIT_CANDLE_LOG_PATH"] = str(TMP / "candlestick_events.csv")
sys.dont_write_bytecode = True
sys.path.insert(0, str(APP_DIR))
bot = importlib.import_module("bot")

D = Decimal


def settings(**kw):
    base = dict(
        capital=D("100"), split_count=4, max_open_trades=2, batch_mode="one", compound=False,
        loss_policy="continue", loss_count=1, profit_target=D("0"), loss_limit=D("0"), win_target=0,
        take_profit=D("0.10"), take_profit_mode="fixed", take_profit_max=D("0.10"), multiplier=750,
        trades_per_signal=1, rr_enabled=True,
    )
    base.update(kw)
    return bot.SessionSettings(**base)


# ---------------------------------------------------------------- pure maths

def test_label_and_break_even():
    assert bot.RiskReward.label(D("3")) == "1:3"
    assert bot.RiskReward.label(D("1.50")) == "1:1.5"
    assert bot.RiskReward.break_even_win_rate(D("3")) == D("25.0")
    assert bot.RiskReward.break_even_win_rate(D("4")) == D("20.0")
    assert bot.RiskReward.break_even_win_rate(D("1")) == D("50.0")
    assert bot.RiskReward.expectancy_r(D("30"), D("3")) == D("0.20")
    assert bot.RiskReward.expectancy_r(D("20"), D("4")) == D("0.00")


def test_amount_plan_1_to_3():
    s = settings(rr_ratio=D("3"), rr_risk_basis="amount", rr_risk_value=D("1.00"))
    plan = bot.RiskReward.plan(s, D("25"), D("100"), 8000.0)
    assert plan.unit == "amount"
    assert plan.risk_amount == D("1.00")
    assert plan.reward_amount == D("3.00")
    assert plan.server_stop_loss == D("1.00")
    assert plan.server_take_profit == D("3.00")
    # 25 * 750 * 0.1 / 8000 = 0.234375 per tick -> 1.00 is ~4.27 ticks
    assert abs(plan.risk_ticks - 4.2666) < 0.01
    assert abs(plan.reward_ticks - 12.8) < 0.01


def test_stake_and_capital_percent():
    s = settings(rr_ratio=D("4"), rr_risk_basis="stake_pct", rr_risk_value=D("4"))
    plan = bot.RiskReward.plan(s, D("25"), D("100"), None)
    assert (plan.risk_amount, plan.reward_amount) == (D("1.00"), D("4.00"))
    s = settings(rr_ratio=D("2"), rr_risk_basis="capital_pct", rr_risk_value=D("2"))
    plan = bot.RiskReward.plan(s, D("25"), D("150"), None)
    assert (plan.risk_amount, plan.reward_amount) == (D("3.00"), D("6.00"))
    assert plan.risk_ticks == 0.0  # no spot -> no tick estimate


def test_risk_capped_below_stake_and_min_target():
    s = settings(rr_ratio=D("2"), rr_risk_basis="capital_pct", rr_risk_value=D("50"))
    plan = bot.RiskReward.plan(s, D("25"), D("100"), None)
    assert plan.risk_amount == D("24.99")
    assert "capped" in plan.note
    s = settings(rr_ratio=D("1"), rr_risk_basis="amount", rr_risk_value=D("0.05"))
    plan = bot.RiskReward.plan(s, D("25"), D("100"), None)
    assert plan.reward_amount == D("0.10")
    assert "minimum" in plan.note


def test_tick_plan_with_spot_and_backups():
    s = settings(rr_ratio=D("3"), rr_risk_basis="ticks", rr_risk_value=D("4"), take_profit_unit="ticks")
    plan = bot.RiskReward.plan(s, D("25"), D("100"), 8000.0)
    assert plan.unit == "ticks"
    assert (plan.risk_ticks, plan.reward_ticks) == (4.0, 12.0)
    assert plan.risk_amount == D("0.93")  # 4 * 0.234375 floored
    assert plan.server_stop_loss == D("1.39")  # 1.5x buffer beyond the local stop
    assert plan.server_take_profit == D("4.21")
    assert bot.RiskReward.ceil_ticks(4.5) == 5
    assert bot.RiskReward.ceil_ticks(12.0) == 12


def test_tick_plan_without_spot_uses_configured_backups():
    s = settings(rr_risk_basis="ticks", rr_risk_value=D("4"), take_profit_unit="ticks",
                 server_stop_loss=D("2.00"), tick_tp_server_backup=D("3.00"))
    plan = bot.RiskReward.plan(s, D("25"), D("100"), None)
    assert plan.server_stop_loss == D("2.00") and plan.server_take_profit == D("3.00")


def test_profit_lock_levels():
    s = settings(rr_ratio=D("3"), rr_risk_value=D("2.00"), rr_breakeven_r=D("1"), rr_lock_r=D("0.25"))
    plan = bot.RiskReward.plan(s, D("25"), D("100"), None)
    assert (plan.lock_trigger, plan.lock_profit) == (D("2.00"), D("0.50"))


def test_validation():
    ok = settings(rr_ratio=D("3"), rr_risk_value=D("1"))
    bot.RiskReward.validate(ok, D("25"))
    bad = [
        settings(rr_ratio=D("0.1")),
        settings(rr_ratio=D("25")),
        settings(rr_risk_value=D("25")),  # amount >= stake
        settings(rr_risk_basis="stake_pct", rr_risk_value=D("99")),
        settings(rr_risk_basis="ticks", rr_risk_value=D("3")),  # unit mismatch (amount)
        settings(rr_breakeven_r=D("3"), rr_ratio=D("3")),  # trigger at target
        settings(rr_breakeven_r=D("1"), rr_lock_r=D("1")),  # lock not below trigger
        settings(rr_lock_r=D("1")),  # lock off: floor must stay below 1R
    ]
    for item in bad:
        try:
            bot.RiskReward.validate(item, D("25"))
        except ValueError:
            continue
        raise AssertionError(f"expected validation error for {item.rr_ratio} {item.rr_risk_basis} {item.rr_risk_value}")
    bot.RiskReward.validate(settings(rr_enabled=False, rr_ratio=D("99")), D("25"))


# ---------------------------------------------------------------- engine hooks

class FakeClient:
    def __init__(self):
        self.requests = []

    def add_handler(self, *a, **k):
        pass

    async def request(self, payload, timeout=None):
        self.requests.append(payload)
        if "proposal" in payload and payload.get("proposal") == 1:
            return {"proposal": {"id": "P1", "ask_price": payload["amount"], "spot": 8000.0}}
        if "buy" in payload:
            return {"buy": {"contract_id": 1000 + len(self.requests)}}
        if "proposal_open_contract" in payload:
            return {"subscription": {"id": "S1"}}
        if "sell" in payload:
            return {"sell": {}}
        return {}


def make_bot(s):
    cfg = bot.BotConfig.load(bot.CONFIG_PATH)
    client = FakeClient()
    tb = bot.TradingBot(cfg, s, client, "VRTC1", D("1000"), "USD", live_flag=False, dry_run=False)
    tb.last_quote = 8000.0
    tb.symbol = "stpRNG"
    return tb, client


def run(coro):
    return asyncio.run(coro)


def test_order_carries_rr_bracket():
    s = settings(rr_ratio=D("4"), rr_risk_basis="amount", rr_risk_value=D("1.25"),
                 server_stop_loss=D("5.00"))
    tb, client = make_bot(s)

    async def go():
        await tb._open_single_trade("MULTUP", D("25"), 1, 1, {})
    run(go())
    proposal = next(r for r in client.requests if r.get("proposal") == 1)
    assert proposal["limit_order"] == {"take_profit": 5.0, "stop_loss": 1.25}
    pos = next(iter(tb.active_positions.values()))
    assert pos.rr_active and pos.risk_amount == D("1.25") and pos.reward_amount == D("5.00")
    assert pos.take_profit == D("5.00") and pos.stop_loss == D("1.25")


def test_order_without_rr_is_unchanged():
    s = settings(rr_enabled=False, take_profit=D("0.40"), take_profit_max=D("0.40"), server_stop_loss=D("2.00"))
    tb, client = make_bot(s)
    run(tb._open_single_trade("MULTDOWN", D("25"), 1, 1, {}))
    proposal = next(r for r in client.requests if r.get("proposal") == 1)
    assert proposal["limit_order"] == {"take_profit": 0.4, "stop_loss": 2.0}
    assert not next(iter(tb.active_positions.values())).rr_active


def _contract(cid, profit, sold=False):
    return {"proposal_open_contract": {"contract_id": cid, "profit": str(profit),
                                       "status": "sold" if sold else "open", "is_sold": 1 if sold else 0}}


def test_amount_profit_lock_and_r_accounting():
    s = settings(rr_ratio=D("3"), rr_risk_value=D("1.00"), rr_breakeven_r=D("1"), rr_lock_r=D("0.10"))
    tb, client = make_bot(s)
    sold = []
    tb._schedule_position_sell = lambda p, reason: (sold.append(reason), setattr(p, "closing_requested", True), setattr(p, "closing_reason", reason))

    async def go():
        await tb._open_single_trade("MULTUP", D("25"), 1, 1, {})
        cid = next(iter(tb.active_positions))
        await tb._on_contract_update(_contract(cid, "0.50"))
        assert not sold
        await tb._on_contract_update(_contract(cid, "1.10"))  # +1.1R arms the lock
        pos = tb.active_positions[cid]
        assert pos.rr_lock_armed and not sold
        await tb._on_contract_update(_contract(cid, "0.09"))  # gives back to the lock
        assert sold == ["rr_profit_lock"]
        await tb._on_contract_update(_contract(cid, "0.08", sold=True))
        assert cid not in tb.active_positions
    run(go())
    assert tb.stats.r_trades == 1
    assert tb.stats.total_r == D("0.08")
    with open(os.environ["DR_EXPLOIT_TRADES_PATH"], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    last = rows[-1]
    assert last["rr_ratio"] == "3" and last["risk_amount"] == "1.00" and last["r_multiple"] == "0.080"
    assert last["exit_reason"] == "rr_profit_lock"


def test_server_stop_is_labelled_and_session_r_stop():
    s = settings(rr_ratio=D("2"), rr_risk_value=D("1.00"), rr_session_stop_r=D("2"))
    tb, client = make_bot(s)

    async def go():
        for _ in range(2):
            await tb._open_single_trade("MULTDOWN", D("25"), 1, 1, {})
            cid = next(iter(tb.active_positions))
            await tb._on_contract_update(_contract(cid, "-1.00", sold=True))
    run(go())
    assert tb.stats.total_r == D("-2")
    assert tb.stop_requested and "Session R stop" in tb.stats.stop_reason


def test_tick_bracket_stop_and_target():
    s = settings(rr_ratio=D("3"), rr_risk_basis="ticks", rr_risk_value=D("4"), take_profit_unit="ticks")
    tb, client = make_bot(s)
    sold = []
    tb._schedule_position_sell = lambda p, reason: (sold.append(reason), setattr(p, "closing_requested", True))

    async def go():
        await tb._open_single_trade("MULTUP", D("25"), 1, 1, {})
        await tb._open_single_trade("MULTDOWN", D("25"), 1, 1, {})
    run(go())
    proposal = [r for r in client.requests if r.get("proposal") == 1][0]
    assert proposal["limit_order"] == {"take_profit": 4.21, "stop_loss": 1.39}
    up, down = list(tb.active_positions.values())
    assert up.take_profit_ticks == 12 and up.take_profit_unit == "ticks"
    for p in (up, down):
        p.entry_spot = 8000.0
        p.last_quote = 8000.0
    tb._evaluate_tick_exits(None, 7999.7)  # up -3 ticks, down +3 ticks
    assert sold == []
    tb._evaluate_tick_exits(None, 7999.6)  # up -4 ticks -> stop
    assert sold == ["rr_stop_loss_4_ticks"]
    tb._evaluate_tick_exits(None, 7998.8)  # down +12 ticks -> target
    assert sold == ["rr_stop_loss_4_ticks", "rr_take_profit_12_ticks"]


def test_smart_mode_uses_r_levels():
    s = settings(take_profit_mode="smart", rr_ratio=D("4"), rr_risk_value=D("1.00"), rr_breakeven_r=D("1.5"),
                 rr_lock_r=D("0.5"), smart_activation=D("9.00"), smart_profit_floor=D("8.00"))
    tb, client = make_bot(s)
    run(tb._open_single_trade("MULTUP", D("25"), 1, 1, {}))
    pos = next(iter(tb.active_positions.values()))
    assert tb._smart_activation_amount(pos) == D("1.50")
    assert tb._smart_floor_amount(pos) == D("0.50")
    s2 = replace(s, rr_breakeven_r=D("0"))
    tb2, _ = make_bot(s2)
    run(tb2._open_single_trade("MULTUP", D("25"), 1, 1, {}))
    pos2 = next(iter(tb2.active_positions.values()))
    assert tb2._smart_activation_amount(pos2) == D("1.00")  # defaults to +1R


def test_live_control_updates_ratio_and_forces_unit():
    s = settings(rr_ratio=D("3"), rr_risk_value=D("1.00"))
    tb, _ = make_bot(s)
    tb._apply_live_control({"settings": {"rr_ratio": "4", "rr_risk_basis": "ticks", "rr_risk_value": "5"}})
    assert tb.settings.rr_ratio == D("4") and tb.settings.take_profit_unit == "ticks"
    try:
        tb._apply_live_control({"settings": {"rr_ratio": "50"}})
    except ValueError:
        pass
    else:
        raise AssertionError("ratio 50 should be rejected")
    assert tb.settings.rr_ratio == D("4")


def test_trade_log_upgrades_old_header():
    path = TMP / "legacy.csv"
    old_fields = list(bot.TradingBot.TRADE_LOG_FIELDS[:37])
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(old_fields)
        w.writerow([f"v{i}" for i in range(len(old_fields))])
    fields = bot.TradingBot._ensure_trade_log_header(path)
    assert fields == list(bot.TradingBot.TRADE_LOG_FIELDS)
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["closed_at_utc"] == "v0" and rows[0]["instance_id"] == "v36" and rows[0]["r_multiple"] == ""


def test_cli_parser_accepts_rr_flags():
    args = bot.build_argument_parser().parse_args(
        ["--risk-reward", "--rr-ratio", "4", "--rr-risk-basis", "stake_pct", "--rr-risk-value", "5",
         "--rr-breakeven-r", "1", "--rr-lock-r", "0.1", "--rr-session-stop-r", "3", "--rr-session-target-r", "6"])
    assert args.rr_enabled is True and args.rr_ratio == "4" and args.rr_risk_basis == "stake_pct"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                import traceback
                traceback.print_exc()
                print("FAIL", name, exc)
    print("failures:", failures)
    sys.exit(1 if failures else 0)
