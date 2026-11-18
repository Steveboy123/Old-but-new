#!/usr/bin/env python3
"""Dr Exploit Control V2.28 - dedicated Rise/Fall engine for Step 100 Index.

This engine intentionally stays separate from the multiplier engine. Deriv
Rise/Fall contracts use CALL/PUT and settle at their configured expiry. The
"Best Odds Auto" mode combines transparent market-direction scores with live
Deriv proposal return; it is not a guarantee of profit or a prediction of the
future.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import time
from collections import deque
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Deque, Dict, Optional

import bot as core

MONEY = Decimal("0.01")
ROOT = Path(__file__).resolve().parent
RF_TRADES_PATH = Path(os.getenv("DR_EXPLOIT_RF_TRADES_PATH", str(ROOT / "rise_fall_trades.csv"))).expanduser()
STOP_FILE = os.getenv("DR_EXPLOIT_RF_STOP_FILE", "").strip()
CONTROL_FILE = os.getenv("DR_EXPLOIT_RF_CONTROL_FILE", "").strip()
TELEMETRY_FILE = os.getenv("DR_EXPLOIT_RF_TELEMETRY_FILE", "").strip()
INSTANCE_ID = os.getenv("DR_EXPLOIT_RF_INSTANCE_ID", "rise-fall") or "rise-fall"


def dec(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(MONEY, rounding=ROUND_DOWN)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class RiseFallSettings:
    capital: Decimal
    split_count: int
    max_open: int
    trades_per_signal: int
    split_delay: float
    compound: bool
    duration: int
    duration_unit: str
    decision_mode: str
    direction_policy: str
    min_confidence: float
    min_edge: float
    min_return_pct: float
    cooldown_ticks: int
    quote_refresh_ticks: int
    lookback: int
    trend_weight: float
    momentum_weight: float
    reversal_weight: float
    breakout_weight: float
    loss_policy: str
    loss_count: int
    profit_target: Decimal
    loss_limit: Decimal
    win_target: int


@dataclass
class RFPosition:
    contract_id: int
    contract_type: str
    direction: str
    stake: Decimal
    ask_price: Decimal
    payout: Decimal
    opened_at: str
    duration: int
    duration_unit: str
    entry_spot: Optional[float] = None
    signal_confidence: float = 0.0
    signal_edge: float = 0.0
    proposal_return_pct: float = 0.0
    break_even_pct: float = 0.0
    combined_score: float = 0.0
    rsi: float = 50.0
    momentum_up: float = 0.0
    momentum_down: float = 0.0
    range_position: float = 0.5
    volatility_step: float = 0.0
    decision_mode: str = ""
    direction_policy: str = ""
    lookback: int = 0
    min_confidence: float = 0.0
    min_edge: float = 0.0
    min_return_pct: float = 0.0
    trend_weight: float = 0.0
    momentum_weight: float = 0.0
    reversal_weight: float = 0.0
    breakout_weight: float = 0.0
    current_profit: Decimal = Decimal("0")
    status: str = "open"
    subscription_id: str = ""


@dataclass
class RFStats:
    realized_profit: Decimal = Decimal("0")
    wins: int = 0
    losses: int = 0
    trades: int = 0
    consecutive_losses: int = 0
    stop_reason: str = ""


class RiseFallAnalyzer:
    """Small transparent directional model built for short Step 100 expiries."""
    def __init__(self, settings: RiseFallSettings):
        self.settings = settings
        self.prices: Deque[float] = deque(maxlen=max(500, settings.lookback * 8))
        self.last_metrics: Dict[str, Any] = {}

    def update_settings(self, settings: RiseFallSettings) -> None:
        self.settings = settings
        if self.prices.maxlen != max(500, settings.lookback * 8):
            old = list(self.prices)
            self.prices = deque(old[-max(500, settings.lookback * 8):], maxlen=max(500, settings.lookback * 8))

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        period = max(2, min(period, len(values)))
        alpha = 2.0 / (period + 1)
        out = values[0]
        for value in values[1:]:
            out = value * alpha + out * (1.0 - alpha)
        return out

    @staticmethod
    def _rsi(values: list[float], period: int = 14) -> float:
        if len(values) < 2:
            return 50.0
        changes = [b-a for a,b in zip(values[:-1], values[1:])][-period:]
        gains = sum(max(0.0, x) for x in changes)
        losses = sum(max(0.0, -x) for x in changes)
        if gains + losses <= 1e-12:
            return 50.0
        if losses <= 1e-12:
            return 100.0
        rs = gains / losses
        return 100.0 - 100.0 / (1.0 + rs)

    def add(self, price: float) -> Dict[str, Any]:
        self.prices.append(float(price))
        vals = list(self.prices)
        s = self.settings
        lb = max(6, s.lookback)
        recent = vals[-lb:]
        if len(recent) < max(6, min(lb, 10)):
            self.last_metrics = {"ready": False, "up_confidence": 0.5, "down_confidence": 0.5, "decision": "WAIT"}
            return self.last_metrics

        changes = [b-a for a,b in zip(recent[:-1], recent[1:])]
        abs_avg = sum(abs(x) for x in changes) / max(1, len(changes))
        scale = max(abs_avg, 1e-9)
        positive = sum(1 for x in changes if x > 0)
        negative = sum(1 for x in changes if x < 0)
        directional_total = max(1, positive + negative)
        momentum_up = positive / directional_total
        momentum_down = negative / directional_total

        fast_period = max(3, min(8, lb // 3 or 3))
        slow_period = max(fast_period + 2, min(21, lb))
        ema_fast = self._ema(recent, fast_period)
        ema_slow = self._ema(recent, slow_period)
        ema_delta = (ema_fast - ema_slow) / scale
        trend_up = clamp(0.5 + ema_delta / 5.0)
        trend_down = 1.0 - trend_up

        rsi = self._rsi(recent, min(14, len(recent)-1))
        # Reversal score intentionally only grows when the market is stretched.
        reversal_up = clamp((42.0 - rsi) / 30.0)
        reversal_down = clamp((rsi - 58.0) / 30.0)

        lo, hi = min(recent), max(recent)
        span = max(hi-lo, scale)
        position = clamp((recent[-1] - lo) / span)
        # Breakout contribution follows pressure near an edge plus recent direction.
        breakout_up = clamp((position - 0.55) * 1.8) * momentum_up
        breakout_down = clamp((0.45 - position) * 1.8) * momentum_down

        weights = {
            "trend": max(0.0, s.trend_weight),
            "momentum": max(0.0, s.momentum_weight),
            "reversal": max(0.0, s.reversal_weight),
            "breakout": max(0.0, s.breakout_weight),
        }
        total_w = max(1e-9, sum(weights.values()))
        trend_pair = (trend_up, trend_down)
        momentum_pair = (momentum_up, momentum_down)
        reversal_pair = (reversal_up, reversal_down)
        breakout_pair = (breakout_up, breakout_down)

        up = (
            weights["trend"]*trend_pair[0] + weights["momentum"]*momentum_pair[0] +
            weights["reversal"]*reversal_pair[0] + weights["breakout"]*breakout_pair[0]
        ) / total_w
        down = (
            weights["trend"]*trend_pair[1] + weights["momentum"]*momentum_pair[1] +
            weights["reversal"]*reversal_pair[1] + weights["breakout"]*breakout_pair[1]
        ) / total_w

        # Normalize to complementary confidences so the edge is easy to reason about.
        pair_sum = max(1e-9, up + down)
        up_conf = clamp(up / pair_sum)
        down_conf = clamp(down / pair_sum)
        edge = abs(up_conf - down_conf)
        decision = "RISE" if up_conf > down_conf else "FALL" if down_conf > up_conf else "WAIT"

        if s.decision_mode == "trend":
            up_conf, down_conf = trend_up, trend_down
        elif s.decision_mode == "momentum":
            pair_sum = max(1e-9, momentum_up + momentum_down)
            up_conf, down_conf = momentum_up/pair_sum, momentum_down/pair_sum
        elif s.decision_mode == "reversal":
            # Add a neutral baseline so no stretched condition means WAIT, not certainty.
            ru, rd = 0.25 + reversal_up, 0.25 + reversal_down
            pair_sum = ru + rd
            up_conf, down_conf = ru/pair_sum, rd/pair_sum
        edge = abs(up_conf - down_conf)
        decision = "RISE" if up_conf > down_conf else "FALL" if down_conf > up_conf else "WAIT"

        if s.direction_policy == "rise_only":
            decision = "RISE"
        elif s.direction_policy == "fall_only":
            decision = "FALL"

        metrics = {
            "ready": True,
            "decision": decision,
            "up_confidence": round(up_conf, 6),
            "down_confidence": round(down_conf, 6),
            "edge": round(edge, 6),
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_delta_steps": ema_delta,
            "rsi": rsi,
            "momentum_up": momentum_up,
            "momentum_down": momentum_down,
            "range_position": position,
            "breakout_up": breakout_up,
            "breakout_down": breakout_down,
            "volatility_step": abs_avg,
            "lookback": len(recent),
        }
        self.last_metrics = metrics
        return metrics


class RiseFallBot:
    def __init__(self, client: core.DerivWebSocketClient, settings: RiseFallSettings, account_id: str,
                 balance: Decimal, currency: str, *, live: bool, dry_run: bool, symbol_override: str = ""):
        self.client = client
        self.settings = settings
        self.account_id = account_id
        self.balance = balance
        self.currency = currency
        self.live = live
        self.dry_run = dry_run
        self.symbol_override = symbol_override.strip()
        self.symbol = ""
        self.symbol_name = "Step 100 Index"
        self.stats = RFStats()
        self.analyzer = RiseFallAnalyzer(settings)
        self.positions: Dict[int, RFPosition] = {}
        self.stop_requested = False
        self.entries_paused = False
        self.shutdown = asyncio.Event()
        self.cooldown = 0
        self.tick_counter = 0
        self.last_quote: Optional[float] = None
        self.last_epoch: int = 0
        self.last_odds: Dict[str, Any] = {}
        self.last_decision: Dict[str, Any] = {}
        self.supported_contracts: list[str] = []
        self.rise_contract_type = "CALL"
        self.fall_contract_type = "PUT"
        self.started = time.time()
        self.control_revision = 0
        self.control_task: Optional[asyncio.Task[Any]] = None
        self.eval_task: Optional[asyncio.Task[Any]] = None
        self.eval_lock = asyncio.Lock()
        self.pending_flat: Dict[str, Any] = {}
        self.last_telemetry = 0.0
        self.recent_prices: Deque[float] = deque(maxlen=180)
        self.recent_results: Deque[Dict[str, Any]] = deque(maxlen=50)

    @property
    def stake(self) -> Decimal:
        effective = self.settings.capital + (self.stats.realized_profit if self.settings.compound else Decimal("0"))
        return max(Decimal("0"), (effective / Decimal(self.settings.split_count)).quantize(MONEY, rounding=ROUND_DOWN))

    async def resolve_symbol(self) -> None:
        if self.symbol_override:
            self.symbol = self.symbol_override
            self.symbol_name = self.symbol_override
            return
        # Filter discovery to the Rise/Fall contract family using Deriv's
        # current active_symbols request shape, then validate the exact market
        # through contracts_for before any proposal/buy request.
        r = await self.client.request({"active_symbols": "brief", "contract_type": ["CALL", "PUT"]})
        rows = r.get("active_symbols") or []
        for item in rows:
            text = " ".join(str(item.get(k, "")) for k in ("underlying_symbol_name","display_name","underlying_symbol","symbol")).lower().replace(" ", "")
            if "step100" in text or "stepindex100" in text:
                self.symbol = str(item.get("underlying_symbol") or item.get("symbol") or "")
                self.symbol_name = str(item.get("underlying_symbol_name") or item.get("display_name") or "Step 100 Index")
                break
        if not self.symbol:
            # Preserve the known Step 100 override used by the multiplier engine as a final fallback.
            self.symbol = "stpRNG"
            self.symbol_name = "Step 100 Index"

    def request_stop(self) -> None:
        """Gracefully stop new entries and exit once fixed-expiry contracts settle."""
        self.stop_requested = True
        if not self.positions:
            self.shutdown.set()

    async def check_contracts(self) -> None:
        r = await self.client.request({"contracts_for": self.symbol})
        available = (r.get("contracts_for") or {}).get("available") or []
        self.supported_contracts = sorted({str(x.get("contract_type")) for x in available if x.get("contract_type")})
        missing = [x for x in ("CALL", "PUT") if x not in self.supported_contracts]
        if missing:
            raise RuntimeError(f"Rise/Fall is not available on {self.symbol}; missing contract types: {', '.join(missing)}")
        # Prefer the current API's explicit sentiment metadata over assumptions
        # about legacy naming. Current contracts_for marks the upward side with
        # sentiment=up. If sentiment is omitted, fall back to CALL/PUT.
        for item in available:
            ctype = str(item.get("contract_type") or "").upper()
            sentiment = str(item.get("sentiment") or "").lower()
            if ctype not in {"CALL", "PUT"}:
                continue
            if sentiment == "up":
                self.rise_contract_type = ctype
            elif sentiment == "down":
                self.fall_contract_type = ctype

    async def seed(self) -> None:
        try:
            r = await self.client.request({"ticks_history": self.symbol, "count": max(80, self.settings.lookback*3), "end": "latest", "style": "ticks"})
            prices = (r.get("history") or {}).get("prices") or []
            for p in prices:
                try: self.analyzer.add(float(p))
                except Exception: pass
        except Exception as exc:
            print(f"RISE/FALL | history seed warning: {exc}", flush=True)

    async def subscribe(self) -> None:
        self.client.add_handler("tick", self.on_tick)
        self.client.add_handler("proposal_open_contract", self.on_contract)
        self.client.add_handler("balance", self.on_balance)
        await self.client.request({"balance": 1, "subscribe": 1})
        await self.client.request({"ticks": self.symbol, "subscribe": 1})

    async def on_balance(self, message: Dict[str, Any]) -> None:
        b = message.get("balance") or {}
        try: self.balance = dec(b.get("balance", self.balance))
        except Exception: pass

    async def on_tick(self, message: Dict[str, Any]) -> None:
        tick = message.get("tick") or {}
        if str(tick.get("symbol") or tick.get("underlying_symbol") or self.symbol) != self.symbol:
            return
        try: quote = float(tick["quote"])
        except Exception: return
        self.last_quote = quote
        self.last_epoch = int(tick.get("epoch") or time.time())
        self.tick_counter += 1
        self.recent_prices.append(quote)
        metrics = self.analyzer.add(quote)
        if self.cooldown > 0: self.cooldown -= 1
        self._apply_pending_if_flat()
        self.write_telemetry()
        should_quote = self.tick_counter % max(1, self.settings.quote_refresh_ticks) == 0
        candidate = bool(metrics.get("ready")) and max(float(metrics.get("up_confidence",0)), float(metrics.get("down_confidence",0))) >= self.settings.min_confidence and float(metrics.get("edge",0)) >= self.settings.min_edge
        if (should_quote or candidate) and (self.eval_task is None or self.eval_task.done()):
            self.eval_task = asyncio.create_task(self.refresh_odds_and_trade(candidate), name="rf-odds")

    def slots_available(self) -> int:
        return max(0, min(self.settings.split_count-len(self.positions), self.settings.max_open-len(self.positions)))

    async def proposal(self, contract_type: str) -> Dict[str, Any]:
        request = {
            "proposal": 1,
            "amount": float(self.stake),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": self.currency,
            "duration": self.settings.duration,
            "duration_unit": self.settings.duration_unit,
            "underlying_symbol": self.symbol,
        }
        r = await self.client.request(request)
        p = r.get("proposal") or {}
        ask = float(p.get("ask_price") or self.stake or 0)
        payout = float(p.get("payout") or 0)
        profit = max(0.0, payout-ask)
        return_pct = profit/ask*100 if ask > 0 else 0.0
        break_even = ask/payout*100 if payout > 0 else 100.0
        return {
            "id": p.get("id"), "ask": ask, "payout": payout, "profit": profit,
            "return_pct": return_pct, "break_even_pct": break_even,
            "spot": p.get("spot"), "contract_type": contract_type,
        }

    async def refresh_odds_and_trade(self, candidate: bool) -> None:
        async with self.eval_lock:
            try:
                rise_r, fall_r = await asyncio.gather(
                    self.proposal(self.rise_contract_type),
                    self.proposal(self.fall_contract_type),
                    return_exceptions=True,
                )
                odds: Dict[str, Any] = {}
                if not isinstance(rise_r, Exception): odds["RISE"] = rise_r
                else: odds["RISE_ERROR"] = str(rise_r)
                if not isinstance(fall_r, Exception): odds["FALL"] = fall_r
                else: odds["FALL_ERROR"] = str(fall_r)
                self.last_odds = odds
                m = self.analyzer.last_metrics
                for label, conf_key in (("RISE","up_confidence"),("FALL","down_confidence")):
                    if label in odds:
                        conf = float(m.get(conf_key, 0.0))
                        odds[label]["model_confidence"] = conf
                        odds[label]["combined_score"] = conf * (1.0 + float(odds[label].get("return_pct",0.0))/100.0)
                self.write_telemetry(force=True)
                if candidate:
                    await self.maybe_trade()
            except Exception as exc:
                self.last_decision = {"action":"WAIT", "reason":f"proposal error: {exc}"}
                self.write_telemetry(force=True)

    async def maybe_trade(self) -> None:
        if self.stop_requested or self.entries_paused or self.cooldown > 0 or self.slots_available() <= 0:
            return
        m = self.analyzer.last_metrics
        if not m.get("ready"):
            return
        candidates = []
        for label, conf_key in (("RISE","up_confidence"),("FALL","down_confidence")):
            if label not in self.last_odds: continue
            if self.settings.direction_policy == "rise_only" and label != "RISE": continue
            if self.settings.direction_policy == "fall_only" and label != "FALL": continue
            conf = float(m.get(conf_key, 0.0))
            other_conf = float(m.get("down_confidence" if label=="RISE" else "up_confidence", 0.0))
            edge = conf - other_conf
            ret = float(self.last_odds[label].get("return_pct",0.0))
            score = float(self.last_odds[label].get("combined_score",0.0))
            candidates.append((score, conf, edge, ret, label))
        if not candidates: return
        qualified = [x for x in candidates if x[1] >= self.settings.min_confidence and x[2] >= self.settings.min_edge and x[3] >= self.settings.min_return_pct]
        # Non-best-odds modes follow the analyzer direction; Best Odds ranks only
        # directions that already passed model confidence + edge + return gates.
        if self.settings.decision_mode != "best_odds":
            desired = str(m.get("decision") or "WAIT")
            qualified = [x for x in qualified if x[4] == desired]
        if not qualified:
            strongest=max(candidates,key=lambda x:x[1])
            self.last_decision={"action":"WAIT","reason":"no direction passed confidence + edge + return gates","direction":strongest[4],"confidence":strongest[1],"edge":strongest[2],"return_pct":strongest[3]}
            return
        qualified.sort(reverse=True)
        score, conf, edge, ret, label = qualified[0]
        self.last_decision={"action":"TRADE" if not self.dry_run else "SIGNAL","direction":label,"confidence":conf,"edge":edge,"return_pct":ret,"combined_score":score}
        if self.dry_run:
            print(f"RISE/FALL SIGNAL | {label} confidence={conf:.1%} edge={edge:.1%} return={ret:.1f}%", flush=True)
            self.cooldown = self.settings.cooldown_ticks
            return
        count = min(self.settings.trades_per_signal, self.slots_available())
        for i in range(count):
            if self.stop_requested: break
            if i and self.settings.split_delay > 0: await asyncio.sleep(self.settings.split_delay)
            await self.open_trade(label, i+1, count)
        self.cooldown = self.settings.cooldown_ticks

    async def open_trade(self, label: str, index: int, total: int) -> None:
        contract_type = self.rise_contract_type if label == "RISE" else self.fall_contract_type
        p = await self.proposal(contract_type)
        pid = p.get("id")
        if not pid: raise RuntimeError("Rise/Fall proposal did not return an ID")
        ask = dec(p.get("ask", self.stake))
        print(f"RISE/FALL OPEN {label} {index}/{total} | stake={ask} {self.currency} | {self.settings.duration}{self.settings.duration_unit}", flush=True)
        r = await self.client.request({"buy": str(pid), "price": float(ask)})
        b = r.get("buy") or {}
        cid = b.get("contract_id")
        if cid is None: raise RuntimeError("Rise/Fall buy response did not include contract_id")
        metrics = dict(self.analyzer.last_metrics or {})
        chosen_conf = float(metrics.get("up_confidence" if label == "RISE" else "down_confidence", 0.0) or 0.0)
        other_conf = float(metrics.get("down_confidence" if label == "RISE" else "up_confidence", 0.0) or 0.0)
        chosen_edge = chosen_conf - other_conf
        combined_score = chosen_conf * (1.0 + float(p.get("return_pct", 0.0) or 0.0) / 100.0)
        pos = RFPosition(
            contract_id=int(cid), contract_type=contract_type, direction=label,
            stake=self.stake, ask_price=ask, payout=dec(p.get("payout",0)),
            opened_at=datetime.now(timezone.utc).isoformat(), duration=self.settings.duration,
            duration_unit=self.settings.duration_unit,
            entry_spot=float(p["spot"]) if p.get("spot") is not None else self.last_quote,
            signal_confidence=chosen_conf, signal_edge=chosen_edge,
            proposal_return_pct=float(p.get("return_pct", 0.0) or 0.0), break_even_pct=float(p.get("break_even_pct", 0.0) or 0.0),
            combined_score=combined_score, rsi=float(metrics.get("rsi", 50.0) or 50.0),
            momentum_up=float(metrics.get("momentum_up", 0.0) or 0.0), momentum_down=float(metrics.get("momentum_down", 0.0) or 0.0),
            range_position=float(metrics.get("range_position", 0.5) or 0.5), volatility_step=float(metrics.get("volatility_step", 0.0) or 0.0),
            decision_mode=self.settings.decision_mode, direction_policy=self.settings.direction_policy, lookback=self.settings.lookback,
            min_confidence=self.settings.min_confidence, min_edge=self.settings.min_edge, min_return_pct=self.settings.min_return_pct,
            trend_weight=self.settings.trend_weight, momentum_weight=self.settings.momentum_weight,
            reversal_weight=self.settings.reversal_weight, breakout_weight=self.settings.breakout_weight,
        )
        self.positions[pos.contract_id] = pos
        await self.client.request({"proposal_open_contract":1,"contract_id":pos.contract_id,"subscribe":1})
        self.write_telemetry(force=True)

    async def on_contract(self, message: Dict[str, Any]) -> None:
        c = message.get("proposal_open_contract") or {}
        try: cid = int(c.get("contract_id"))
        except Exception: return
        pos = self.positions.get(cid)
        if not pos: return
        try: pos.current_profit = dec(c.get("profit", pos.current_profit))
        except Exception: pass
        pos.status = str(c.get("status") or pos.status)
        sub = (message.get("subscription") or {}).get("id")
        if sub: pos.subscription_id = str(sub)
        final = bool(c.get("is_sold")) or pos.status in {"won","lost","sold","expired"}
        if not final:
            self.write_telemetry(); return
        profit = pos.current_profit
        if c.get("profit") is not None:
            try: profit = dec(c.get("profit"))
            except Exception: pass
        self.stats.realized_profit += profit
        self.stats.trades += 1
        if profit > 0:
            self.stats.wins += 1; self.stats.consecutive_losses = 0; result="WIN"
        else:
            self.stats.losses += 1; self.stats.consecutive_losses += 1; result="LOSS"
        row = {
            "closed_utc": datetime.now(timezone.utc).isoformat(), "opened_utc": pos.opened_at, "direction": pos.direction,
            "result": result, "stake": str(pos.stake), "profit": str(profit),
            "session_profit": str(self.stats.realized_profit), "duration": pos.duration, "duration_unit": pos.duration_unit,
            "mode": pos.decision_mode or self.settings.decision_mode, "decision_mode": pos.decision_mode or self.settings.decision_mode,
            "direction_policy": pos.direction_policy, "signal_confidence": f"{pos.signal_confidence:.6f}", "signal_edge": f"{pos.signal_edge:.6f}",
            "proposal_return_pct": f"{pos.proposal_return_pct:.6f}", "break_even_pct": f"{pos.break_even_pct:.6f}", "combined_score": f"{pos.combined_score:.6f}",
            "rsi": f"{pos.rsi:.6f}", "momentum_up": f"{pos.momentum_up:.6f}", "momentum_down": f"{pos.momentum_down:.6f}",
            "range_position": f"{pos.range_position:.6f}", "volatility_step": f"{pos.volatility_step:.9f}", "lookback": pos.lookback,
            "min_confidence": f"{pos.min_confidence:.6f}", "min_edge": f"{pos.min_edge:.6f}", "min_return_pct": f"{pos.min_return_pct:.6f}",
            "trend_weight": f"{pos.trend_weight:.6f}", "momentum_weight": f"{pos.momentum_weight:.6f}", "reversal_weight": f"{pos.reversal_weight:.6f}", "breakout_weight": f"{pos.breakout_weight:.6f}",
        }
        self.recent_results.append(row)
        self._append_trade_csv(row)
        self.positions.pop(cid, None)
        # Release the per-contract stream once the position has settled.
        if pos.subscription_id:
            try:
                await self.client.request({"forget": pos.subscription_id})
            except Exception:
                pass
        self._apply_pending_if_flat()
        self._check_stop_rules()
        if self.stop_requested and not self.positions: self.shutdown.set()
        self.write_telemetry(force=True)
        print(f"RISE/FALL CLOSED | {pos.direction} {result} profit={profit} | session={self.stats.realized_profit}", flush=True)

    def _append_trade_csv(self, row: Dict[str, Any]) -> None:
        import csv
        fields = [
            "closed_utc","opened_utc","direction","result","stake","profit","session_profit","duration","duration_unit","mode","decision_mode","direction_policy",
            "signal_confidence","signal_edge","proposal_return_pct","break_even_pct","combined_score","rsi","momentum_up","momentum_down","range_position","volatility_step","lookback",
            "min_confidence","min_edge","min_return_pct","trend_weight","momentum_weight","reversal_weight","breakout_weight",
        ]
        if RF_TRADES_PATH.exists():
            try:
                with RF_TRADES_PATH.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f); old_fields = reader.fieldnames or []; old_rows = list(reader)
                if old_fields != fields:
                    tmp = RF_TRADES_PATH.with_suffix(".csv.tmp")
                    with tmp.open("w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); writer.writeheader()
                        for old_row in old_rows: writer.writerow({k: old_row.get(k, "") for k in fields})
                    tmp.replace(RF_TRADES_PATH)
            except Exception:
                pass
        exists = RF_TRADES_PATH.exists() and RF_TRADES_PATH.stat().st_size > 0
        with RF_TRADES_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if not exists: writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in fields})

    def _check_stop_rules(self) -> None:
        s=self.settings; st=self.stats
        if s.profit_target > 0 and st.realized_profit >= s.profit_target: st.stop_reason="Session profit target reached"
        elif s.loss_limit > 0 and st.realized_profit <= -s.loss_limit: st.stop_reason="Session loss limit reached"
        elif s.win_target > 0 and st.wins >= s.win_target: st.stop_reason="Session win target reached"
        elif s.loss_policy == "first" and st.losses >= 1: st.stop_reason="Stopped after first loss"
        elif s.loss_policy == "consecutive" and st.consecutive_losses >= s.loss_count: st.stop_reason=f"Stopped after {s.loss_count} consecutive losses"
        elif s.loss_policy == "total" and st.losses >= s.loss_count: st.stop_reason=f"Stopped after {s.loss_count} total losses"
        if st.stop_reason: self.stop_requested=True

    def _json(self, value: Any) -> Any:
        if isinstance(value, Decimal): return float(value)
        if isinstance(value, dict): return {k:self._json(v) for k,v in value.items()}
        if isinstance(value, (list,tuple,deque)): return [self._json(v) for v in value]
        return value

    def telemetry(self) -> Dict[str, Any]:
        return {
            "version":"2.28","engine":"rise_fall","instance":INSTANCE_ID,
            "running":not self.shutdown.is_set(),"stop_requested":self.stop_requested,"entries_paused":self.entries_paused,
            "runtime_seconds":int(time.time()-self.started),"account_balance":float(self.balance),"currency":self.currency,
            "symbol":self.symbol,"symbol_name":self.symbol_name,"quote":self.last_quote,"epoch":self.last_epoch,
            "supported_contracts":self.supported_contracts,"settings":self._json(asdict(self.settings)),
            "contract_mapping":{"RISE":self.rise_contract_type,"FALL":self.fall_contract_type},
            "stake":float(self.stake),"open":len(self.positions),"positions":[self._json(asdict(p)) for p in self.positions.values()],
            "stats":self._json(asdict(self.stats)),"metrics":self.analyzer.last_metrics,"odds":self.last_odds,
            "decision":self.last_decision,"cooldown":self.cooldown,"tick_counter":self.tick_counter,
            "recent_prices":list(self.recent_prices),"recent_results":list(self.recent_results),"control_revision":self.control_revision,
        }

    def write_telemetry(self, force: bool=False) -> None:
        if not TELEMETRY_FILE: return
        now=time.monotonic()
        if not force and now-self.last_telemetry < 0.35: return
        self.last_telemetry=now
        path=Path(TELEMETRY_FILE); path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix(path.suffix+".tmp")
        try:
            tmp.write_text(json.dumps(self.telemetry(),separators=(",",":")),encoding="utf-8"); os.replace(tmp,path)
        except Exception: pass

    def _apply_pending_if_flat(self) -> None:
        if self.positions or not self.pending_flat: return
        changes=dict(self.pending_flat); self.pending_flat.clear()
        self.settings=replace(self.settings,**changes); self.analyzer.update_settings(self.settings)

    async def control_loop(self) -> None:
        last_rev=0
        while not self.shutdown.is_set():
            try:
                if STOP_FILE and Path(STOP_FILE).exists() and not self.stop_requested:
                    self.request_stop(); print("RISE/FALL | graceful stop: no new entries",flush=True)
                if CONTROL_FILE and Path(CONTROL_FILE).exists():
                    data=json.loads(Path(CONTROL_FILE).read_text(encoding="utf-8") or "{}")
                    rev=int(data.get("revision",0) or 0)
                    if rev>last_rev:
                        last_rev=rev; self.control_revision=rev; await self.apply_control(data)
            except Exception as exc:
                print(f"RISE/FALL CONTROL ERROR | {exc}",flush=True)
            await asyncio.sleep(.4)

    async def apply_control(self, data: Dict[str,Any]) -> None:
        action=str(data.get("action","apply"))
        if action=="pause": self.entries_paused=True
        elif action=="resume": self.entries_paused=False
        raw=data.get("settings") if isinstance(data.get("settings"),dict) else {}
        if raw:
            converters={
                "capital":dec,"split_count":int,"max_open":int,"trades_per_signal":int,"split_delay":float,"compound":bool,
                "duration":int,"duration_unit":str,"decision_mode":str,"direction_policy":str,"min_confidence":float,"min_edge":float,
                "min_return_pct":float,"cooldown_ticks":int,"quote_refresh_ticks":int,"lookback":int,
                "trend_weight":float,"momentum_weight":float,"reversal_weight":float,"breakout_weight":float,
                "loss_policy":str,"loss_count":int,"profit_target":dec,"loss_limit":dec,"win_target":int,
            }
            changes={k:converters[k](v) for k,v in raw.items() if k in converters}
            flat_only={k:v for k,v in changes.items() if k in {"capital","split_count","compound"}}
            immediate={k:v for k,v in changes.items() if k not in flat_only}
            if immediate:
                self.settings=replace(self.settings,**immediate); self.analyzer.update_settings(self.settings)
            if flat_only:
                if self.positions: self.pending_flat.update(flat_only)
                else:
                    self.settings=replace(self.settings,**flat_only); self.analyzer.update_settings(self.settings)
        self.write_telemetry(force=True)

    async def run(self) -> None:
        await self.resolve_symbol(); await self.check_contracts(); await self.seed(); await self.subscribe()
        self.control_task=asyncio.create_task(self.control_loop(),name="rf-control")
        print("DR EXPLOIT CONTROL V2.28 | RISE/FALL | RUNNING",flush=True)
        print(f"Rise/Fall market: {self.symbol_name} [{self.symbol}] | CALL/PUT | duration={self.settings.duration}{self.settings.duration_unit}",flush=True)
        self.write_telemetry(force=True)
        try:
            while not self.shutdown.is_set():
                if self.client.closed.is_set():
                    raise ConnectionError("Deriv WebSocket disconnected")
                await asyncio.sleep(.25)
        finally:
            if self.control_task: self.control_task.cancel(); await asyncio.gather(self.control_task,return_exceptions=True)
            if self.eval_task: self.eval_task.cancel(); await asyncio.gather(self.eval_task,return_exceptions=True)
            self.write_telemetry(force=True)


def build_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Dr Exploit Rise/Fall Step 100 engine")
    p.add_argument("--capital",required=True); p.add_argument("--split",type=int,default=4); p.add_argument("--max-open",type=int,default=2)
    p.add_argument("--trades-per-signal",type=int,default=1); p.add_argument("--split-delay",type=float,default=0)
    p.add_argument("--duration",type=int,default=5); p.add_argument("--duration-unit",choices=("t","s","m"),default="t")
    p.add_argument("--decision-mode",choices=("best_odds","consensus","trend","momentum","reversal"),default="best_odds")
    p.add_argument("--direction-policy",choices=("auto","rise_only","fall_only"),default="auto")
    p.add_argument("--min-confidence",type=float,default=.62); p.add_argument("--min-edge",type=float,default=.12); p.add_argument("--min-return-pct",type=float,default=0)
    p.add_argument("--cooldown-ticks",type=int,default=5); p.add_argument("--quote-refresh-ticks",type=int,default=4); p.add_argument("--lookback",type=int,default=24)
    p.add_argument("--trend-weight",type=float,default=1.2); p.add_argument("--momentum-weight",type=float,default=1.1); p.add_argument("--reversal-weight",type=float,default=.55); p.add_argument("--breakout-weight",type=float,default=.8)
    p.add_argument("--loss-policy",choices=("continue","first","consecutive","total"),default="consecutive"); p.add_argument("--loss-count",type=int,default=3)
    p.add_argument("--profit-target",default="0"); p.add_argument("--loss-limit",default="0"); p.add_argument("--win-target",type=int,default=0)
    g=p.add_mutually_exclusive_group(); g.add_argument("--compound",action="store_true"); g.add_argument("--fixed",action="store_true")
    p.add_argument("--live",action="store_true"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--symbol",default="")
    return p


def validate_args(a: argparse.Namespace) -> RiseFallSettings:
    capital=dec(a.capital)
    if capital<=0: raise ValueError("Capital must be greater than 0")
    if not 1<=a.split<=50 or not 1<=a.max_open<=a.split: raise ValueError("Split/max-open is invalid")
    if not 1<=a.trades_per_signal<=a.max_open: raise ValueError("Trades per signal must be <= max open")
    if a.duration<1: raise ValueError("Duration must be at least 1")
    if not 0.5<=a.min_confidence<=.99: raise ValueError("Min confidence must be 0.50-0.99")
    if not 0<=a.min_edge<=.95: raise ValueError("Min edge must be 0-0.95")
    if a.lookback<6 or a.lookback>500: raise ValueError("Lookback must be 6-500")
    return RiseFallSettings(
        capital=capital,split_count=a.split,max_open=a.max_open,trades_per_signal=a.trades_per_signal,split_delay=max(0,a.split_delay),compound=bool(a.compound),
        duration=a.duration,duration_unit=a.duration_unit,decision_mode=a.decision_mode,direction_policy=a.direction_policy,min_confidence=a.min_confidence,min_edge=a.min_edge,min_return_pct=max(0,a.min_return_pct),
        cooldown_ticks=max(0,a.cooldown_ticks),quote_refresh_ticks=max(1,a.quote_refresh_ticks),lookback=a.lookback,trend_weight=max(0,a.trend_weight),momentum_weight=max(0,a.momentum_weight),reversal_weight=max(0,a.reversal_weight),breakout_weight=max(0,a.breakout_weight),
        loss_policy=a.loss_policy,loss_count=max(1,a.loss_count),profit_target=abs(dec(a.profit_target)),loss_limit=abs(dec(a.loss_limit)),win_target=max(0,a.win_target),
    )


async def async_main(args: argparse.Namespace) -> int:
    core.load_env_file(ROOT/".env")
    token=(os.getenv("DERIV_API_TOKEN") or os.getenv("DERIV_PAT_TOKEN") or "").strip(); app_id=(os.getenv("DERIV_APP_ID") or "").strip(); account=(os.getenv("DERIV_ACCOUNT_ID") or "").strip()
    if not token or not app_id or not account: raise RuntimeError("DERIV_API_TOKEN, DERIV_APP_ID and DERIV_ACCOUNT_ID are required")
    settings=validate_args(args)
    if args.live and os.getenv("DERIV_LIVE_CONFIRM")!="YES_I_ACCEPT_REAL_MONEY_RISK": raise RuntimeError("Live Rise/Fall trading was not confirmed")
    # Use the shared throttled/backoff connector so Rise/Fall respects
    # Cloudflare/Deriv Retry-After responses instead of immediately asking
    # the OTP endpoint again.
    client=await core.connect_authenticated_client(token,app_id,account,attempts=5)
    try:
        br=await client.request({"balance":1}); b=br.get("balance") or {}; balance=dec(b.get("balance",0)); currency=str(b.get("currency") or "USD")
        if settings.capital>balance and balance>0: raise RuntimeError(f"Selected Rise/Fall capital {settings.capital} exceeds account balance {balance} {currency}")
        bot=RiseFallBot(client,settings,account,balance,currency,live=bool(args.live),dry_run=bool(args.dry_run),symbol_override=args.symbol or os.getenv("DERIV_SYMBOL", ""))
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM):
            try: loop.add_signal_handler(sig,bot.request_stop)
            except Exception: pass
        await bot.run(); return 0
    finally: await client.close()


def main() -> int:
    try: return asyncio.run(async_main(build_parser().parse_args()))
    except KeyboardInterrupt: return 130
    except Exception as exc:
        print(f"RISE/FALL ERROR | {type(exc).__name__}: {exc}",flush=True); return 1


if __name__=="__main__": raise SystemExit(main())
