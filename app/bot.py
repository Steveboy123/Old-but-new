#!/usr/bin/env python3
"""Dr Exploit Control V2.27 — Step 100 multiplier trading bot with Strategy Fusion diagnostics, reconnect, graceful entry-stop, and recovery-aware Smart Loss Guard support.

Core features
-------------
* PAT + OTP authenticated Deriv Options WebSocket connection.
* Step 100 / ``stpRNG`` multiplier trading.
* Nine exclusive configurable strategies, including a selectable multi-pattern candlestick engine, plus Strategy Fusion.
* Selectable session capital allocation and split count.
* One or multiple simultaneous positions.
* Open one slot per signal or fill every available slot per signal.
* Optional stagger delay between split trade openings.
* Fixed staking or session compounding.
* Continue after losses, stop after first loss, or stop after configurable
  consecutive/total losses.
* Optional session profit target, loss limit, and win target.
* Fixed/adaptive TP measured in money or Step Index ticks, plus smart-trailing/hybrid exits.
* Optional server-side and recovery-aware Smart Loss protection.
* Live console dashboard and detailed CSV trade history.

This program cannot guarantee profit. Test on a Deriv demo account first.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import copy
import getpass
import json
import logging
import os
import signal
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed


ROOT = Path(__file__).resolve().parent
FIXED_DERIV_APP_ID = "346MK1Nd8vpaSJy7EzQQT"
CONFIG_PATH = Path(os.getenv("DR_EXPLOIT_CONFIG_PATH", str(ROOT / "config.json"))).resolve()
LOG_PATH = Path(os.getenv("DR_EXPLOIT_TRADES_PATH", str(ROOT / "trades_v2.csv"))).resolve()
INSTANCE_ID = os.getenv("DR_EXPLOIT_INSTANCE_ID", "main").strip() or "main"
STOP_FILE_RAW = os.getenv("DR_EXPLOIT_STOP_FILE", "").strip()
ENSEMBLE_LOG_RAW = os.getenv("DR_EXPLOIT_ENSEMBLE_LOG_PATH", "").strip()
if ENSEMBLE_LOG_RAW:
    ENSEMBLE_LOG_PATH = Path(ENSEMBLE_LOG_RAW).resolve()
elif INSTANCE_ID == "main":
    ENSEMBLE_LOG_PATH = (ROOT / "ensemble_decisions.csv").resolve()
else:
    # Strategy Lab / alternate instances get their own analytics file so
    # parallel testers never interleave decision logs.
    ENSEMBLE_LOG_PATH = LOG_PATH.with_name(f"{LOG_PATH.stem}_ensemble_decisions.csv")

CANDLE_LOG_RAW = os.getenv("DR_EXPLOIT_CANDLE_LOG_PATH", "").strip()
if CANDLE_LOG_RAW:
    CANDLE_LOG_PATH = Path(CANDLE_LOG_RAW).resolve()
elif INSTANCE_ID == "main":
    CANDLE_LOG_PATH = (ROOT / "candlestick_events.csv").resolve()
else:
    CANDLE_LOG_PATH = LOG_PATH.with_name(f"{LOG_PATH.stem}_candlestick_events.csv")

DIAGNOSTIC_LOG_RAW = os.getenv("DR_EXPLOIT_DIAGNOSTIC_LOG_PATH", "").strip()
if DIAGNOSTIC_LOG_RAW:
    DIAGNOSTIC_LOG_PATH = Path(DIAGNOSTIC_LOG_RAW).resolve()
elif INSTANCE_ID == "main":
    DIAGNOSTIC_LOG_PATH = (ROOT / "entry_diagnostics.jsonl").resolve()
else:
    DIAGNOSTIC_LOG_PATH = LOG_PATH.with_name(f"{LOG_PATH.stem}_entry_diagnostics.jsonl")

LIVE_CONTROL_RAW = os.getenv("DR_EXPLOIT_LIVE_CONTROL_FILE", "").strip()
TELEMETRY_RAW = os.getenv("DR_EXPLOIT_TELEMETRY_FILE", "").strip()

BOT_VERSION = "2.27"
LOGGER = logging.getLogger("dr_exploit_control")
STEP100_ALLOWED_MULTIPLIERS = (750, 2000, 3500, 5500, 7500)
DERIV_OPTIONS_REST_BASE = "https://api.derivws.com"
MONEY_STEP = Decimal("0.01")


# ---------------------------------------------------------------------------
# Deriv OTP / WebSocket helpers
# ---------------------------------------------------------------------------


def _extract_websocket_url(payload: Dict[str, Any]) -> str:
    candidates = [
        payload,
        payload.get("data") if isinstance(payload.get("data"), dict) else {},
        payload.get("result") if isinstance(payload.get("result"), dict) else {},
    ]
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        for key in ("websocket_url", "websocketUrl", "ws_url", "wsUrl", "url"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("wss://"):
                return value
    raise RuntimeError(f"OTP response did not include a WebSocket URL: {payload}")




class OTPRateLimitError(RuntimeError):
    """Transient Deriv OTP REST rate limit with a server-advised retry delay."""
    def __init__(self, message: str, retry_after: float = 30.0):
        self.retry_after = max(1.0, float(retry_after or 30.0))
        super().__init__(message)


_OTP_REQUEST_LOCK = threading.Lock()
_OTP_LAST_REQUEST_MONOTONIC = 0.0
_OTP_BLOCK_UNTIL_MONOTONIC = 0.0
_OTP_MIN_INTERVAL_SECONDS = 8.0
_OTP_PROCESS_LOCK_PATH = ROOT / ".deriv_otp_request.lock"
_OTP_STATE_PATH = ROOT / ".deriv_otp_rate_state.json"


def _acquire_otp_process_lock(timeout: float = 90.0) -> int:
    """Cross-process lock so main/Rise-Fall/Lab/UI do not hit OTP together."""
    deadline = time.monotonic() + max(5.0, timeout)
    while True:
        try:
            fd = os.open(
                str(_OTP_PROCESS_LOCK_PATH),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(fd, f"pid={os.getpid()} created={time.time():.3f}\n".encode("ascii"))
            except Exception:
                pass
            return fd
        except FileExistsError:
            # Recover automatically if a previous process crashed while holding it.
            try:
                age = time.time() - _OTP_PROCESS_LOCK_PATH.stat().st_mtime
                if age > 90.0:
                    _OTP_PROCESS_LOCK_PATH.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for the local Deriv OTP request lock")
            time.sleep(0.25)


def _release_otp_process_lock(fd: Optional[int]) -> None:
    try:
        if fd is not None:
            os.close(fd)
    except Exception:
        pass
    try:
        _OTP_PROCESS_LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _read_otp_shared_state() -> Dict[str, float]:
    try:
        payload = json.loads(_OTP_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {
                "last_request": float(payload.get("last_request") or 0.0),
                "block_until": float(payload.get("block_until") or 0.0),
            }
    except Exception:
        pass
    return {"last_request": 0.0, "block_until": 0.0}


def _write_otp_shared_state(*, last_request: float, block_until: float) -> None:
    # Contains timing only; never credentials/tokens/account IDs.
    temp = _OTP_STATE_PATH.with_suffix(".tmp")
    try:
        temp.write_text(
            json.dumps({"last_request": last_request, "block_until": block_until}),
            encoding="utf-8",
        )
        os.replace(temp, _OTP_STATE_PATH)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass


def _retry_after_seconds(exc: urllib.error.HTTPError, detail: str) -> float:
    # Cloudflare 1015 currently emits Retry-After and a JSON retry_after value.
    header = None
    try:
        header = exc.headers.get("Retry-After") if exc.headers else None
    except Exception:
        header = None
    for raw in (header,):
        try:
            if raw not in (None, ""):
                return max(1.0, float(raw))
        except Exception:
            pass
    try:
        payload = json.loads(detail) if detail else {}
        value = payload.get("retry_after") if isinstance(payload, dict) else None
        if value not in (None, ""):
            return max(1.0, float(value))
    except Exception:
        pass
    return 30.0 if exc.code == 429 else 5.0


def request_authenticated_websocket_url_sync(token: str, app_id: str, account_id: str) -> str:
    account_id = account_id.strip()
    if not account_id:
        raise RuntimeError("DERIV_ACCOUNT_ID is required")

    encoded_account = urllib.parse.quote(account_id, safe="")
    url = f"{DERIV_OPTIONS_REST_BASE}/trading/v1/options/accounts/{encoded_account}/otp"
    request = urllib.request.Request(
        url,
        method="POST",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Deriv-App-ID": app_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    global _OTP_LAST_REQUEST_MONOTONIC, _OTP_BLOCK_UNTIL_MONOTONIC
    # Serialize both threads AND sibling app processes. Main bot, Rise/Fall,
    # Strategy Lab and the web dashboard all share the same application root.
    with _OTP_REQUEST_LOCK:
        process_fd: Optional[int] = None
        try:
            process_fd = _acquire_otp_process_lock()
            state = _read_otp_shared_state()
            now_epoch = time.time()
            wait_until = max(
                float(state.get("block_until") or 0.0),
                float(state.get("last_request") or 0.0) + _OTP_MIN_INTERVAL_SECONDS,
            )
            wait_for = max(0.0, wait_until - now_epoch)
            if wait_for > 0:
                LOGGER.warning(
                    "Shared Deriv OTP cooldown active; waiting %.1fs before requesting a new authenticated URL",
                    wait_for,
                )
                time.sleep(wait_for)

            request_epoch = time.time()
            _OTP_LAST_REQUEST_MONOTONIC = time.monotonic()
            _write_otp_shared_state(
                last_request=request_epoch,
                block_until=max(request_epoch, float(state.get("block_until") or 0.0)),
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                if exc.code == 429:
                    retry_after = _retry_after_seconds(exc, detail)
                    block_until = time.time() + retry_after
                    _OTP_BLOCK_UNTIL_MONOTONIC = time.monotonic() + retry_after
                    _write_otp_shared_state(
                        last_request=time.time(),
                        block_until=block_until,
                    )
                    raise OTPRateLimitError(
                        f"Deriv authenticated URL endpoint is rate limited (HTTP 429). "
                        f"Retry after about {retry_after:.0f}s. {detail or exc.reason}",
                        retry_after=retry_after,
                    ) from exc
                raise RuntimeError(
                    f"Failed to obtain authenticated WebSocket URL (HTTP {exc.code}): "
                    f"{detail or exc.reason}"
                ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Failed to contact Deriv OTP endpoint: {exc.reason}") from exc
        finally:
            _release_otp_process_lock(process_fd)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Deriv OTP endpoint returned invalid JSON: {raw[:300]}") from exc
    return _extract_websocket_url(payload)


async def request_authenticated_websocket_url(token: str, app_id: str, account_id: str) -> str:
    return await asyncio.to_thread(
        request_authenticated_websocket_url_sync,
        token,
        app_id,
        account_id,
    )


class DerivAPIError(RuntimeError):
    def __init__(self, error: Dict[str, Any]):
        self.code = str(error.get("code", "UnknownError"))
        self.message = str(error.get("message", "Unknown Deriv API error"))
        super().__init__(f"{self.code}: {self.message}")


# ---------------------------------------------------------------------------
# Configuration and session settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BotConfig:
    multiplier: int
    take_profit: Decimal
    dashboard_seconds: int
    symbol_names: tuple[str, ...]
    strategies: Dict[str, Dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "BotConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        profiles: Dict[str, Dict[str, Any]] = {}
        supplied = raw.get("strategies")
        if isinstance(supplied, dict):
            for name, values in supplied.items():
                if isinstance(values, dict):
                    profiles[str(name)] = dict(values)

        # Backward compatibility with older V2.x config files.
        legacy_ema = raw.get("strategy")
        legacy_hammer = raw.get("hammer_strategy")
        if "ema_rsi" not in profiles and isinstance(legacy_ema, dict):
            profiles["ema_rsi"] = dict(legacy_ema)
        if "hammer" not in profiles and isinstance(legacy_hammer, dict):
            profiles["hammer"] = dict(legacy_hammer)
            # Old V2.x Hammer configs had only Hammer + Shooting Star behavior.
            # Preserve that exact scope unless the newer enabled_patterns field
            # is explicitly present.
            profiles["hammer"].setdefault("enabled_patterns", ["hammer", "shooting_star"])
            # Legacy Hammer configs were tick-count + wick/body-ratio based.
            # Keep their exact geometry unless the newer fields are explicit.
            profiles["hammer"].setdefault("candle_mode", "ticks")
            profiles["hammer"].setdefault("shape_mode", "ratio")

        defaults = {
            "ema_rsi": {
                "ema_fast": 8, "ema_slow": 21, "rsi_period": 12,
                "up_rsi_min": 54, "up_rsi_max": 66,
                "down_rsi_min": 34, "down_rsi_max": 46,
                "momentum_ticks": 5, "momentum_required": 4,
                "confirmations": 2, "cooldown_ticks": 3, "history_count": 250,
            },
            "hammer": {
                "candle_mode": "time", "candle_seconds": 60, "candle_ticks": 8,
                "shape_mode": "percent",
                "min_long_wick_body_ratio": 2.2, "max_opposite_wick_body_ratio": 0.5,
                "min_body_range_ratio": 0.05, "max_body_range_ratio": 0.45,
                "min_body_pct": 5.0, "max_body_pct": 45.0,
                "min_long_wick_pct": 35.0, "max_opposite_wick_pct": 12.0,
                "body_edge_zone_pct": 45.0, "flat_wick_max_pct": 5.0,
                "rejection_min_body_pct": 20.0, "rejection_max_body_pct": 82.0,
                "rejection_min_wick_pct": 15.0, "rejection_max_opposite_wick_pct": 6.0,
                "context_mode": "trend", "context_agreement_pct": 60.0,
                "context_candles": 3, "breakout_expiry_ticks": 10,
                "cooldown_ticks": 3, "history_count": 420,
                "enabled_patterns": ["hammer", "bullish_hammer", "bearish_hammer", "inverted_hammer", "bullish_inverted_hammer", "bearish_inverted_hammer", "dragonfly_doji", "hanging_man", "shooting_star", "bullish_upper_wick_rejection", "bearish_upper_wick_rejection", "flat_bottom_bullish_rejection", "flat_bottom_bearish_rejection", "gravestone_doji", "bullish_engulfing", "bearish_engulfing", "bullish_harami", "bearish_harami", "tweezer_bottom", "tweezer_top"],
                "confirmation_mode": "breakout", "pattern_conflict_policy": "block",
                "min_pattern_score": 0.55, "doji_body_ratio": 0.08,
                "doji_body_pct": 8.0, "marubozu_body_ratio": 0.80,
                "marubozu_body_pct": 80.0,
            },
            "macd": {
                "fast_period": 12, "slow_period": 26, "signal_period": 9,
                "histogram_threshold": 0.01, "confirmations": 2,
                "cooldown_ticks": 5, "history_count": 320,
            },
            "bollinger_reversion": {
                "period": 20, "stddev_multiplier": 2.0, "rsi_period": 14,
                "oversold": 35, "overbought": 65, "confirmations": 2,
                "cooldown_ticks": 4, "history_count": 280,
            },
            "bollinger_breakout": {
                "period": 20, "stddev_multiplier": 2.0,
                "momentum_ticks": 4, "momentum_required": 3,
                "confirmations": 2, "cooldown_ticks": 4, "history_count": 280,
            },
            "stochastic": {
                "lookback_period": 14, "smooth_k": 3, "smooth_d": 3,
                "oversold": 20, "overbought": 80, "confirmations": 1,
                "cooldown_ticks": 4, "history_count": 280,
            },
            "donchian": {
                "period": 20, "breakout_buffer": 0.0, "confirmations": 1,
                "cooldown_ticks": 5, "history_count": 280,
            },
            "rsi_pullback": {
                "trend_ema": 34, "pullback_ema": 8, "rsi_period": 14,
                "up_rsi_min": 48, "up_rsi_max": 60,
                "down_rsi_min": 40, "down_rsi_max": 52,
                "confirmations": 1, "cooldown_ticks": 4, "history_count": 320,
            },
            "tick_momentum": {
                "lookback_ticks": 6, "required_directional_ticks": 5,
                "min_net_change": 0.2, "confirmations": 1,
                "cooldown_ticks": 3, "history_count": 220,
            },
        }
        for name, values in defaults.items():
            merged = dict(values)
            merged.update(profiles.get(name, {}))
            profiles[name] = merged

        return cls(
            multiplier=int(raw.get("multiplier", 750)),
            take_profit=Decimal(str(raw.get("take_profit", "0.10"))),
            dashboard_seconds=max(1, int(raw.get("dashboard_seconds", 3))),
            symbol_names=tuple(str(value) for value in raw.get(
                "symbol_display_names", ["Step 100 Index", "Step Index 100", "Step 100"]
            )),
            strategies=profiles,
        )

    def profile(self, name: str) -> Dict[str, Any]:
        try:
            return self.strategies[name]
        except KeyError as exc:
            raise RuntimeError(f"Strategy configuration is missing: {name}") from exc

    # Compatibility properties used by the original EMA/Hammer code and logs.
    @property
    def ema_fast(self) -> int:
        return int(self.profile("ema_rsi")["ema_fast"])

    @property
    def ema_slow(self) -> int:
        return int(self.profile("ema_rsi")["ema_slow"])

    @property
    def rsi_period(self) -> int:
        return int(self.profile("ema_rsi")["rsi_period"])

    @property
    def up_rsi_min(self) -> Decimal:
        return Decimal(str(self.profile("ema_rsi")["up_rsi_min"]))

    @property
    def up_rsi_max(self) -> Decimal:
        return Decimal(str(self.profile("ema_rsi")["up_rsi_max"]))

    @property
    def down_rsi_min(self) -> Decimal:
        return Decimal(str(self.profile("ema_rsi")["down_rsi_min"]))

    @property
    def down_rsi_max(self) -> Decimal:
        return Decimal(str(self.profile("ema_rsi")["down_rsi_max"]))

    @property
    def momentum_ticks(self) -> int:
        return int(self.profile("ema_rsi")["momentum_ticks"])

    @property
    def momentum_required(self) -> int:
        return int(self.profile("ema_rsi")["momentum_required"])

    @property
    def confirmations(self) -> int:
        return int(self.profile("ema_rsi")["confirmations"])

    @property
    def cooldown_ticks(self) -> int:
        return int(self.profile("ema_rsi")["cooldown_ticks"])

    @property
    def history_count(self) -> int:
        return int(self.profile("ema_rsi")["history_count"])

    @property
    def hammer_candle_ticks(self) -> int:
        return max(3, int(self.profile("hammer")["candle_ticks"]))

    @property
    def hammer_min_long_wick_body(self) -> float:
        return max(1.0, float(self.profile("hammer")["min_long_wick_body_ratio"]))

    @property
    def hammer_max_opposite_wick_body(self) -> float:
        return max(0.0, float(self.profile("hammer")["max_opposite_wick_body_ratio"]))

    @property
    def hammer_min_body_range(self) -> float:
        return min(0.5, max(0.0, float(self.profile("hammer")["min_body_range_ratio"])))

    @property
    def hammer_max_body_range(self) -> float:
        return min(1.0, max(0.01, float(self.profile("hammer")["max_body_range_ratio"])))

    @property
    def hammer_context_candles(self) -> int:
        return max(1, int(self.profile("hammer")["context_candles"]))

    @property
    def hammer_breakout_expiry_ticks(self) -> int:
        return max(1, int(self.profile("hammer")["breakout_expiry_ticks"]))


@dataclass(frozen=True)
class SessionSettings:
    capital: Decimal
    split_count: int
    max_open_trades: int
    batch_mode: str  # one | all | count (legacy-compatible opening mode)
    compound: bool
    loss_policy: str  # continue | first | consecutive | total
    loss_count: int
    profit_target: Decimal
    loss_limit: Decimal
    win_target: int
    take_profit: Decimal
    take_profit_mode: str  # fixed | adaptive | smart | hybrid
    take_profit_max: Decimal
    multiplier: int
    take_profit_unit: str = "amount"  # amount | ticks (all profit-exit modes)
    take_profit_ticks: int = 3
    take_profit_ticks_max: int = 8
    tick_tp_server_backup: Decimal = Decimal("2.00")
    split_delay_seconds: float = 0.0
    strategy_mode: str = "ema_rsi"  # single strategy ID or "ensemble"
    ensemble_preset: str = "custom"  # custom | best | all
    ensemble_strategies: tuple[str, ...] = ()
    ensemble_policy: str = "regime"  # regime | majority | weighted | any | unanimous
    ensemble_min_votes: int = 2
    ensemble_memory_ticks: int = 8
    ensemble_confirmations: int = 1
    ensemble_cooldown_ticks: int = 3
    ensemble_regime_lookback: int = 24
    ensemble_trend_threshold: float = 0.45
    ensemble_weight_threshold: float = 1.25
    ensemble_conflict_margin: float = 0.20
    ensemble_weights: Dict[str, float] = field(default_factory=dict)
    ensemble_profile_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    server_stop_loss: Decimal = Decimal("0")
    smart_activation: Decimal = Decimal("0.15")
    smart_profit_floor: Decimal = Decimal("0.10")
    smart_trail_percent: Decimal = Decimal("30")
    smart_min_distance: Decimal = Decimal("0.05")
    smart_activation_ticks: int = 3
    smart_profit_floor_ticks: int = 1
    smart_min_distance_ticks: int = 1
    smart_adverse_ticks: int = 3
    smart_adverse_window: int = 4
    smart_opposite_signal: bool = True
    smart_server_take_profit: Decimal = Decimal("2.00")
    # Recovery-aware local loss management. Amount fields are positive currency
    # values; the engine compares them against negative floating P/L.
    smart_loss_enabled: bool = False
    smart_loss_soft_trigger: Decimal = Decimal("0.60")
    smart_loss_reset_level: Decimal = Decimal("0.20")
    smart_loss_hard_cap: Decimal = Decimal("2.50")
    smart_loss_grace_ticks: int = 8
    smart_loss_max_watch_ticks: int = 30
    smart_loss_adverse_ticks: int = 4
    smart_loss_adverse_window: int = 6
    smart_loss_opposite_signal: bool = True
    smart_loss_rescue_profit: Decimal = Decimal("0.02")
    trades_per_signal: int = 0  # 0 = use legacy batch_mode; otherwise exact max positions per fresh signal

    @property
    def stop_after_loss(self) -> bool:
        return self.loss_policy != "continue"


@dataclass
class TradePosition:
    contract_id: int
    direction: str
    stake: Decimal
    take_profit: Decimal
    opened_at: datetime
    take_profit_unit: str = "amount"
    take_profit_ticks: int = 0
    entry_spot: Optional[float] = None
    favorable_ticks: float = 0.0
    peak_favorable_ticks: float = 0.0
    worst_ticks: float = 0.0
    entry_pattern: str = ""
    current_profit: Decimal = Decimal("0")
    peak_profit: Decimal = Decimal("0")
    trailing_floor: Decimal = Decimal("0")
    trailing_floor_ticks: float = 0.0
    protection_active: bool = False
    status: str = "open"
    subscription_id: str = ""
    closing_requested: bool = False
    closing_reason: str = ""
    last_quote: Optional[float] = None
    adverse_tick_history: list[bool] = field(default_factory=list)
    entry_sources: str = ""
    entry_regime: str = ""
    entry_vote_summary: str = ""
    worst_profit: Decimal = Decimal("0")
    loss_guard_active: bool = False
    loss_guard_ever_triggered: bool = False
    loss_guard_ticks: int = 0
    loss_guard_trough: Decimal = Decimal("0")
    loss_guard_trigger_count: int = 0
    loss_guard_reset_count: int = 0
    loss_adverse_tick_history: list[bool] = field(default_factory=list)


@dataclass
class SessionStats:
    started_at: float = field(default_factory=time.monotonic)
    realized_profit: Decimal = Decimal("0")
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    trades: int = 0
    last_signal: str = "WAITING"
    stop_reason: str = ""

    @property
    def win_rate(self) -> Decimal:
        if self.trades <= 0:
            return Decimal("0")
        return (Decimal(self.wins) / Decimal(self.trades) * Decimal("100")).quantize(
            Decimal("0.1")
        )


# ---------------------------------------------------------------------------
# Money management
# ---------------------------------------------------------------------------


class MoneyManager:
    """Calculates split stakes and tracks the session allocation.

    ``capital`` is a session allocation, not a separate Deriv wallet. In fixed
    mode, every slot retains its original size. In compound mode, the slot size
    is recalculated only when there are no open positions, preventing a mixed
    batch from unexpectedly exceeding the allocation.
    """

    def __init__(self, settings: SessionSettings):
        self.settings = settings
        self.realized_profit = Decimal("0")
        self._slot_stake = Decimal("0")
        self.refresh_slot_stake()

    @staticmethod
    def floor_money(value: Decimal) -> Decimal:
        return value.quantize(MONEY_STEP, rounding=ROUND_DOWN)

    @property
    def effective_capital(self) -> Decimal:
        if not self.settings.compound:
            return self.settings.capital
        return max(Decimal("0"), self.settings.capital + self.realized_profit)

    @property
    def slot_stake(self) -> Decimal:
        return self._slot_stake

    def refresh_slot_stake(self) -> Decimal:
        self._slot_stake = self.floor_money(
            self.effective_capital / Decimal(self.settings.split_count)
        )
        return self._slot_stake

    def register_result(self, profit: Decimal, positions_are_flat: bool) -> None:
        self.realized_profit += profit
        if self.settings.compound and positions_are_flat:
            self.refresh_slot_stake()

    def available_slots(self, open_count: int, opening_count: int = 0) -> int:
        in_use = open_count + opening_count
        split_available = self.settings.split_count - in_use
        max_open_available = self.settings.max_open_trades - in_use
        return max(0, min(split_available, max_open_available))


# ---------------------------------------------------------------------------
# WebSocket dispatcher
# ---------------------------------------------------------------------------


MessageHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class DerivWebSocketClient:
    def __init__(self, ws_url: str):
        self.url = ws_url
        self.ws: Any = None
        self.receiver_task: Optional[asyncio.Task[None]] = None
        self.keepalive_task: Optional[asyncio.Task[None]] = None
        self.pending: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self.handlers: Dict[str, list[MessageHandler]] = {}
        self.req_id = 0
        self.send_lock = asyncio.Lock()
        self.closed = asyncio.Event()

    async def connect(self) -> None:
        self.ws = await websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_queue=2048,
        )
        self.closed.clear()
        self.receiver_task = asyncio.create_task(self._receiver(), name="deriv-receiver")
        self.keepalive_task = asyncio.create_task(self._keepalive(), name="deriv-keepalive")

    def add_handler(self, msg_type: str, handler: MessageHandler) -> None:
        self.handlers.setdefault(msg_type, []).append(handler)

    async def request(self, payload: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("WebSocket is not connected")

        self.req_id += 1
        req_id = self.req_id
        body = dict(payload)
        body["req_id"] = req_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self.pending[req_id] = future
        try:
            async with self.send_lock:
                await self.ws.send(json.dumps(body))
            response = await asyncio.wait_for(future, timeout=timeout)
            if "error" in response:
                raise DerivAPIError(response["error"])
            return response
        finally:
            self.pending.pop(req_id, None)

    @staticmethod
    def _consume_future_exception(future: asyncio.Future[Any]) -> None:
        """Prevent orphaned request futures from producing noisy warnings."""
        if future.cancelled():
            return
        try:
            future.exception()
        except Exception:
            pass

    async def _receiver(self) -> None:
        try:
            assert self.ws is not None
            async for raw in self.ws:
                message = json.loads(raw)
                req_id = message.get("req_id")
                if isinstance(req_id, int) and req_id in self.pending:
                    future = self.pending[req_id]
                    if not future.done():
                        future.set_result(message)

                msg_type = message.get("msg_type")
                if isinstance(msg_type, str):
                    for handler in tuple(self.handlers.get(msg_type, [])):
                        asyncio.create_task(self._safe_handle(handler, message))
        except ConnectionClosed as exc:
            LOGGER.error("Deriv connection closed: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Receiver stopped unexpectedly")
        finally:
            self.closed.set()
            error = ConnectionError("Deriv WebSocket disconnected")
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
                    future.add_done_callback(self._consume_future_exception)

    async def _safe_handle(self, handler: MessageHandler, message: Dict[str, Any]) -> None:
        try:
            await handler(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Message handler failed for %s", message.get("msg_type"))

    async def _keepalive(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                await self.request({"ping": 1}, timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("Keepalive stopped: %s; forcing reconnect state", exc)
            # Wake the main reconnect loop even if the receiver has not yet
            # noticed the broken transport.
            self.closed.set()
            try:
                if self.ws is not None:
                    await self.ws.close()
            except Exception:
                pass

    async def close(self) -> None:
        for task in (self.keepalive_task, self.receiver_task):
            if task and not task.done():
                task.cancel()
        if self.ws is not None:
            await self.ws.close()
        tasks = [task for task in (self.keepalive_task, self.receiver_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class TickStrategy:
    """Original EMA + RSI + tick-momentum strategy."""

    label = "EMA + RSI momentum"

    def __init__(self, config: BotConfig):
        self.cfg = config
        self.prices: Deque[float] = deque(maxlen=max(config.history_count, 300))
        self.up_confirmations = 0
        self.down_confirmations = 0

    def seed(self, prices: list[float]) -> None:
        self.prices.clear()
        self.prices.extend(float(price) for price in prices)
        self.reset_confirmation()

    def reset_confirmation(self) -> None:
        self.up_confirmations = 0
        self.down_confirmations = 0

    @staticmethod
    def ema(values: list[float], period: int) -> float:
        if len(values) < period:
            raise ValueError("Not enough prices for EMA")
        alpha = 2.0 / (period + 1.0)
        result = sum(values[:period]) / period
        for value in values[period:]:
            result = alpha * value + (1.0 - alpha) * result
        return result

    @staticmethod
    def rsi(values: list[float], period: int) -> float:
        if len(values) < period + 1:
            raise ValueError("Not enough prices for RSI")
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        recent = changes[-period:]
        gains = sum(max(change, 0.0) for change in recent) / period
        losses = sum(max(-change, 0.0) for change in recent) / period
        if losses == 0:
            return 100.0 if gains > 0 else 50.0
        if gains == 0:
            return 0.0
        relative_strength = gains / losses
        return 100.0 - (100.0 / (1.0 + relative_strength))

    def add_tick(self, price: float, epoch: Optional[int] = None) -> tuple[Optional[str], Dict[str, Any]]:
        self.prices.append(float(price))
        needed = max(
            self.cfg.ema_slow + 1,
            self.cfg.rsi_period + 1,
            self.cfg.momentum_ticks + 1,
        )
        if len(self.prices) < needed:
            return None, {}

        values = list(self.prices)
        ema_fast = self.ema(values, self.cfg.ema_fast)
        ema_slow = self.ema(values, self.cfg.ema_slow)
        rsi_value = self.rsi(values, self.cfg.rsi_period)

        recent = values[-(self.cfg.momentum_ticks + 1) :]
        changes = [recent[index] - recent[index - 1] for index in range(1, len(recent))]
        positive = sum(change > 0 for change in changes)
        negative = sum(change < 0 for change in changes)
        net_change = recent[-1] - recent[0]

        up_raw = (
            ema_fast > ema_slow
            and float(self.cfg.up_rsi_min) <= rsi_value <= float(self.cfg.up_rsi_max)
            and positive >= self.cfg.momentum_required
            and net_change > 0
        )
        down_raw = (
            ema_fast < ema_slow
            and float(self.cfg.down_rsi_min) <= rsi_value <= float(self.cfg.down_rsi_max)
            and negative >= self.cfg.momentum_required
            and net_change < 0
        )

        if up_raw:
            self.up_confirmations += 1
            self.down_confirmations = 0
        elif down_raw:
            self.down_confirmations += 1
            self.up_confirmations = 0
        else:
            self.reset_confirmation()

        signal_name: Optional[str] = None
        if self.up_confirmations >= self.cfg.confirmations:
            signal_name = "MULTUP"
            self.reset_confirmation()
        elif self.down_confirmations >= self.cfg.confirmations:
            signal_name = "MULTDOWN"
            self.reset_confirmation()

        return signal_name, {
            "strategy": "ema_rsi",
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi": rsi_value,
            "net_change": net_change,
            "positive_ticks": float(positive),
            "negative_ticks": float(negative),
        }


@dataclass(frozen=True)
class SyntheticCandle:
    open: float
    high: float
    low: float
    close: float
    start_epoch: int = 0
    end_epoch: int = 0


class HammerStrategy:
    """Synthetic tick-candle hammer and shooting-star breakout strategy.

    A bullish hammer requires a long lower wick after a short downward context.
    A bearish signal uses the mirrored pattern (shooting star / bearish hammer)
    after an upward context. A trade is emitted only when a later tick breaks
    above the bullish candle high or below the bearish candle low.
    """

    label = "Hammer candle breakout"

    def __init__(self, config: BotConfig):
        self.cfg = config
        self.candles: Deque[SyntheticCandle] = deque(maxlen=100)
        self.current_ticks: list[float] = []
        self.pending_direction: Optional[str] = None
        self.pending_level = 0.0
        self.pending_ticks_remaining = 0
        self.last_pattern = "NONE"
        self.last_candle: Optional[SyntheticCandle] = None

    def seed(self, prices: list[float]) -> None:
        self.candles.clear()
        self.current_ticks.clear()
        self.reset_confirmation()
        for raw_price in prices:
            self.current_ticks.append(float(raw_price))
            if len(self.current_ticks) >= self.cfg.hammer_candle_ticks:
                self.candles.append(self._make_candle(self.current_ticks))
                self.current_ticks.clear()

    def reset_confirmation(self) -> None:
        self.pending_direction = None
        self.pending_level = 0.0
        self.pending_ticks_remaining = 0

    @staticmethod
    def _make_candle(values: list[float]) -> SyntheticCandle:
        return SyntheticCandle(
            open=float(values[0]),
            high=max(values),
            low=min(values),
            close=float(values[-1]),
        )

    def _has_context(self, direction: str) -> bool:
        count = self.cfg.hammer_context_candles
        if len(self.candles) < count:
            return False
        recent = list(self.candles)[-count:]
        start = recent[0].open
        end = recent[-1].close
        required = max(1, (count + 1) // 2)
        bearish_candles = sum(candle.close < candle.open for candle in recent)
        bullish_candles = sum(candle.close > candle.open for candle in recent)
        if direction == "MULTUP":
            return end < start and bearish_candles >= required
        return end > start and bullish_candles >= required

    def _classify(self, candle: SyntheticCandle) -> Optional[str]:
        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return None

        raw_body = abs(candle.close - candle.open)
        # A tiny floor lets doji-like hammers be measured without dividing by zero.
        body = max(raw_body, candle_range * 0.02, 1e-12)
        upper_wick = candle.high - max(candle.open, candle.close)
        lower_wick = min(candle.open, candle.close) - candle.low
        body_ratio = raw_body / candle_range
        body_low = min(candle.open, candle.close)
        body_high = max(candle.open, candle.close)

        bullish_shape = (
            self.cfg.hammer_min_body_range <= body_ratio <= self.cfg.hammer_max_body_range
            and lower_wick >= self.cfg.hammer_min_long_wick_body * body
            and upper_wick <= self.cfg.hammer_max_opposite_wick_body * body
            and body_low >= candle.low + candle_range * 0.55
        )
        bearish_shape = (
            self.cfg.hammer_min_body_range <= body_ratio <= self.cfg.hammer_max_body_range
            and upper_wick >= self.cfg.hammer_min_long_wick_body * body
            and lower_wick <= self.cfg.hammer_max_opposite_wick_body * body
            and body_high <= candle.low + candle_range * 0.45
        )

        if bullish_shape and self._has_context("MULTUP"):
            return "BULLISH_HAMMER"
        if bearish_shape and self._has_context("MULTDOWN"):
            return "BEARISH_HAMMER"
        return None

    def _set_pending(self, pattern: str, candle: SyntheticCandle) -> None:
        if pattern == "BULLISH_HAMMER":
            self.pending_direction = "MULTUP"
            self.pending_level = candle.high
        else:
            self.pending_direction = "MULTDOWN"
            self.pending_level = candle.low
        self.pending_ticks_remaining = self.cfg.hammer_breakout_expiry_ticks

    def add_tick(self, price: float) -> tuple[Optional[str], Dict[str, Any]]:
        price = float(price)
        signal_name: Optional[str] = None

        # Confirm a previously completed hammer with a breakout on a later tick.
        if self.pending_direction is not None:
            if self.pending_direction == "MULTUP" and price > self.pending_level:
                signal_name = "MULTUP"
                self.reset_confirmation()
            elif self.pending_direction == "MULTDOWN" and price < self.pending_level:
                signal_name = "MULTDOWN"
                self.reset_confirmation()
            else:
                self.pending_ticks_remaining -= 1
                if self.pending_ticks_remaining <= 0:
                    self.reset_confirmation()

        self.current_ticks.append(price)
        if len(self.current_ticks) >= self.cfg.hammer_candle_ticks:
            completed = self._make_candle(self.current_ticks)
            self.current_ticks.clear()
            pattern = self._classify(completed)
            self.candles.append(completed)
            self.last_candle = completed
            self.last_pattern = pattern or "NONE"
            if pattern and signal_name is None:
                self._set_pending(pattern, completed)

        candle = self.last_candle
        metrics: Dict[str, Any] = {
            "strategy": "hammer",
            "last_pattern": self.last_pattern,
            "pending": self.pending_direction or "NONE",
            "pending_level": self.pending_level,
            "pending_ticks": self.pending_ticks_remaining,
            "candle_progress": len(self.current_ticks),
            "candle_ticks": self.cfg.hammer_candle_ticks,
        }
        if candle is not None:
            candle_range = candle.high - candle.low
            raw_body = abs(candle.close - candle.open)
            metrics.update(
                {
                    "candle_open": candle.open,
                    "candle_high": candle.high,
                    "candle_low": candle.low,
                    "candle_close": candle.close,
                    "body_range_ratio": raw_body / candle_range if candle_range > 0 else 0.0,
                }
            )
        return signal_name, metrics


class CombinedStrategy:
    """Run both strategies and emit one safe, de-duplicated signal.

    A signal from either strategy is accepted. When both strategies signal on
    the same tick they must agree on direction; conflicting directions are
    ignored. The nested metrics are kept so adaptive take-profit can reward
    confluence without opening duplicate trades.
    """

    label = "EMA + RSI OR Hammer (combined)"

    def __init__(self, config: BotConfig):
        self.ema = TickStrategy(config)
        self.hammer = HammerStrategy(config)

    def seed(self, prices: list[float]) -> None:
        self.ema.seed(prices)
        self.hammer.seed(prices)

    def reset_confirmation(self) -> None:
        self.ema.reset_confirmation()
        self.hammer.reset_confirmation()

    def add_tick(self, price: float) -> tuple[Optional[str], Dict[str, Any]]:
        ema_signal, ema_metrics = self.ema.add_tick(price)
        hammer_signal, hammer_metrics = self.hammer.add_tick(price)

        signal: Optional[str]
        conflict = bool(ema_signal and hammer_signal and ema_signal != hammer_signal)
        if conflict:
            signal = None
        elif ema_signal and hammer_signal:
            signal = ema_signal
        else:
            signal = ema_signal or hammer_signal

        sources = []
        if ema_signal:
            sources.append("EMA_RSI")
        if hammer_signal:
            sources.append("HAMMER")

        return signal, {
            "strategy": "both",
            "ema": ema_metrics,
            "hammer": hammer_metrics,
            "ema_signal": ema_signal or "NONE",
            "hammer_signal": hammer_signal or "NONE",
            "signal_sources": "+".join(sources) if sources else "NONE",
            "conflict": conflict,
        }



# ---------------------------------------------------------------------------
# V2.7 exclusive multi-strategy engine
# ---------------------------------------------------------------------------


class IndicatorTools:
    @staticmethod
    def ema(values: list[float], period: int) -> float:
        if period < 1 or len(values) < period:
            raise ValueError("Not enough values for EMA")
        alpha = 2.0 / (period + 1.0)
        result = sum(values[:period]) / period
        for value in values[period:]:
            result = alpha * value + (1.0 - alpha) * result
        return result

    @staticmethod
    def ema_series(values: list[float], period: int) -> list[Optional[float]]:
        result: list[Optional[float]] = [None] * len(values)
        if period < 1 or len(values) < period:
            return result
        current = sum(values[:period]) / period
        result[period - 1] = current
        alpha = 2.0 / (period + 1.0)
        for index in range(period, len(values)):
            current = alpha * values[index] + (1.0 - alpha) * current
            result[index] = current
        return result

    @staticmethod
    def rsi(values: list[float], period: int) -> float:
        if period < 1 or len(values) < period + 1:
            raise ValueError("Not enough values for RSI")
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        recent = changes[-period:]
        gains = sum(max(change, 0.0) for change in recent) / period
        losses = sum(max(-change, 0.0) for change in recent) / period
        if losses == 0:
            return 100.0 if gains > 0 else 50.0
        if gains == 0:
            return 0.0
        relative_strength = gains / losses
        return 100.0 - 100.0 / (1.0 + relative_strength)

    @staticmethod
    def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    @staticmethod
    def direction_counts(values: list[float], changes_count: int) -> tuple[int, int, float]:
        recent = values[-(changes_count + 1):]
        changes = [recent[index] - recent[index - 1] for index in range(1, len(recent))]
        positive = sum(change > 0 for change in changes)
        negative = sum(change < 0 for change in changes)
        return positive, negative, recent[-1] - recent[0]


class DirectionalSignalGate:
    """Require repeated alignment, then wait for a neutral state before rearming."""

    def __init__(self, confirmations: int, cooldown_ticks: int):
        self.confirmations = max(1, int(confirmations))
        self.cooldown_ticks = max(0, int(cooldown_ticks))
        self.cooldown_remaining = 0
        self.up_count = 0
        self.down_count = 0
        self.armed = True

    def reset(self) -> None:
        self.cooldown_remaining = 0
        self.up_count = 0
        self.down_count = 0
        self.armed = True

    def update(self, direction: Optional[str]) -> Optional[str]:
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            return None
        if direction is None:
            self.up_count = 0
            self.down_count = 0
            self.armed = True
            return None
        if not self.armed:
            return None
        if direction == "MULTUP":
            self.up_count += 1
            self.down_count = 0
            count = self.up_count
        else:
            self.down_count += 1
            self.up_count = 0
            count = self.down_count
        if count < self.confirmations:
            return None
        self.up_count = 0
        self.down_count = 0
        self.armed = False
        self.cooldown_remaining = self.cooldown_ticks
        return direction


class TickStrategy:
    label = "EMA + RSI Momentum"
    strategy_id = "ema_rsi"

    def __init__(self, config: BotConfig):
        self.cfg = config
        self.p = config.profile(self.strategy_id)
        self.history_count = max(50, int(self.p.get("history_count", 250)))
        self.minimum_history = max(
            int(self.p["ema_slow"]) + 1,
            int(self.p["rsi_period"]) + 1,
            int(self.p["momentum_ticks"]) + 1,
        )
        self.prices: Deque[float] = deque(maxlen=max(self.history_count, 500))
        self.gate = DirectionalSignalGate(self.p["confirmations"], self.p["cooldown_ticks"])

    def seed(self, prices: list[float]) -> None:
        self.prices.clear()
        self.prices.extend(float(price) for price in prices)
        self.reset_confirmation()

    def reset_confirmation(self) -> None:
        self.gate.reset()

    def add_tick(self, price: float) -> tuple[Optional[str], Dict[str, Any]]:
        self.prices.append(float(price))
        if len(self.prices) < self.minimum_history:
            return None, {}
        values = list(self.prices)
        ema_fast = IndicatorTools.ema(values, int(self.p["ema_fast"]))
        ema_slow = IndicatorTools.ema(values, int(self.p["ema_slow"]))
        rsi_value = IndicatorTools.rsi(values, int(self.p["rsi_period"]))
        positive, negative, net_change = IndicatorTools.direction_counts(values, int(self.p["momentum_ticks"]))
        up_raw = (
            ema_fast > ema_slow
            and float(self.p["up_rsi_min"]) <= rsi_value <= float(self.p["up_rsi_max"])
            and positive >= int(self.p["momentum_required"])
            and net_change > 0
        )
        down_raw = (
            ema_fast < ema_slow
            and float(self.p["down_rsi_min"]) <= rsi_value <= float(self.p["down_rsi_max"])
            and negative >= int(self.p["momentum_required"])
            and net_change < 0
        )
        raw = "MULTUP" if up_raw else "MULTDOWN" if down_raw else None
        signal = self.gate.update(raw)
        total = max(1, positive + negative)
        favorable = positive if raw == "MULTUP" else negative if raw == "MULTDOWN" else max(positive, negative)
        strength = IndicatorTools.clamp(favorable / total)
        return signal, {
            "strategy": self.strategy_id, "ema_fast": ema_fast, "ema_slow": ema_slow,
            "rsi": rsi_value, "net_change": net_change,
            "positive_ticks": float(positive), "negative_ticks": float(negative),
            "strength": strength,
        }

    def dashboard_lines(self, metrics: Dict[str, Any]) -> list[str]:
        if not metrics:
            return []
        return [
            f"EMA{self.p['ema_fast']}: {metrics.get('ema_fast', 0):.5f}  "
            f"EMA{self.p['ema_slow']}: {metrics.get('ema_slow', 0):.5f}  "
            f"RSI: {metrics.get('rsi', 0):.1f}"
        ]


class HammerStrategy:
    """Chart-aligned selectable candlestick-pattern engine (legacy id ``hammer``).

    V2.17 keeps the old strategy id for config compatibility, but expands the
    detector into a family/sub-type engine. Pattern geometry can be configured
    directly in percentages of the candle range so the UI matches what a trader
    sees on a 1-minute chart.
    """
    label = "Candlestick Pattern Engine Pro"
    strategy_id = "hammer"
    PATTERN_LABELS = {
        # Hammer / rejection family
        "hammer": "Hammer (Either Body)",
        "bullish_hammer": "Bullish Hammer",
        "bearish_hammer": "Bearish Hammer",
        "inverted_hammer": "Inverted Hammer (Either Body)",
        "bullish_inverted_hammer": "Bullish Inverted Hammer",
        "bearish_inverted_hammer": "Bearish Inverted Hammer",
        "hanging_man": "Hanging Man (Either Body)",
        "bullish_hanging_man": "Bullish Hanging Man",
        "bearish_hanging_man": "Bearish Hanging Man",
        "shooting_star": "Shooting Star (Either Body)",
        "bullish_shooting_star": "Bullish Shooting Star",
        "bearish_shooting_star": "Bearish Shooting Star",
        "bullish_upper_wick_rejection": "Bullish Upper-Wick Rejection",
        "bearish_upper_wick_rejection": "Bearish Upper-Wick Rejection",
        "bullish_lower_wick_rejection": "Bullish Lower-Wick Rejection",
        "bearish_lower_wick_rejection": "Bearish Lower-Wick Rejection",
        "flat_bottom_bullish_rejection": "Flat-Bottom Bullish Rejection",
        "flat_bottom_bearish_rejection": "Flat-Bottom Bearish Rejection",
        "bullish_belt_hold": "Bullish Belt Hold",
        "bearish_belt_hold": "Bearish Belt Hold",
        # Doji / indecision family
        "standard_doji": "Standard Doji",
        "dragonfly_doji": "Dragonfly Doji",
        "gravestone_doji": "Gravestone Doji",
        "long_legged_doji": "Long-Legged Doji",
        "rickshaw_man": "Rickshaw Man Doji",
        "four_price_doji": "Four-Price Doji",
        "spinning_top": "Spinning Top",
        "high_wave": "High-Wave Candle",
        # Body / two-candle reversals
        "bullish_engulfing": "Bullish Engulfing",
        "bearish_engulfing": "Bearish Engulfing",
        "bullish_harami": "Bullish Harami",
        "bearish_harami": "Bearish Harami",
        "piercing_line": "Piercing Line",
        "dark_cloud_cover": "Dark Cloud Cover",
        "tweezer_bottom": "Tweezer Bottom",
        "tweezer_top": "Tweezer Top",
        # Star family
        "morning_star": "Morning Star",
        "evening_star": "Evening Star",
        "morning_doji_star": "Morning Doji Star",
        "evening_doji_star": "Evening Doji Star",
        # Momentum / continuation family
        "bullish_marubozu": "Bullish Marubozu",
        "bearish_marubozu": "Bearish Marubozu",
        "three_white_soldiers": "Three White Soldiers",
        "three_black_crows": "Three Black Crows",
    }
    ALL_PATTERNS = tuple(PATTERN_LABELS)

    def __init__(self, config: BotConfig):
        self.cfg = config
        self.p = config.profile(self.strategy_id)
        self.candle_mode = str(self.p.get("candle_mode", "time")).strip().lower()
        if self.candle_mode not in {"ticks", "time"}:
            self.candle_mode = "time"
        self.candle_seconds = max(5, int(self.p.get("candle_seconds", 60)))
        self.candle_ticks = max(3, int(self.p.get("candle_ticks", 8)))
        self.context_candles = max(1, int(self.p.get("context_candles", 3)))
        estimated = self.candle_ticks if self.candle_mode == "ticks" else self.candle_seconds
        self.history_count = max(int(self.p.get("history_count", 420)), estimated * (self.context_candles + 4))
        self.minimum_history = estimated * (self.context_candles + 3)

        requested = self.p.get("enabled_patterns") or ["hammer", "shooting_star"]
        self.enabled_patterns = tuple(x for x in requested if x in self.ALL_PATTERNS) or ("hammer", "shooting_star")
        self.confirmation_mode = str(self.p.get("confirmation_mode", "breakout")).lower()
        if self.confirmation_mode not in {"breakout", "close"}:
            self.confirmation_mode = "breakout"
        self.pattern_conflict_policy = str(self.p.get("pattern_conflict_policy", "block")).lower()
        if self.pattern_conflict_policy not in {"block", "highest_score"}:
            self.pattern_conflict_policy = "block"
        self.min_pattern_score = self._clamp(float(self.p.get("min_pattern_score", .55)))

        self.shape_mode = str(self.p.get("shape_mode", "percent")).lower()
        if self.shape_mode not in {"percent", "ratio"}:
            self.shape_mode = "percent"
        # Percent settings are human-facing percentages of the full candle range.
        self.min_body_pct = self._pct("min_body_pct", float(self.p.get("min_body_range_ratio", .05)) * 100, 0, 95)
        self.max_body_pct = self._pct("max_body_pct", float(self.p.get("max_body_range_ratio", .45)) * 100, 1, 100)
        self.min_long_wick_pct = self._pct("min_long_wick_pct", 35.0, 0, 100)
        self.max_opposite_wick_pct = self._pct("max_opposite_wick_pct", 12.0, 0, 100)
        self.body_edge_zone_pct = self._pct("body_edge_zone_pct", 45.0, 5, 80)
        self.flat_wick_max_pct = self._pct("flat_wick_max_pct", 5.0, 0, 30)
        self.rejection_min_body_pct = self._pct("rejection_min_body_pct", 20.0, 0, 100)
        self.rejection_max_body_pct = self._pct("rejection_max_body_pct", 82.0, 1, 100)
        self.rejection_min_wick_pct = self._pct("rejection_min_wick_pct", 15.0, 0, 100)
        self.rejection_max_opposite_wick_pct = self._pct("rejection_max_opposite_wick_pct", 6.0, 0, 40)

        self.long_wick_body_ratio = max(0.1, float(self.p.get("min_long_wick_body_ratio", 2.2)))
        self.max_opposite_wick_body_ratio = max(0.0, float(self.p.get("max_opposite_wick_body_ratio", .5)))
        self.doji_body_ratio = self._pct("doji_body_pct", float(self.p.get("doji_body_ratio", .08)) * 100, 0.1, 35) / 100
        self.marubozu_body_ratio = self._pct("marubozu_body_pct", float(self.p.get("marubozu_body_ratio", .80)) * 100, 40, 99.9) / 100
        self.context_mode = str(self.p.get("context_mode", "trend")).lower()
        if self.context_mode not in {"off", "trend", "strict"}:
            self.context_mode = "trend"
        self.context_agreement_pct = self._pct("context_agreement_pct", 60.0, 0, 100)

        self.candles: Deque[SyntheticCandle] = deque(maxlen=240)
        self.current_ticks: list[float] = []
        self.current_bucket: Optional[int] = None
        self.current_bucket_start_epoch = 0
        self.last_tick_epoch = 0
        self.pending_direction: Optional[str] = None
        self.pending_level = 0.0
        self.pending_ticks_remaining = 0
        self.pending_pattern = "NONE"
        self.pending_score = 0.0
        self.last_pattern = "NONE"
        self.last_pattern_id = "NONE"
        self.last_patterns: list[str] = []
        self.last_pattern_score = 0.0
        self.last_candle: Optional[SyntheticCandle] = None
        self.pattern_counts = {x: 0 for x in self.ALL_PATTERNS}
        self.last_conflict = "NONE"

    def _pct(self, key: str, default: float, lo: float, hi: float) -> float:
        try:
            value = float(self.p.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    @staticmethod
    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @staticmethod
    def _body(c: SyntheticCandle) -> float:
        return abs(c.close - c.open)

    @staticmethod
    def _range(c: SyntheticCandle) -> float:
        return max(0.0, c.high - c.low)

    @staticmethod
    def _make_candle(v: list[float], start_epoch: int = 0, end_epoch: int = 0) -> SyntheticCandle:
        return SyntheticCandle(float(v[0]), max(v), min(v), float(v[-1]), int(start_epoch or 0), int(end_epoch or 0))

    def _shape(self, c: SyntheticCandle) -> Dict[str, float]:
        r = self._range(c)
        if r <= 1e-12:
            return {"range": 0.0, "body": 0.0, "body_ratio": 0.0, "upper": 0.0, "lower": 0.0, "upper_ratio": 0.0, "lower_ratio": 0.0, "body_pct": 0.0, "upper_pct": 0.0, "lower_pct": 0.0}
        b = self._body(c)
        u = max(0.0, c.high - max(c.open, c.close))
        l = max(0.0, min(c.open, c.close) - c.low)
        return {
            "range": r, "body": b, "body_ratio": b / r,
            "upper": u, "lower": l, "upper_ratio": u / r, "lower_ratio": l / r,
            "body_pct": b / r * 100, "upper_pct": u / r * 100, "lower_pct": l / r * 100,
        }

    def seed(self, prices: list[float], epochs: Optional[list[int]] = None) -> None:
        self.candles.clear(); self.current_ticks.clear(); self.current_bucket = None
        self.current_bucket_start_epoch = 0; self.last_tick_epoch = 0; self.reset_confirmation()
        if self.candle_mode == "time":
            use_epochs = epochs if epochs and len(epochs) == len(prices) else None
            if use_epochs is None:
                end = int(time.time()); use_epochs = list(range(end - len(prices) + 1, end + 1))
            for x, e in zip(prices, use_epochs):
                self._seed_time_tick(float(x), int(e))
        else:
            for x in prices:
                self.current_ticks.append(float(x))
                if len(self.current_ticks) >= self.candle_ticks:
                    self.candles.append(self._make_candle(self.current_ticks)); self.current_ticks.clear()

    def _seed_time_tick(self, price: float, epoch: int) -> None:
        bucket = int(epoch) // self.candle_seconds
        if self.current_bucket is None:
            self.current_bucket = bucket; self.current_bucket_start_epoch = bucket * self.candle_seconds
        elif bucket != self.current_bucket:
            if self.current_ticks:
                self.candles.append(self._make_candle(self.current_ticks, self.current_bucket_start_epoch, (self.current_bucket + 1) * self.candle_seconds - 1))
            self.current_ticks = []; self.current_bucket = bucket; self.current_bucket_start_epoch = bucket * self.candle_seconds
        self.current_ticks.append(float(price)); self.last_tick_epoch = int(epoch)

    def reset_confirmation(self) -> None:
        self.pending_direction = None; self.pending_level = 0.0; self.pending_ticks_remaining = 0
        self.pending_pattern = "NONE"; self.pending_score = 0.0

    def _has_context(self, direction: str) -> bool:
        if self.context_mode == "off":
            return True
        if len(self.candles) < self.context_candles:
            return False
        recent = list(self.candles)[-self.context_candles:]
        requested_pct = max(self.context_agreement_pct, 75.0) if self.context_mode == "strict" else self.context_agreement_pct
        required = max(1, int((len(recent) * requested_pct + 99.999) // 100))
        bearish = sum(c.close < c.open for c in recent)
        bullish = sum(c.close > c.open for c in recent)
        if direction == "MULTUP":
            return recent[-1].close <= recent[0].open and bearish >= required
        return recent[-1].close >= recent[0].open and bullish >= required

    def _context_direction(self) -> Optional[str]:
        if self._has_context("MULTUP"):
            return "MULTUP"
        if self._has_context("MULTDOWN"):
            return "MULTDOWN"
        return None

    def _wick_shapes(self, c: SyntheticCandle, sh: Dict[str, float]) -> tuple[bool, bool, float, float]:
        r = sh["range"]
        if r <= 1e-12:
            return False, False, 0.0, 0.0
        body = max(sh["body"], r * .01, 1e-12)
        blo = min(c.open, c.close); bhi = max(c.open, c.close)
        if self.shape_mode == "percent":
            lower_shape = (
                self.min_body_pct <= sh["body_pct"] <= self.max_body_pct
                and sh["lower_pct"] >= self.min_long_wick_pct
                and sh["upper_pct"] <= self.max_opposite_wick_pct
                and blo >= c.high - r * (self.body_edge_zone_pct / 100)
            )
            upper_shape = (
                self.min_body_pct <= sh["body_pct"] <= self.max_body_pct
                and sh["upper_pct"] >= self.min_long_wick_pct
                and sh["lower_pct"] <= self.max_opposite_wick_pct
                and bhi <= c.low + r * (self.body_edge_zone_pct / 100)
            )
            lower_quality = self._clamp(.45 + min(sh["lower_pct"] / max(self.min_long_wick_pct, 1), 2) * .20 + (100 - sh["body_pct"]) / 100 * .15)
            upper_quality = self._clamp(.45 + min(sh["upper_pct"] / max(self.min_long_wick_pct, 1), 2) * .20 + (100 - sh["body_pct"]) / 100 * .15)
        else:
            minb = float(self.p.get("min_body_range_ratio", .05)); maxb = float(self.p.get("max_body_range_ratio", .45))
            lower_shape = minb <= sh["body_ratio"] <= maxb and sh["lower"] >= self.long_wick_body_ratio * body and sh["upper"] <= self.max_opposite_wick_body_ratio * body and blo >= c.low + r * .55
            upper_shape = minb <= sh["body_ratio"] <= maxb and sh["upper"] >= self.long_wick_body_ratio * body and sh["lower"] <= self.max_opposite_wick_body_ratio * body and bhi <= c.low + r * .45
            lower_quality = self._clamp(.55 + .15 * min(2, sh["lower"] / body) / max(self.long_wick_body_ratio, .1) + .15 * (1 - sh["body_ratio"]))
            upper_quality = self._clamp(.55 + .15 * min(2, sh["upper"] / body) / max(self.long_wick_body_ratio, .1) + .15 * (1 - sh["body_ratio"]))
        return lower_shape, upper_shape, lower_quality, upper_quality

    def _append_contextual(self, out: list[tuple[str, str, float]], pattern: str, direction: str, score: float, body_color: Optional[str] = None, c: Optional[SyntheticCandle] = None) -> None:
        if pattern not in self.enabled_patterns or not self._has_context(direction):
            return
        if c is not None and body_color == "bullish" and not (c.close > c.open):
            return
        if c is not None and body_color == "bearish" and not (c.close < c.open):
            return
        out.append((pattern, direction, self._clamp(score)))

    def _detect_patterns(self, c: SyntheticCandle) -> list[tuple[str, str, float]]:
        out: list[tuple[str, str, float]] = []
        sh = self._shape(c); r = sh["range"]
        if r <= 1e-12:
            if "four_price_doji" in self.enabled_patterns:
                direction = self._context_direction()
                if direction:
                    out.append(("four_price_doji", direction, .55))
            return [m for m in out if m[2] >= self.min_pattern_score]

        br = sh["body_ratio"]; bullish = c.close > c.open; bearish = c.close < c.open
        lower_shape, upper_shape, lower_score, upper_score = self._wick_shapes(c, sh)

        # Classic wick patterns plus explicit body-color sub-types.
        if lower_shape:
            self._append_contextual(out, "hammer", "MULTUP", lower_score)
            self._append_contextual(out, "bullish_hammer", "MULTUP", lower_score + .03, "bullish", c)
            self._append_contextual(out, "bearish_hammer", "MULTUP", lower_score, "bearish", c)
            self._append_contextual(out, "hanging_man", "MULTDOWN", lower_score)
            self._append_contextual(out, "bullish_hanging_man", "MULTDOWN", lower_score, "bullish", c)
            self._append_contextual(out, "bearish_hanging_man", "MULTDOWN", lower_score + .03, "bearish", c)
        if upper_shape:
            self._append_contextual(out, "inverted_hammer", "MULTUP", upper_score)
            self._append_contextual(out, "bullish_inverted_hammer", "MULTUP", upper_score + .03, "bullish", c)
            self._append_contextual(out, "bearish_inverted_hammer", "MULTUP", upper_score, "bearish", c)
            self._append_contextual(out, "shooting_star", "MULTDOWN", upper_score)
            self._append_contextual(out, "bullish_shooting_star", "MULTDOWN", upper_score, "bullish", c)
            self._append_contextual(out, "bearish_shooting_star", "MULTDOWN", upper_score + .03, "bearish", c)

        # Wider rejection family: intentionally allows larger bodies than a
        # textbook hammer, covering the user's flat-bottom upper-wick examples.
        rejection_body_ok = self.rejection_min_body_pct <= sh["body_pct"] <= self.rejection_max_body_pct
        upper_reject = rejection_body_ok and sh["upper_pct"] >= self.rejection_min_wick_pct and sh["lower_pct"] <= self.rejection_max_opposite_wick_pct
        lower_reject = rejection_body_ok and sh["lower_pct"] >= self.rejection_min_wick_pct and sh["upper_pct"] <= self.rejection_max_opposite_wick_pct
        upper_reject_score = self._clamp(.50 + sh["upper_pct"] / 100 * .35 + (1 - sh["lower_pct"] / 100) * .10)
        lower_reject_score = self._clamp(.50 + sh["lower_pct"] / 100 * .35 + (1 - sh["upper_pct"] / 100) * .10)
        if upper_reject:
            self._append_contextual(out, "bullish_upper_wick_rejection", "MULTUP", upper_reject_score, "bullish", c)
            self._append_contextual(out, "bearish_upper_wick_rejection", "MULTDOWN", upper_reject_score, "bearish", c)
            if sh["lower_pct"] <= self.flat_wick_max_pct:
                self._append_contextual(out, "flat_bottom_bullish_rejection", "MULTUP", upper_reject_score + .05, "bullish", c)
                self._append_contextual(out, "flat_bottom_bearish_rejection", "MULTDOWN", upper_reject_score + .05, "bearish", c)
        if lower_reject:
            self._append_contextual(out, "bullish_lower_wick_rejection", "MULTUP", lower_reject_score, "bullish", c)
            self._append_contextual(out, "bearish_lower_wick_rejection", "MULTDOWN", lower_reject_score, "bearish", c)

        # Belt-hold candles: strong body starts near the extreme after context.
        if sh["body_pct"] >= 55 and sh["lower_pct"] <= self.flat_wick_max_pct and sh["upper_pct"] <= 25:
            self._append_contextual(out, "bullish_belt_hold", "MULTUP", .58 + sh["body_ratio"] * .25, "bullish", c)
        if sh["body_pct"] >= 55 and sh["upper_pct"] <= self.flat_wick_max_pct and sh["lower_pct"] <= 25:
            self._append_contextual(out, "bearish_belt_hold", "MULTDOWN", .58 + sh["body_ratio"] * .25, "bearish", c)

        # Doji / indecision family.
        if br <= self.doji_body_ratio:
            direction = self._context_direction()
            if direction and "standard_doji" in self.enabled_patterns and sh["upper_pct"] < 65 and sh["lower_pct"] < 65:
                out.append(("standard_doji", direction, self._clamp(.55 + (1 - br) * .15)))
            if sh["lower_ratio"] >= .65 and sh["upper_ratio"] <= .12 and "dragonfly_doji" in self.enabled_patterns:
                self._append_contextual(out, "dragonfly_doji", "MULTUP", .70 + sh["lower_ratio"] * .25)
            if sh["upper_ratio"] >= .65 and sh["lower_ratio"] <= .12 and "gravestone_doji" in self.enabled_patterns:
                self._append_contextual(out, "gravestone_doji", "MULTDOWN", .70 + sh["upper_ratio"] * .25)
            if sh["upper_ratio"] >= .28 and sh["lower_ratio"] >= .28 and direction and "long_legged_doji" in self.enabled_patterns:
                out.append(("long_legged_doji", direction, self._clamp(.60 + (sh["upper_ratio"] + sh["lower_ratio"]) * .18)))
            body_mid = (c.open + c.close) / 2
            center = c.low + r / 2
            if sh["upper_ratio"] >= .25 and sh["lower_ratio"] >= .25 and abs(body_mid - center) <= r * .12 and direction and "rickshaw_man" in self.enabled_patterns:
                out.append(("rickshaw_man", direction, self._clamp(.62 + (sh["upper_ratio"] + sh["lower_ratio"]) * .17)))

        if .08 < br <= .35 and sh["upper_ratio"] >= .22 and sh["lower_ratio"] >= .22 and "spinning_top" in self.enabled_patterns:
            direction = self._context_direction()
            if direction:
                out.append(("spinning_top", direction, self._clamp(.52 + (sh["upper_ratio"] + sh["lower_ratio"]) * .18)))
        if br <= .20 and sh["upper_ratio"] >= .30 and sh["lower_ratio"] >= .30 and "high_wave" in self.enabled_patterns:
            direction = self._context_direction()
            if direction:
                out.append(("high_wave", direction, self._clamp(.57 + (sh["upper_ratio"] + sh["lower_ratio"]) * .18)))

        prev = self.candles[-1] if self.candles else None
        if prev:
            pb = self._body(prev); mid = (prev.open + prev.close) / 2
            if "bullish_engulfing" in self.enabled_patterns and prev.close < prev.open and bullish and c.open <= prev.close and c.close >= prev.open and self._has_context("MULTUP"):
                out.append(("bullish_engulfing", "MULTUP", self._clamp(.65 + min(self._body(c) / max(pb, 1e-12), 2) * .15)))
            if "bearish_engulfing" in self.enabled_patterns and prev.close > prev.open and bearish and c.open >= prev.close and c.close <= prev.open and self._has_context("MULTDOWN"):
                out.append(("bearish_engulfing", "MULTDOWN", self._clamp(.65 + min(self._body(c) / max(pb, 1e-12), 2) * .15)))
            if "piercing_line" in self.enabled_patterns and prev.close < prev.open and bullish and c.close >= mid and c.close < prev.open and self._has_context("MULTUP"):
                out.append(("piercing_line", "MULTUP", self._clamp(.55 + (c.close - prev.close) / max(pb, 1e-12) * .35)))
            if "dark_cloud_cover" in self.enabled_patterns and prev.close > prev.open and bearish and c.close <= mid and c.close > prev.open and self._has_context("MULTDOWN"):
                out.append(("dark_cloud_cover", "MULTDOWN", self._clamp(.55 + (prev.close - c.close) / max(pb, 1e-12) * .35)))
            p_lo, p_hi = min(prev.open, prev.close), max(prev.open, prev.close)
            cur_lo, cur_hi = min(c.open, c.close), max(c.open, c.close)
            pr = max(self._range(prev), 1e-12)
            inside = cur_lo >= p_lo and cur_hi <= p_hi and self._body(c) <= max(self._body(prev) * .75, 1e-12)
            if "bullish_harami" in self.enabled_patterns and prev.close < prev.open and bullish and inside and self._has_context("MULTUP"):
                out.append(("bullish_harami", "MULTUP", self._clamp(.58 + (self._body(prev) - self._body(c)) / max(self._body(prev), 1e-12) * .22)))
            if "bearish_harami" in self.enabled_patterns and prev.close > prev.open and bearish and inside and self._has_context("MULTDOWN"):
                out.append(("bearish_harami", "MULTDOWN", self._clamp(.58 + (self._body(prev) - self._body(c)) / max(self._body(prev), 1e-12) * .22)))
            tol = max(.1, pr * .12)
            if "tweezer_bottom" in self.enabled_patterns and prev.close < prev.open and bullish and abs(prev.low - c.low) <= tol and self._has_context("MULTUP"):
                out.append(("tweezer_bottom", "MULTUP", self._clamp(.62 + (1 - min(abs(prev.low - c.low) / tol, 1)) * .20)))
            if "tweezer_top" in self.enabled_patterns and prev.close > prev.open and bearish and abs(prev.high - c.high) <= tol and self._has_context("MULTDOWN"):
                out.append(("tweezer_top", "MULTDOWN", self._clamp(.62 + (1 - min(abs(prev.high - c.high) / tol, 1)) * .20)))

        if len(self.candles) >= 2:
            first, middle = list(self.candles)[-2:]
            fb = self._body(first); middle_shape = self._shape(middle); fm = (first.open + first.close) / 2
            if "morning_star" in self.enabled_patterns and first.close < first.open and middle_shape["body_ratio"] <= .35 and bullish and c.close >= fm and self._has_context("MULTUP"):
                out.append(("morning_star", "MULTUP", self._clamp(.72 + min(.2, self._body(c) / max(fb, 1e-12) * .1))))
            if "evening_star" in self.enabled_patterns and first.close > first.open and middle_shape["body_ratio"] <= .35 and bearish and c.close <= fm and self._has_context("MULTDOWN"):
                out.append(("evening_star", "MULTDOWN", self._clamp(.72 + min(.2, self._body(c) / max(fb, 1e-12) * .1))))
            if "morning_doji_star" in self.enabled_patterns and first.close < first.open and middle_shape["body_ratio"] <= self.doji_body_ratio and bullish and c.close >= fm and self._has_context("MULTUP"):
                out.append(("morning_doji_star", "MULTUP", self._clamp(.76 + min(.18, self._body(c) / max(fb, 1e-12) * .08))))
            if "evening_doji_star" in self.enabled_patterns and first.close > first.open and middle_shape["body_ratio"] <= self.doji_body_ratio and bearish and c.close <= fm and self._has_context("MULTDOWN"):
                out.append(("evening_doji_star", "MULTDOWN", self._clamp(.76 + min(.18, self._body(c) / max(fb, 1e-12) * .08))))

            a, b = list(self.candles)[-2:]; ash = self._shape(a); bsh = self._shape(b)
            if "three_white_soldiers" in self.enabled_patterns and a.close > a.open and b.close > b.open and bullish and a.close < b.close < c.close and ash["body_ratio"] >= .45 and bsh["body_ratio"] >= .45 and br >= .45:
                out.append(("three_white_soldiers", "MULTUP", self._clamp(.72 + (ash["body_ratio"] + bsh["body_ratio"] + br) / 12)))
            if "three_black_crows" in self.enabled_patterns and a.close < a.open and b.close < b.open and bearish and a.close > b.close > c.close and ash["body_ratio"] >= .45 and bsh["body_ratio"] >= .45 and br >= .45:
                out.append(("three_black_crows", "MULTDOWN", self._clamp(.72 + (ash["body_ratio"] + bsh["body_ratio"] + br) / 12)))

        if br >= self.marubozu_body_ratio and sh["upper_ratio"] <= .10 and sh["lower_ratio"] <= .10:
            score = self._clamp(.65 + br * .30)
            if bullish and "bullish_marubozu" in self.enabled_patterns:
                out.append(("bullish_marubozu", "MULTUP", score))
            elif bearish and "bearish_marubozu" in self.enabled_patterns:
                out.append(("bearish_marubozu", "MULTDOWN", score))

        return [m for m in out if m[2] >= self.min_pattern_score]

    def _write_candle_event(self, c: SyntheticCandle, matches: list[tuple[str, str, float]]) -> None:
        try:
            CANDLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True); new = not CANDLE_LOG_PATH.exists(); sh = self._shape(c)
            with CANDLE_LOG_PATH.open('a', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                if new:
                    w.writerow(['timestamp_utc','instance_id','candle_mode','timeframe_seconds','candle_start_utc','candle_end_utc','open','high','low','close','body_ratio','upper_wick_ratio','lower_wick_ratio','body_pct','upper_wick_pct','lower_wick_pct','matches','directions','scores','confirmation_mode','shape_mode'])
                w.writerow([datetime.now(timezone.utc).isoformat(), INSTANCE_ID, self.candle_mode, self.candle_seconds if self.candle_mode == 'time' else '', datetime.fromtimestamp(c.start_epoch, timezone.utc).isoformat() if c.start_epoch else '', datetime.fromtimestamp(c.end_epoch, timezone.utc).isoformat() if c.end_epoch else '', c.open, c.high, c.low, c.close, sh['body_ratio'], sh['upper_ratio'], sh['lower_ratio'], sh['body_pct'], sh['upper_pct'], sh['lower_pct'], '+'.join(m[0] for m in matches), '+'.join(m[1] for m in matches), '+'.join(f'{m[2]:.4f}' for m in matches), self.confirmation_mode, self.shape_mode])
        except Exception as exc:
            LOGGER.debug('Candlestick event log skipped: %s', exc)

    def _set_pending(self, pat: str, direction: str, score: float, c: SyntheticCandle) -> None:
        self.pending_direction = direction
        self.pending_level = c.high if direction == "MULTUP" else c.low
        self.pending_ticks_remaining = max(1, int(self.p.get("breakout_expiry_ticks", 10)))
        self.pending_pattern = pat; self.pending_score = score

    def _process_completed_candle(self, completed: SyntheticCandle) -> tuple[Optional[str], str, float]:
        matches = self._detect_patterns(completed)
        self._write_candle_event(completed, matches)
        self.candles.append(completed); self.last_candle = completed
        self.last_patterns = [m[0] for m in matches]
        self.last_pattern_id = self.last_patterns[0] if self.last_patterns else "NONE"
        self.last_pattern = ("BULLISH_HAMMER" if self.last_pattern_id == "hammer" else "BEARISH_HAMMER" if self.last_pattern_id == "shooting_star" else self.last_pattern_id.upper() if self.last_pattern_id != "NONE" else "NONE")
        self.last_pattern_score = max((m[2] for m in matches), default=0.0)
        self.last_conflict = "NONE"
        for pat, _, _ in matches:
            self.pattern_counts[pat] = self.pattern_counts.get(pat, 0) + 1
        if not matches:
            return None, "NONE", 0.0
        dirs = {m[1] for m in matches}
        if len(dirs) != 1:
            if self.pattern_conflict_policy == "highest_score":
                best = max(matches, key=lambda m: m[2])
            else:
                self.last_conflict = '+'.join(sorted(dirs))
                return None, "NONE", 0.0
        else:
            best = max(matches, key=lambda m: m[2])
        if self.confirmation_mode == "close":
            return best[1], best[0], best[2]
        self._set_pending(best[0], best[1], best[2], completed)
        return None, "NONE", 0.0

    def _check_pending(self, price: float, decrement: bool = True) -> tuple[Optional[str], str, float]:
        if not self.pending_direction:
            return None, "NONE", 0.0
        if self.pending_direction == "MULTUP" and price > self.pending_level:
            pat, score = self.pending_pattern, self.pending_score; self.reset_confirmation(); return "MULTUP", pat, score
        if self.pending_direction == "MULTDOWN" and price < self.pending_level:
            pat, score = self.pending_pattern, self.pending_score; self.reset_confirmation(); return "MULTDOWN", pat, score
        if decrement:
            self.pending_ticks_remaining -= 1
            if self.pending_ticks_remaining <= 0:
                self.reset_confirmation()
        return None, "NONE", 0.0

    def _live_preview(self, epoch: int) -> Dict[str, Any]:
        if not self.current_ticks:
            return {}
        if self.candle_mode == "time" and self.current_bucket is not None:
            c = self._make_candle(self.current_ticks, self.current_bucket_start_epoch, (self.current_bucket + 1) * self.candle_seconds - 1)
        else:
            c = self._make_candle(self.current_ticks)
        sh = self._shape(c)
        matches = self._detect_patterns(c)
        return {
            "live_candle_open": c.open, "live_candle_high": c.high, "live_candle_low": c.low, "live_candle_close": c.close,
            "live_body_pct": sh['body_pct'], "live_upper_wick_pct": sh['upper_pct'], "live_lower_wick_pct": sh['lower_pct'],
            "live_candidate_patterns": [m[0] for m in matches],
            "live_candidate_details": [{"pattern": m[0], "direction": m[1], "score": m[2]} for m in matches[:8]],
            "live_direction": "BULLISH" if c.close > c.open else "BEARISH" if c.close < c.open else "DOJI",
        }

    def add_tick(self, price: float, epoch: Optional[int] = None) -> tuple[Optional[str], Dict[str, Any]]:
        price = float(price); epoch = int(epoch or time.time()); self.last_tick_epoch = epoch
        signal, signal_pattern, signal_score = self._check_pending(price, True)
        if self.candle_mode == "time":
            bucket = epoch // self.candle_seconds
            if self.current_bucket is None:
                self.current_bucket = bucket; self.current_bucket_start_epoch = bucket * self.candle_seconds
            elif bucket != self.current_bucket:
                completed = self._make_candle(self.current_ticks, self.current_bucket_start_epoch, (self.current_bucket + 1) * self.candle_seconds - 1) if self.current_ticks else None
                self.current_ticks = []; self.current_bucket = bucket; self.current_bucket_start_epoch = bucket * self.candle_seconds
                if completed is not None:
                    close_signal, close_pattern, close_score = self._process_completed_candle(completed)
                    if signal is None and close_signal:
                        signal, signal_pattern, signal_score = close_signal, close_pattern, close_score
                    elif signal is None and self.pending_direction:
                        signal, signal_pattern, signal_score = self._check_pending(price, False)
            self.current_ticks.append(price)
        else:
            self.current_ticks.append(price)
            if len(self.current_ticks) >= self.candle_ticks:
                completed = self._make_candle(self.current_ticks); self.current_ticks.clear()
                close_signal, close_pattern, close_score = self._process_completed_candle(completed)
                if signal is None and close_signal:
                    signal, signal_pattern, signal_score = close_signal, close_pattern, close_score

        elapsed = 0; remaining = 0
        if self.candle_mode == "time" and self.current_bucket is not None:
            elapsed = max(0, min(self.candle_seconds, epoch - self.current_bucket_start_epoch + 1)); remaining = max(0, self.candle_seconds - elapsed)
        metrics = {
            "strategy": self.strategy_id, "last_pattern": self.last_pattern, "last_pattern_id": self.last_pattern_id,
            "detected_patterns": list(self.last_patterns), "pattern_score": self.last_pattern_score,
            "signal_pattern": signal_pattern, "enabled_patterns": list(self.enabled_patterns), "confirmation_mode": self.confirmation_mode,
            "pattern_conflict_policy": self.pattern_conflict_policy, "pattern_conflict": self.last_conflict,
            "pending": self.pending_direction or "NONE", "pending_pattern": self.pending_pattern,
            "pending_level": self.pending_level, "pending_ticks": self.pending_ticks_remaining,
            "candle_mode": self.candle_mode, "candle_progress": len(self.current_ticks), "candle_ticks": self.candle_ticks,
            "candle_seconds": self.candle_seconds, "candle_elapsed_seconds": elapsed, "candle_seconds_remaining": remaining,
            "candle_bucket_start_epoch": self.current_bucket_start_epoch, "pattern_counts": dict(self.pattern_counts),
            "shape_mode": self.shape_mode,
            "shape_thresholds": {"min_body_pct": self.min_body_pct, "max_body_pct": self.max_body_pct, "min_long_wick_pct": self.min_long_wick_pct, "max_opposite_wick_pct": self.max_opposite_wick_pct, "rejection_min_wick_pct": self.rejection_min_wick_pct, "flat_wick_max_pct": self.flat_wick_max_pct},
            "strength": signal_score if signal else (self.last_pattern_score if self.last_pattern_id != "NONE" else .10),
        }
        metrics.update(self._live_preview(epoch))
        if self.last_candle:
            c = self.last_candle; sh = self._shape(c)
            metrics.update(candle_open=c.open, candle_high=c.high, candle_low=c.low, candle_close=c.close, candle_start_epoch=c.start_epoch, candle_end_epoch=c.end_epoch, body_range_ratio=sh['body_ratio'], upper_wick_ratio=sh['upper_ratio'], lower_wick_ratio=sh['lower_ratio'], body_pct=sh['body_pct'], upper_wick_pct=sh['upper_pct'], lower_wick_pct=sh['lower_pct'])
        return signal, metrics

    def dashboard_lines(self, metrics: Dict[str, Any]) -> list[str]:
        if not metrics:
            return []
        detected = ','.join(metrics.get('detected_patterns') or []) or 'NONE'
        if metrics.get('candle_mode') == 'time':
            seconds = int(metrics.get('candle_seconds', self.candle_seconds)); elapsed = int(metrics.get('candle_elapsed_seconds', 0)); remaining = int(metrics.get('candle_seconds_remaining', 0))
            progress = f"{elapsed}/{seconds} sec ({seconds//60}m aligned; {remaining}s left)" if seconds % 60 == 0 else f"{elapsed}/{seconds} sec ({remaining}s left)"
        else:
            progress = f"{metrics.get('candle_progress', 0)}/{metrics.get('candle_ticks', self.candle_ticks)} ticks"
        lines = [f"Candles: {progress} | Detected: {detected} | score={float(metrics.get('pattern_score', 0)):.2f} | Confirm={metrics.get('confirmation_mode', self.confirmation_mode).upper()} | Pending={metrics.get('pending_pattern', 'NONE')}:{metrics.get('pending', 'NONE')}"]
        if metrics.get('live_candle_open') is not None:
            candidates = ','.join(metrics.get('live_candidate_patterns') or []) or 'NONE'
            lines.append(f"LIVE CANDLE O={metrics.get('live_candle_open',0):.5f} H={metrics.get('live_candle_high',0):.5f} L={metrics.get('live_candle_low',0):.5f} C={metrics.get('live_candle_close',0):.5f} body={float(metrics.get('live_body_pct',0)):.1f}% upper={float(metrics.get('live_upper_wick_pct',0)):.1f}% lower={float(metrics.get('live_lower_wick_pct',0)):.1f}% candidates={candidates}")
        if metrics.get('candle_open') is not None:
            lines.append(f"CANDLE OHLC O={metrics.get('candle_open',0):.5f} H={metrics.get('candle_high',0):.5f} L={metrics.get('candle_low',0):.5f} C={metrics.get('candle_close',0):.5f} body={float(metrics.get('body_pct',float(metrics.get('body_range_ratio',0))*100)):.1f}% upper={float(metrics.get('upper_wick_pct',float(metrics.get('upper_wick_ratio',0))*100)):.1f}% lower={float(metrics.get('lower_wick_pct',float(metrics.get('lower_wick_ratio',0))*100)):.1f}%")
        return lines

class BasePriceStrategy:
    strategy_id = "base"
    label = "Base strategy"

    def __init__(self, config: BotConfig):
        self.cfg = config
        self.p = config.profile(self.strategy_id)
        self.history_count = max(100, int(self.p.get("history_count", 250)))
        self.minimum_history = 30
        self.prices: Deque[float] = deque(maxlen=max(self.history_count, 600))
        self.gate = DirectionalSignalGate(self.p.get("confirmations", 1), self.p.get("cooldown_ticks", 0))

    def seed(self, prices: list[float]) -> None:
        self.prices.clear(); self.prices.extend(float(price) for price in prices); self.reset_confirmation()

    def reset_confirmation(self) -> None:
        self.gate.reset()

    def dashboard_lines(self, metrics: Dict[str, Any]) -> list[str]:
        line = metrics.get("dashboard") if metrics else None
        return [str(line)] if line else []


class MacdStrategy(BasePriceStrategy):
    strategy_id = "macd"; label = "MACD Trend Crossover"
    def __init__(self, config: BotConfig):
        super().__init__(config)
        self.minimum_history = int(self.p["slow_period"]) + int(self.p["signal_period"]) + 5

    def add_tick(self, price: float) -> tuple[Optional[str], Dict[str, Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history: return None, {}
        fast=IndicatorTools.ema_series(values,int(self.p["fast_period"])); slow=IndicatorTools.ema_series(values,int(self.p["slow_period"]))
        macd_values=[f-s for f,s in zip(fast,slow) if f is not None and s is not None]
        signal_series=IndicatorTools.ema_series(macd_values,int(self.p["signal_period"]))
        signal_value=next((v for v in reversed(signal_series) if v is not None),None)
        if signal_value is None: return None, {}
        macd_value=macd_values[-1]; histogram=macd_value-signal_value; threshold=float(self.p["histogram_threshold"])
        raw="MULTUP" if histogram>threshold else "MULTDOWN" if histogram<-threshold else None
        signal=self.gate.update(raw)
        changes=[abs(values[i]-values[i-1]) for i in range(max(1,len(values)-20),len(values))]
        scale=statistics.fmean(changes) if changes else 0.1
        strength=IndicatorTools.clamp(abs(histogram)/max(scale,1e-9))
        return signal,{"strategy":self.strategy_id,"macd":macd_value,"signal_line":signal_value,"histogram":histogram,"strength":strength,
                       "dashboard":f"MACD: {macd_value:.5f}  Signal: {signal_value:.5f}  Histogram: {histogram:.5f}"}


class BollingerReversionStrategy(BasePriceStrategy):
    strategy_id="bollinger_reversion"; label="Bollinger Mean Reversion"
    def __init__(self,config:BotConfig):
        super().__init__(config); self.minimum_history=max(int(self.p["period"]),int(self.p["rsi_period"])+1)
    def add_tick(self,price:float)->tuple[Optional[str],Dict[str,Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history:return None,{}
        window=values[-int(self.p["period"]):]; middle=statistics.fmean(window); dev=statistics.pstdev(window); mult=float(self.p["stddev_multiplier"])
        upper=middle+mult*dev; lower=middle-mult*dev; rsi=IndicatorTools.rsi(values,int(self.p["rsi_period"])); current=values[-1]
        raw="MULTUP" if current<=lower and rsi<=float(self.p["oversold"]) else "MULTDOWN" if current>=upper and rsi>=float(self.p["overbought"]) else None
        signal=self.gate.update(raw); distance=(lower-current if raw=="MULTUP" else current-upper if raw=="MULTDOWN" else 0.0)
        strength=IndicatorTools.clamp(distance/max(dev,1e-9)+0.25)
        return signal,{"strategy":self.strategy_id,"middle":middle,"upper":upper,"lower":lower,"rsi":rsi,"strength":strength,
                       "dashboard":f"Bollinger MR: lower={lower:.5f} mid={middle:.5f} upper={upper:.5f} RSI={rsi:.1f}"}


class BollingerBreakoutStrategy(BasePriceStrategy):
    strategy_id="bollinger_breakout"; label="Bollinger Breakout"
    def __init__(self,config:BotConfig):
        super().__init__(config); self.minimum_history=max(int(self.p["period"])+1,int(self.p["momentum_ticks"])+1)
    def add_tick(self,price:float)->tuple[Optional[str],Dict[str,Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history:return None,{}
        period=int(self.p["period"]); basis=values[-(period+1):-1]; middle=statistics.fmean(basis); dev=statistics.pstdev(basis); mult=float(self.p["stddev_multiplier"])
        upper=middle+mult*dev; lower=middle-mult*dev; current=values[-1]
        positive,negative,net=IndicatorTools.direction_counts(values,int(self.p["momentum_ticks"])); required=int(self.p["momentum_required"])
        raw="MULTUP" if current>upper and positive>=required and net>0 else "MULTDOWN" if current<lower and negative>=required and net<0 else None
        signal=self.gate.update(raw); favorable=positive if raw=="MULTUP" else negative if raw=="MULTDOWN" else max(positive,negative)
        strength=IndicatorTools.clamp(favorable/max(1,int(self.p["momentum_ticks"]))*0.65+abs(current-(upper if raw=="MULTUP" else lower if raw=="MULTDOWN" else middle))/max(dev*3,1e-9)*0.35)
        return signal,{"strategy":self.strategy_id,"middle":middle,"upper":upper,"lower":lower,"net_change":net,"positive_ticks":positive,"negative_ticks":negative,"strength":strength,
                       "dashboard":f"Bollinger BO: lower={lower:.5f} mid={middle:.5f} upper={upper:.5f} net={net:.5f}"}


class StochasticStrategy(BasePriceStrategy):
    strategy_id="stochastic"; label="Stochastic Reversal"
    def __init__(self,config:BotConfig):
        super().__init__(config); self.minimum_history=int(self.p["lookback_period"])+int(self.p["smooth_k"])+int(self.p["smooth_d"])+3
    def _oscillator(self,values:list[float])->tuple[float,float]:
        look=int(self.p["lookback_period"]); raw=[]
        for end in range(look-1,len(values)):
            win=values[end-look+1:end+1]; lo=min(win); hi=max(win); raw.append(50.0 if hi==lo else (values[end]-lo)/(hi-lo)*100.0)
        sk=int(self.p["smooth_k"]); sd=int(self.p["smooth_d"])
        smooth_k=[statistics.fmean(raw[i-sk+1:i+1]) for i in range(sk-1,len(raw))]
        smooth_d=[statistics.fmean(smooth_k[i-sd+1:i+1]) for i in range(sd-1,len(smooth_k))]
        return smooth_k[-1],smooth_d[-1]
    def add_tick(self,price:float)->tuple[Optional[str],Dict[str,Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history:return None,{}
        k,d=self._oscillator(values); oversold=float(self.p["oversold"]); overbought=float(self.p["overbought"])
        raw="MULTUP" if k>d and k<=oversold+10 else "MULTDOWN" if k<d and k>=overbought-10 else None
        signal=self.gate.update(raw); strength=IndicatorTools.clamp((abs(k-d)/20.0)+((oversold+10-k)/50.0 if raw=="MULTUP" else (k-(overbought-10))/50.0 if raw=="MULTDOWN" else 0))
        return signal,{"strategy":self.strategy_id,"stoch_k":k,"stoch_d":d,"strength":strength,"dashboard":f"Stochastic: %K={k:.1f}  %D={d:.1f}  zones={oversold:.0f}/{overbought:.0f}"}


class DonchianStrategy(BasePriceStrategy):
    strategy_id="donchian"; label="Donchian Breakout"
    def __init__(self,config:BotConfig):
        super().__init__(config); self.minimum_history=int(self.p["period"])+1; self.pending_direction=None; self.pending_level=0.0
    def reset_confirmation(self)->None:
        super().reset_confirmation(); self.pending_direction=None; self.pending_level=0.0
    def add_tick(self,price:float)->tuple[Optional[str],Dict[str,Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history:return None,{}
        period=int(self.p["period"]); prior=values[-(period+1):-1]; upper=max(prior); lower=min(prior); current=values[-1]; buffer=float(self.p["breakout_buffer"])
        raw=None
        if self.pending_direction=="MULTUP" and current>=self.pending_level: raw="MULTUP"
        elif self.pending_direction=="MULTDOWN" and current<=self.pending_level: raw="MULTDOWN"
        elif current>upper+buffer: self.pending_direction="MULTUP"; self.pending_level=upper+buffer; raw="MULTUP"
        elif current<lower-buffer: self.pending_direction="MULTDOWN"; self.pending_level=lower-buffer; raw="MULTDOWN"
        else: self.pending_direction=None
        signal=self.gate.update(raw)
        if signal:self.pending_direction=None
        width=max(upper-lower,1e-9); strength=IndicatorTools.clamp(abs(current-(upper if raw=="MULTUP" else lower if raw=="MULTDOWN" else (upper+lower)/2))/width*2)
        return signal,{"strategy":self.strategy_id,"upper":upper,"lower":lower,"channel_width":width,"strength":strength,"dashboard":f"Donchian: low={lower:.5f} high={upper:.5f} width={width:.5f}"}


class RsiPullbackStrategy(BasePriceStrategy):
    strategy_id="rsi_pullback"; label="RSI Trend Pullback"
    def __init__(self,config:BotConfig):
        super().__init__(config); self.minimum_history=max(int(self.p["trend_ema"])+2,int(self.p["rsi_period"])+1); self.pullback_up=False; self.pullback_down=False
    def reset_confirmation(self)->None:
        super().reset_confirmation(); self.pullback_up=False; self.pullback_down=False
    def add_tick(self,price:float)->tuple[Optional[str],Dict[str,Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history:return None,{}
        trend=IndicatorTools.ema(values,int(self.p["trend_ema"])); pull=IndicatorTools.ema(values,int(self.p["pullback_ema"])); rsi=IndicatorTools.rsi(values,int(self.p["rsi_period"])); current=values[-1]
        fast_series=IndicatorTools.ema_series(values,int(self.p["pullback_ema"])); slow_series=IndicatorTools.ema_series(values,int(self.p["trend_ema"])); prev_fast=fast_series[-2]; prev_slow=slow_series[-2]
        if prev_fast is not None and prev_slow is not None:
            if prev_fast>prev_slow and values[-2]<=prev_fast:self.pullback_up=True
            if prev_fast<prev_slow and values[-2]>=prev_fast:self.pullback_down=True
        raw=None
        if self.pullback_up and pull>trend and current>pull and float(self.p["up_rsi_min"])<=rsi<=float(self.p["up_rsi_max"]): raw="MULTUP"
        elif self.pullback_down and pull<trend and current<pull and float(self.p["down_rsi_min"])<=rsi<=float(self.p["down_rsi_max"]): raw="MULTDOWN"
        signal=self.gate.update(raw)
        if signal=="MULTUP":self.pullback_up=False
        elif signal=="MULTDOWN":self.pullback_down=False
        strength=IndicatorTools.clamp(abs(pull-trend)/max(abs(current)*0.0001,1e-9)*0.4+abs(rsi-50)/20*0.6)
        return signal,{"strategy":self.strategy_id,"trend_ema":trend,"pullback_ema":pull,"rsi":rsi,"strength":strength,"dashboard":f"RSI Pullback: trendEMA={trend:.5f} pullEMA={pull:.5f} RSI={rsi:.1f} armedUP={self.pullback_up} armedDOWN={self.pullback_down}"}


class TickMomentumStrategy(BasePriceStrategy):
    strategy_id="tick_momentum"; label="Tick Momentum Burst"
    def __init__(self,config:BotConfig):
        super().__init__(config); self.minimum_history=int(self.p["lookback_ticks"])+1
    def add_tick(self,price:float)->tuple[Optional[str],Dict[str,Any]]:
        self.prices.append(float(price)); values=list(self.prices)
        if len(values)<self.minimum_history:return None,{}
        look=int(self.p["lookback_ticks"]); positive,negative,net=IndicatorTools.direction_counts(values,look); required=int(self.p["required_directional_ticks"]); minimum=float(self.p["min_net_change"])
        raw="MULTUP" if positive>=required and net>=minimum else "MULTDOWN" if negative>=required and net<=-minimum else None
        signal=self.gate.update(raw); favorable=positive if raw=="MULTUP" else negative if raw=="MULTDOWN" else max(positive,negative)
        strength=IndicatorTools.clamp(0.6*favorable/max(1,look)+0.4*abs(net)/max(minimum*3,1e-9))
        return signal,{"strategy":self.strategy_id,"positive_ticks":positive,"negative_ticks":negative,"net_change":net,"strength":strength,"dashboard":f"Tick burst: +ticks={positive} -ticks={negative} net={net:.5f} window={look}"}


class CombinedStrategy:
    """Legacy explicit ensemble. It is intentionally separate from exclusive UI strategies."""
    label="EMA + RSI OR Hammer (legacy ensemble)"; strategy_id="both"
    def __init__(self,config:BotConfig):
        self.ema=TickStrategy(config); self.hammer=HammerStrategy(config)
        self.history_count=max(self.ema.history_count,self.hammer.history_count); self.minimum_history=max(self.ema.minimum_history,self.hammer.minimum_history)
    def seed(self,prices:list[float],epochs:Optional[list[int]]=None)->None:self.ema.seed(prices);self.hammer.seed(prices,epochs)
    def reset_confirmation(self)->None:self.ema.reset_confirmation();self.hammer.reset_confirmation()
    def add_tick(self,price:float,epoch:Optional[int]=None)->tuple[Optional[str],Dict[str,Any]]:
        es,em=self.ema.add_tick(price); hs,hm=(self.hammer.add_tick(price,epoch) if epoch is not None else self.hammer.add_tick(price)); conflict=bool(es and hs and es!=hs)
        signal=None if conflict else es if es and hs else es or hs
        sources=[]
        if es:sources.append("EMA_RSI")
        if hs:sources.append("HAMMER")
        return signal,{"strategy":"both","ema":em,"hammer":hm,"ema_signal":es or "NONE","hammer_signal":hs or "NONE","signal_sources":"+".join(sources) if sources else "NONE","conflict":conflict,"strength":max(float(em.get("strength",0)),float(hm.get("strength",0)))}
    def dashboard_lines(self,metrics:Dict[str,Any])->list[str]:
        lines=[f"Legacy ensemble: EMA={metrics.get('ema_signal','NONE')} Hammer={metrics.get('hammer_signal','NONE')} Conflict={'YES' if metrics.get('conflict') else 'NO'}"]
        lines.extend(self.ema.dashboard_lines(metrics.get('ema') or {})); lines.extend(self.hammer.dashboard_lines(metrics.get('hammer') or {})); return lines


STRATEGY_FACTORIES: Dict[str, type] = {
    "ema_rsi": TickStrategy,
    "hammer": HammerStrategy,
    "macd": MacdStrategy,
    "bollinger_reversion": BollingerReversionStrategy,
    "bollinger_breakout": BollingerBreakoutStrategy,
    "stochastic": StochasticStrategy,
    "donchian": DonchianStrategy,
    "rsi_pullback": RsiPullbackStrategy,
    "tick_momentum": TickMomentumStrategy,
    "both": CombinedStrategy,
}

SINGLE_STRATEGY_IDS = tuple(key for key in STRATEGY_FACTORIES if key != "both")
TREND_STRATEGY_IDS = {
    "ema_rsi", "macd", "bollinger_breakout", "donchian", "rsi_pullback", "tick_momentum"
}
RANGE_STRATEGY_IDS = {"bollinger_reversion", "stochastic", "hammer"}
BEST_MIX_STRATEGY_IDS = (
    "bollinger_reversion", "stochastic", "hammer",
    "ema_rsi", "donchian", "tick_momentum",
)
STRATEGY_LABELS: Dict[str, str] = {key: factory.label for key, factory in STRATEGY_FACTORIES.items()}
STRATEGY_LABELS["ensemble"] = "Multi-Strategy Ensemble"


def _config_with_strategy_override(config: BotConfig, strategy_id: str, override: Dict[str, Any]) -> BotConfig:
    strategies = {name: dict(values) for name, values in config.strategies.items()}
    merged = dict(strategies.get(strategy_id, {}))
    merged.update(override or {})
    strategies[strategy_id] = merged
    return BotConfig(
        multiplier=config.multiplier,
        take_profit=config.take_profit,
        dashboard_seconds=config.dashboard_seconds,
        symbol_names=config.symbol_names,
        strategies=strategies,
    )


class EnsembleStrategy:
    """Runs multiple independent strategy engines and combines their signals.

    Each child strategy keeps its own confirmation/cooldown logic.  Recent child
    signals are held for a short configurable memory window so two strategies
    do not have to fire on the exact same tick to count as agreement.
    """

    label = "Multi-Strategy Ensemble"
    strategy_id = "ensemble"

    def __init__(self, config: BotConfig, settings: SessionSettings):
        requested = tuple(s for s in settings.ensemble_strategies if s in SINGLE_STRATEGY_IDS)
        if settings.ensemble_preset == "best":
            requested = BEST_MIX_STRATEGY_IDS
        elif settings.ensemble_preset == "all":
            requested = SINGLE_STRATEGY_IDS
        if not requested:
            requested = BEST_MIX_STRATEGY_IDS

        self.selected = tuple(dict.fromkeys(requested))
        self.policy = settings.ensemble_policy
        self.min_votes = max(1, settings.ensemble_min_votes)
        self.memory_ticks = max(0, settings.ensemble_memory_ticks)
        self.regime_lookback = max(5, settings.ensemble_regime_lookback)
        self.trend_threshold = max(0.05, min(0.95, float(settings.ensemble_trend_threshold)))
        self.weight_threshold = max(0.1, float(settings.ensemble_weight_threshold))
        self.conflict_margin = max(0.0, float(settings.ensemble_conflict_margin))
        self.weights = {sid: max(0.05, float(settings.ensemble_weights.get(sid, 1.0))) for sid in self.selected}
        self.children: Dict[str, Any] = {}
        for sid in self.selected:
            child_config = config
            override = settings.ensemble_profile_overrides.get(sid) or {}
            if override:
                child_config = _config_with_strategy_override(config, sid, override)
            self.children[sid] = STRATEGY_FACTORIES[sid](child_config)

        self.history_count = max([getattr(child, "history_count", 250) for child in self.children.values()] + [250])
        self.minimum_history = max([getattr(child, "minimum_history", 30) for child in self.children.values()] + [30])
        self.prices: Deque[float] = deque(maxlen=max(self.history_count, self.regime_lookback + 20, 600))
        self.signal_memory: Dict[str, Dict[str, Any]] = {}
        self.gate = DirectionalSignalGate(max(1, settings.ensemble_confirmations), 0)
        # TradingBot uses this profile to derive its post-entry cooldown.
        self.p = {"cooldown_ticks": max(0, settings.ensemble_cooldown_ticks)}
        self.last_regime = "RANGE"

    def seed(self, prices: list[float], epochs: Optional[list[int]] = None) -> None:
        self.prices.clear()
        self.prices.extend(float(p) for p in prices)
        for sid, child in self.children.items():
            if sid == "hammer": child.seed(prices, epochs)
            else: child.seed(prices)
        self.signal_memory.clear()
        self.gate.reset()

    def reset_confirmation(self) -> None:
        for child in self.children.values():
            child.reset_confirmation()
        self.signal_memory.clear()
        self.gate.reset()

    def _regime(self) -> tuple[str, float]:
        values = list(self.prices)
        if len(values) < self.regime_lookback + 1:
            return "RANGE", 0.0
        window = values[-(self.regime_lookback + 1):]
        path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
        displacement = abs(window[-1] - window[0])
        efficiency = 0.0 if path <= 1e-12 else displacement / path
        return ("TREND" if efficiency >= self.trend_threshold else "RANGE"), efficiency

    def _eligible_ids(self, regime: str) -> set[str]:
        if self.policy != "regime":
            return set(self.selected)
        group = TREND_STRATEGY_IDS if regime == "TREND" else RANGE_STRATEGY_IDS
        eligible = set(self.selected).intersection(group)
        return eligible or set(self.selected)

    def add_tick(self, price: float, epoch: Optional[int] = None) -> tuple[Optional[str], Dict[str, Any]]:
        self.prices.append(float(price))
        # Age remembered signals before adding fresh ones.
        for sid in list(self.signal_memory):
            self.signal_memory[sid]["age"] += 1
            if self.signal_memory[sid]["age"] > self.memory_ticks:
                self.signal_memory.pop(sid, None)

        child_metrics: Dict[str, Any] = {}
        fresh: Dict[str, str] = {}
        for sid, child in self.children.items():
            signal, metrics = (child.add_tick(price, epoch) if epoch is not None else child.add_tick(price)) if sid == "hammer" else child.add_tick(price)
            child_metrics[sid] = metrics or {}
            if signal:
                strength = float((metrics or {}).get("strength", 0.5) or 0.5)
                self.signal_memory[sid] = {
                    "direction": signal,
                    "age": 0,
                    "strength": max(0.0, min(1.0, strength)),
                }
                fresh[sid] = signal

        regime, efficiency = self._regime()
        self.last_regime = regime
        eligible = self._eligible_ids(regime)
        votes = {
            sid: value for sid, value in self.signal_memory.items()
            if sid in eligible
        }
        up_ids = [sid for sid, v in votes.items() if v["direction"] == "MULTUP"]
        down_ids = [sid for sid, v in votes.items() if v["direction"] == "MULTDOWN"]
        up_score = sum(self.weights.get(sid, 1.0) * max(0.25, float(votes[sid]["strength"])) for sid in up_ids)
        down_score = sum(self.weights.get(sid, 1.0) * max(0.25, float(votes[sid]["strength"])) for sid in down_ids)
        raw: Optional[str] = None
        required_votes = 1

        if self.policy == "any":
            if up_ids and not down_ids:
                raw = "MULTUP"
            elif down_ids and not up_ids:
                raw = "MULTDOWN"
        elif self.policy == "unanimous":
            required_votes = max(1, len(eligible))
            if len(up_ids) >= required_votes:
                raw = "MULTUP"
            elif len(down_ids) >= required_votes:
                raw = "MULTDOWN"
        elif self.policy == "weighted":
            required_votes = self.min_votes
            if len(up_ids) >= required_votes and up_score >= self.weight_threshold and up_score >= down_score + self.conflict_margin:
                raw = "MULTUP"
            elif len(down_ids) >= required_votes and down_score >= self.weight_threshold and down_score >= up_score + self.conflict_margin:
                raw = "MULTDOWN"
        else:  # majority and regime-aware majority
            required_votes = min(self.min_votes, max(1, len(eligible)))
            if len(up_ids) >= required_votes and len(up_ids) > len(down_ids):
                raw = "MULTUP"
            elif len(down_ids) >= required_votes and len(down_ids) > len(up_ids):
                raw = "MULTDOWN"

        signal = self.gate.update(raw)
        winning_ids = up_ids if raw == "MULTUP" else down_ids if raw == "MULTDOWN" else []
        max_possible = sum(self.weights.get(sid, 1.0) for sid in eligible) or 1.0
        winning_score = up_score if raw == "MULTUP" else down_score if raw == "MULTDOWN" else 0.0
        strength = IndicatorTools.clamp(winning_score / max_possible)
        vote_summary = ", ".join(
            f"{sid}:{v['direction'].replace('MULT','')}@{v['age']}"
            for sid, v in sorted(votes.items())
        ) or "NONE"
        if self.policy == "weighted":
            gate_text = f"need={required_votes} votes score>={self.weight_threshold:.2f}"
        elif self.policy in {"majority", "regime"}:
            gate_text = f"need={required_votes} votes"
        elif self.policy == "unanimous":
            gate_text = f"need=all({required_votes})"
        else:
            gate_text = "need=1 non-conflicting"

        metrics = {
            "strategy": "ensemble",
            "strength": strength,
            "ensemble_policy": self.policy,
            "ensemble_regime": regime,
            "regime_efficiency": efficiency,
            "ensemble_sources": "+".join(winning_ids) if winning_ids else "NONE",
            "ensemble_vote_summary": vote_summary,
            "ensemble_up_votes": len(up_ids),
            "ensemble_down_votes": len(down_ids),
            "ensemble_up_score": up_score,
            "ensemble_down_score": down_score,
            "ensemble_required_votes": required_votes,
            "ensemble_weight_threshold": self.weight_threshold,
            "ensemble_gate_text": gate_text,
            "ensemble_raw_direction": raw or "",
            "ensemble_output_signal": signal or "",
            "ensemble_gate_armed": bool(self.gate.armed),
            "ensemble_gate_up_count": int(self.gate.up_count),
            "ensemble_gate_down_count": int(self.gate.down_count),
            "ensemble_selected": list(self.selected),
            "ensemble_eligible": sorted(eligible),
            "ensemble_signal_memory": {
                sid: {"direction": str(v.get("direction", "")), "age": int(v.get("age", 0)), "strength": float(v.get("strength", 0.0))}
                for sid, v in votes.items()
            },
            "ensemble_fresh": fresh,
            "ensemble_child_metrics": child_metrics,
            "dashboard": (
                f"Ensemble {self.policy.upper()} | regime={regime} eff={efficiency:.2f} | {gate_text} | "
                f"UP {len(up_ids)}/{up_score:.2f} DOWN {len(down_ids)}/{down_score:.2f} | {vote_summary}"
            ),
        }
        return signal, metrics

    def dashboard_lines(self, metrics: Dict[str, Any]) -> list[str]:
        if not metrics:
            return []
        lines = [str(metrics.get("dashboard") or "Ensemble waiting for signals")]
        candle_metrics = (metrics.get("ensemble_child_metrics") or {}).get("hammer") or {}
        candle_child = self.children.get("hammer")
        if candle_child is not None and candle_metrics:
            lines.extend(candle_child.dashboard_lines(candle_metrics))
        return lines


def create_strategy(strategy_mode: str, config: BotConfig, settings: Optional[SessionSettings] = None):
    if strategy_mode == "ensemble":
        if settings is None:
            raise RuntimeError("Ensemble strategy requires session settings")
        return EnsembleStrategy(config, settings)
    try:
        return STRATEGY_FACTORIES[strategy_mode](config)
    except KeyError as exc:
        raise RuntimeError(f"Unknown strategy: {strategy_mode}") from exc


class AdaptiveTakeProfit:
    """Select a take-profit from a configured minimum/maximum range.

    The score is deliberately bounded and transparent. It is calculated at the
    moment each split position is proposed, so staggered positions may receive
    different targets as ticks change. This is a target selector, not a profit
    prediction and it never lowers the target below the configured minimum.
    """

    @staticmethod
    def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    @classmethod
    def ema_score(cls, direction: str, metrics: Dict[str, Any]) -> float:
        if not metrics:
            return 0.0
        momentum_ticks = max(
            1.0,
            float(metrics.get("positive_ticks", 0.0))
            + float(metrics.get("negative_ticks", 0.0)),
        )
        favorable = (
            float(metrics.get("positive_ticks", 0.0))
            if direction == "MULTUP"
            else float(metrics.get("negative_ticks", 0.0))
        )
        momentum = cls.clamp(favorable / momentum_ticks)

        rsi = float(metrics.get("rsi", 50.0))
        if direction == "MULTUP":
            rsi_strength = cls.clamp((rsi - 50.0) / 18.0)
            aligned = float(metrics.get("ema_fast", 0.0)) > float(metrics.get("ema_slow", 0.0))
        else:
            rsi_strength = cls.clamp((50.0 - rsi) / 18.0)
            aligned = float(metrics.get("ema_fast", 0.0)) < float(metrics.get("ema_slow", 0.0))

        net = float(metrics.get("net_change", 0.0))
        net_aligned = net > 0 if direction == "MULTUP" else net < 0
        alignment = (0.5 if aligned else 0.0) + (0.5 if net_aligned else 0.0)
        return cls.clamp(0.50 * momentum + 0.30 * rsi_strength + 0.20 * alignment)

    @classmethod
    def hammer_score(cls, direction: str, metrics: Dict[str, Any]) -> float:
        if not metrics:
            return 0.0
        pattern = str(metrics.get("last_pattern", "NONE"))
        expected = "BULLISH_HAMMER" if direction == "MULTUP" else "BEARISH_HAMMER"
        pattern_score = 1.0 if pattern == expected else 0.25

        body_ratio = cls.clamp(float(metrics.get("body_range_ratio", 0.35)), 0.0, 1.0)
        # Hammer bodies are normally small; smaller bodies receive more credit,
        # but never by themselves create a high-confidence score.
        body_quality = cls.clamp((0.35 - body_ratio) / 0.33)

        candle_range = abs(
            float(metrics.get("candle_high", 0.0))
            - float(metrics.get("candle_low", 0.0))
        )
        range_quality = 1.0 if candle_range > 0 else 0.0
        return cls.clamp(0.55 * pattern_score + 0.30 * body_quality + 0.15 * range_quality)

    @classmethod
    def score(cls, direction: str, metrics: Dict[str, Any]) -> float:
        strategy = str(metrics.get("strategy", ""))
        if strategy == "ema_rsi":
            return cls.ema_score(direction, metrics)
        if strategy == "hammer":
            return cls.hammer_score(direction, metrics)
        if strategy == "both":
            ema_signal = str(metrics.get("ema_signal", "NONE"))
            hammer_signal = str(metrics.get("hammer_signal", "NONE"))
            ema = cls.ema_score(direction, metrics.get("ema") or {})
            hammer = cls.hammer_score(direction, metrics.get("hammer") or {})
            scores = []
            if ema_signal == direction:
                scores.append(ema)
            if hammer_signal == direction:
                scores.append(hammer)
            if not scores:
                # During a staggered batch the original signal may have passed;
                # use current direction alignment but naturally fall toward min TP.
                scores = [ema * 0.65, hammer * 0.65]
            result = max(scores)
            if ema_signal == direction and hammer_signal == direction:
                result += 0.15  # confluence bonus
            return cls.clamp(result)
        if strategy == "ensemble":
            return cls.clamp(float(metrics.get("strength", 0.0)))
        if strategy in STRATEGY_FACTORIES:
            return cls.clamp(float(metrics.get("strength", 0.0)))
        return 0.0

    @classmethod
    def target(
        cls,
        direction: str,
        metrics: Dict[str, Any],
        minimum: Decimal,
        maximum: Decimal,
    ) -> tuple[Decimal, float]:
        minimum = max(Decimal("0.10"), minimum)
        maximum = max(minimum, maximum)
        score = cls.score(direction, metrics)
        raw = minimum + (maximum - minimum) * Decimal(str(score))
        target = raw.quantize(MONEY_STEP, rounding=ROUND_DOWN)
        return max(minimum, min(maximum, target)), score


class SmartExit:
    """Pure helpers for local profit-protection exits.

    No algorithm can identify the final best price in advance. These helpers
    protect a portion of a peak that has already occurred, then optionally use
    adverse ticks and an opposite strategy signal to exit earlier.
    """

    @staticmethod
    def trailing_floor(
        peak_profit: Decimal,
        protected_minimum: Decimal,
        trail_percent: Decimal,
        minimum_distance: Decimal,
    ) -> Decimal:
        peak_profit = max(Decimal("0"), peak_profit)
        protected_minimum = max(Decimal("0"), protected_minimum)
        percentage_distance = peak_profit * trail_percent / Decimal("100")
        giveback = max(minimum_distance, percentage_distance)
        raw_floor = max(protected_minimum, peak_profit - giveback)
        return raw_floor.quantize(MONEY_STEP, rounding=ROUND_DOWN)

    @staticmethod
    def trailing_floor_ticks(
        peak_ticks: float,
        protected_minimum_ticks: int,
        trail_percent: Decimal,
        minimum_distance_ticks: int,
    ) -> float:
        peak = max(0.0, float(peak_ticks))
        protected = max(0.0, float(protected_minimum_ticks))
        percentage_distance = peak * float(trail_percent) / 100.0
        giveback = max(float(minimum_distance_ticks), percentage_distance)
        return round(max(protected, peak - giveback), 6)

    @staticmethod
    def is_adverse(direction: str, previous_quote: float, current_quote: float) -> bool:
        if current_quote == previous_quote:
            return False
        if direction == "MULTUP":
            return current_quote < previous_quote
        return current_quote > previous_quote

    @staticmethod
    def opposite(direction: str) -> str:
        return "MULTDOWN" if direction == "MULTUP" else "MULTUP"


class SmartLossGuard:
    """Recovery-aware helpers for local loss management.

    The guard deliberately separates a *soft* drawdown watch from a *hard*
    loss cap. Crossing the soft trigger does not sell by itself: the trade gets
    a configurable grace period and can reset the watch when it recovers.
    """

    @staticmethod
    def soft_loss(trigger: Decimal) -> Decimal:
        return -abs(trigger)

    @staticmethod
    def reset_profit(level: Decimal) -> Decimal:
        return -abs(level)

    @staticmethod
    def hard_loss(cap: Decimal) -> Decimal:
        return -abs(cap)


# ---------------------------------------------------------------------------
# Trading engine
# ---------------------------------------------------------------------------


class TradingBot:
    def __init__(
        self,
        config: BotConfig,
        settings: SessionSettings,
        client: DerivWebSocketClient,
        account_id: str,
        account_balance: Decimal,
        currency: str,
        live_flag: bool,
        dry_run: bool,
        symbol_override: str = "",
        token: str = "",
        app_id: str = "",
        allow_existing_positions: bool = False,
        instance_id: str = INSTANCE_ID,
    ):
        self.cfg = config
        self.settings = settings
        self.client = client
        self.account_id = account_id
        self.loginid = account_id
        self.account_balance = account_balance
        self.currency = currency
        self.live_flag = live_flag
        self.dry_run = dry_run
        self.symbol_override = symbol_override.strip()
        self.is_virtual = account_id.upper().startswith(("DOT", "VRTC", "DEMO"))
        self.token = token
        self.app_id = app_id
        self.allow_existing_positions = allow_existing_positions
        self.instance_id = instance_id or "main"
        self.reconnect_count = 0
        self.reconnect_attempts = 0
        self.reconnect_failures = 0
        self.reconnecting = False
        self.reconnect_next_delay = 0.0
        self.last_reconnect_error = ""
        self.last_disconnect_at = ""
        self.last_reconnect_at = ""
        self.external_position_ids: set[int] = set()

        self.strategy = create_strategy(settings.strategy_mode, config, settings)
        self.money = MoneyManager(settings)
        self.stats = SessionStats()

        self.symbol = ""
        self.symbol_display_name = ""
        self.active_positions: Dict[int, TradePosition] = {}
        self.opening_count = 0
        self.batch_opening = False
        self.stop_requested = False
        self.shutdown = asyncio.Event()
        self.cooldown_remaining = 0
        self.require_neutral_before_rearm = False
        self.last_metrics: Dict[str, Any] = {}
        self.last_quote: Optional[float] = None
        self.dashboard_task: Optional[asyncio.Task[None]] = None
        self.control_task: Optional[asyncio.Task[None]] = None
        self.stop_file_path: Optional[Path] = Path(STOP_FILE_RAW) if STOP_FILE_RAW else None
        self.live_control_path: Optional[Path] = Path(LIVE_CONTROL_RAW) if LIVE_CONTROL_RAW else None
        self.telemetry_path: Optional[Path] = Path(TELEMETRY_RAW) if TELEMETRY_RAW else None
        self.live_control_revision = 0
        self.live_control_status = "ready"
        self.live_control_message = "No live changes applied"
        self.entries_paused = False
        self.pending_flat_settings: Dict[str, Any] = {}
        self.runtime_prices: Deque[float] = deque(maxlen=2500)
        self.runtime_epochs: Deque[int] = deque(maxlen=2500)
        self.last_telemetry_write = 0.0
        self.balance_subscription_id = ""
        self.last_selected_take_profit = settings.take_profit
        self.last_selected_take_profit_ticks = settings.take_profit_ticks
        self.last_signal_strength = 0.0

    def _attach_client_handlers(self) -> None:
        self.client.add_handler("tick", self._on_tick)
        self.client.add_handler("proposal_open_contract", self._on_contract_update)
        self.client.add_handler("balance", self._on_balance)

    async def run(self) -> None:
        self._attach_client_handlers()
        try:
            await self._confirm_live_mode()
            await self._resolve_symbol()
            await self._check_existing_positions()
            await self._seed_history()
            await self._subscribe_balance()
            await self._subscribe_ticks()
            self._validate_session()
            self._print_start_summary()
            self.dashboard_task = asyncio.create_task(self._dashboard_loop(), name=f"dashboard-{self.instance_id}")
            if self.stop_file_path is not None or self.live_control_path is not None:
                self.control_task = asyncio.create_task(
                    self._control_file_loop(), name=f"control-{self.instance_id}"
                )

            while not self.shutdown.is_set():
                wait_shutdown = asyncio.create_task(self.shutdown.wait())
                wait_disconnect = asyncio.create_task(self.client.closed.wait())
                done, pending = await asyncio.wait(
                    {wait_shutdown, wait_disconnect},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

                if self.shutdown.is_set():
                    break
                if self.client.closed.is_set():
                    reconnected = await self._reconnect_until_ready()
                    if not reconnected:
                        break
        finally:
            if self.control_task and not self.control_task.done():
                self.control_task.cancel()
                await asyncio.gather(self.control_task, return_exceptions=True)
            if self.dashboard_task and not self.dashboard_task.done():
                self.dashboard_task.cancel()
                await asyncio.gather(self.dashboard_task, return_exceptions=True)
            await self._forget_balance_subscription()
            await self.client.close()

    async def _control_file_loop(self) -> None:
        """Handle graceful stop plus V2.22 live runtime-control messages."""
        try:
            while not self.shutdown.is_set():
                if self.stop_file_path is not None and self.stop_file_path.exists():
                    self._request_stop("User requested entry stop")
                    if self.shutdown.is_set():
                        return
                await self._read_live_control_file()
                if self.pending_flat_settings and not self.active_positions and not self.batch_opening and self.opening_count == 0:
                    self._apply_pending_flat_settings()
                await asyncio.sleep(0.20)
        except asyncio.CancelledError:
            raise

    async def _read_live_control_file(self) -> None:
        path = self.live_control_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            revision = int(payload.get("revision", 0) or 0)
            if revision <= self.live_control_revision:
                return
            self._apply_live_control(payload)
            self.live_control_revision = revision
            if self.live_control_status != "queued":
                self.live_control_status = "applied"
                self.live_control_message = str(payload.get("label") or f"Live revision {revision} applied")
            self._write_runtime_telemetry(force=True)
        except Exception as exc:
            self.live_control_status = "error"
            self.live_control_message = str(exc)
            LOGGER.error("Live control update failed: %s", exc)

    @staticmethod
    def _runtime_decimal(value: Any) -> Decimal:
        return Decimal(str(value))

    def _apply_live_control(self, payload: Dict[str, Any]) -> None:
        action = str(payload.get("action") or "apply").strip().lower()
        if action == "pause":
            self.entries_paused = True
            LOGGER.info("LIVE CONTROL | New entries paused; open positions remain managed")
        elif action == "resume":
            if not self.stop_requested:
                self.entries_paused = False
                LOGGER.info("LIVE CONTROL | New entries resumed")

        raw = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        if raw:
            field_types = {
                "max_open_trades": int, "trades_per_signal": int, "split_delay_seconds": float,
                "loss_policy": str, "loss_count": int, "profit_target": self._runtime_decimal,
                "loss_limit": self._runtime_decimal, "win_target": int, "take_profit": self._runtime_decimal,
                "take_profit_mode": str, "take_profit_max": self._runtime_decimal, "multiplier": int,
                "take_profit_unit": str, "take_profit_ticks": int, "take_profit_ticks_max": int,
                "tick_tp_server_backup": self._runtime_decimal, "server_stop_loss": self._runtime_decimal,
                "smart_activation": self._runtime_decimal, "smart_profit_floor": self._runtime_decimal,
                "smart_trail_percent": self._runtime_decimal, "smart_min_distance": self._runtime_decimal,
                "smart_activation_ticks": int, "smart_profit_floor_ticks": int, "smart_min_distance_ticks": int,
                "smart_adverse_ticks": int, "smart_adverse_window": int, "smart_opposite_signal": bool,
                "smart_server_take_profit": self._runtime_decimal, "smart_loss_enabled": bool,
                "smart_loss_soft_trigger": self._runtime_decimal, "smart_loss_reset_level": self._runtime_decimal,
                "smart_loss_hard_cap": self._runtime_decimal, "smart_loss_grace_ticks": int,
                "smart_loss_max_watch_ticks": int, "smart_loss_adverse_ticks": int,
                "smart_loss_adverse_window": int, "smart_loss_opposite_signal": bool,
                "smart_loss_rescue_profit": self._runtime_decimal,
                "ensemble_policy": str, "ensemble_min_votes": int, "ensemble_memory_ticks": int,
                "ensemble_confirmations": int, "ensemble_cooldown_ticks": int,
                "ensemble_regime_lookback": int, "ensemble_trend_threshold": float,
                "ensemble_weight_threshold": float, "ensemble_conflict_margin": float,
            }
            immediate: Dict[str, Any] = {}
            for key, converter in field_types.items():
                if key in raw:
                    val = raw[key]
                    if converter is bool:
                        if isinstance(val, str):
                            immediate[key] = val.strip().lower() in {"1","true","yes","on"}
                        else:
                            immediate[key] = bool(val)
                    else:
                        immediate[key] = converter(val)
            # Capital/split/compound change stake math. Queue only these until flat.
            deferred: Dict[str, Any] = {}
            if "capital" in raw: deferred["capital"] = self._runtime_decimal(raw["capital"])
            if "split_count" in raw: deferred["split_count"] = int(raw["split_count"])
            if "compound" in raw:
                val = raw["compound"]
                deferred["compound"] = val.strip().lower() in {"1","true","yes","on"} if isinstance(val,str) else bool(val)
            candidate = replace(self.settings, **immediate) if immediate else self.settings
            future = replace(candidate, **deferred) if deferred else candidate
            if future.max_open_trades < 1 or future.max_open_trades > 50:
                raise ValueError("Maximum open trades must be between 1 and 50")
            if future.split_count < 1 or future.split_count > 50 or future.max_open_trades > future.split_count:
                raise ValueError("Split must be 1-50 and cannot be below maximum open trades")
            if future.trades_per_signal < 1 or future.trades_per_signal > future.max_open_trades:
                raise ValueError("Trades per signal must be between 1 and maximum open trades")
            if future.multiplier not in STEP100_ALLOWED_MULTIPLIERS:
                raise ValueError(f"Multiplier must be one of {STEP100_ALLOWED_MULTIPLIERS}")
            if future.take_profit_mode not in {"fixed", "adaptive", "smart", "hybrid"}:
                raise ValueError("Unknown take-profit mode")
            if future.take_profit_unit not in {"amount", "ticks"}:
                raise ValueError("Take-profit unit must be amount or ticks")
            if future.take_profit <= 0 or future.take_profit_max <= 0 or future.take_profit_max < future.take_profit:
                raise ValueError("Take-profit maximum cannot be below the minimum")
            if future.take_profit_ticks < 1 or future.take_profit_ticks_max < future.take_profit_ticks:
                raise ValueError("Take-profit tick maximum cannot be below the minimum")
            future_stake = MoneyManager.floor_money(future.capital / Decimal(future.split_count))
            if future_stake < Decimal("0.35"):
                raise ValueError("The requested sizing would make stake per slot lower than 0.35")
            if future.server_stop_loss < 0 or (future.server_stop_loss > 0 and future.server_stop_loss >= future_stake):
                raise ValueError("Server stop loss must be below stake per slot or zero")
            if future.smart_loss_enabled:
                if future.smart_loss_reset_level >= future.smart_loss_soft_trigger:
                    raise ValueError("Smart Loss reset must be below its soft trigger")
                if future.smart_loss_hard_cap <= future.smart_loss_soft_trigger or future.smart_loss_hard_cap >= future_stake:
                    raise ValueError("Smart Loss hard cap must be above soft trigger and below stake per slot")
            if immediate:
                self.settings = candidate
                self.money.settings = self.settings
            if deferred:
                if self.active_positions or self.batch_opening or self.opening_count:
                    self.pending_flat_settings.update(deferred)
                    self.live_control_status = "queued"
                    self.live_control_message = "Stake-sizing changes queued until positions are flat"
                else:
                    self.settings = replace(self.settings, **deferred)
                    self.money.settings = self.settings
                    self.money.refresh_slot_stake()

        cfg_payload = payload.get("config") if isinstance(payload.get("config"), dict) else None
        requested_strategy = str(payload.get("strategy") or "").strip().lower()
        if cfg_payload is not None or requested_strategy:
            strategies = dict(self.cfg.strategies)
            if cfg_payload is not None:
                supplied = cfg_payload.get("strategies")
                if isinstance(supplied, dict):
                    for name, values in supplied.items():
                        if isinstance(values, dict): strategies[str(name)] = dict(values)
                dashboard_seconds = max(1, int(cfg_payload.get("dashboard_seconds", self.cfg.dashboard_seconds)))
                self.cfg = replace(self.cfg, strategies=strategies, dashboard_seconds=dashboard_seconds)
            if requested_strategy and requested_strategy != self.settings.strategy_mode:
                self.settings = replace(self.settings, strategy_mode=requested_strategy)
                self.money.settings = self.settings
            self._reload_strategy_live()

    def _apply_pending_flat_settings(self) -> None:
        if not self.pending_flat_settings:
            return
        changes = dict(self.pending_flat_settings)
        self.pending_flat_settings.clear()
        self.settings = replace(self.settings, **changes)
        self.money.settings = self.settings
        self.money.refresh_slot_stake()
        self.live_control_status = "applied"
        self.live_control_message = "Queued stake-sizing changes applied after positions became flat"
        LOGGER.info("LIVE CONTROL | Queued stake-sizing changes applied")

    def _reload_strategy_live(self) -> None:
        replacement = create_strategy(self.settings.strategy_mode, self.cfg, self.settings)
        prices = list(self.runtime_prices)
        epochs = list(self.runtime_epochs)
        minimum = int(getattr(replacement, "minimum_history", 1))
        if len(prices) >= minimum:
            if isinstance(replacement, (HammerStrategy, EnsembleStrategy, CombinedStrategy)):
                replacement.seed(prices, epochs if len(epochs) == len(prices) else None)
            else:
                replacement.seed(prices)
        self.strategy = replacement
        self.strategy.reset_confirmation()
        self.cooldown_remaining = 0
        self.require_neutral_before_rearm = True
        LOGGER.info("LIVE CONTROL | Strategy engine reloaded: %s", self.strategy.label)

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, Decimal): return float(value)
        if isinstance(value, datetime): return value.isoformat()
        if isinstance(value, dict): return {str(k): TradingBot._json_value(v) for k,v in value.items()}
        if isinstance(value, (list, tuple)): return [TradingBot._json_value(v) for v in value]
        return value

    def _settings_telemetry(self) -> Dict[str, Any]:
        result = {}
        for name in self.settings.__dataclass_fields__:
            value = getattr(self.settings, name)
            result[name] = self._json_value(value)
        return result

    def _write_runtime_telemetry(self, force: bool = False) -> None:
        path = self.telemetry_path
        if path is None:
            return
        now = time.monotonic()
        if not force and now - self.last_telemetry_write < 0.35:
            return
        self.last_telemetry_write = now
        try:
            open_profit = sum((p.current_profit for p in self.active_positions.values()), Decimal("0"))
            positions = []
            for p in self.active_positions.values():
                positions.append({
                    "contract_id": p.contract_id, "direction": p.direction, "stake": float(p.stake),
                    "take_profit": float(p.take_profit), "take_profit_unit": p.take_profit_unit,
                    "take_profit_ticks": p.take_profit_ticks, "opened_at": p.opened_at.isoformat(),
                    "entry_spot": p.entry_spot, "last_quote": p.last_quote, "profit": float(p.current_profit),
                    "peak_profit": float(p.peak_profit), "worst_profit": float(p.worst_profit),
                    "favorable_ticks": p.favorable_ticks, "peak_favorable_ticks": p.peak_favorable_ticks,
                    "worst_ticks": p.worst_ticks, "trailing_floor": float(p.trailing_floor),
                    "trailing_floor_ticks": p.trailing_floor_ticks, "protection_active": p.protection_active,
                    "loss_guard_active": p.loss_guard_active, "loss_guard_ticks": p.loss_guard_ticks,
                    "status": p.status, "closing_requested": p.closing_requested, "closing_reason": p.closing_reason,
                    "entry_pattern": p.entry_pattern, "entry_sources": p.entry_sources, "entry_regime": p.entry_regime,
                })
            runtime = max(0, int(time.monotonic() - self.stats.started_at))
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "version": BOT_VERSION,
                "running": not self.shutdown.is_set(), "paused": self.entries_paused, "stop_requested": self.stop_requested,
                "mode": "signals" if self.dry_run else ("live" if self.live_flag else "demo"),
                "account": self.account_id, "balance": float(self.account_balance), "currency": self.currency,
                "symbol": self.symbol, "symbol_display_name": self.symbol_display_name, "quote": self.last_quote,
                "runtime_seconds": runtime, "strategy_label": self.strategy.label,
                "realized_profit": float(self.stats.realized_profit), "open_profit": float(open_profit),
                "trades": self.stats.trades, "wins": self.stats.wins, "losses": self.stats.losses,
                "win_rate": float(self.stats.win_rate), "consecutive_losses": self.stats.consecutive_losses,
                "last_signal": self.stats.last_signal, "stop_reason": self.stats.stop_reason,
                "cooldown_remaining": self.cooldown_remaining, "neutral_rearm": self.require_neutral_before_rearm,
                "opening_count": self.opening_count, "batch_opening": self.batch_opening,
                "available_slots": self.money.available_slots(len(self.active_positions), self.opening_count),
                "effective_capital": float(self.money.effective_capital), "slot_stake": float(self.money.slot_stake),
                "settings": self._settings_telemetry(), "metrics": self._json_value(self.last_metrics),
                "positions": positions, "external_positions": len(self.external_position_ids),
                "reconnect": {"active": self.reconnecting, "success": self.reconnect_count, "attempts": self.reconnect_attempts,
                              "failures": self.reconnect_failures, "next_delay": self.reconnect_next_delay,
                              "last_error": self.last_reconnect_error, "last_disconnect": self.last_disconnect_at,
                              "last_reconnect": self.last_reconnect_at},
                "live_control": {"revision": self.live_control_revision, "status": self.live_control_status,
                                 "message": self.live_control_message, "pending_flat": self._json_value(self.pending_flat_settings)},
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:
            LOGGER.debug("Telemetry write skipped: %s", exc)

    async def _reconnect_until_ready(self) -> bool:
        """Retry the Deriv link until it recovers or the session is stopped.

        While reconnecting no new entries are opened. Any managed open
        contracts keep their server-side Deriv limits; after recovery the bot
        refreshes history, restores balance/tick streams and re-subscribes to
        every contract it already owns so Smart/Hybrid management can resume.
        """
        if not self.token or not self.app_id:
            self.last_reconnect_error = "Reconnect credentials unavailable"
            LOGGER.error("Connection lost and reconnect credentials are unavailable.")
            self._write_entry_diagnostic(
                "reconnect_aborted", "credentials_unavailable", None,
                {"auto_reconnect": True},
            )
            return False

        self.reconnecting = True
        self.last_disconnect_at = datetime.now(timezone.utc).isoformat()
        delay = 1.0
        self.reconnect_next_delay = delay
        self._write_entry_diagnostic(
            "connection_lost", "websocket_closed", None,
            {"auto_reconnect": True, "active_positions": len(self.active_positions)},
        )
        LOGGER.warning(
            "Connection lost. Instance %s will keep running and reconnect automatically. "
            "No new trade can open while disconnected; server-side limits remain on Deriv.",
            self.instance_id,
        )

        while not self.shutdown.is_set():
            self.reconnect_attempts += 1
            attempt = self.reconnect_attempts
            self.reconnect_next_delay = 0.0
            self._write_entry_diagnostic(
                "reconnect_attempt", "automatic_retry", None,
                {"attempt": attempt, "successful_reconnects": self.reconnect_count},
            )
            LOGGER.warning("RECONNECTING | instance=%s attempt=%d", self.instance_id, attempt)
            try:
                try:
                    await self.client.close()
                except Exception:
                    pass
                new_client = await connect_authenticated_client(
                    self.token, self.app_id, self.account_id, attempts=1
                )
                self.client = new_client
                self.balance_subscription_id = ""
                self._attach_client_handlers()
                await self._seed_history()
                await self._subscribe_balance()
                await self._subscribe_ticks()
                await self._resubscribe_active_positions()

                self.reconnect_count += 1
                self.reconnecting = False
                self.reconnect_next_delay = 0.0
                self.last_reconnect_error = ""
                self.last_reconnect_at = datetime.now(timezone.utc).isoformat()
                self._write_entry_diagnostic(
                    "reconnected", "streams_restored", None,
                    {
                        "attempt": attempt,
                        "successful_reconnects": self.reconnect_count,
                        "active_positions": len(self.active_positions),
                    },
                )
                LOGGER.info(
                    "RECONNECTED | instance=%s reconnects=%d attempts=%d active_positions=%d",
                    self.instance_id, self.reconnect_count, self.reconnect_attempts,
                    len(self.active_positions),
                )
                return True
            except Exception as exc:
                self.reconnect_failures += 1
                self.last_reconnect_error = f"{type(exc).__name__}: {exc}"[:500]
                self.reconnect_next_delay = delay
                self._write_entry_diagnostic(
                    "reconnect_failed", "retry_scheduled", None,
                    {
                        "attempt": attempt,
                        "failures": self.reconnect_failures,
                        "retry_in_seconds": delay,
                        "error": self.last_reconnect_error,
                    },
                )
                LOGGER.warning(
                    "Reconnect failed for instance %s on attempt %d: %s. Retrying in %.0f second(s)...",
                    self.instance_id, attempt, exc, delay,
                )
                try:
                    await asyncio.wait_for(self.shutdown.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(30.0, delay * 2.0)
                self.reconnect_next_delay = delay

        self.reconnecting = False
        self.reconnect_next_delay = 0.0
        return False

    async def _resubscribe_active_positions(self) -> None:
        if not self.active_positions:
            return
        LOGGER.info("Restoring %d managed contract subscription(s)...", len(self.active_positions))
        for position in list(self.active_positions.values()):
            position.subscription_id = ""
            try:
                response = await self.client.request(
                    {
                        "proposal_open_contract": 1,
                        "contract_id": position.contract_id,
                        "subscribe": 1,
                    },
                    timeout=20.0,
                )
                subscription = response.get("subscription") or {}
                position.subscription_id = str(subscription.get("id") or "")
                await self._on_contract_update(response)
            except DerivAPIError as exc:
                if exc.code in {"ContractNotFound", "BetExpired"}:
                    LOGGER.warning(
                        "Managed contract %s is no longer open after reconnect; reconciling settlement.",
                        position.contract_id,
                    )
                    await self._reconcile_settled_position(position)
                else:
                    LOGGER.warning("Could not restore contract %s: %s", position.contract_id, exc)
            except Exception as exc:
                LOGGER.warning("Could not restore contract %s: %s", position.contract_id, exc)

    async def _confirm_live_mode(self) -> None:
        if self.is_virtual or self.dry_run:
            return
        if not self.live_flag:
            raise RuntimeError(
                "This appears to be a REAL account. Restart with run_real_money.bat or --live."
            )
        confirmation = os.getenv("DERIV_LIVE_CONFIRM", "")
        if confirmation != "YES_I_ACCEPT_REAL_MONEY_RISK":
            typed = input("REAL-MONEY ACCOUNT: type LIVE to allow automatic trades: ").strip()
            if typed != "LIVE":
                raise RuntimeError("Live trading was not confirmed")

    def _selected_cooldown_ticks(self) -> int:
        profile = getattr(self.strategy, "p", {})
        try:
            return max(0, int(profile.get("cooldown_ticks", self.cfg.cooldown_ticks)))
        except (TypeError, ValueError):
            return max(0, self.cfg.cooldown_ticks)

    def _validate_session(self) -> None:
        stake = self.money.slot_stake
        if stake <= 0:
            raise RuntimeError("Calculated stake is zero. Increase capital or reduce the split count.")
        if self.settings.capital > self.account_balance and self.account_balance > 0:
            raise RuntimeError(
                f"Selected capital {self.settings.capital} exceeds account balance "
                f"{self.account_balance} {self.currency}."
            )

    @staticmethod
    def _normalized_name(value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())

    @staticmethod
    def _symbol_candidates(item: Dict[str, Any]) -> list[str]:
        fields = [
            "display_name",
            "market_display_name",
            "underlying_symbol",
            "symbol",
            "underlying_symbol_type",
            "symbol_type",
            "market",
            "submarket",
            "submarket_display_name",
            "description",
        ]
        candidates: list[str] = []
        for field_name in fields:
            value = item.get(field_name)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        return candidates

    @staticmethod
    def _symbol_summary(item: Dict[str, Any]) -> str:
        pieces = []
        for key in (
            "display_name",
            "market_display_name",
            "underlying_symbol",
            "underlying_symbol_type",
            "symbol",
            "symbol_type",
            "market",
            "submarket",
        ):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                pieces.append(f"{key}={value.strip()}")
        return " | ".join(pieces) if pieces else str(item)

    async def _resolve_symbol(self) -> None:
        if self.symbol_override:
            self.symbol = self.symbol_override
            self.symbol_display_name = self.symbol_override
            LOGGER.info("Using DERIV_SYMBOL override: %s", self.symbol_override)
            return

        response = await self.client.request({"active_symbols": "full"})
        symbols = response.get("active_symbols") or []
        wanted = {self._normalized_name(name) for name in self.cfg.symbol_names}
        exact_matches = []
        loose_matches = []

        for item in symbols:
            candidates = self._symbol_candidates(item)
            normalized = [self._normalized_name(candidate) for candidate in candidates]
            if any(candidate in wanted for candidate in normalized):
                exact_matches.append(item)
                continue
            haystack = " ".join(normalized)
            if "step" in haystack and "100" in haystack:
                loose_matches.append(item)

        matches = exact_matches or loose_matches
        if not matches:
            available = [
                self._symbol_summary(item)
                for item in symbols
                if "step" in self._symbol_summary(item).lower()
            ]
            raise RuntimeError(
                "Could not find Step 100. Set DERIV_SYMBOL=stpRNG in .env. "
                "Available Step markets: " + " ; ".join(available[:15])
            )

        selected = matches[0]
        for item in matches:
            joined = " ".join(
                self._normalized_name(candidate) for candidate in self._symbol_candidates(item)
            )
            if any(name in joined for name in ("step100index", "stepindex100", "step100")):
                selected = item
                break

        self.symbol = str(selected.get("underlying_symbol") or selected.get("symbol") or "")
        self.symbol_display_name = str(
            selected.get("display_name") or selected.get("market_display_name") or self.symbol
        )
        if not self.symbol:
            raise RuntimeError(
                "Deriv returned a Step 100 market without a symbol code. "
                f"Matched item: {self._symbol_summary(selected)}"
            )
        LOGGER.info("Selected market: %s [%s]", self.symbol_display_name, self.symbol)

    async def _check_existing_positions(self) -> None:
        if self.dry_run:
            return
        response = await self.client.request({"portfolio": 1})
        contracts = (response.get("portfolio") or {}).get("contracts") or []
        ids: list[int] = []
        for contract in contracts:
            try:
                ids.append(int(contract.get("contract_id")))
            except (TypeError, ValueError):
                pass
        self.external_position_ids = set(ids)
        if not ids:
            return
        if not self.allow_existing_positions:
            raise RuntimeError(
                "This account already has open contract(s): "
                + ", ".join(str(value) for value in ids)
                + ". Close them before starting this bot, or launch with --allow-existing-positions."
            )
        LOGGER.warning(
            "Starting while %d existing account position(s) are open: %s. "
            "They are treated as EXTERNAL and will not be managed or closed by instance %s.",
            len(ids), ", ".join(str(value) for value in ids), self.instance_id,
        )

    async def _seed_history(self) -> None:
        history_count = max(int(getattr(self.strategy, "history_count", 250)), int(getattr(self.strategy, "minimum_history", 30)))
        response = await self.client.request(
            {
                "ticks_history": self.symbol,
                "count": history_count,
                "end": "latest",
                "style": "ticks",
                "adjust_start_time": 1,
            }
        )
        history = response.get("history") or {}
        prices = [float(price) for price in history.get("prices") or []]
        epochs = [int(value) for value in history.get("times") or []]
        minimum_history = int(getattr(self.strategy, "minimum_history", 30))
        if len(prices) < minimum_history:
            raise RuntimeError(
                f"Only received {len(prices)} historical ticks; need at least "
                f"{minimum_history} for {self.strategy.label}."
            )
        if isinstance(self.strategy, (HammerStrategy, EnsembleStrategy, CombinedStrategy)):
            self.strategy.seed(prices, epochs if len(epochs) == len(prices) else None)
        else:
            self.strategy.seed(prices)
        self.runtime_prices.extend(prices)
        if len(epochs) == len(prices):
            self.runtime_epochs.extend(epochs)
        else:
            now_epoch = int(time.time())
            self.runtime_epochs.extend(range(now_epoch - len(prices) + 1, now_epoch + 1))
        LOGGER.info("Loaded %d historical ticks for %s", len(prices), self.strategy.label)

    async def _subscribe_ticks(self) -> None:
        await self.client.request({"ticks": self.symbol, "subscribe": 1})

    async def _subscribe_balance(self) -> None:
        response = await self.client.request({"balance": 1, "subscribe": 1})
        subscription = response.get("subscription") or {}
        self.balance_subscription_id = str(subscription.get("id") or "")
        await self._on_balance(response)

    async def _forget_balance_subscription(self) -> None:
        if not self.balance_subscription_id or self.client.closed.is_set():
            return
        try:
            await self.client.request({"forget": self.balance_subscription_id}, timeout=5)
        except Exception:
            pass

    async def _on_balance(self, message: Dict[str, Any]) -> None:
        balance = message.get("balance") or {}
        raw_balance = balance.get("balance")
        if raw_balance is not None:
            try:
                self.account_balance = Decimal(str(raw_balance))
            except InvalidOperation:
                pass
        currency = balance.get("currency")
        if isinstance(currency, str) and currency:
            self.currency = currency

    def _print_start_summary(self) -> None:
        mode = "SIGNALS ONLY" if self.dry_run else ("LIVE" if self.live_flag else "DEMO")
        print("\n" + "=" * 72)
        print("DR EXPLOIT CONTROL V2.22")
        print("=" * 72)
        print(f"Build source:           {Path(__file__).resolve()}")
        print(f"Entry diagnostics:      {DIAGNOSTIC_LOG_PATH}")
        print(f"Mode:                  {mode}")
        print(f"Instance:              {self.instance_id}")
        print(f"Account:               {self.account_id}")
        print(f"Market:                {self.symbol_display_name} [{self.symbol}]")
        print(f"Strategy:              {self.strategy.label}")
        print(f"Account balance:       {self.account_balance} {self.currency}")
        print(f"Session capital:       {self.settings.capital} {self.currency}")
        print(f"Capital split:         {self.settings.split_count}")
        print(f"Stake per slot:        {self.money.slot_stake} {self.currency}")
        print(f"Maximum open trades:   {self.settings.max_open_trades}")
        if self.settings.trades_per_signal > 0:
            print(f"Trades per signal:     {self.settings.trades_per_signal}")
        else:
            print(f"Signal opening mode:   {self.settings.batch_mode.upper()} (legacy)")
        print(f"Split opening delay:   {self.settings.split_delay_seconds:g} seconds")
        print(f"Compounding:           {'ON' if self.settings.compound else 'OFF'}")
        print(f"Loss policy:           {self._loss_policy_label()}")
        if self.settings.take_profit_mode == "adaptive":
            if self.settings.take_profit_unit == "ticks":
                print(f"Exit mode:             ADAPTIVE TP {self.settings.take_profit_ticks}-{self.settings.take_profit_ticks_max} net Step ticks")
                print(f"Tick server backup:    {self.settings.tick_tp_server_backup} {self.currency}")
            else:
                print(
                    f"Exit mode:             ADAPTIVE TP {self.settings.take_profit}-"
                    f"{self.settings.take_profit_max} {self.currency}"
                )
        elif self.settings.take_profit_mode in {"smart", "hybrid"}:
            print(
                f"Exit mode:             {self.settings.take_profit_mode.upper()} SMART EXIT ({self.settings.take_profit_unit.upper()})"
            )
            if self.settings.take_profit_unit == "ticks":
                print(
                    f"Profit protection:     activate={self.settings.smart_activation_ticks} ticks "
                    f"floor={self.settings.smart_profit_floor_ticks} ticks "
                    f"trail={self.settings.smart_trail_percent}%/min {self.settings.smart_min_distance_ticks} tick(s)"
                )
            else:
                print(
                    f"Profit protection:     activate={self.settings.smart_activation} "
                    f"floor={self.settings.smart_profit_floor} "
                    f"trail={self.settings.smart_trail_percent}%/min {self.settings.smart_min_distance}"
                )
            print(
                f"Emergency server TP:   {self.settings.smart_server_take_profit} {self.currency}"
            )
        else:
            if self.settings.take_profit_unit == "ticks":
                print(f"Exit mode:             FIXED TP {self.settings.take_profit_ticks} net Step ticks")
                print(f"Tick server backup:    {self.settings.tick_tp_server_backup} {self.currency}")
            else:
                print(f"Exit mode:             FIXED TP {self.settings.take_profit} {self.currency}")
        print(f"Multiplier:            x{self.settings.multiplier}")
        print(f"Server stop loss:       {self.settings.server_stop_loss if self.settings.server_stop_loss > 0 else 'OFF'}")
        if self.settings.smart_loss_enabled:
            print(
                f"Smart loss guard:       soft=-{self.settings.smart_loss_soft_trigger} "
                f"reset=-{self.settings.smart_loss_reset_level} hard=-{self.settings.smart_loss_hard_cap} "
                f"grace={self.settings.smart_loss_grace_ticks} max_watch={self.settings.smart_loss_max_watch_ticks}"
            )
            print(
                f"Loss recovery:          adverse={self.settings.smart_loss_adverse_ticks}/{self.settings.smart_loss_adverse_window} "
                f"opposite={'ON' if self.settings.smart_loss_opposite_signal else 'OFF'} "
                f"rescue=+{self.settings.smart_loss_rescue_profit if self.settings.smart_loss_rescue_profit > 0 else 'OFF'}"
            )
        else:
            print("Smart loss guard:       OFF")
        if self.settings.profit_target > 0:
            print(f"Session profit target: {self.settings.profit_target} {self.currency}")
        if self.settings.loss_limit > 0:
            print(f"Session loss limit:    {self.settings.loss_limit} {self.currency}")
        if self.settings.win_target > 0:
            print(f"Session win target:    {self.settings.win_target}")
        print("=" * 72)
        print("Press Ctrl+C to stop opening trades and exit.\n")

    def _loss_policy_label(self) -> str:
        policy = self.settings.loss_policy
        if policy == "continue":
            return "Continue after losses"
        if policy == "first":
            return "Stop after first loss"
        if policy == "consecutive":
            return f"Stop after {self.settings.loss_count} consecutive losses"
        if policy == "total":
            return f"Stop after {self.settings.loss_count} total losses"
        return policy

    def _write_entry_diagnostic(
        self,
        event: str,
        reason: str = "",
        signal_name: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a sanitized entry-decision event for UI troubleshooting.

        This file intentionally contains no API token, account ID, or contract
        IDs. Diagnostic I/O is non-fatal and can never prevent an order.
        """
        try:
            metrics = self.last_metrics or {}
            active_count = len(getattr(self, "active_positions", {}) or {})
            opening_count = int(getattr(self, "opening_count", 0) or 0)
            try:
                available_slots = int(self.money.available_slots(active_count, opening_count))
            except Exception:
                available_slots = 0
            row: Dict[str, Any] = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "version": BOT_VERSION,
                "instance_id": self.instance_id,
                "event": str(event),
                "reason": str(reason),
                "strategy_mode": self.settings.strategy_mode,
                "signal": str(signal_name or ""),
                "quote": float(self.last_quote) if self.last_quote is not None else None,
                "entries_enabled": not bool(self.stop_requested or self.shutdown.is_set()),
                "batch_opening": bool(self.batch_opening),
                "cooldown_remaining": int(self.cooldown_remaining),
                "require_neutral_before_rearm": bool(self.require_neutral_before_rearm),
                "active_positions": active_count,
                "opening_count": opening_count,
                "available_slots": available_slots,
                "reconnects": int(getattr(self, "reconnect_count", 0) or 0),
                "reconnect_attempts": int(getattr(self, "reconnect_attempts", 0) or 0),
                "reconnect_failures": int(getattr(self, "reconnect_failures", 0) or 0),
                "reconnecting": bool(getattr(self, "reconnecting", False)),
                "reconnect_next_delay": float(getattr(self, "reconnect_next_delay", 0.0) or 0.0),
            }
            if self.settings.strategy_mode == "ensemble":
                row["ensemble"] = {
                    "policy": metrics.get("ensemble_policy", ""),
                    "regime": metrics.get("ensemble_regime", ""),
                    "regime_efficiency": metrics.get("regime_efficiency", 0),
                    "gate": metrics.get("ensemble_gate_text", ""),
                    "required_votes": metrics.get("ensemble_required_votes", 0),
                    "raw_direction": metrics.get("ensemble_raw_direction", ""),
                    "output_signal": metrics.get("ensemble_output_signal", ""),
                    "gate_armed": metrics.get("ensemble_gate_armed", True),
                    "up_votes": metrics.get("ensemble_up_votes", 0),
                    "down_votes": metrics.get("ensemble_down_votes", 0),
                    "up_score": metrics.get("ensemble_up_score", 0),
                    "down_score": metrics.get("ensemble_down_score", 0),
                    "sources": metrics.get("ensemble_sources", ""),
                    "vote_summary": metrics.get("ensemble_vote_summary", ""),
                    "fresh": metrics.get("ensemble_fresh", {}),
                    "eligible": metrics.get("ensemble_eligible", []),
                    "selected": metrics.get("ensemble_selected", []),
                    "memory": metrics.get("ensemble_signal_memory", {}),
                }
            if extra:
                row["extra"] = extra
            DIAGNOSTIC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DIAGNOSTIC_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as exc:
            LOGGER.debug("Entry diagnostic log skipped: %s", exc)

    def _entry_stop_pending(self) -> bool:
        """Return True when no new entry may be sent.

        The control-file check is synchronous and cheap. Calling it directly
        on the entry path closes the tiny race between a Web UI stop click and
        the background control task noticing that request.
        """
        if self.stop_requested or self.shutdown.is_set() or self.entries_paused:
            return True
        stop_file_path = getattr(self, "stop_file_path", None)
        if stop_file_path is not None and stop_file_path.exists():
            self._request_stop("User requested entry stop")
            return True
        return False

    def _signal_trade_count(self, available_slots: int) -> int:
        """Return how many positions a fresh signal may open right now."""
        available = max(0, int(available_slots))
        if available <= 0:
            return 0
        requested_per_signal = int(self.settings.trades_per_signal or 0)
        if requested_per_signal > 0:
            return min(requested_per_signal, available)
        # Backward compatibility for older launchers/configs that only know
        # the historical one/all switch.
        return 1 if self.settings.batch_mode == "one" else available

    @staticmethod
    def _clean_pattern_name(value: Any) -> str:
        pattern = str(value or "").strip()
        return "" if pattern.upper() in {"", "NONE", "NULL"} else pattern

    def _entry_pattern_from_metrics(self, metrics: Dict[str, Any]) -> str:
        """Resolve the pattern from the frozen entry-time signal metrics.

        Never read ``self.last_metrics`` here. Split batches can span several
        market ticks, so the live metrics may already describe a different
        candle by the time later positions are opened.
        """
        metrics = metrics or {}
        if self.settings.strategy_mode == "ensemble":
            sources = str(metrics.get("ensemble_sources", ""))
            if "hammer" not in {part.strip() for part in sources.split("+") if part.strip()}:
                return ""
            hammer_metrics = (metrics.get("ensemble_child_metrics", {}) or {}).get("hammer", {}) or {}
            return self._clean_pattern_name(
                hammer_metrics.get("signal_pattern") or hammer_metrics.get("last_pattern_id")
            )
        return self._clean_pattern_name(metrics.get("signal_pattern") or metrics.get("last_pattern_id"))

    def _freeze_signal_snapshot(self, direction: str) -> Dict[str, Any]:
        """Capture immutable entry context before an asynchronous batch starts."""
        snapshot = copy.deepcopy(self.last_metrics or {})
        snapshot["entry_signal_direction"] = str(direction or "")
        snapshot["entry_signal_quote"] = self.last_quote
        snapshot["entry_signal_epoch"] = int(time.time())
        snapshot["entry_pattern_frozen"] = self._entry_pattern_from_metrics(snapshot)
        return snapshot

    async def _on_tick(self, message: Dict[str, Any]) -> None:
        tick = message.get("tick") or {}
        if str(tick.get("symbol") or self.symbol) != self.symbol:
            return
        quote = tick.get("quote")
        if quote is None:
            return

        self.last_quote = float(quote)
        tick_epoch = int(tick.get("epoch") or time.time())
        self.runtime_prices.append(self.last_quote)
        self.runtime_epochs.append(tick_epoch)
        if isinstance(self.strategy, (HammerStrategy, EnsembleStrategy, CombinedStrategy)):
            signal_name, metrics = self.strategy.add_tick(self.last_quote, tick_epoch)
        else:
            signal_name, metrics = self.strategy.add_tick(self.last_quote)
        if metrics:
            self.last_metrics = metrics

        # Smart/hybrid exits must keep managing existing positions even when
        # the bot has stopped opening new trades.
        self._evaluate_tick_exits(signal_name, self.last_quote)
        self._write_runtime_telemetry()

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

        if self._entry_stop_pending():
            self._write_entry_diagnostic("blocked", "entry_stop", signal_name)
            return
        if self.batch_opening:
            self._write_entry_diagnostic("blocked", "batch_opening", signal_name)
            return
        if self.cooldown_remaining > 0:
            self._write_entry_diagnostic("blocked", "cooldown", signal_name)
            return

        if self.require_neutral_before_rearm:
            if signal_name is None:
                self.require_neutral_before_rearm = False
                self._write_entry_diagnostic("rearmed", "neutral_seen", signal_name)
            else:
                self._write_entry_diagnostic("blocked", "awaiting_neutral_rearm", signal_name)
            return

        if signal_name is None:
            reason = "no_strategy_signal"
            if self.settings.strategy_mode == "ensemble":
                raw = str((self.last_metrics or {}).get("ensemble_raw_direction", ""))
                if raw:
                    reason = "ensemble_raw_vote_waiting_confirmation_or_rearm"
                elif int((self.last_metrics or {}).get("ensemble_up_votes", 0) or 0) or int((self.last_metrics or {}).get("ensemble_down_votes", 0) or 0):
                    reason = "ensemble_not_enough_agreement"
                else:
                    reason = "ensemble_no_eligible_votes"
            self._write_entry_diagnostic("waiting", reason, signal_name)
            return

        self.stats.last_signal = signal_name
        signal_snapshot = self._freeze_signal_snapshot(signal_name)
        if self.settings.strategy_mode == "hammer" and not signal_snapshot.get("entry_pattern_frozen"):
            # A Hammer trade without a pattern means the entry context is corrupt.
            # Fail closed instead of opening an unattributable trade that would
            # poison optimization/history data.
            self._write_entry_diagnostic(
                "blocked",
                "hammer_signal_missing_frozen_pattern",
                signal_name,
                {"metrics": signal_snapshot},
            )
            LOGGER.warning(
                "HAMMER ENTRY BLOCKED: signal %s had no valid frozen pattern; waiting for a fresh signal",
                signal_name,
            )
            self.require_neutral_before_rearm = True
            self.cooldown_remaining = max(self.cooldown_remaining, self._selected_cooldown_ticks())
            return
        self._write_entry_diagnostic(
            "signal_ready",
            "strategy_gate_passed",
            signal_name,
            {"entry_pattern": signal_snapshot.get("entry_pattern_frozen", "")},
        )
        if self.settings.strategy_mode == "ensemble":
            try:
                self._write_ensemble_decision_log(signal_name)
            except Exception as exc:
                # Analytics must never prevent a valid trade from reaching the
                # availability/order path. Keep this second guard even though
                # the logger itself is defensive.
                LOGGER.warning("Ensemble analytics failed; continuing with signal %s: %s", signal_name, exc)
        available = self.money.available_slots(len(self.active_positions), self.opening_count)
        if available <= 0:
            self._write_entry_diagnostic("blocked", "no_available_slots", signal_name)
            return

        count = self._signal_trade_count(available)
        self.require_neutral_before_rearm = True

        if self.dry_run:
            LOGGER.info(
                "SIGNAL ONLY: %s at %.5f | would open %d trade(s) of %s %s | delay=%gs",
                signal_name,
                self.last_quote,
                count,
                self.money.slot_stake,
                self.currency,
                self.settings.split_delay_seconds,
            )
            self._write_entry_diagnostic("dry_run", "signal_would_open", signal_name, {"requested_count": count})
            if self.settings.take_profit_mode == "adaptive":
                target, score, tick_target = self._select_take_profit(signal_name, signal_snapshot)
                if tick_target:
                    LOGGER.info(
                        "SIGNAL ONLY adaptive TP: %s net ticks (server backup %s %s, strength %.0f%%)",
                        tick_target, target, self.currency, score * 100.0,
                    )
                else:
                    LOGGER.info(
                        "SIGNAL ONLY adaptive TP: %s %s (strength %.0f%%)",
                        target, self.currency, score * 100.0,
                    )
            self.cooldown_remaining = self._selected_cooldown_ticks()
            return

        self.batch_opening = True
        self.opening_count = count
        self._write_entry_diagnostic("open_scheduled", "opening_task_created", signal_name, {"requested_count": count})
        asyncio.create_task(self._open_trade_batch(signal_name, count, signal_snapshot), name="open-trade-batch")

    def _write_ensemble_decision_log(self, direction: str) -> None:
        """Write ensemble analytics without ever blocking a trade.

        Analytics are diagnostic only. A missing path, permissions problem,
        malformed row, or any other logging error must never consume a valid
        signal before the order-opening path runs.
        """
        metrics = self.last_metrics or {}
        try:
            ENSEMBLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            new_file = not ENSEMBLE_LOG_PATH.exists()
            with ENSEMBLE_LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if new_file:
                    writer.writerow([
                        "timestamp_utc", "instance_id", "direction", "policy", "regime",
                        "regime_efficiency", "sources", "vote_summary", "up_votes", "down_votes",
                        "up_score", "down_score", "signal_strength", "required_votes", "gate",
                    ])
                writer.writerow([
                    datetime.now(timezone.utc).isoformat(), self.instance_id, direction,
                    metrics.get("ensemble_policy", ""), metrics.get("ensemble_regime", ""),
                    metrics.get("regime_efficiency", ""), metrics.get("ensemble_sources", ""),
                    metrics.get("ensemble_vote_summary", ""), metrics.get("ensemble_up_votes", ""),
                    metrics.get("ensemble_down_votes", ""), metrics.get("ensemble_up_score", ""),
                    metrics.get("ensemble_down_score", ""), metrics.get("strength", ""),
                    metrics.get("ensemble_required_votes", ""), metrics.get("ensemble_gate_text", ""),
                ])
        except Exception as exc:
            # Deliberately non-fatal: trading must not depend on analytics I/O.
            LOGGER.warning("Ensemble analytics log skipped (trade execution continues): %s", exc)

    def _evaluate_tick_exits(self, signal_name: Optional[str], quote: float) -> None:
        profit_exit_enabled = self.settings.take_profit_mode in {"smart", "hybrid"}
        loss_guard_enabled = self.settings.smart_loss_enabled
        tick_measurement_enabled = self.settings.take_profit_unit == "ticks"
        tick_tp_enabled = tick_measurement_enabled and self.settings.take_profit_mode in {"fixed", "adaptive"}
        smart_tick_enabled = tick_measurement_enabled and self.settings.take_profit_mode in {"smart", "hybrid"}
        if not profit_exit_enabled and not loss_guard_enabled and not tick_tp_enabled:
            return

        for position in list(self.active_positions.values()):
            if position.closing_requested:
                position.last_quote = quote
                continue

            adverse = False
            if position.last_quote is not None and quote != position.last_quote:
                adverse = SmartExit.is_adverse(position.direction, position.last_quote, quote)
                if profit_exit_enabled:
                    position.adverse_tick_history.append(adverse)
                    window = self.settings.smart_adverse_window
                    if len(position.adverse_tick_history) > window:
                        del position.adverse_tick_history[:-window]
                if loss_guard_enabled:
                    position.loss_adverse_tick_history.append(adverse)
                    loss_window = self.settings.smart_loss_adverse_window
                    if len(position.loss_adverse_tick_history) > loss_window:
                        del position.loss_adverse_tick_history[:-loss_window]
            position.last_quote = quote

            if tick_measurement_enabled and position.entry_spot is not None:
                signed_move = (quote - position.entry_spot) if position.direction == "MULTUP" else (position.entry_spot - quote)
                position.favorable_ticks = round(signed_move / 0.1, 6)
                position.peak_favorable_ticks = max(position.peak_favorable_ticks, position.favorable_ticks)
                position.worst_ticks = min(position.worst_ticks, position.favorable_ticks)

                if tick_tp_enabled and position.take_profit_ticks > 0:
                    if position.favorable_ticks >= position.take_profit_ticks:
                        self._schedule_position_sell(position, f"take_profit_ticks_{position.take_profit_ticks}")
                        continue

                if smart_tick_enabled:
                    if (
                        not position.protection_active
                        and position.favorable_ticks >= self.settings.smart_activation_ticks
                    ):
                        position.protection_active = True
                    if position.protection_active:
                        position.trailing_floor_ticks = SmartExit.trailing_floor_ticks(
                            position.peak_favorable_ticks,
                            self.settings.smart_profit_floor_ticks,
                            self.settings.smart_trail_percent,
                            self.settings.smart_min_distance_ticks,
                        )
                        if (
                            position.favorable_ticks <= position.trailing_floor_ticks
                            and position.peak_favorable_ticks >= self.settings.smart_activation_ticks
                        ):
                            self._schedule_position_sell(position, "trailing_tick_floor")
                            continue

            # Smart Loss is intentionally allowed to watch a trade before profit
            # protection activates. Crossing the soft trigger only starts a watch;
            # it does not immediately close the position.
            if loss_guard_enabled and position.loss_guard_active:
                position.loss_guard_ticks += 1
                if position.loss_guard_ticks >= self.settings.smart_loss_grace_ticks:
                    if (
                        self.settings.smart_loss_opposite_signal
                        and signal_name == SmartExit.opposite(position.direction)
                    ):
                        self._schedule_position_sell(position, "smart_loss_opposite_signal")
                        continue

                    adverse_count = sum(position.loss_adverse_tick_history)
                    if (
                        len(position.loss_adverse_tick_history) >= self.settings.smart_loss_adverse_ticks
                        and adverse_count >= self.settings.smart_loss_adverse_ticks
                    ):
                        self._schedule_position_sell(
                            position,
                            f"smart_loss_adverse_{adverse_count}_of_{len(position.loss_adverse_tick_history)}",
                        )
                        continue

                max_watch = self.settings.smart_loss_max_watch_ticks
                if (
                    max_watch > 0
                    and position.loss_guard_ticks >= max_watch
                    and position.current_profit <= SmartLossGuard.soft_loss(self.settings.smart_loss_soft_trigger)
                ):
                    self._schedule_position_sell(position, "smart_loss_watch_timeout")
                    continue

            if self.settings.take_profit_mode != "hybrid":
                continue
            if not position.protection_active:
                continue
            # Never intentionally trigger a momentum exit below the protected
            # minimum. Tick mode uses net favorable underlying ticks; amount mode
            # uses floating P/L. Market movement can still cause exit slippage.
            if self.settings.take_profit_unit == "ticks":
                if position.entry_spot is None or position.favorable_ticks < self.settings.smart_profit_floor_ticks:
                    continue
            elif position.current_profit < self.settings.smart_profit_floor:
                continue

            if (
                self.settings.smart_opposite_signal
                and signal_name == SmartExit.opposite(position.direction)
            ):
                self._schedule_position_sell(position, "opposite_strategy_signal")
                continue

            adverse_count = sum(position.adverse_tick_history)
            if (
                len(position.adverse_tick_history) >= self.settings.smart_adverse_ticks
                and adverse_count >= self.settings.smart_adverse_ticks
            ):
                self._schedule_position_sell(
                    position,
                    f"momentum_reversal_{adverse_count}_of_{len(position.adverse_tick_history)}",
                )

    def _evaluate_smart_loss_profit(self, position: TradePosition, profit: Decimal) -> bool:
        """Update recovery-aware loss state. Return True if a close was scheduled."""
        if not self.settings.smart_loss_enabled or position.closing_requested:
            return False

        position.worst_profit = min(position.worst_profit, profit)
        soft_loss = SmartLossGuard.soft_loss(self.settings.smart_loss_soft_trigger)
        reset_profit = SmartLossGuard.reset_profit(self.settings.smart_loss_reset_level)
        hard_loss = SmartLossGuard.hard_loss(self.settings.smart_loss_hard_cap)

        # Hard cap is the only immediate local loss exit. It exists to prevent a
        # recovery watch from turning into a full-stake loss.
        if profit <= hard_loss:
            self._schedule_position_sell(position, "smart_loss_hard_cap")
            return True

        # Optional break-even rescue: if a trade that entered loss-watch mode later
        # reaches a small positive P/L, secure the recovery instead of allowing it
        # to fall negative again. Set rescue_profit to 0 to disable this behavior.
        if (
            position.loss_guard_ever_triggered
            and self.settings.smart_loss_rescue_profit > 0
            and profit >= self.settings.smart_loss_rescue_profit
        ):
            self._schedule_position_sell(position, "smart_loss_recovery_rescue")
            return True

        # Once a smart-profit floor is active, profit protection owns the exit.
        if position.protection_active:
            position.loss_guard_active = False
            position.loss_guard_ticks = 0
            position.loss_adverse_tick_history.clear()
            return False

        if position.loss_guard_active:
            position.loss_guard_trough = min(position.loss_guard_trough, profit)
            # A meaningful recovery cancels the watch and gives the original trade
            # thesis another chance. This is the main anti-whipsaw feature.
            if profit >= reset_profit:
                position.loss_guard_active = False
                position.loss_guard_ticks = 0
                position.loss_guard_reset_count += 1
                position.loss_adverse_tick_history.clear()
                LOGGER.info(
                    "SMART LOSS RECOVERED | contract_id=%s profit=%s reset_at=%s trough=%s",
                    position.contract_id, profit, reset_profit, position.loss_guard_trough,
                )
            return False

        if profit <= soft_loss:
            position.loss_guard_active = True
            position.loss_guard_ever_triggered = True
            position.loss_guard_ticks = 0
            position.loss_guard_trough = profit
            position.loss_guard_trigger_count += 1
            position.loss_adverse_tick_history.clear()
            LOGGER.info(
                "SMART LOSS WATCH | contract_id=%s profit=%s soft=%s hard=%s grace=%d",
                position.contract_id, profit, soft_loss, hard_loss, self.settings.smart_loss_grace_ticks,
            )
        return False

    def _schedule_position_sell(self, position: TradePosition, reason: str) -> None:
        if position.closing_requested or position.contract_id not in self.active_positions:
            return
        position.closing_requested = True
        position.closing_reason = reason
        LOGGER.info(
            "SMART EXIT REQUEST | contract_id=%s reason=%s profit=%s peak=%s floor=%s",
            position.contract_id,
            reason,
            position.current_profit,
            position.peak_profit,
            position.trailing_floor,
        )
        asyncio.create_task(
            self._sell_position(position),
            name=f"sell-{position.contract_id}",
        )

    async def _open_trade_batch(self, direction: str, count: int, signal_snapshot: Dict[str, Any]) -> None:
        opened = 0
        stake = self.money.slot_stake
        try:
            for index in range(count):
                if self._entry_stop_pending():
                    break
                await self._open_single_trade(direction, stake, index + 1, count, signal_snapshot)
                opened += 1

                # Stagger a multi-position batch when requested. Trade 1 opens
                # immediately; each later split opens after this interval.
                has_more = index + 1 < count
                if has_more and self.settings.split_delay_seconds > 0:
                    LOGGER.info(
                        "Split delay: waiting %g second(s) before trade %d/%d",
                        self.settings.split_delay_seconds,
                        index + 2,
                        count,
                    )
                    await self._wait_split_delay(self.settings.split_delay_seconds)
        except (ConnectionError, ConnectionClosed, asyncio.TimeoutError) as exc:
            self._write_entry_diagnostic("batch_error", "connection_interrupted", direction, {"opened": opened, "requested": count, "error": type(exc).__name__})
            LOGGER.warning(
                "Trade batch interrupted by connection loss after %d of %d requested position(s): %s. "
                "The batch is cancelled; the bot will reconnect and wait for a fresh signal.",
                opened, count, exc,
            )
            self.require_neutral_before_rearm = True
            self.cooldown_remaining = max(self.cooldown_remaining, self._selected_cooldown_ticks())
        except Exception as exc:
            self._write_entry_diagnostic("batch_error", "unexpected_error", direction, {"opened": opened, "requested": count, "error": f"{type(exc).__name__}: {exc}"[:500]})
            LOGGER.exception(
                "Trade batch failed after opening %d of %d requested position(s). "
                "No further positions will be opened until the next fresh signal.",
                opened,
                count,
            )
            self.require_neutral_before_rearm = True
            self.cooldown_remaining = max(self.cooldown_remaining, self._selected_cooldown_ticks())
        finally:
            self.opening_count = 0
            self.batch_opening = False
            if self.stop_requested and not self.active_positions:
                self.shutdown.set()

    async def _wait_split_delay(self, delay_seconds: float) -> None:
        """Wait between split entries, while reacting quickly to stop requests."""
        deadline = time.monotonic() + max(0.0, delay_seconds)
        while not self.stop_requested and not self.shutdown.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.25))

    def _select_take_profit(self, direction: str, metrics: Optional[Dict[str, Any]] = None) -> tuple[Decimal, float, int]:
        signal_metrics = metrics if metrics is not None else self.last_metrics
        if self.settings.take_profit_mode in {"smart", "hybrid"}:
            self.last_selected_take_profit=self.settings.smart_server_take_profit;self.last_selected_take_profit_ticks=0;self.last_signal_strength=AdaptiveTakeProfit.score(direction,signal_metrics)
            return self.settings.smart_server_take_profit,self.last_signal_strength,0
        if self.settings.take_profit_unit=="ticks":
            score=AdaptiveTakeProfit.score(direction,signal_metrics) if self.settings.take_profit_mode=="adaptive" else 0.0
            lo=max(1,int(self.settings.take_profit_ticks));hi=max(lo,int(self.settings.take_profit_ticks_max));ticks=int(round(lo+(hi-lo)*score)) if self.settings.take_profit_mode=="adaptive" else lo
            self.last_selected_take_profit=self.settings.tick_tp_server_backup;self.last_selected_take_profit_ticks=ticks;self.last_signal_strength=score
            return self.settings.tick_tp_server_backup,score,ticks
        if self.settings.take_profit_mode!="adaptive":
            self.last_selected_take_profit=self.settings.take_profit;self.last_selected_take_profit_ticks=0;self.last_signal_strength=0.0;return self.settings.take_profit,0.0,0
        target,score=AdaptiveTakeProfit.target(direction,signal_metrics,self.settings.take_profit,self.settings.take_profit_max);self.last_selected_take_profit=target;self.last_selected_take_profit_ticks=0;self.last_signal_strength=score;return target,score,0


    async def _open_single_trade(
        self,
        direction: str,
        stake: Decimal,
        batch_number: int,
        batch_total: int,
        signal_snapshot: Dict[str, Any],
    ) -> None:
        if self._entry_stop_pending():
            LOGGER.info("Entry cancelled before proposal because graceful stop is active")
            return
        selected_take_profit, signal_strength, selected_take_profit_ticks = self._select_take_profit(direction, signal_snapshot)
        limit_order = {"take_profit": float(selected_take_profit)}
        if self.settings.server_stop_loss > 0:
            limit_order["stop_loss"] = float(self.settings.server_stop_loss)
        proposal_request = {
            "proposal": 1,
            "amount": float(stake),
            "basis": "stake",
            "contract_type": direction,
            "currency": self.currency,
            "duration_unit": "s",
            "multiplier": self.settings.multiplier,
            "underlying_symbol": self.symbol,
            "limit_order": limit_order,
        }
        self._write_entry_diagnostic("proposal_request", "sending_proposal", direction, {"batch_number": batch_number, "batch_total": batch_total})
        try:
            proposal_response = await self.client.request(proposal_request)
        except Exception as exc:
            self._write_entry_diagnostic("proposal_error", "proposal_request_failed", direction, {"error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        proposal = proposal_response.get("proposal") or {}
        self._write_entry_diagnostic("proposal_ok", "proposal_received", direction, {"has_id": bool(proposal.get("id"))})
        proposal_id = proposal.get("id")
        try:
            proposal_spot = float(proposal.get("spot")) if proposal.get("spot") is not None else self.last_quote
        except (TypeError, ValueError):
            proposal_spot = self.last_quote
        if not proposal_id:
            raise RuntimeError("Deriv proposal did not include an ID")

        ask_price = Decimal(str(proposal.get("ask_price", stake)))
        if ask_price <= 0:
            raise RuntimeError(f"Invalid proposal ask price: {ask_price}")

        if self._entry_stop_pending():
            LOGGER.info("Entry cancelled after proposal and before buy because graceful stop is active")
            return

        LOGGER.info(
            "OPENING %s %d/%d | stake=%s %s | x%d | TP=%s | strength=%.0f%%",
            direction,
            batch_number,
            batch_total,
            stake,
            self.currency,
            self.settings.multiplier,
            (f"{selected_take_profit_ticks} ticks (server {selected_take_profit})" if selected_take_profit_ticks else selected_take_profit),
            signal_strength * 100.0,
        )
        self._write_entry_diagnostic("buy_request", "sending_buy", direction, {"batch_number": batch_number, "batch_total": batch_total})
        try:
            buy_response = await self.client.request(
                {"buy": str(proposal_id), "price": float(ask_price)}
            )
        except Exception as exc:
            self._write_entry_diagnostic("buy_error", "buy_request_failed", direction, {"error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        buy = buy_response.get("buy") or {}
        contract_id = buy.get("contract_id")
        if contract_id is None:
            raise RuntimeError("Deriv buy response did not include contract_id")

        position = TradePosition(
            contract_id=int(contract_id),
            direction=direction,
            stake=stake,
            take_profit=selected_take_profit,
            opened_at=datetime.now(timezone.utc),
            take_profit_unit=self.settings.take_profit_unit,
            take_profit_ticks=selected_take_profit_ticks,
            entry_spot=proposal_spot,
            entry_pattern=str(signal_snapshot.get("entry_pattern_frozen") or ""),
            last_quote=self.last_quote,
            entry_sources=str(signal_snapshot.get("ensemble_sources", "")),
            entry_regime=str(signal_snapshot.get("ensemble_regime", "")),
            entry_vote_summary=str(signal_snapshot.get("ensemble_vote_summary", "")),
        )
        self.active_positions[position.contract_id] = position
        self._write_entry_diagnostic(
            "buy_success",
            "contract_opened",
            direction,
            {
                "batch_number": batch_number,
                "batch_total": batch_total,
                "entry_pattern": position.entry_pattern,
                "frozen_signal_epoch": signal_snapshot.get("entry_signal_epoch"),
            },
        )
        LOGGER.info(
            "OPEN contract_id=%s direction=%s stake=%s TP=%s",
            position.contract_id,
            direction,
            stake,
            (f"{position.take_profit_ticks} ticks" if position.take_profit_unit == "ticks" else position.take_profit),
        )

        response = await self.client.request(
            {
                "proposal_open_contract": 1,
                "contract_id": position.contract_id,
                "subscribe": 1,
            }
        )
        subscription = response.get("subscription") or {}
        position.subscription_id = str(subscription.get("id") or "")

    async def _on_contract_update(self, message: Dict[str, Any]) -> None:
        contract = message.get("proposal_open_contract") or {}
        contract_id_raw = contract.get("contract_id")
        if contract_id_raw is None:
            return
        try:
            contract_id = int(contract_id_raw)
        except (TypeError, ValueError):
            return

        position = self.active_positions.get(contract_id)
        if position is None:
            return

        if contract.get("entry_spot") is not None:
            try:
                position.entry_spot = float(contract.get("entry_spot"))
            except (TypeError, ValueError):
                pass

        try:
            profit = Decimal(str(contract.get("profit", "0")))
        except InvalidOperation:
            profit = Decimal("0")
        position.current_profit = profit
        position.status = str(contract.get("status") or "open")

        is_sold = bool(contract.get("is_sold")) or position.status in {
            "sold",
            "won",
            "lost",
            "cancelled",
            "expired",
        }
        if not is_sold:
            position.peak_profit = max(position.peak_profit, profit)
            position.worst_profit = min(position.worst_profit, profit)
            mode = self.settings.take_profit_mode

            # Profit protection activation is evaluated before loss-guard ownership
            # so a trade that has already become a protected winner is not treated
            # as a loss candidate on a small giveback.
            if (
                mode in {"smart", "hybrid"}
                and self.settings.take_profit_unit == "amount"
                and profit >= self.settings.smart_activation
            ):
                position.protection_active = True

            if self._evaluate_smart_loss_profit(position, profit):
                return

            if mode in {"smart", "hybrid"}:
                if self.settings.take_profit_unit == "amount" and position.protection_active:
                    position.trailing_floor = SmartExit.trailing_floor(
                        position.peak_profit,
                        self.settings.smart_profit_floor,
                        self.settings.smart_trail_percent,
                        self.settings.smart_min_distance,
                    )
                    if (
                        profit <= position.trailing_floor
                        and position.peak_profit >= self.settings.smart_activation
                    ):
                        self._schedule_position_sell(position, "trailing_profit_floor")
                        return

                # Tick Smart/Hybrid trailing is evaluated from the underlying tick
                # stream. This money TP remains the server-side emergency ceiling.
                if profit >= position.take_profit and not position.closing_requested:
                    self._schedule_position_sell(position, "server_take_profit_fallback")
                return

            if profit >= position.take_profit and not position.closing_requested:
                position.closing_reason = "take_profit_fallback"
                position.closing_requested = True
                LOGGER.info(
                    "TAKE PROFIT REACHED | contract_id=%s profit=%s %s target=%s",
                    contract_id,
                    profit,
                    self.currency,
                    position.take_profit,
                )
                asyncio.create_task(
                    self._sell_position(position),
                    name=f"sell-{position.contract_id}",
                )
            return

        await self._finalize_position(position, profit, position.status)

    async def _sell_position(self, position: TradePosition) -> None:
        """Close a position at the configured take-profit without duplicate sells.

        Every proposal already includes a server-side take-profit.  The server can
        therefore settle the contract between the last open-contract tick and our
        fallback market-sell request.  In that race Deriv returns ContractNotFound
        (or BetExpired).  Those responses mean "not currently open", not that the
        strategy failed, so keep the close guard set and reconcile the final state.
        """
        # Give the attached server-side take-profit a brief chance to publish its
        # final contract update before issuing the client-side fallback sell.
        await asyncio.sleep(0.35)
        if position.contract_id not in self.active_positions:
            return

        try:
            await self.client.request(
                {"sell": position.contract_id, "price": 0},
                timeout=15.0,
            )
            LOGGER.info(
                "Sell request accepted for contract_id=%s; waiting for final status.",
                position.contract_id,
            )
        except DerivAPIError as exc:
            if exc.code in {"BetExpired", "ContractNotFound"}:
                position.status = "settling"
                LOGGER.info(
                    "Contract %s is no longer listed as open (%s). "
                    "Reconciling its final settlement instead of retrying sell.",
                    position.contract_id,
                    exc.code,
                )
                await self._reconcile_settled_position(position)
            else:
                position.closing_requested = False
                LOGGER.exception("Exit sell failed for contract %s", position.contract_id)
        except Exception:
            position.closing_requested = False
            LOGGER.exception("Exit sell failed for contract %s", position.contract_id)

    async def _reconcile_settled_position(self, position: TradePosition) -> None:
        """Resolve a contract that disappeared during a take-profit sell race.

        First wait for the normal proposal_open_contract final update.  If it does
        not arrive, query the contract directly several times.  As a final safety
        check, use portfolio (open positions only).  When the ID is absent there,
        remove the stale dashboard row and record the last server-reported profit.
        """
        for attempt in range(6):
            if position.contract_id not in self.active_positions:
                return
            await asyncio.sleep(0.5 + attempt * 0.25)
            try:
                response = await self.client.request(
                    {
                        "proposal_open_contract": 1,
                        "contract_id": position.contract_id,
                    },
                    timeout=10.0,
                )
                contract = response.get("proposal_open_contract") or {}
                status = str(contract.get("status") or "open")
                is_sold = bool(contract.get("is_sold")) or status in {
                    "sold",
                    "won",
                    "lost",
                    "cancelled",
                    "expired",
                }
                if is_sold:
                    await self._on_contract_update(response)
                    return
            except DerivAPIError as exc:
                if exc.code not in {"BetExpired", "ContractNotFound"}:
                    LOGGER.warning(
                        "Settlement refresh failed for contract %s: %s",
                        position.contract_id,
                        exc,
                    )
            except Exception as exc:
                LOGGER.warning(
                    "Settlement refresh failed for contract %s: %s",
                    position.contract_id,
                    exc,
                )

        if position.contract_id not in self.active_positions:
            return

        try:
            response = await self.client.request({"portfolio": 1}, timeout=10.0)
            contracts = (response.get("portfolio") or {}).get("contracts") or []
            open_ids = {
                int(item.get("contract_id"))
                for item in contracts
                if item.get("contract_id") is not None
            }
        except Exception as exc:
            # Do not retry sell blindly when account state could not be checked.
            LOGGER.warning(
                "Could not reconcile contract %s from portfolio: %s. "
                "Keeping it in SETTLING state and waiting for a final stream update.",
                position.contract_id,
                exc,
            )
            return

        if position.contract_id not in open_ids:
            LOGGER.info(
                "Contract %s is absent from the open portfolio; treating it as "
                "server-side take-profit settlement at last reported P/L=%s %s.",
                position.contract_id,
                position.current_profit,
                self.currency,
            )
            await self._finalize_position(
                position,
                position.current_profit,
                "server_take_profit",
            )
            return

        # The position is genuinely still open. Allow one later fallback sell.
        position.status = "open"
        position.closing_requested = False
        LOGGER.warning(
            "Contract %s is still open after settlement reconciliation; "
            "the bot may retry its take-profit sell on a later update.",
            position.contract_id,
        )

    async def _finalize_position(
        self,
        position: TradePosition,
        profit: Decimal,
        status: str,
    ) -> None:
        if position.contract_id not in self.active_positions:
            return

        del self.active_positions[position.contract_id]
        if position.subscription_id:
            asyncio.create_task(
                self._forget_subscription(position.subscription_id),
                name=f"forget-{position.contract_id}",
            )

        if not position.closing_reason:
            if profit <= 0:
                position.closing_reason = "platform_stopout_or_settlement"
            elif self.settings.take_profit_mode in {"smart", "hybrid"}:
                position.closing_reason = (
                    "server_emergency_take_profit"
                    if profit >= position.take_profit
                    else "server_settlement"
                )
            else:
                position.closing_reason = "server_take_profit"

        result = "WIN" if profit > 0 else "LOSS"
        self.stats.trades += 1
        self.stats.realized_profit += profit
        if profit > 0:
            self.stats.wins += 1
            self.stats.consecutive_losses = 0
        else:
            self.stats.losses += 1
            self.stats.consecutive_losses += 1

        self.money.register_result(profit, positions_are_flat=not self.active_positions)
        if not self.active_positions and not self.batch_opening and self.opening_count == 0:
            self._apply_pending_flat_settings()
        self._write_runtime_telemetry(force=True)
        closed_duration = max(0.0, (datetime.now(timezone.utc) - position.opened_at).total_seconds())
        self._write_entry_diagnostic(
            "trade_closed",
            "position_finalized",
            position.direction,
            {
                "profit": float(profit),
                "result": result,
                "exit_reason": position.closing_reason,
                "entry_sources": position.entry_sources,
                "entry_regime": position.entry_regime,
                "entry_vote_summary": position.entry_vote_summary,
                "duration_seconds": round(closed_duration, 3),
            },
        )
        self._write_trade_log(position, profit, result, status)
        LOGGER.info(
            "CLOSED contract_id=%s result=%s profit=%s %s reason=%s | session=%s",
            position.contract_id,
            result,
            profit,
            self.currency,
            position.closing_reason,
            self.stats.realized_profit,
        )

        self._apply_stop_rules(profit)
        self.strategy.reset_confirmation()
        self.cooldown_remaining = max(self.cooldown_remaining, self._selected_cooldown_ticks())
        self.require_neutral_before_rearm = True

        if self.stop_requested and not self.active_positions and not self.batch_opening:
            self.shutdown.set()

    async def _forget_subscription(self, subscription_id: str) -> None:
        try:
            await self.client.request({"forget": subscription_id}, timeout=10)
        except Exception:
            pass

    def _apply_stop_rules(self, latest_profit: Decimal) -> None:
        policy = self.settings.loss_policy
        if latest_profit <= 0:
            if policy == "first":
                self._request_stop("Stopped after first loss")
            elif policy == "consecutive" and (
                self.stats.consecutive_losses >= self.settings.loss_count
            ):
                self._request_stop(
                    f"Reached {self.settings.loss_count} consecutive losses"
                )
            elif policy == "total" and self.stats.losses >= self.settings.loss_count:
                self._request_stop(f"Reached {self.settings.loss_count} total losses")

        if (
            self.settings.profit_target > 0
            and self.stats.realized_profit >= self.settings.profit_target
        ):
            self._request_stop(
                f"Profit target reached: {self.stats.realized_profit} {self.currency}"
            )
        if (
            self.settings.loss_limit > 0
            and self.stats.realized_profit <= -self.settings.loss_limit
        ):
            self._request_stop(
                f"Loss limit reached: {self.stats.realized_profit} {self.currency}"
            )
        if self.settings.win_target > 0 and self.stats.wins >= self.settings.win_target:
            self._request_stop(f"Win target reached: {self.stats.wins}")
        if self.money.effective_capital <= 0:
            self._request_stop("Allocated session capital has been exhausted")
        elif self.settings.compound and not self.active_positions:
            if self.money.slot_stake <= 0:
                self._request_stop("Compounded stake fell below 0.01")

    def _request_stop(self, reason: str) -> None:
        if self.stop_requested:
            return
        self.stop_requested = True
        self.stats.stop_reason = reason
        LOGGER.warning("ENTRY STOP REQUESTED: %s", reason)
        if self.active_positions:
            LOGGER.warning(
                "New MULTUP/MULTDOWN entries are disabled. Keeping the connection alive and "
                "waiting for %d managed position(s) to close through their normal exit rules.",
                len(self.active_positions),
            )
        elif not self.batch_opening:
            self.shutdown.set()

    def _write_trade_log(
        self,
        position: TradePosition,
        profit: Decimal,
        result: str,
        status: str,
    ) -> None:
        closed_at = datetime.now(timezone.utc)
        duration_seconds = max(0.0, (closed_at - position.opened_at).total_seconds())
        new_file = not LOG_PATH.exists()
        with LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(
                    [
                        "closed_at_utc",
                        "account",
                        "symbol",
                        "contract_id",
                        "direction",
                        "stake",
                        "currency",
                        "multiplier",
                        "take_profit",
                        "take_profit_unit",
                        "take_profit_ticks",
                        "entry_spot",
                        "max_favorable_ticks",
                        "worst_ticks",
                        "entry_pattern",
                        "exit_mode",
                        "exit_reason",
                        "peak_profit",
                        "trailing_floor",
                        "trailing_floor_ticks",
                        "worst_profit",
                        "smart_loss_triggered",
                        "smart_loss_trigger_count",
                        "smart_loss_reset_count",
                        "smart_loss_trough",
                        "profit",
                        "result",
                        "status",
                        "duration_seconds",
                        "session_profit",
                        "account_balance_last_seen",
                        "split_count",
                        "max_open_trades",
                        "compound",
                        "split_delay_seconds",
                        "strategy",
                        "instance_id",
                    ]
                )
            writer.writerow(
                [
                    closed_at.isoformat(),
                    self.account_id,
                    self.symbol,
                    position.contract_id,
                    position.direction,
                    str(position.stake),
                    self.currency,
                    self.settings.multiplier,
                    str(position.take_profit),
                    position.take_profit_unit,
                    position.take_profit_ticks,
                    position.entry_spot if position.entry_spot is not None else "",
                    f"{position.peak_favorable_ticks:.3f}",
                    f"{position.worst_ticks:.3f}",
                    position.entry_pattern,
                    self.settings.take_profit_mode,
                    position.closing_reason,
                    str(position.peak_profit),
                    str(position.trailing_floor),
                    f"{position.trailing_floor_ticks:.3f}",
                    str(position.worst_profit),
                    position.loss_guard_ever_triggered,
                    position.loss_guard_trigger_count,
                    position.loss_guard_reset_count,
                    str(position.loss_guard_trough),
                    str(profit),
                    result,
                    status,
                    f"{duration_seconds:.3f}",
                    str(self.stats.realized_profit),
                    str(self.account_balance),
                    self.settings.split_count,
                    self.settings.max_open_trades,
                    self.settings.compound,
                    self.settings.split_delay_seconds,
                    self.settings.strategy_mode,
                    self.instance_id,
                ]
            )

    async def _dashboard_loop(self) -> None:
        try:
            while not self.shutdown.is_set():
                await asyncio.sleep(self.cfg.dashboard_seconds)
                self._render_dashboard()
        except asyncio.CancelledError:
            raise

    def _hammer_dashboard_lines(self, metrics: Dict[str, Any]) -> list[str]:
        if not metrics:
            return []
        lines = [
            f"Hammer candle: {metrics.get('candle_progress', 0)}/"
            f"{metrics.get('candle_ticks', self.cfg.hammer_candle_ticks)} ticks  "
            f"Last pattern: {metrics.get('last_pattern', 'NONE')}  "
            f"Pending: {metrics.get('pending', 'NONE')}"
        ]
        if metrics.get("candle_open") is not None:
            lines.append(
                f"Last OHLC: O={metrics.get('candle_open', 0):.5f} "
                f"H={metrics.get('candle_high', 0):.5f} "
                f"L={metrics.get('candle_low', 0):.5f} "
                f"C={metrics.get('candle_close', 0):.5f}"
            )
        return lines

    def _render_dashboard(self) -> None:
        runtime = int(time.monotonic() - self.stats.started_at)
        hours, remainder = divmod(runtime, 3600)
        minutes, seconds = divmod(remainder, 60)
        open_profit = sum(
            (position.current_profit for position in self.active_positions.values()),
            Decimal("0"),
        )
        available_slots = self.money.available_slots(
            len(self.active_positions), self.opening_count
        )
        mode = "SIGNALS" if self.dry_run else ("LIVE" if self.live_flag else "DEMO")
        state = "DRAINING" if self.stop_requested else ("PAUSED" if self.entries_paused else "RUNNING")

        lines = [
            "",
            "=" * 72,
            f"DR EXPLOIT CONTROL V2.22 | {mode} | {state}",
            "=" * 72,
            f"Account: {self.account_id:<18} Balance: {self.account_balance} {self.currency}",
            f"Market:  {self.symbol:<18} Quote: {self.last_quote if self.last_quote is not None else '-'}",
            f"Capital: {self.settings.capital:<10} Effective: {self.money.effective_capital:<10} Stake/slot: {self.money.slot_stake}",
            f"Open: {len(self.active_positions)}/{self.settings.max_open_trades}  Available slots: {available_slots}  Split: {self.settings.split_count}",
            (f"Entries: BLOCKED — waiting for {len(self.active_positions)} managed open trade(s) to finish" if self.stop_requested else ("Entries: PAUSED — live runtime pause" if self.entries_paused else "Entries: ENABLED")),
            f"Strategy: {self.strategy.label}  Trades/signal: {self.settings.trades_per_signal if self.settings.trades_per_signal > 0 else ('1' if self.settings.batch_mode == 'one' else 'ALL')}  Delay: {self.settings.split_delay_seconds:g}s",
            (
                (f"Take profit: ADAPTIVE TICKS {self.settings.take_profit_ticks}-{self.settings.take_profit_ticks_max} | Last selected: {self.last_selected_take_profit_ticks} ticks | server={self.settings.tick_tp_server_backup}" if self.settings.take_profit_unit == "ticks" else f"Take profit: ADAPTIVE AMOUNT {self.settings.take_profit}-{self.settings.take_profit_max} | Last selected: {self.last_selected_take_profit} | Signal strength: {self.last_signal_strength * 100:.0f}%")
                if self.settings.take_profit_mode == "adaptive"
                else (
                    (f"Take profit: {self.settings.take_profit_mode.upper()} TICKS activate={self.settings.smart_activation_ticks} floor={self.settings.smart_profit_floor_ticks} trail={self.settings.smart_trail_percent}% min={self.settings.smart_min_distance_ticks} server=${self.settings.smart_server_take_profit}" if self.settings.take_profit_unit == "ticks" else f"Take profit: {self.settings.take_profit_mode.upper()} AMOUNT activate={self.settings.smart_activation} floor={self.settings.smart_profit_floor} trail={self.settings.smart_trail_percent}% server={self.settings.smart_server_take_profit}")
                    if self.settings.take_profit_mode in {"smart", "hybrid"}
                    else (f"Take profit: FIXED {self.settings.take_profit_ticks} ticks | server={self.settings.tick_tp_server_backup}" if self.settings.take_profit_unit == "ticks" else f"Take profit: FIXED {self.settings.take_profit}")
                )
            ),
            (
                f"Smart loss: ON soft=-{self.settings.smart_loss_soft_trigger} reset=-{self.settings.smart_loss_reset_level} "
                f"hard=-{self.settings.smart_loss_hard_cap} grace={self.settings.smart_loss_grace_ticks} "
                f"max={self.settings.smart_loss_max_watch_ticks} rescue=+{self.settings.smart_loss_rescue_profit if self.settings.smart_loss_rescue_profit > 0 else 'OFF'}"
                if self.settings.smart_loss_enabled
                else "Smart loss: OFF"
            ),
            f"Wins: {self.stats.wins}  Losses: {self.stats.losses}  Win rate: {self.stats.win_rate}%  Consecutive losses: {self.stats.consecutive_losses}",
            f"Realized P/L: {self.stats.realized_profit}  Open P/L: {open_profit}  Signal: {self.stats.last_signal}",
            f"Runtime: {hours:02d}:{minutes:02d}:{seconds:02d}  Loss policy: {self._loss_policy_label()}  Reconnects: {self.reconnect_count}",
            f"Instance: {self.instance_id}  External account positions ignored: {len(self.external_position_ids)}"
            + ("  LINK: RECONNECTING" if self.reconnecting else "  LINK: ONLINE"),
            (
                f"Position sync: managed={len(self.active_positions)} "
                f"subscribed={sum(1 for position in self.active_positions.values() if position.subscription_id)} "
                f"external={len(self.external_position_ids)} opening={self.opening_count}"
            ),
            (
                f"Reconnect guard: AUTO | attempts={self.reconnect_attempts} failures={self.reconnect_failures} "
                f"success={self.reconnect_count} next={self.reconnect_next_delay:g}s"
                + (f" | last_error={self.last_reconnect_error}" if self.last_reconnect_error else "")
            ),
        ]
        if self.last_metrics:
            try:
                lines.extend(self.strategy.dashboard_lines(self.last_metrics))
            except Exception:
                LOGGER.exception("Failed to format strategy dashboard metrics")
        if self.active_positions:
            lines.append("Open positions:")
            for position in list(self.active_positions.values())[:10]:
                lines.append(
                    f"  #{position.contract_id} {position.direction:<8} "
                    f"stake={position.stake} TP={(('SMART-TICKS a=' + str(self.settings.smart_activation_ticks) + '/f=' + str(self.settings.smart_profit_floor_ticks)) if self.settings.take_profit_mode in {'smart', 'hybrid'} and position.take_profit_unit == 'ticks' else ((str(position.take_profit_ticks)+'ticks') if position.take_profit_unit == 'ticks' else str(position.take_profit)))} "
                    f"P/L={position.current_profit} status={position.status} "
                    f"peak={(str(round(position.peak_favorable_ticks, 2)) + 't') if position.take_profit_unit == 'ticks' else position.peak_profit} "
                    f"floor={(str(round(position.trailing_floor_ticks, 2)) + 't') if position.take_profit_unit == 'ticks' else position.trailing_floor} "
                    f"worst={position.worst_profit} loss_watch={'ON' if position.loss_guard_active else 'off'}"
                )
        if self.stats.stop_reason:
            lines.append(f"Stop reason: {self.stats.stop_reason}")
        lines.append("=" * 72)
        self._write_runtime_telemetry(force=True)
        print("\n".join(lines), flush=True)


# ---------------------------------------------------------------------------
# Input helpers and CLI
# ---------------------------------------------------------------------------


def parse_decimal(raw: str, name: str, allow_zero: bool = False) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if allow_zero:
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    elif value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value.quantize(MONEY_STEP, rounding=ROUND_DOWN)


def ask_decimal(
    prompt: str,
    default: Optional[Decimal] = None,
    allow_zero: bool = False,
) -> Decimal:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return parse_decimal(raw, prompt, allow_zero=allow_zero)
        except ValueError as exc:
            print(f"Invalid value: {exc}")


def ask_int(
    prompt: str,
    default: Optional[int] = None,
    minimum: int = 1,
    maximum: Optional[int] = None,
) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if value < minimum:
            print(f"Value must be at least {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Value must be no more than {maximum}.")
            continue
        return value


def ask_nonnegative_float(
    prompt: str,
    default: float = 0.0,
    maximum: float = 3600.0,
) -> float:
    while True:
        raw = input(f"{prompt} [{default:g}]: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a number, for example 0, 0.5, 1, or 2.")
            continue
        if not (0 <= value <= maximum):
            print(f"Value must be between 0 and {maximum:g} seconds.")
            continue
        if value != value or value in {float("inf"), float("-inf")}:
            print("Please enter a finite number.")
            continue
        return value


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    default_label = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{default_label}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Enter Y or N.")


def ask_choice(prompt: str, choices: Dict[str, str], default: str) -> str:
    while True:
        print(f"\n{prompt}")
        for key, label in choices.items():
            marker = " (default)" if key == default else ""
            print(f"  {key}) {label}{marker}")
        raw = input("> ").strip() or default
        if raw in choices:
            return raw
        print("Choose one of the listed numbers.")


def collect_session_settings(
    args: argparse.Namespace,
    config: BotConfig,
    account_balance: Decimal,
    currency: str,
) -> SessionSettings:
    print("\n" + "=" * 72)
    print("DR EXPLOIT CONTROL V2.22 — SESSION SETUP")
    print("=" * 72)
    print(f"Account balance: {account_balance} {currency}")
    print("Only the amount you allocate below will be used to calculate trade sizes.")

    ensemble_preset = str(getattr(args, "ensemble_preset", None) or "custom")
    ensemble_policy = str(getattr(args, "ensemble_policy", None) or "regime")
    ensemble_min_votes = int(getattr(args, "ensemble_min_votes", None) or 2)
    ensemble_memory_ticks = int(getattr(args, "ensemble_memory_ticks", None) or 8)
    ensemble_confirmations = int(getattr(args, "ensemble_confirmations", None) or 1)
    ensemble_cooldown_ticks = int(getattr(args, "ensemble_cooldown_ticks", None) or 3)
    ensemble_regime_lookback = int(getattr(args, "ensemble_regime_lookback", None) or 24)
    ensemble_trend_threshold = float(getattr(args, "ensemble_trend_threshold", None) or 0.45)
    ensemble_weight_threshold = float(getattr(args, "ensemble_weight_threshold", None) or 1.25)
    ensemble_conflict_margin = float(getattr(args, "ensemble_conflict_margin", None) or 0.20)
    raw_ensemble_strategies = str(getattr(args, "ensemble_strategies", None) or "")
    ensemble_strategies = tuple(s.strip() for s in raw_ensemble_strategies.split(",") if s.strip() in SINGLE_STRATEGY_IDS)
    try:
        ensemble_weights = json.loads(getattr(args, "ensemble_weights_json", None) or "{}")
        if not isinstance(ensemble_weights, dict): ensemble_weights = {}
    except json.JSONDecodeError:
        ensemble_weights = {}
    try:
        ensemble_profile_overrides = json.loads(getattr(args, "ensemble_config_json", None) or "{}")
        if not isinstance(ensemble_profile_overrides, dict): ensemble_profile_overrides = {}
    except json.JSONDecodeError:
        ensemble_profile_overrides = {}

    if args.strategy:
        strategy_mode = args.strategy
    else:
        strategy_choice = ask_choice(
            "Select trading strategy:",
            {
                "1": "EMA + RSI momentum",
                "2": "Candlestick patterns",
                "3": "MACD trend crossover",
                "4": "Bollinger mean reversion",
                "5": "Bollinger breakout",
                "6": "Stochastic reversal",
                "7": "Donchian breakout",
                "8": "RSI trend pullback",
                "9": "Tick momentum burst",
                "10": "Best Mix candidate (regime-aware multi-strategy)",
                "11": "All strategies (regime-aware multi-strategy)",
            },
            default="1",
        )
        strategy_mode = {
            "1": "ema_rsi", "2": "hammer", "3": "macd",
            "4": "bollinger_reversion", "5": "bollinger_breakout",
            "6": "stochastic", "7": "donchian", "8": "rsi_pullback",
            "9": "tick_momentum", "10": "ensemble", "11": "ensemble",
        }[strategy_choice]
        if strategy_choice == "10":
            ensemble_preset = "best"
        elif strategy_choice == "11":
            ensemble_preset = "all"

    capital = (
        parse_decimal(args.capital, "Capital")
        if args.capital is not None
        else ask_decimal("How much account balance should this session use")
    )
    if account_balance > 0 and capital > account_balance:
        raise RuntimeError(
            f"Selected capital {capital} is larger than account balance {account_balance}."
        )

    if args.split is not None:
        split_count = args.split
    else:
        split_choice = ask_choice(
            "Split the selected capital into:",
            {
                "1": "1 trade slot",
                "2": "2 trade slots",
                "3": "4 trade slots",
                "4": "5 trade slots",
                "5": "10 trade slots",
                "6": "Custom number",
            },
            default="1",
        )
        split_map = {"1": 1, "2": 2, "3": 4, "4": 5, "5": 10}
        split_count = (
            split_map[split_choice]
            if split_choice in split_map
            else ask_int("Custom split count", minimum=1, maximum=50)
        )

    calculated_stake = MoneyManager.floor_money(capital / Decimal(split_count))
    if calculated_stake <= 0:
        raise RuntimeError("The split creates a zero stake. Increase capital or reduce split.")
    print(f"Calculated stake per slot: {calculated_stake} {currency}")

    max_open = (
        args.max_open
        if args.max_open is not None
        else ask_int(
            "Maximum simultaneous open trades",
            default=split_count,
            minimum=1,
            maximum=split_count,
        )
    )
    if max_open > split_count:
        raise RuntimeError("Maximum open trades cannot exceed the split count.")

    if getattr(args, "trades_per_signal", None) is not None:
        trades_per_signal = int(args.trades_per_signal)
        if trades_per_signal < 1 or trades_per_signal > max_open:
            raise RuntimeError("--trades-per-signal must be between 1 and --max-open")
        batch_mode = "one" if trades_per_signal == 1 else ("all" if trades_per_signal == max_open else "count")
    elif args.batch_mode:
        # Legacy command-line compatibility. ALL maps to the current max-open
        # value while ONE maps to exactly one trade per fresh signal.
        batch_mode = args.batch_mode
        trades_per_signal = 1 if batch_mode == "one" else max_open
    else:
        trades_per_signal = ask_int(
            "How many trades should each fresh signal open",
            default=1,
            minimum=1,
            maximum=max_open,
        )
        batch_mode = "one" if trades_per_signal == 1 else ("all" if trades_per_signal == max_open else "count")

    if args.split_delay is not None:
        if not (0 <= args.split_delay <= 3600) or args.split_delay != args.split_delay:
            raise RuntimeError("--split-delay must be a finite number from 0 to 3600 seconds")
        split_delay_seconds = args.split_delay
    elif trades_per_signal > 1:
        split_delay_seconds = ask_nonnegative_float(
            "Delay between each split trade opening in seconds (0 disables)",
            default=0.0,
        )
    else:
        split_delay_seconds = 0.0

    compound = args.compound if args.compound is not None else ask_yes_no(
        "Compound session profit into future trade sizes", default=False
    )

    if args.loss_policy:
        loss_policy = args.loss_policy
    else:
        loss_choice = ask_choice(
            "What should happen after a losing trade?",
            {
                "1": "Continue trading",
                "2": "Stop after the first loss",
                "3": "Stop after X consecutive losses",
                "4": "Stop after X total losses",
            },
            default="1",
        )
        loss_policy = {
            "1": "continue",
            "2": "first",
            "3": "consecutive",
            "4": "total",
        }[loss_choice]

    loss_count = 1
    if loss_policy in {"consecutive", "total"}:
        loss_count = args.loss_count or ask_int("Number of losses before stopping", default=3)

    take_profit_unit=str(getattr(args,"take_profit_unit",None) or "amount").lower()
    if take_profit_unit not in {"amount","ticks"}:raise RuntimeError("Take-profit unit must be amount or ticks.")
    take_profit_ticks=max(1,int(getattr(args,"take_profit_ticks",None) or 3));take_profit_ticks_max=max(take_profit_ticks,int(getattr(args,"take_profit_ticks_max",None) or take_profit_ticks))
    tick_tp_server_backup=parse_decimal(args.tick_tp_server_backup,"Tick TP server backup") if getattr(args,"tick_tp_server_backup",None) is not None else Decimal("2.00")

    if args.take_profit_mode:
        take_profit_mode = args.take_profit_mode
    else:
        tp_choice = ask_choice(
            "Exit mode:",
            {
                "1": "Fixed take-profit target",
                "2": "Adaptive take-profit target",
                "3": "Smart trailing-profit exit",
                "4": "Hybrid smart exit (trailing + reversal signals)",
            },
            default="4",
        )
        take_profit_mode = {
            "1": "fixed",
            "2": "adaptive",
            "3": "smart",
            "4": "hybrid",
        }[tp_choice]

    smart_activation = Decimal("0.15")
    smart_profit_floor = Decimal("0.10")
    smart_trail_percent = Decimal("30")
    smart_min_distance = Decimal("0.05")
    smart_activation_ticks = 3
    smart_profit_floor_ticks = 1
    smart_min_distance_ticks = 1
    smart_adverse_ticks = 3
    smart_adverse_window = 4
    smart_opposite_signal = True
    smart_server_take_profit = Decimal("2.00")

    if take_profit_mode == "adaptive":
        take_profit = (
            parse_decimal(args.take_profit, "Minimum take profit")
            if args.take_profit is not None
            else ask_decimal(
                "Minimum adaptive take profit (cannot be below 0.10)",
                default=Decimal("0.10"),
            )
        )
        take_profit = max(Decimal("0.10"), take_profit)
        take_profit_max = (
            parse_decimal(args.take_profit_max, "Maximum take profit")
            if args.take_profit_max is not None
            else ask_decimal("Maximum adaptive take profit", default=Decimal("0.30"))
        )
        if take_profit_max < take_profit:
            raise RuntimeError("Maximum adaptive take profit cannot be below the minimum.")
    elif take_profit_mode in {"smart", "hybrid"}:
        smart_activation = (
            parse_decimal(args.smart_activation, "Smart exit activation")
            if args.smart_activation is not None
            else Decimal("0.15")
        )
        smart_profit_floor = (
            parse_decimal(args.smart_profit_floor, "Smart protected profit floor")
            if args.smart_profit_floor is not None
            else Decimal("0.10")
        )
        smart_profit_floor = max(Decimal("0.10"), smart_profit_floor)
        smart_activation_ticks = max(1, int(args.smart_activation_ticks or 3))
        smart_profit_floor_ticks = max(1, int(args.smart_profit_floor_ticks or 1))
        smart_min_distance_ticks = max(1, int(args.smart_min_distance_ticks or 1))
        if take_profit_unit == "ticks":
            if smart_activation_ticks < smart_profit_floor_ticks:
                raise RuntimeError("Smart activation ticks cannot be below protected floor ticks.")
        elif smart_activation < smart_profit_floor:
            raise RuntimeError("Smart activation cannot be below the protected profit floor.")
        smart_trail_percent = (
            parse_decimal(args.smart_trail_percent, "Trailing giveback percent")
            if args.smart_trail_percent is not None
            else ask_decimal("Trailing giveback percent", default=Decimal("30"))
        )
        if not Decimal("1") <= smart_trail_percent <= Decimal("95"):
            raise RuntimeError("Trailing giveback percent must be from 1 to 95.")
        smart_min_distance = (
            parse_decimal(args.smart_min_distance, "Minimum trail distance")
            if args.smart_min_distance is not None
            else ask_decimal("Minimum trailing distance", default=Decimal("0.05"))
        )
        smart_server_take_profit = (
            parse_decimal(args.smart_server_take_profit, "Emergency server take profit")
            if args.smart_server_take_profit is not None
            else ask_decimal("Emergency server-side take profit", default=Decimal("2.00"))
        )
        if take_profit_unit == "amount" and smart_server_take_profit < smart_activation:
            raise RuntimeError("Emergency server take profit cannot be below activation.")
        smart_adverse_ticks = args.smart_adverse_ticks or 3
        smart_adverse_window = args.smart_adverse_window or 4
        if smart_adverse_ticks < 1 or smart_adverse_window < smart_adverse_ticks:
            raise RuntimeError("Adverse tick window must be at least the adverse tick count.")
        if args.smart_opposite_signal is None:
            smart_opposite_signal = True
        else:
            smart_opposite_signal = args.smart_opposite_signal
        take_profit = smart_profit_floor
        take_profit_max = smart_server_take_profit
    else:
        take_profit = (
            parse_decimal(args.take_profit, "Take profit")
            if args.take_profit is not None
            else ask_decimal("Fixed take profit for each trade", default=config.take_profit)
        )
        take_profit_max = take_profit

    multiplier = args.multiplier or config.multiplier
    if multiplier not in STEP100_ALLOWED_MULTIPLIERS:
        supported = ", ".join(f"x{value}" for value in STEP100_ALLOWED_MULTIPLIERS)
        raise RuntimeError(
            f"Unsupported Step 100 multiplier x{multiplier}. "
            f"Choose one of: {supported}."
        )

    profit_target = (
        parse_decimal(args.profit_target, "Profit target", allow_zero=True)
        if args.profit_target is not None
        else ask_decimal(
            "Session profit target (0 disables)",
            default=Decimal("0"),
            allow_zero=True,
        )
    )
    loss_limit = (
        parse_decimal(args.loss_limit, "Loss limit", allow_zero=True)
        if args.loss_limit is not None
        else ask_decimal(
            "Session loss limit (0 disables)",
            default=Decimal("0"),
            allow_zero=True,
        )
    )
    win_target = (
        args.win_target
        if args.win_target is not None
        else ask_int("Stop after number of wins (0 disables)", default=0, minimum=0)
    )

    server_stop_loss = (
        parse_decimal(args.server_stop_loss, "Server stop loss", allow_zero=True)
        if getattr(args, "server_stop_loss", None) is not None
        else Decimal("0")
    )
    if server_stop_loss < 0:
        raise RuntimeError("Server stop loss cannot be negative.")
    if server_stop_loss > 0 and server_stop_loss >= calculated_stake:
        raise RuntimeError("Server stop loss must be below the stake per trade, or set it to 0 to disable.")

    smart_loss_enabled = bool(args.smart_loss_enabled) if args.smart_loss_enabled is not None else False
    smart_loss_soft_trigger = (
        parse_decimal(args.smart_loss_soft_trigger, "Smart loss soft trigger")
        if args.smart_loss_soft_trigger is not None else Decimal("0.60")
    )
    smart_loss_reset_level = (
        parse_decimal(args.smart_loss_reset_level, "Smart loss recovery reset", allow_zero=True)
        if args.smart_loss_reset_level is not None else Decimal("0.20")
    )
    smart_loss_hard_cap = (
        parse_decimal(args.smart_loss_hard_cap, "Smart loss hard cap")
        if args.smart_loss_hard_cap is not None else Decimal("2.50")
    )
    smart_loss_grace_ticks = args.smart_loss_grace_ticks if args.smart_loss_grace_ticks is not None else 8
    smart_loss_max_watch_ticks = args.smart_loss_max_watch_ticks if args.smart_loss_max_watch_ticks is not None else 30
    smart_loss_adverse_ticks = args.smart_loss_adverse_ticks if args.smart_loss_adverse_ticks is not None else 4
    smart_loss_adverse_window = args.smart_loss_adverse_window if args.smart_loss_adverse_window is not None else 6
    smart_loss_opposite_signal = (
        args.smart_loss_opposite_signal if args.smart_loss_opposite_signal is not None else True
    )
    smart_loss_rescue_profit = (
        parse_decimal(args.smart_loss_rescue_profit, "Smart loss rescue profit", allow_zero=True)
        if args.smart_loss_rescue_profit is not None else Decimal("0.02")
    )
    if smart_loss_enabled and smart_loss_reset_level >= smart_loss_soft_trigger:
        raise RuntimeError("Smart loss reset level must be smaller than the soft trigger.")
    if smart_loss_enabled and smart_loss_hard_cap <= smart_loss_soft_trigger:
        raise RuntimeError("Smart loss hard cap must be larger than the soft trigger.")
    if smart_loss_enabled and smart_loss_hard_cap >= calculated_stake:
        raise RuntimeError("Smart loss hard cap must be below the stake per trade.")
    if smart_loss_grace_ticks < 0 or smart_loss_max_watch_ticks < 0:
        raise RuntimeError("Smart loss tick settings cannot be negative.")
    if smart_loss_enabled and smart_loss_max_watch_ticks and smart_loss_max_watch_ticks < smart_loss_grace_ticks:
        raise RuntimeError("Smart loss max watch ticks must be 0 or at least the grace ticks.")
    if smart_loss_enabled and (smart_loss_adverse_ticks < 1 or smart_loss_adverse_window < smart_loss_adverse_ticks):
        raise RuntimeError("Smart loss adverse window must be at least the adverse tick count.")

    settings = SessionSettings(
        capital=capital,
        split_count=split_count,
        max_open_trades=max_open,
        batch_mode=batch_mode,
        compound=compound,
        loss_policy=loss_policy,
        loss_count=loss_count,
        profit_target=profit_target,
        loss_limit=loss_limit,
        win_target=win_target,
        take_profit=take_profit,
        take_profit_mode=take_profit_mode,
        take_profit_max=take_profit_max,
        multiplier=multiplier,
        take_profit_unit=take_profit_unit,
        take_profit_ticks=take_profit_ticks,
        take_profit_ticks_max=take_profit_ticks_max,
        tick_tp_server_backup=tick_tp_server_backup,
        split_delay_seconds=split_delay_seconds,
        strategy_mode=strategy_mode,
        ensemble_preset=ensemble_preset,
        ensemble_strategies=ensemble_strategies,
        ensemble_policy=ensemble_policy,
        ensemble_min_votes=ensemble_min_votes,
        ensemble_memory_ticks=ensemble_memory_ticks,
        ensemble_confirmations=ensemble_confirmations,
        ensemble_cooldown_ticks=ensemble_cooldown_ticks,
        ensemble_regime_lookback=ensemble_regime_lookback,
        ensemble_trend_threshold=ensemble_trend_threshold,
        ensemble_weight_threshold=ensemble_weight_threshold,
        ensemble_conflict_margin=ensemble_conflict_margin,
        ensemble_weights=ensemble_weights,
        ensemble_profile_overrides=ensemble_profile_overrides,
        server_stop_loss=server_stop_loss,
        smart_activation=smart_activation,
        smart_profit_floor=smart_profit_floor,
        smart_trail_percent=smart_trail_percent,
        smart_min_distance=smart_min_distance,
        smart_activation_ticks=smart_activation_ticks,
        smart_profit_floor_ticks=smart_profit_floor_ticks,
        smart_min_distance_ticks=smart_min_distance_ticks,
        smart_adverse_ticks=smart_adverse_ticks,
        smart_adverse_window=smart_adverse_window,
        smart_opposite_signal=smart_opposite_signal,
        smart_server_take_profit=smart_server_take_profit,
        smart_loss_enabled=smart_loss_enabled,
        smart_loss_soft_trigger=smart_loss_soft_trigger,
        smart_loss_reset_level=smart_loss_reset_level,
        smart_loss_hard_cap=smart_loss_hard_cap,
        smart_loss_grace_ticks=smart_loss_grace_ticks,
        smart_loss_max_watch_ticks=smart_loss_max_watch_ticks,
        smart_loss_adverse_ticks=smart_loss_adverse_ticks,
        smart_loss_adverse_window=smart_loss_adverse_window,
        smart_loss_opposite_signal=smart_loss_opposite_signal,
        smart_loss_rescue_profit=smart_loss_rescue_profit,
        trades_per_signal=trades_per_signal,
    )

    print("\nSession preview")
    print("-" * 72)
    strategy_label = STRATEGY_LABELS.get(settings.strategy_mode, settings.strategy_mode)
    print(f"Strategy:            {strategy_label}")
    if settings.strategy_mode == "ensemble":
        selected = settings.ensemble_strategies or (BEST_MIX_STRATEGY_IDS if settings.ensemble_preset == "best" else SINGLE_STRATEGY_IDS if settings.ensemble_preset == "all" else ())
        print(f"Ensemble preset:     {settings.ensemble_preset.upper()}")
        print(f"Decision policy:     {settings.ensemble_policy.upper()}")
        print(f"Selected engines:    {', '.join(selected)}")
    print(f"Capital:             {settings.capital} {currency}")
    print(f"Split:               {settings.split_count}")
    print(f"Stake per trade:     {calculated_stake} {currency}")
    print(f"Maximum open:        {settings.max_open_trades}")
    print(f"Trades per signal:   {settings.trades_per_signal if settings.trades_per_signal > 0 else settings.batch_mode}")
    print(f"Split delay:         {settings.split_delay_seconds:g} seconds")
    print(f"Continue after loss: {'YES' if settings.loss_policy == 'continue' else 'NO'}")
    print(f"Compounding:         {'YES' if settings.compound else 'NO'}")
    if settings.take_profit_mode in {"fixed", "adaptive"}:
        print(f"TP measurement:      {settings.take_profit_unit.upper()}")
        if settings.take_profit_unit == "ticks":
            ticks_text=str(settings.take_profit_ticks) if settings.take_profit_mode=="fixed" else f"{settings.take_profit_ticks}-{settings.take_profit_ticks_max}"
            print(f"TP tick target:       {ticks_text} net Step ticks")
            print(f"Tick server backup:   {settings.tick_tp_server_backup} {currency}")

    if settings.take_profit_mode == "adaptive":
        if settings.take_profit_unit == "ticks":
            print(f"Exit mode:           ADAPTIVE TP {settings.take_profit_ticks}-{settings.take_profit_ticks_max} net Step ticks")
            print(f"Server TP backup:    {settings.tick_tp_server_backup} {currency}")
        else:
            print(
                f"Exit mode:           ADAPTIVE TP {settings.take_profit}-"
                f"{settings.take_profit_max} {currency} per trade"
            )
        print("Adaptive behavior:   Recalculated for each split at order time")
    elif settings.take_profit_mode in {"smart", "hybrid"}:
        print(f"Exit mode:           {settings.take_profit_mode.upper()} SMART EXIT")
        print(f"TP measurement:      {settings.take_profit_unit.upper()}")
        if settings.take_profit_unit == "ticks":
            print(
                f"Profit protection:   activation={settings.smart_activation_ticks} ticks "
                f"floor={settings.smart_profit_floor_ticks} ticks trail={settings.smart_trail_percent}% "
                f"min_distance={settings.smart_min_distance_ticks} tick(s)"
            )
        else:
            print(
                f"Profit protection:   activation={settings.smart_activation} "
                f"floor={settings.smart_profit_floor} trail={settings.smart_trail_percent}%"
            )
        print(
            f"Reversal filter:     {settings.smart_adverse_ticks} adverse ticks in "
            f"{settings.smart_adverse_window}; opposite signal="
            f"{'ON' if settings.smart_opposite_signal else 'OFF'}"
        )
        print(f"Emergency server TP: {settings.smart_server_take_profit} {currency}")
    else:
        if settings.take_profit_unit == "ticks":
            print(f"Exit mode:           FIXED TP {settings.take_profit_ticks} net Step ticks")
            print(f"Server TP backup:    {settings.tick_tp_server_backup} {currency}")
        else:
            print(f"Exit mode:           FIXED TP {settings.take_profit} {currency} per trade")
    print(f"Multiplier:          x{settings.multiplier}")
    print(f"Server stop loss:    {settings.server_stop_loss if settings.server_stop_loss > 0 else 'OFF'}")
    if settings.smart_loss_enabled:
        print(
            f"Smart loss guard:    soft=-{settings.smart_loss_soft_trigger} reset=-{settings.smart_loss_reset_level} "
            f"hard=-{settings.smart_loss_hard_cap} grace={settings.smart_loss_grace_ticks} "
            f"max_watch={settings.smart_loss_max_watch_ticks}"
        )
        print(
            f"Recovery rules:      adverse={settings.smart_loss_adverse_ticks}/{settings.smart_loss_adverse_window} "
            f"opposite={'ON' if settings.smart_loss_opposite_signal else 'OFF'} "
            f"rescue=+{settings.smart_loss_rescue_profit if settings.smart_loss_rescue_profit > 0 else 'OFF'}"
        )
    else:
        print("Smart loss guard:    OFF")
    print("-" * 72)

    if not args.yes and not ask_yes_no("Start with these settings", default=False):
        raise RuntimeError("Session cancelled by user")
    return settings


def enforce_fixed_app_id_file(path: Path) -> None:
    values: Dict[str, str] = {}
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        for raw in lines:
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    values["DERIV_APP_ID"] = FIXED_DERIV_APP_ID
    ordered = [
        "# Dr Exploit Control local credentials. Never share this file.",
        f"DERIV_API_TOKEN={values.get('DERIV_API_TOKEN', '')}",
        f"DERIV_APP_ID={FIXED_DERIV_APP_ID}",
        f"DERIV_ACCOUNT_ID={values.get('DERIV_ACCOUNT_ID', '')}",
        f"DERIV_SYMBOL={values.get('DERIV_SYMBOL', 'stpRNG') or 'stpRNG'}",
        "DERIV_TRADE_TYPE=multiplier",
        "",
    ]
    path.write_text("\n".join(ordered), encoding="utf-8")
    os.environ["DERIV_APP_ID"] = FIXED_DERIV_APP_ID


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dr Exploit Control V2.22")
    parser.add_argument("--app-id", help="Deriv App ID; otherwise DERIV_APP_ID")
    parser.add_argument("--account-id", help="Deriv Options account ID; otherwise DERIV_ACCOUNT_ID")
    parser.add_argument("--symbol", help="Underlying symbol; otherwise DERIV_SYMBOL")
    parser.add_argument("--live", action="store_true", help="Allow real-money trading")
    parser.add_argument("--dry-run", action="store_true", help="Signals only; never buy")
    parser.add_argument("--yes", action="store_true", help="Accept session preview")
    parser.add_argument(
        "--allow-existing-positions",
        action="store_true",
        help="Launch even when the account already has open positions; existing contracts stay unmanaged",
    )
    parser.add_argument(
        "--strategy",
        choices=tuple(STRATEGY_FACTORIES) + ("ensemble",),
        help="Trading strategy; otherwise choose during setup",
    )

    parser.add_argument("--ensemble-preset", choices=("custom", "best", "all"), default=None)
    parser.add_argument("--ensemble-strategies", help="Comma-separated strategy IDs for a custom ensemble")
    parser.add_argument("--ensemble-policy", choices=("regime", "majority", "weighted", "any", "unanimous"), default=None)
    parser.add_argument("--ensemble-min-votes", type=int, default=None)
    parser.add_argument("--ensemble-memory-ticks", type=int, default=None)
    parser.add_argument("--ensemble-confirmations", type=int, default=None)
    parser.add_argument("--ensemble-cooldown-ticks", type=int, default=None)
    parser.add_argument("--ensemble-regime-lookback", type=int, default=None)
    parser.add_argument("--ensemble-trend-threshold", type=float, default=None)
    parser.add_argument("--ensemble-weight-threshold", type=float, default=None)
    parser.add_argument("--ensemble-conflict-margin", type=float, default=None)
    parser.add_argument("--ensemble-weights-json", default=None, help="JSON mapping strategy IDs to voting weights")
    parser.add_argument("--ensemble-config-json", default=None, help="JSON mapping strategy IDs to exact profile overrides")
    parser.add_argument("--server-stop-loss", default=None, help="Optional server-side stop loss amount; 0 disables")
    loss_guard_group = parser.add_mutually_exclusive_group()
    loss_guard_group.add_argument("--smart-loss", dest="smart_loss_enabled", action="store_true")
    loss_guard_group.add_argument("--no-smart-loss", dest="smart_loss_enabled", action="store_false")
    parser.set_defaults(smart_loss_enabled=None)
    parser.add_argument("--smart-loss-soft-trigger", default=None, help="Loss amount that starts recovery watch")
    parser.add_argument("--smart-loss-reset-level", default=None, help="Loss amount the trade must recover to before watch resets")
    parser.add_argument("--smart-loss-hard-cap", default=None, help="Immediate local hard loss cap")
    parser.add_argument("--smart-loss-grace-ticks", type=int, default=None)
    parser.add_argument("--smart-loss-max-watch-ticks", type=int, default=None, help="0 disables watch timeout")
    parser.add_argument("--smart-loss-adverse-ticks", type=int, default=None)
    parser.add_argument("--smart-loss-adverse-window", type=int, default=None)
    loss_opp_group = parser.add_mutually_exclusive_group()
    loss_opp_group.add_argument("--smart-loss-opposite-signal", dest="smart_loss_opposite_signal", action="store_true")
    loss_opp_group.add_argument("--no-smart-loss-opposite-signal", dest="smart_loss_opposite_signal", action="store_false")
    parser.set_defaults(smart_loss_opposite_signal=None)
    parser.add_argument("--smart-loss-rescue-profit", default=None, help="Positive P/L that closes a recovered watched trade; 0 disables")

    parser.add_argument("--capital", help="Session capital allocation")
    parser.add_argument("--split", type=int, choices=range(1, 51), metavar="1-50")
    parser.add_argument("--max-open", type=int, choices=range(1, 51), metavar="1-50")
    parser.add_argument("--batch-mode", choices=("one", "all"), help="Legacy one/all opening mode")
    parser.add_argument(
        "--trades-per-signal",
        type=int,
        choices=range(1, 51),
        metavar="1-50",
        help="Exact maximum number of positions to open for each fresh signal",
    )
    parser.add_argument(
        "--split-delay",
        type=float,
        default=None,
        help="Seconds between multiple entries opened from the same signal; 0 disables",
    )
    compound_group = parser.add_mutually_exclusive_group()
    compound_group.add_argument("--compound", dest="compound", action="store_true")
    compound_group.add_argument("--fixed", dest="compound", action="store_false")
    parser.set_defaults(compound=None)
    parser.add_argument(
        "--loss-policy",
        choices=("continue", "first", "consecutive", "total"),
    )
    parser.add_argument("--loss-count", type=int)
    parser.add_argument("--profit-target")
    parser.add_argument("--loss-limit")
    parser.add_argument("--win-target", type=int)
    parser.add_argument("--take-profit-mode", choices=("fixed", "adaptive", "smart", "hybrid"))
    parser.add_argument("--take-profit-unit", choices=("amount", "ticks"), default=None)
    parser.add_argument("--take-profit-ticks", type=int)
    parser.add_argument("--take-profit-ticks-max", type=int)
    parser.add_argument("--tick-tp-server-backup")
    parser.add_argument(
        "--take-profit",
        help="Fixed target, or minimum target in adaptive mode (minimum enforced: 0.10)",
    )
    parser.add_argument("--take-profit-max", help="Maximum target in adaptive mode")
    parser.add_argument("--smart-activation", help="Profit that activates smart protection")
    parser.add_argument("--smart-profit-floor", help="Minimum protected profit after activation")
    parser.add_argument("--smart-trail-percent", help="Percentage of peak profit allowed as giveback")
    parser.add_argument("--smart-min-distance", help="Minimum currency giveback from peak")
    parser.add_argument("--smart-activation-ticks", type=int, help="Net favorable Step ticks that activate Smart/Hybrid tick protection")
    parser.add_argument("--smart-profit-floor-ticks", type=int, help="Minimum protected favorable Step ticks after activation")
    parser.add_argument("--smart-min-distance-ticks", type=int, help="Minimum tick giveback distance from the peak")
    parser.add_argument("--smart-adverse-ticks", type=int, help="Adverse ticks needed for hybrid exit")
    parser.add_argument("--smart-adverse-window", type=int, help="Recent tick window for hybrid exit")
    opposite_group = parser.add_mutually_exclusive_group()
    opposite_group.add_argument(
        "--smart-opposite-signal", dest="smart_opposite_signal", action="store_true"
    )
    opposite_group.add_argument(
        "--no-smart-opposite-signal", dest="smart_opposite_signal", action="store_false"
    )
    parser.set_defaults(smart_opposite_signal=None)
    parser.add_argument("--smart-server-take-profit", help="Emergency server-side TP for smart modes")
    parser.add_argument(
        "--multiplier",
        type=int,
        choices=STEP100_ALLOWED_MULTIPLIERS,
        help="Step 100 multiplier: 750, 2000, 3500, 5500, or 7500",
    )
    return parser


async def get_initial_balance(client: DerivWebSocketClient) -> tuple[Decimal, str]:
    response = await client.request({"balance": 1})
    balance = response.get("balance") or {}
    try:
        amount = Decimal(str(balance.get("balance", "0")))
    except InvalidOperation:
        amount = Decimal("0")
    currency = str(balance.get("currency") or "USD")
    return amount, currency


async def connect_authenticated_client(
    token: str,
    app_id: str,
    account_id: str,
    attempts: int = 3,
) -> DerivWebSocketClient:
    """Create a fresh OTP WebSocket connection, retrying brief startup failures."""
    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        client: Optional[DerivWebSocketClient] = None
        try:
            ws_url = await request_authenticated_websocket_url(token, app_id, account_id)
            client = DerivWebSocketClient(ws_url)
            await client.connect()
            return client
        except Exception as exc:
            last_error = exc
            if client is not None:
                await client.close()
            if attempt < attempts:
                if isinstance(exc, OTPRateLimitError):
                    # Respect the server delay, and if the endpoint continues to
                    # rate-limit us, progressively increase 30 -> 60 -> 120...
                    exponential_floor = min(300.0, 30.0 * (2.0 ** max(0, attempt - 1)))
                    delay = max(exc.retry_after, exponential_floor) + min(5.0, float(attempt))
                else:
                    # Exponential startup/reconnect backoff for transient failures.
                    delay = min(30.0, 2.0 ** max(0, attempt - 1))
                LOGGER.warning(
                    "Authenticated WebSocket connection attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt, attempts, exc, delay,
                )
                await asyncio.sleep(delay)

    raise RuntimeError(
        f"Could not establish an authenticated Deriv WebSocket after {attempts} attempts: {last_error}"
    ) from last_error


async def async_main(args: argparse.Namespace) -> int:
    enforce_fixed_app_id_file(ROOT / ".env")
    load_env_file(ROOT / ".env")
    os.environ["DERIV_APP_ID"] = FIXED_DERIV_APP_ID
    config = BotConfig.load(CONFIG_PATH)

    token = os.getenv("DERIV_API_TOKEN", "").strip()
    if not token:
        token = getpass.getpass("Deriv API token (hidden; not saved): ").strip()
    if not token:
        raise RuntimeError("A Deriv API token is required")

    app_id = FIXED_DERIV_APP_ID
    if not app_id:
        raise RuntimeError("DERIV_APP_ID is required")

    account_id = (args.account_id or os.getenv("DERIV_ACCOUNT_ID") or "").strip()
    if not account_id:
        raise RuntimeError("DERIV_ACCOUNT_ID is required")

    # Use a short-lived connection only to read the starting balance. The
    # interactive setup uses blocking console input and may take several
    # minutes. Keeping the OTP socket open during those prompts can let the
    # connection time out because the asyncio loop cannot service it.
    preview_client = await connect_authenticated_client(token, app_id, account_id)
    try:
        account_balance, currency = await get_initial_balance(preview_client)
    finally:
        await preview_client.close()

    settings = collect_session_settings(args, config, account_balance, currency)

    # Obtain a NEW one-time OTP and a fresh WebSocket only after the user has
    # confirmed the session. This prevents a stale/disconnected socket at the
    # first portfolio request.
    client = await connect_authenticated_client(token, app_id, account_id)
    try:
        bot = TradingBot(
            config=config,
            settings=settings,
            client=client,
            account_id=account_id,
            account_balance=account_balance,
            currency=currency,
            live_flag=bool(args.live),
            dry_run=bool(args.dry_run),
            symbol_override=(args.symbol or os.getenv("DERIV_SYMBOL") or "").strip(),
            token=token,
            app_id=app_id,
            allow_existing_positions=bool(args.allow_existing_positions),
            instance_id=INSTANCE_ID,
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, bot._request_stop, "Stopped by user")
            except (NotImplementedError, RuntimeError):
                pass

        await bot.run()
        return 0
    except Exception:
        if not client.closed.is_set():
            await client.close()
        raise


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = build_argument_parser().parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        LOGGER.info("Stopped by user")
        return 130
    except DerivAPIError as exc:
        LOGGER.error("Deriv API rejected the request: %s", exc)
        if exc.code in {"InvalidContractProposal", "ContractBuyValidationError"}:
            LOGGER.error(
                "Check the market symbol, supported multiplier (750/2000/3500/5500/7500), stake, selected take-profit range, and account availability."
            )
        return 1
    except Exception as exc:
        LOGGER.error("Bot stopped: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
