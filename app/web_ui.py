#!/usr/bin/env python3
"""Local web dashboard for Dr Exploit Control V2.27.

This server uses only the Python standard library. It binds to 127.0.0.1 by
default. Credentials remain in the local .env file and are never returned to
the browser.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Optional

from ai_optimizer import (
    call_ollama_optimizer, fallback_refine_multiplier, fallback_refine_rf, initial_multiplier_candidates,
    initial_rf_candidates, list_ollama_models, load_optimizer_settings, ollama_provider_status,
    sanitize_multiplier_candidate, sanitize_optimizer_settings, sanitize_rf_candidate, save_optimizer_settings,
)

ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "bot.py"
RISE_FALL_BOT_PATH = ROOT / "rise_fall_bot.py"
RISE_FALL_SESSION_PATH = ROOT / "rise_fall_session.json"
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"
FIXED_DERIV_APP_ID = "346MK1Nd8vpaSJy7EzQQT"
_APP_ID_WATCH_INTERVAL = 1.0
TRADES_PATH = ROOT / "trades_v2.csv"
RISE_FALL_TRADES_PATH = ROOT / "rise_fall_trades.csv"
RISE_FALL_PRESETS_PATH = ROOT / "rise_fall_presets.json"
SESSION_PATH = ROOT / "web_session.json"
SECRET_PATH = ROOT / ".webui_secret"
TEMPLATE_PATH = ROOT / "templates" / "index.html"
STRATEGY_PROFILES_PATH = ROOT / "strategy_profiles.json"
USER_PRESETS_PATH = ROOT / "user_presets.json"
STATIC_DIR = ROOT / "static"
LAB_DIR = ROOT / "strategy_lab_runs"
LAB_DIR.mkdir(exist_ok=True)
RF_LAB_DIR = ROOT / "rise_fall_lab_runs"
RF_LAB_DIR.mkdir(exist_ok=True)
CONTROL_DIR = ROOT / ".runtime_control"
CONTROL_DIR.mkdir(exist_ok=True)
DIAGNOSTIC_DIR = ROOT / "session_diagnostics"
DIAGNOSTIC_DIR.mkdir(exist_ok=True)
SUPPORT_REPORT_DIR = ROOT / "support_reports"
SUPPORT_REPORT_DIR.mkdir(exist_ok=True)
AI_OPTIMIZER_SETTINGS_PATH = ROOT / "ai_optimizer.json"
AI_OPTIMIZER_HISTORY_PATH = ROOT / "ai_optimizer_history.json"
AI_OPTIMIZER_RUN_DIR = ROOT / "ai_optimizer_runs"
AI_OPTIMIZER_RUN_DIR.mkdir(exist_ok=True)
WEB_UI_VERSION = "2.27"
LOG_LIMIT = 4000

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

CANDLE_PATTERN_IDS = {
    "hammer","bullish_hammer","bearish_hammer","inverted_hammer","bullish_inverted_hammer","bearish_inverted_hammer",
    "hanging_man","bullish_hanging_man","bearish_hanging_man","shooting_star","bullish_shooting_star","bearish_shooting_star",
    "bullish_upper_wick_rejection","bearish_upper_wick_rejection","bullish_lower_wick_rejection","bearish_lower_wick_rejection",
    "flat_bottom_bullish_rejection","flat_bottom_bearish_rejection","bullish_belt_hold","bearish_belt_hold",
    "standard_doji","dragonfly_doji","gravestone_doji","long_legged_doji","rickshaw_man","four_price_doji","spinning_top","high_wave",
    "bullish_engulfing","bearish_engulfing","bullish_harami","bearish_harami","piercing_line","dark_cloud_cover","tweezer_bottom","tweezer_top",
    "morning_star","evening_star","morning_doji_star","evening_doji_star",
    "bullish_marubozu","bearish_marubozu","three_white_soldiers","three_black_crows",
}


def _decimal(value: Any, name: str, *, minimum: Decimal = Decimal("0"), allow_zero: bool = True) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    if allow_zero:
        if parsed < minimum:
            raise ValueError(f"{name} must be at least {minimum}")
    elif parsed <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}")
    return parsed


def _integer(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a whole number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_env_raw(path: Path = ENV_PATH) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_env_raw(values: Dict[str, str], path: Path = ENV_PATH) -> None:
    lines = [
        "# Dr Exploit Control local credentials. Never share this file.",
        f"DERIV_API_TOKEN={values.get('DERIV_API_TOKEN', '')}",
        f"DERIV_APP_ID={FIXED_DERIV_APP_ID}",
        f"DERIV_ACCOUNT_ID={values.get('DERIV_ACCOUNT_ID', '')}",
        f"DERIV_SYMBOL={values.get('DERIV_SYMBOL', 'stpRNG') or 'stpRNG'}",
        "DERIV_TRADE_TYPE=multiplier",
        "",
        "# Real-money confirmation is set temporarily by the web UI only after",
        "# you type LIVE. Do not permanently enable it here.",
        "# DERIV_LIVE_CONFIRM=YES_I_ACCEPT_REAL_MONEY_RISK",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def enforce_app_id(path: Path = ENV_PATH) -> Dict[str, str]:
    values = _read_env_raw(path)
    if values.get("DERIV_APP_ID") != FIXED_DERIV_APP_ID:
        values["DERIV_APP_ID"] = FIXED_DERIV_APP_ID
        _write_env_raw(values, path)
    return values


def read_env(path: Path = ENV_PATH) -> Dict[str, str]:
    if path == ENV_PATH:
        return enforce_app_id(path)
    return _read_env_raw(path)


def write_env(values: Dict[str, str], path: Path = ENV_PATH) -> None:
    existing = _read_env_raw(path)
    existing.update({k: v for k, v in values.items() if v is not None and k != "DERIV_APP_ID"})
    existing["DERIV_APP_ID"] = FIXED_DERIV_APP_ID
    _write_env_raw(existing, path)


def _app_id_watch_loop() -> None:
    while True:
        try:
            enforce_app_id()
        except Exception:
            pass
        time.sleep(_APP_ID_WATCH_INTERVAL)


def ensure_secret() -> str:
    if SECRET_PATH.exists():
        value = SECRET_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    SECRET_PATH.write_text(value, encoding="utf-8")
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    return value


WEB_UI_SECRET = ensure_secret()

STEP100_ALLOWED_MULTIPLIERS = (750, 2000, 3500, 5500, 7500)



def load_strategy_profile_data() -> Dict[str, Any]:
    try:
        data = json.loads(STRATEGY_PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load strategy_profiles.json: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("catalog"), list) or not isinstance(data.get("profiles"), dict):
        raise RuntimeError("strategy_profiles.json has an invalid structure")
    return data


STRATEGY_PROFILE_DATA = load_strategy_profile_data()
STRATEGY_CATALOG: list[Dict[str, Any]] = [dict(item) for item in STRATEGY_PROFILE_DATA["catalog"]]
STRATEGY_PROFILES: Dict[str, Dict[str, Dict[str, Any]]] = {
    str(name): {str(profile): dict(values) for profile, values in profile_map.items()}
    for name, profile_map in STRATEGY_PROFILE_DATA["profiles"].items()
}
STRATEGY_IDS = tuple(item["id"] for item in STRATEGY_CATALOG)
STRATEGY_PROFILE_NAMES = ("custom", "balanced", "fast", "strict")

DEFAULT_RISE_FALL_SESSION: Dict[str, Any] = {
    "mode": "signals",
    "capital": "50.00",
    "split": 5,
    "max_open": 2,
    "trades_per_signal": 1,
    "split_delay": 0.5,
    "compound": False,
    "duration": 5,
    "duration_unit": "t",
    "decision_mode": "best_odds",
    "direction_policy": "auto",
    "min_confidence": 0.62,
    "min_edge": 0.12,
    "min_return_pct": 0.0,
    "cooldown_ticks": 5,
    "quote_refresh_ticks": 4,
    "lookback": 24,
    "trend_weight": 1.20,
    "momentum_weight": 1.10,
    "reversal_weight": 0.55,
    "breakout_weight": 0.80,
    "loss_policy": "consecutive",
    "loss_count": 3,
    "profit_target": "0.00",
    "loss_limit": "0.00",
    "win_target": 0,
    "live_confirmation": "",
}


def load_rise_fall_session() -> Dict[str, Any]:
    base = dict(DEFAULT_RISE_FALL_SESSION)
    try:
        raw = json.loads(RISE_FALL_SESSION_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict): base.update(raw)
    except (OSError, json.JSONDecodeError):
        pass
    base["live_confirmation"] = ""
    return base


def save_rise_fall_session(settings: Dict[str, Any]) -> None:
    clean = dict(settings); clean["live_confirmation"] = ""
    RISE_FALL_SESSION_PATH.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")


def _rise_fall_preset_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        raise ValueError("Rise/Fall preset name is required")
    if len(name) > 60:
        raise ValueError("Rise/Fall preset name must be 60 characters or fewer")
    if any(ch in name for ch in "\r\n\t"):
        raise ValueError("Rise/Fall preset name contains invalid characters")
    return name


def _validated_rise_fall_preset_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidate = dict(payload or {})
    if str(candidate.get("mode", "")).strip().lower() == "live":
        candidate["live_confirmation"] = "LIVE"
    settings = validate_rise_fall_session(candidate)
    settings["live_confirmation"] = ""
    return settings


def load_rise_fall_presets() -> Dict[str, Dict[str, Any]]:
    if not RISE_FALL_PRESETS_PATH.exists():
        return {}
    try:
        raw = json.loads(RISE_FALL_PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("presets"), dict):
        raw = raw["presets"]
    if not isinstance(raw, dict):
        return {}
    return {str(name): dict(item) for name, item in raw.items() if isinstance(item, dict)}


def write_rise_fall_presets(presets: Dict[str, Dict[str, Any]]) -> None:
    payload = {"version": 1, "presets": presets}
    tmp = RISE_FALL_PRESETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(RISE_FALL_PRESETS_PATH)


def rise_fall_preset_summaries() -> list[Dict[str, Any]]:
    rows = []
    for name, item in load_rise_fall_presets().items():
        settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
        rows.append({
            "name": name,
            "saved_at": str(item.get("saved_at", "")),
            "duration": settings.get("duration", ""),
            "duration_unit": str(settings.get("duration_unit", "")),
            "decision_mode": str(settings.get("decision_mode", "")),
            "capital": str(settings.get("capital", "")),
            "split": settings.get("split", ""),
            "compound": bool(settings.get("compound", False)),
        })
    rows.sort(key=lambda row: str(row.get("name", "")).lower())
    return rows


def save_rise_fall_preset(name: str, settings_payload: Dict[str, Any]) -> Dict[str, Any]:
    name = _rise_fall_preset_name(name)
    settings = _validated_rise_fall_preset_session(settings_payload)
    presets = load_rise_fall_presets()
    presets[name] = {"saved_at": datetime.now(timezone.utc).isoformat(), "settings": settings}
    write_rise_fall_presets(presets)
    return presets[name]


def apply_rise_fall_preset(name: str) -> Dict[str, Any]:
    name = _rise_fall_preset_name(name)
    item = load_rise_fall_presets().get(name)
    if not isinstance(item, dict):
        raise ValueError("Saved Rise/Fall preset was not found")
    settings = _validated_rise_fall_preset_session(item.get("settings") if isinstance(item.get("settings"), dict) else {})
    save_rise_fall_session(settings)
    return {"name": name, "session": settings}


def delete_rise_fall_preset(name: str) -> None:
    name = _rise_fall_preset_name(name)
    presets = load_rise_fall_presets()
    if name not in presets:
        raise ValueError("Saved Rise/Fall preset was not found")
    presets.pop(name, None)
    write_rise_fall_presets(presets)


def export_rise_fall_preset(name: str) -> Dict[str, Any]:
    name = _rise_fall_preset_name(name)
    item = load_rise_fall_presets().get(name)
    if not isinstance(item, dict):
        raise ValueError("Saved Rise/Fall preset was not found")
    settings = _validated_rise_fall_preset_session(item.get("settings") if isinstance(item.get("settings"), dict) else {})
    return {
        "format": "control-nexus-rise-fall-preset",
        "version": 1,
        "app_version": WEB_UI_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "preset": {"saved_at": str(item.get("saved_at", "")), "settings": settings},
        "privacy": "Credentials, API tokens, account IDs and contract IDs are not included.",
    }


def import_rise_fall_preset(payload: Dict[str, Any], *, overwrite: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Rise/Fall preset file must contain a JSON object")
    if payload.get("format") == "control-nexus-rise-fall-preset":
        if int(payload.get("version", 0) or 0) != 1:
            raise ValueError("Unsupported Rise/Fall preset file version")
        name = _rise_fall_preset_name(payload.get("name", ""))
        item = payload.get("preset") if isinstance(payload.get("preset"), dict) else {}
    else:
        name = _rise_fall_preset_name(payload.get("name", ""))
        item = payload
    raw_settings = item.get("settings") if isinstance(item.get("settings"), dict) else item.get("session") if isinstance(item.get("session"), dict) else {}
    settings = _validated_rise_fall_preset_session(raw_settings)
    presets = load_rise_fall_presets()
    if name in presets and not overwrite:
        raise ValueError(f'Rise/Fall preset "{name}" already exists')
    stored = {"saved_at": datetime.now(timezone.utc).isoformat(), "settings": settings}
    presets[name] = stored
    write_rise_fall_presets(presets)
    save_rise_fall_session(settings)
    return {"name": name, "session": settings, "preset": stored}


def _rf_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rf_breakdown(rows: list[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for row in rows:
        key = str(key_fn(row) or "unknown")
        groups.setdefault(key, []).append(row)
    result: Dict[str, Dict[str, Any]] = {}
    for key, items in sorted(groups.items()):
        profits = [_rf_float(x.get("profit"), 0.0) for x in items]
        wins = sum(1 for x in items if _rf_float(x.get("profit"), 0.0) > 0)
        losses = len(items) - wins
        gross_win = sum(x for x in profits if x > 0)
        gross_loss = abs(sum(x for x in profits if x < 0))
        result[key] = {
            "trades": len(items), "wins": wins, "losses": losses,
            "win_rate": round(wins / len(items) * 100.0, 2) if items else 0.0,
            "profit": round(sum(profits), 4),
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else (None if gross_win <= 0 else "inf"),
            "avg_profit": round(sum(profits) / len(items), 4) if items else 0.0,
        }
    return result


def _rf_confidence_band(row: Dict[str, Any]) -> str:
    c = _rf_float(row.get("signal_confidence"), -1.0)
    if c < 0: return "legacy / unavailable"
    pct = c * 100.0 if c <= 1.0 else c
    if pct < 60: return "<60%"
    if pct < 65: return "60-65%"
    if pct < 70: return "65-70%"
    if pct < 75: return "70-75%"
    return "75%+"


def _rf_edge_band(row: Dict[str, Any]) -> str:
    edge = _rf_float(row.get("signal_edge"), -1.0)
    if edge < 0: return "legacy / unavailable"
    pct = edge * 100.0 if edge <= 1.0 else edge
    if pct < 10: return "<10%"
    if pct < 20: return "10-20%"
    if pct < 30: return "20-30%"
    if pct < 40: return "30-40%"
    return "40%+"


def build_rise_fall_optimization_report(report_format: str = "md") -> Dict[str, Any]:
    report_format = str(report_format or "md").strip().lower()
    if report_format not in {"md", "json"}:
        raise ValueError("Rise/Fall optimization format must be md or json")
    rows: list[Dict[str, Any]] = []
    if RISE_FALL_TRADES_PATH.exists():
        try:
            with RISE_FALL_TRADES_PATH.open("r", newline="", encoding="utf-8") as handle:
                rows = [dict(row) for row in csv.DictReader(handle)]
        except OSError:
            rows = []
    profits = [_rf_float(row.get("profit"), 0.0) for row in rows]
    stakes = [_rf_float(row.get("stake"), 0.0) for row in rows]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = max_drawdown = 0.0
    win_streak = loss_streak = max_win_streak = max_loss_streak = 0
    for p in profits:
        equity += p; peak = max(peak, equity); max_drawdown = max(max_drawdown, peak - equity)
        if p > 0:
            win_streak += 1; loss_streak = 0; max_win_streak = max(max_win_streak, win_streak)
        else:
            loss_streak += 1; win_streak = 0; max_loss_streak = max(max_loss_streak, loss_streak)
    total = len(rows)
    telemetry = RISE_FALL_MANAGER.status().get("telemetry") or {}
    current_metrics = telemetry.get("metrics") if isinstance(telemetry.get("metrics"), dict) else {}
    current_decision = telemetry.get("decision") if isinstance(telemetry.get("decision"), dict) else {}
    current_odds = telemetry.get("odds") if isinstance(telemetry.get("odds"), dict) else {}
    allowed_recent = (
        "closed_utc","opened_utc","direction","result","stake","profit","duration","duration_unit","mode","decision_mode","direction_policy",
        "signal_confidence","signal_edge","proposal_return_pct","break_even_pct","combined_score","rsi","momentum_up","momentum_down","range_position",
        "volatility_step","lookback","min_confidence","min_edge","min_return_pct","trend_weight","momentum_weight","reversal_weight","breakout_weight",
    )
    recent = [{key: row.get(key, "") for key in allowed_recent if key in row} for row in rows[-250:]]
    rich_rows = sum(1 for row in rows if str(row.get("signal_confidence", "")).strip() != "")
    current_config = load_rise_fall_session()
    current_config["live_confirmation"] = ""
    report = {
        "report_type": "rise_fall_step100_optimization",
        "report_version": 1,
        "app_version": WEB_UI_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "Step 100 Index",
        "contract_family": "Rise/Fall CALL/PUT",
        "current_configuration": current_config,
        "performance": {
            "trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / total * 100.0, 3) if total else 0.0,
            "net_profit": round(sum(profits), 4),
            "gross_profit": round(gross_win, 4),
            "gross_loss": round(gross_loss, 4),
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else (None if gross_win <= 0 else "inf"),
            "avg_profit_per_trade": round(sum(profits) / total, 4) if total else 0.0,
            "avg_win": round(gross_win / len(wins), 4) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 4) if losses else 0.0,
            "total_staked": round(sum(stakes), 4),
            "return_on_total_stake_pct": round(sum(profits) / sum(stakes) * 100.0, 4) if sum(stakes) > 0 else 0.0,
            "max_drawdown": round(max_drawdown, 4),
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
        },
        "breakdowns": {
            "direction": _rf_breakdown(rows, lambda r: r.get("direction") or "unknown"),
            "duration": _rf_breakdown(rows, lambda r: f"{r.get('duration','?')}{r.get('duration_unit','') or ''}"),
            "decision_mode": _rf_breakdown(rows, lambda r: r.get("decision_mode") or r.get("mode") or "unknown"),
            "confidence_band": _rf_breakdown(rows, _rf_confidence_band),
            "edge_band": _rf_breakdown(rows, _rf_edge_band),
        },
        "current_runtime_snapshot": {
            "running": bool(RISE_FALL_MANAGER.running()),
            "metrics": current_metrics,
            "decision": current_decision,
            "odds": {k: v for k, v in current_odds.items() if k in {"RISE", "FALL", "RISE_ERROR", "FALL_ERROR"}},
        },
        "data_quality": {
            "history_rows": total,
            "rich_signal_rows": rich_rows,
            "legacy_rows_without_signal_snapshot": max(0, total - rich_rows),
            "note": "V2.22+ trades include entry-time model/proposal/config fields. Older V2.21 rows remain useful for direction/duration/P&L analysis but cannot support confidence/edge optimization.",
        },
        "recent_trades": recent,
        "privacy": "Sanitized optimization file: API token, App ID, account ID and Deriv contract IDs are intentionally omitted.",
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if report_format == "json":
        return {"format": "json", "filename": f"RISE_FALL_STEP100_OPTIMIZATION_{stamp}.json", "content": json.dumps(report, indent=2, ensure_ascii=False) + "\n"}
    perf = report["performance"]
    lines = [
        f"# Rise/Fall Step 100 Optimization Report — {report['generated_at']}", "",
        "> Upload this file when you want configuration optimization. Credentials, account IDs and contract IDs are intentionally omitted.", "",
        "## Performance summary",
        f"- Trades: **{perf['trades']}**", f"- Wins / losses: **{perf['wins']} / {perf['losses']}**", f"- Win rate: **{perf['win_rate']}%**",
        f"- Net P/L: **{perf['net_profit']}**", f"- Profit factor: **{perf['profit_factor']}**", f"- Average win / loss: **{perf['avg_win']} / -{perf['avg_loss']}**",
        f"- Max drawdown: **{perf['max_drawdown']}**", f"- Longest win / loss streak: **{perf['max_win_streak']} / {perf['max_loss_streak']}**", "",
        "## Current Rise/Fall configuration", "```json", json.dumps(report["current_configuration"], indent=2), "```", "",
        "## Performance breakdowns", "```json", json.dumps(report["breakdowns"], indent=2), "```", "",
        "## Current runtime model snapshot", "```json", json.dumps(report["current_runtime_snapshot"], indent=2), "```", "",
        "## Data quality", "```json", json.dumps(report["data_quality"], indent=2), "```", "",
        "## Recent sanitized trades", "```json", json.dumps(report["recent_trades"], indent=2), "```", "",
        report["privacy"], "",
    ]
    return {"format": "md", "filename": f"RISE_FALL_STEP100_OPTIMIZATION_{stamp}.md", "content": "\n".join(lines)}


def validate_rise_fall_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(payload.get("mode", "signals")).strip().lower()
    if mode not in {"signals", "demo", "live"}: raise ValueError("Rise/Fall mode must be signals, demo, or live")
    capital = _decimal(payload.get("capital", "50"), "Rise/Fall capital", minimum=Decimal("0"), allow_zero=False)
    split = _integer(payload.get("split", 5), "Rise/Fall split", minimum=1, maximum=50)
    max_open = _integer(payload.get("max_open", 2), "Rise/Fall maximum open", minimum=1, maximum=50)
    if max_open > split: raise ValueError("Rise/Fall maximum open cannot exceed split")
    trades = _integer(payload.get("trades_per_signal", 1), "Rise/Fall trades per signal", minimum=1, maximum=50)
    if trades > max_open: raise ValueError("Rise/Fall trades per signal cannot exceed maximum open")
    stake = (capital / Decimal(split)).quantize(Decimal("0.01"))
    if stake < Decimal("0.35"): raise ValueError("Rise/Fall stake per split is below 0.35")
    duration = _integer(payload.get("duration", 5), "Rise/Fall duration", minimum=1, maximum=10000)
    duration_unit = str(payload.get("duration_unit", "t")).strip().lower()
    if duration_unit not in {"t","s","m"}: raise ValueError("Rise/Fall duration unit must be ticks, seconds, or minutes")
    decision_mode = str(payload.get("decision_mode", "best_odds")).strip().lower()
    if decision_mode not in {"best_odds","consensus","trend","momentum","reversal"}: raise ValueError("Unknown Rise/Fall decision mode")
    direction_policy = str(payload.get("direction_policy", "auto")).strip().lower()
    if direction_policy not in {"auto","rise_only","fall_only"}: raise ValueError("Unknown Rise/Fall direction policy")
    def fl(name, default, lo, hi):
        try: v=float(payload.get(name,default))
        except (TypeError,ValueError) as exc: raise ValueError(f"{name} must be numeric") from exc
        if not lo <= v <= hi: raise ValueError(f"{name} must be between {lo} and {hi}")
        return v
    min_conf=fl("min_confidence",.62,.50,.99); min_edge=fl("min_edge",.12,0,.95); min_return=fl("min_return_pct",0,0,1000)
    split_delay=fl("split_delay",.5,0,3600)
    lookback=_integer(payload.get("lookback",24),"Rise/Fall lookback",minimum=6,maximum=500)
    cooldown=_integer(payload.get("cooldown_ticks",5),"Rise/Fall cooldown",minimum=0,maximum=10000)
    refresh=_integer(payload.get("quote_refresh_ticks",4),"Rise/Fall quote refresh",minimum=1,maximum=1000)
    weights={k:fl(k,d,0,20) for k,d in (("trend_weight",1.2),("momentum_weight",1.1),("reversal_weight",.55),("breakout_weight",.8))}
    if sum(weights.values()) <= 0: raise ValueError("At least one Rise/Fall strategy weight must be above zero")
    loss_policy=str(payload.get("loss_policy","consecutive")).strip().lower()
    if loss_policy not in {"continue","first","consecutive","total"}: raise ValueError("Unknown Rise/Fall loss policy")
    loss_count=_integer(payload.get("loss_count",3),"Rise/Fall loss count",minimum=1,maximum=100000)
    profit_target=_decimal(payload.get("profit_target",0),"Rise/Fall profit target",minimum=Decimal("0"))
    loss_limit=_decimal(payload.get("loss_limit",0),"Rise/Fall loss limit",minimum=Decimal("0"))
    win_target=_integer(payload.get("win_target",0),"Rise/Fall win target",minimum=0,maximum=100000)
    live_confirmation=str(payload.get("live_confirmation","")).strip().upper()
    if mode=="live" and live_confirmation != "LIVE": raise ValueError("Type LIVE in the Rise/Fall confirmation field for real-money trading")
    return {
        "mode":mode,"capital":format(capital,"f"),"split":split,"max_open":max_open,"trades_per_signal":trades,
        "split_delay":split_delay,"compound":_boolean(payload.get("compound",False)),"duration":duration,"duration_unit":duration_unit,
        "decision_mode":decision_mode,"direction_policy":direction_policy,"min_confidence":min_conf,"min_edge":min_edge,"min_return_pct":min_return,
        "cooldown_ticks":cooldown,"quote_refresh_ticks":refresh,"lookback":lookback,**weights,"loss_policy":loss_policy,"loss_count":loss_count,
        "profit_target":format(profit_target,"f"),"loss_limit":format(loss_limit,"f"),"win_target":win_target,"live_confirmation":live_confirmation,
        "stake_per_trade":format(stake,"f"),
    }


def build_rise_fall_command(settings: Dict[str, Any], python_executable: str = sys.executable) -> list[str]:
    c=[python_executable,"-u",str(RISE_FALL_BOT_PATH),"--capital",settings["capital"],"--split",str(settings["split"]),"--max-open",str(settings["max_open"]),
       "--trades-per-signal",str(settings["trades_per_signal"]),"--split-delay",str(settings["split_delay"]),"--duration",str(settings["duration"]),"--duration-unit",settings["duration_unit"],
       "--decision-mode",settings["decision_mode"],"--direction-policy",settings["direction_policy"],"--min-confidence",str(settings["min_confidence"]),"--min-edge",str(settings["min_edge"]),
       "--min-return-pct",str(settings["min_return_pct"]),"--cooldown-ticks",str(settings["cooldown_ticks"]),"--quote-refresh-ticks",str(settings["quote_refresh_ticks"]),"--lookback",str(settings["lookback"]),
       "--trend-weight",str(settings["trend_weight"]),"--momentum-weight",str(settings["momentum_weight"]),"--reversal-weight",str(settings["reversal_weight"]),"--breakout-weight",str(settings["breakout_weight"]),
       "--loss-policy",settings["loss_policy"],"--loss-count",str(settings["loss_count"]),"--profit-target",settings["profit_target"],"--loss-limit",settings["loss_limit"],"--win-target",str(settings["win_target"])]
    c.append("--compound" if settings.get("compound") else "--fixed")
    if settings["mode"]=="signals": c.append("--dry-run")
    elif settings["mode"]=="live": c.append("--live")
    return c

DEFAULT_SESSION: Dict[str, Any] = {
    "mode": "signals",
    "strategy_engine": "single",
    "strategy": "ema_rsi",
    "strategy_profile": "balanced",
    "ensemble_preset": "best",
    "ensemble_policy": "regime",
    "ensemble_min_votes": 2,
    "ensemble_memory_ticks": 8,
    "ensemble_confirmations": 1,
    "ensemble_cooldown_ticks": 3,
    "ensemble_regime_lookback": 24,
    "ensemble_trend_threshold": 0.45,
    "ensemble_weight_threshold": 1.25,
    "ensemble_conflict_margin": 0.20,
    "ensemble_strategies": "bollinger_reversion,stochastic,hammer,ema_rsi,donchian,tick_momentum",
    "ensemble_profiles_json": "{}",
    "ensemble_weights_json": "{}",
    "capital": "100.00",
    "split": 4,
    "max_open": 2,
    "batch_mode": "all",
    "trades_per_signal": 1,
    "split_delay": 1.0,
    "compound": False,
    "loss_policy": "consecutive",
    "loss_count": 2,
    "profit_target": "2.00",
    "loss_limit": "4.00",
    "win_target": 0,
    "take_profit_mode": "hybrid",
    "take_profit_unit": "amount",
    "take_profit": "0.10",
    "take_profit_max": "0.30",
    "take_profit_ticks": 3,
    "take_profit_ticks_max": 8,
    "tick_tp_server_backup": "2.00",
    "smart_activation": "0.15",
    "smart_profit_floor": "0.10",
    "smart_trail_percent": "30",
    "smart_min_distance": "0.05",
    "smart_activation_ticks": 3,
    "smart_profit_floor_ticks": 1,
    "smart_min_distance_ticks": 1,
    "smart_adverse_ticks": 3,
    "smart_adverse_window": 4,
    "smart_opposite_signal": True,
    "smart_server_take_profit": "2.00",
    "server_stop_loss": "0.00",
    "smart_loss_enabled": False,
    "smart_loss_soft_trigger": "0.60",
    "smart_loss_reset_level": "0.20",
    "smart_loss_hard_cap": "2.50",
    "smart_loss_grace_ticks": 8,
    "smart_loss_max_watch_ticks": 30,
    "smart_loss_adverse_ticks": 4,
    "smart_loss_adverse_window": 6,
    "smart_loss_opposite_signal": True,
    "smart_loss_rescue_profit": "0.02",
    "multiplier": 750,
}


def load_last_session() -> Dict[str, Any]:
    if not SESSION_PATH.exists():
        return dict(DEFAULT_SESSION)
    try:
        raw = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SESSION)
    merged = dict(DEFAULT_SESSION)
    if isinstance(raw, dict):
        merged.update(raw)
        # Migrate V2.18.4 and older session files that only had ONE/ALL.
        if "trades_per_signal" not in raw:
            legacy_mode = str(raw.get("batch_mode", "all")).strip().lower()
            merged["trades_per_signal"] = 1 if legacy_mode == "one" else int(raw.get("max_open", merged.get("max_open", 1)) or 1)
    if merged.get("mode") == "live":
        merged["mode"] = "signals"
    return merged


def save_last_session(settings: Dict[str, Any]) -> None:
    safe = dict(settings)
    safe.pop("live_confirmation", None)
    SESSION_PATH.write_text(json.dumps(safe, indent=2), encoding="utf-8")


def _preset_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        raise ValueError("Preset name is required")
    if len(name) > 60:
        raise ValueError("Preset name must be 60 characters or fewer")
    if any(ch in name for ch in "\r\n\t"):
        raise ValueError("Preset name contains invalid characters")
    return name


def load_user_presets() -> Dict[str, Dict[str, Any]]:
    if not USER_PRESETS_PATH.exists():
        return {}
    try:
        raw = json.loads(USER_PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("presets"), dict):
        raw = raw["presets"]
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def write_user_presets(presets: Dict[str, Dict[str, Any]]) -> None:
    payload = {"version": 1, "presets": presets}
    tmp = USER_PRESETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(USER_PRESETS_PATH)


def user_preset_summaries() -> list[Dict[str, Any]]:
    rows = []
    for name, item in load_user_presets().items():
        session = item.get("session") if isinstance(item.get("session"), dict) else {}
        rows.append({
            "name": name,
            "saved_at": str(item.get("saved_at", "")),
            "strategy_engine": str(session.get("strategy_engine", "single")),
            "strategy": str(session.get("strategy", "")),
            "mode": str(session.get("mode", "")),
            "take_profit_mode": str(session.get("take_profit_mode", "")),
        })
    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return rows


def validate_preset_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidate = dict(payload or {})
    # LIVE is never stored as an authorization secret, but supplying it here lets
    # the normal validator check a saved live-mode preset. The user still has to
    # type LIVE when actually starting a real-money session.
    if str(candidate.get("mode", "")).strip().lower() == "live":
        candidate["live_confirmation"] = "LIVE"
    result = validate_session(candidate)
    result.pop("live_confirmation", None)
    # A Full Preset owns the exact config snapshot. Force Custom so starting the
    # preset cannot silently replace those saved values with a built-in profile.
    if result.get("strategy_engine") == "single":
        result["strategy_profile"] = "custom"
    else:
        selected = [x for x in str(result.get("ensemble_strategies", "")).split(",") if x]
        result["ensemble_profiles_json"] = json.dumps({sid: "custom" for sid in selected}, separators=(",", ":"))
    return result


def save_full_preset(name: str, session_payload: Dict[str, Any], config_payload: Dict[str, Any]) -> Dict[str, Any]:
    name = _preset_name(name)
    if MANAGER.running() or LAB_MANAGER.any_running():
        raise ValueError("Stop the main bot and Strategy Lab before saving a full preset")
    config = validate_config(config_payload)
    session = validate_preset_session(session_payload)
    presets = load_user_presets()
    presets[name] = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "config": config,
    }
    write_user_presets(presets)
    return presets[name]


def apply_full_preset(name: str) -> Dict[str, Any]:
    name = _preset_name(name)
    if MANAGER.running() or LAB_MANAGER.any_running():
        raise ValueError("Stop the main bot and Strategy Lab before loading a full preset")
    presets = load_user_presets()
    item = presets.get(name)
    if not isinstance(item, dict):
        raise ValueError("Saved preset was not found")
    config = validate_config(item.get("config") if isinstance(item.get("config"), dict) else {})
    session = validate_preset_session(item.get("session") if isinstance(item.get("session"), dict) else {})
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_suffix(".json.bak"))
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    save_last_session(session)
    return {"name": name, "session": session, "config": config}


def delete_full_preset(name: str) -> None:
    name = _preset_name(name)
    presets = load_user_presets()
    if name not in presets:
        raise ValueError("Saved preset was not found")
    presets.pop(name, None)
    write_user_presets(presets)


def export_full_preset(name: str) -> Dict[str, Any]:
    """Return a portable preset file payload. Credentials are never included."""
    name = _preset_name(name)
    presets = load_user_presets()
    item = presets.get(name)
    if not isinstance(item, dict):
        raise ValueError("Saved preset was not found")
    config = validate_config(item.get("config") if isinstance(item.get("config"), dict) else {})
    session = validate_preset_session(item.get("session") if isinstance(item.get("session"), dict) else {})
    return {
        "format": "control-nexus-full-preset",
        "version": 1,
        "app_version": WEB_UI_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "preset": {
            "saved_at": str(item.get("saved_at", "")),
            "session": session,
            "config": config,
        },
    }


def import_full_preset(payload: Dict[str, Any], *, overwrite: bool = False) -> Dict[str, Any]:
    """Validate and store a portable full-preset payload."""
    if MANAGER.running() or LAB_MANAGER.any_running():
        raise ValueError("Stop the main bot and Strategy Lab before importing a full preset")
    if not isinstance(payload, dict):
        raise ValueError("Preset file must contain a JSON object")

    # Preferred portable format. Also accept a simple {name, session, config}
    # object so older/manual exports can still be shared.
    if payload.get("format") == "control-nexus-full-preset":
        if int(payload.get("version", 0) or 0) != 1:
            raise ValueError("Unsupported Control Nexus preset file version")
        item = payload.get("preset") if isinstance(payload.get("preset"), dict) else {}
        name = _preset_name(payload.get("name", ""))
    else:
        item = payload
        name = _preset_name(payload.get("name", ""))

    session = validate_preset_session(item.get("session") if isinstance(item.get("session"), dict) else {})
    config = validate_config(item.get("config") if isinstance(item.get("config"), dict) else {})
    presets = load_user_presets()
    if name in presets and not overwrite:
        raise ValueError(f'Preset "{name}" already exists')

    stored = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "config": config,
    }
    presets[name] = stored
    write_user_presets(presets)

    # Importing is also a load operation: make the shared configuration the
    # current Session + Strategy setup immediately.
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_suffix(".json.bak"))
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    save_last_session(session)
    return {"name": name, "session": session, "config": config, "preset": stored}


def validate_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(payload.get("mode", "signals")).strip().lower()
    if mode not in {"signals", "demo", "live"}:
        raise ValueError("Mode must be signals, demo, or live")

    strategy_engine = str(payload.get("strategy_engine", "single")).strip().lower()
    if strategy_engine not in {"single", "best", "all", "custom"}:
        raise ValueError("Unknown strategy engine mode")

    strategy = str(payload.get("strategy", "ema_rsi")).strip().lower()
    strategy_profile = str(payload.get("strategy_profile", "custom")).strip().lower()
    ensemble_preset = "custom"
    ensemble_strategies: list[str] = []
    ensemble_profiles: Dict[str, str] = {}
    ensemble_weights: Dict[str, float] = {}
    ensemble_config: Dict[str, Dict[str, Any]] = {}

    if strategy_engine == "single":
        if strategy not in STRATEGY_IDS:
            raise ValueError("Unknown or non-exclusive strategy")
        if strategy_profile not in STRATEGY_PROFILE_NAMES:
            raise ValueError("Unknown strategy configuration profile")
        if strategy_profile != "custom" and strategy_profile not in STRATEGY_PROFILES.get(strategy, {}):
            raise ValueError(f"Profile {strategy_profile} is not available for {strategy}")
    else:
        strategy = "ensemble"
        strategy_profile = "custom"
        ensemble_preset = "best" if strategy_engine == "best" else "all" if strategy_engine == "all" else "custom"
        if ensemble_preset == "best":
            default_selected = ["bollinger_reversion", "stochastic", "hammer", "ema_rsi", "donchian", "tick_momentum"]
        elif ensemble_preset == "all":
            default_selected = list(STRATEGY_IDS)
        else:
            default_selected = []
        raw_selected = str(payload.get("ensemble_strategies", "") or "")
        requested = [item.strip() for item in raw_selected.split(",") if item.strip()]
        ensemble_strategies = default_selected if ensemble_preset in {"best", "all"} else requested
        ensemble_strategies = list(dict.fromkeys(item for item in ensemble_strategies if item in STRATEGY_IDS))
        if not ensemble_strategies:
            raise ValueError("Select at least one strategy for the ensemble")

        try:
            raw_profiles = json.loads(str(payload.get("ensemble_profiles_json", "{}") or "{}"))
            if isinstance(raw_profiles, dict):
                ensemble_profiles = {str(k): str(v) for k, v in raw_profiles.items()}
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid ensemble profile selection") from exc
        try:
            raw_weights = json.loads(str(payload.get("ensemble_weights_json", "{}") or "{}"))
            if isinstance(raw_weights, dict):
                ensemble_weights = {str(k): float(v) for k, v in raw_weights.items()}
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("Invalid ensemble strategy weights") from exc

        current_config = normalize_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        for sid in ensemble_strategies:
            profile_name = ensemble_profiles.get(sid, "balanced")
            if profile_name not in STRATEGY_PROFILE_NAMES:
                raise ValueError(f"Unknown profile {profile_name} for {sid}")
            if profile_name == "custom":
                ensemble_config[sid] = dict(current_config["strategies"][sid])
            else:
                if profile_name not in STRATEGY_PROFILES.get(sid, {}):
                    raise ValueError(f"Profile {profile_name} is not available for {sid}")
                ensemble_config[sid] = dict(STRATEGY_PROFILES[sid][profile_name])
            weight = ensemble_weights.get(sid, 1.0)
            if not (0.05 <= float(weight) <= 10.0):
                raise ValueError(f"Weight for {sid} must be between 0.05 and 10")
            ensemble_weights[sid] = float(weight)

    ensemble_policy = str(payload.get("ensemble_policy", "regime")).strip().lower()
    if ensemble_policy not in {"regime", "majority", "weighted", "any", "unanimous"}:
        raise ValueError("Unknown ensemble decision policy")
    ensemble_min_votes = _integer(payload.get("ensemble_min_votes", 2), "Ensemble minimum votes", minimum=1, maximum=9)
    ensemble_memory_ticks = _integer(payload.get("ensemble_memory_ticks", 8), "Ensemble signal memory", minimum=0, maximum=100)
    ensemble_confirmations = _integer(payload.get("ensemble_confirmations", 1), "Ensemble confirmations", minimum=1, maximum=20)
    ensemble_cooldown_ticks = _integer(payload.get("ensemble_cooldown_ticks", 3), "Ensemble cooldown", minimum=0, maximum=1000)
    ensemble_regime_lookback = _integer(payload.get("ensemble_regime_lookback", 24), "Regime lookback", minimum=5, maximum=1000)
    try:
        ensemble_trend_threshold = float(payload.get("ensemble_trend_threshold", 0.45))
        ensemble_weight_threshold = float(payload.get("ensemble_weight_threshold", 1.25))
        ensemble_conflict_margin = float(payload.get("ensemble_conflict_margin", 0.20))
    except (TypeError, ValueError) as exc:
        raise ValueError("Ensemble thresholds must be numeric") from exc
    if not (0.05 <= ensemble_trend_threshold <= 0.95):
        raise ValueError("Regime trend threshold must be between 0.05 and 0.95")
    if not (0.1 <= ensemble_weight_threshold <= 20):
        raise ValueError("Weighted-vote threshold must be between 0.1 and 20")
    if not (0 <= ensemble_conflict_margin <= 20):
        raise ValueError("Conflict margin must be between 0 and 20")

    capital = _decimal(payload.get("capital"), "Capital", minimum=Decimal("0"), allow_zero=False)
    split = _integer(payload.get("split"), "Split", minimum=1, maximum=50)
    max_open = _integer(payload.get("max_open"), "Maximum open trades", minimum=1, maximum=50)
    if max_open > split:
        raise ValueError("Maximum open trades cannot exceed the split count")

    stake = (capital / Decimal(split)).quantize(Decimal("0.01"))
    if stake < Decimal("0.35"):
        raise ValueError("Stake per split is below 0.35; increase capital or reduce splits")

    server_stop_loss = _decimal(payload.get("server_stop_loss", 0), "Server stop loss", minimum=Decimal("0"))
    if server_stop_loss > 0 and server_stop_loss >= stake:
        raise ValueError("Server stop loss must be below the stake per trade, or 0 to disable")

    smart_loss_enabled = _boolean(payload.get("smart_loss_enabled", False))
    smart_loss_soft_trigger = _decimal(
        payload.get("smart_loss_soft_trigger", "0.60"), "Smart loss soft trigger", minimum=Decimal("0.01")
    )
    smart_loss_reset_level = _decimal(
        payload.get("smart_loss_reset_level", "0.20"), "Smart loss recovery reset", minimum=Decimal("0")
    )
    smart_loss_hard_cap = _decimal(
        payload.get("smart_loss_hard_cap", "2.50"), "Smart loss hard cap", minimum=Decimal("0.01")
    )
    if smart_loss_enabled and smart_loss_reset_level >= smart_loss_soft_trigger:
        raise ValueError("Smart loss recovery reset must be smaller than the soft trigger")
    if smart_loss_enabled and smart_loss_hard_cap <= smart_loss_soft_trigger:
        raise ValueError("Smart loss hard cap must be larger than the soft trigger")
    if smart_loss_enabled and smart_loss_hard_cap >= stake:
        raise ValueError("Smart loss hard cap must be below the stake per trade")
    smart_loss_grace_ticks = _integer(
        payload.get("smart_loss_grace_ticks", 8), "Smart loss grace ticks", minimum=0, maximum=10000
    )
    smart_loss_max_watch_ticks = _integer(
        payload.get("smart_loss_max_watch_ticks", 30), "Smart loss max watch ticks", minimum=0, maximum=100000
    )
    if smart_loss_enabled and smart_loss_max_watch_ticks and smart_loss_max_watch_ticks < smart_loss_grace_ticks:
        raise ValueError("Smart loss max watch ticks must be 0 or at least the grace ticks")
    smart_loss_adverse_ticks = _integer(
        payload.get("smart_loss_adverse_ticks", 4), "Smart loss adverse ticks", minimum=1, maximum=50
    )
    smart_loss_adverse_window = _integer(
        payload.get("smart_loss_adverse_window", 6), "Smart loss adverse window", minimum=1, maximum=100
    )
    if smart_loss_enabled and smart_loss_adverse_window < smart_loss_adverse_ticks:
        raise ValueError("Smart loss adverse window cannot be smaller than the adverse tick count")
    smart_loss_opposite_signal = _boolean(payload.get("smart_loss_opposite_signal", True))
    smart_loss_rescue_profit = _decimal(
        payload.get("smart_loss_rescue_profit", "0.02"), "Smart loss rescue profit", minimum=Decimal("0")
    )

    raw_trades_per_signal = payload.get("trades_per_signal", None)
    if raw_trades_per_signal in (None, ""):
        # Backward compatibility for older clients that still send batch_mode.
        legacy_batch_mode = str(payload.get("batch_mode", "all")).strip().lower()
        if legacy_batch_mode not in {"one", "all"}:
            raise ValueError("Opening mode must be one or all")
        trades_per_signal = 1 if legacy_batch_mode == "one" else max_open
    else:
        trades_per_signal = _integer(raw_trades_per_signal, "Trades per signal", minimum=1, maximum=50)
        if trades_per_signal > max_open:
            raise ValueError("Trades per signal cannot exceed maximum open trades")
    batch_mode = "one" if trades_per_signal == 1 else ("all" if trades_per_signal == max_open else "count")

    try:
        split_delay = float(payload.get("split_delay", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Split delay must be a number") from exc
    if split_delay < 0 or split_delay > 3600 or split_delay != split_delay:
        raise ValueError("Split delay must be between 0 and 3600 seconds")

    loss_policy = str(payload.get("loss_policy", "continue")).strip().lower()
    if loss_policy not in {"continue", "first", "consecutive", "total"}:
        raise ValueError("Unknown loss policy")
    loss_count = _integer(payload.get("loss_count", 1), "Loss count", minimum=1, maximum=100000)

    profit_target = _decimal(payload.get("profit_target", 0), "Profit target", minimum=Decimal("0"))
    loss_limit = _decimal(payload.get("loss_limit", 0), "Loss limit", minimum=Decimal("0"))
    win_target = _integer(payload.get("win_target", 0), "Win target", minimum=0, maximum=1000000)

    take_profit_mode = str(payload.get("take_profit_mode", "hybrid")).strip().lower()
    if take_profit_mode not in {"fixed", "adaptive", "smart", "hybrid"}:
        raise ValueError("Unknown exit mode")
    take_profit_unit=str(payload.get("take_profit_unit","amount")).strip().lower()
    if take_profit_unit not in {"amount","ticks"}:raise ValueError("Take-profit measurement must be amount or ticks")
    take_profit_ticks=_integer(payload.get("take_profit_ticks",3),"Take-profit ticks",minimum=1,maximum=100000);take_profit_ticks_max=_integer(payload.get("take_profit_ticks_max",take_profit_ticks),"Maximum take-profit ticks",minimum=1,maximum=100000)
    if take_profit_mode=="fixed":take_profit_ticks_max=take_profit_ticks
    if take_profit_ticks_max<take_profit_ticks:raise ValueError("Maximum take-profit ticks cannot be below the minimum")
    tick_tp_server_backup=_decimal(payload.get("tick_tp_server_backup","2.00"),"Tick TP server backup",minimum=Decimal("0.10"))
    tp_min = _decimal(payload.get("take_profit", "0.10"), "Take-profit minimum", minimum=Decimal("0.10"))
    tp_max = _decimal(payload.get("take_profit_max", tp_min), "Take-profit maximum", minimum=Decimal("0.10"))
    if take_profit_mode == "fixed":
        tp_max = tp_min
    if tp_max < tp_min:
        raise ValueError("Maximum take profit cannot be below the minimum")

    smart_activation = _decimal(
        payload.get("smart_activation", "0.15"), "Smart activation", minimum=Decimal("0.10")
    )
    smart_profit_floor = _decimal(
        payload.get("smart_profit_floor", "0.10"), "Protected profit floor", minimum=Decimal("0.10")
    )
    if take_profit_unit == "amount" and smart_activation < smart_profit_floor:
        raise ValueError("Smart activation cannot be below the protected profit floor")
    smart_trail_percent = _decimal(
        payload.get("smart_trail_percent", "30"), "Trailing giveback percent", minimum=Decimal("1")
    )
    if smart_trail_percent > Decimal("95"):
        raise ValueError("Trailing giveback percent cannot exceed 95")
    smart_min_distance = _decimal(
        payload.get("smart_min_distance", "0.05"), "Minimum trail distance", minimum=Decimal("0.01")
    )
    smart_activation_ticks = _integer(payload.get("smart_activation_ticks", 3), "Smart activation ticks", minimum=1, maximum=100000)
    smart_profit_floor_ticks = _integer(payload.get("smart_profit_floor_ticks", 1), "Smart protected floor ticks", minimum=1, maximum=100000)
    smart_min_distance_ticks = _integer(payload.get("smart_min_distance_ticks", 1), "Smart minimum trail distance ticks", minimum=1, maximum=100000)
    if take_profit_unit == "ticks" and smart_activation_ticks < smart_profit_floor_ticks:
        raise ValueError("Smart activation ticks cannot be below protected floor ticks")
    smart_adverse_ticks = _integer(
        payload.get("smart_adverse_ticks", 3), "Adverse tick count", minimum=1, maximum=20
    )
    smart_adverse_window = _integer(
        payload.get("smart_adverse_window", 4), "Adverse tick window", minimum=1, maximum=50
    )
    if smart_adverse_window < smart_adverse_ticks:
        raise ValueError("Adverse tick window cannot be smaller than the required count")
    smart_server_take_profit = _decimal(
        payload.get("smart_server_take_profit", "2.00"),
        "Emergency server take profit",
        minimum=Decimal("0.10"),
    )
    if take_profit_unit == "amount" and smart_server_take_profit < smart_activation:
        raise ValueError("Emergency server take profit cannot be below smart activation")

    multiplier = _integer(payload.get("multiplier", 750), "Multiplier", minimum=1, maximum=7500)
    if multiplier not in STEP100_ALLOWED_MULTIPLIERS:
        allowed_text = ", ".join(str(value) for value in STEP100_ALLOWED_MULTIPLIERS)
        raise ValueError(f"Step 100 multiplier must be one of: {allowed_text}")

    live_confirmation = str(payload.get("live_confirmation", "")).strip()
    if mode == "live" and live_confirmation != "LIVE":
        raise ValueError("Type LIVE to enable real-money mode")

    return {
        "mode": mode,
        "strategy_engine": strategy_engine,
        "strategy": strategy,
        "strategy_profile": strategy_profile,
        "ensemble_preset": ensemble_preset,
        "ensemble_policy": ensemble_policy,
        "ensemble_min_votes": ensemble_min_votes,
        "ensemble_memory_ticks": ensemble_memory_ticks,
        "ensemble_confirmations": ensemble_confirmations,
        "ensemble_cooldown_ticks": ensemble_cooldown_ticks,
        "ensemble_regime_lookback": ensemble_regime_lookback,
        "ensemble_trend_threshold": ensemble_trend_threshold,
        "ensemble_weight_threshold": ensemble_weight_threshold,
        "ensemble_conflict_margin": ensemble_conflict_margin,
        "ensemble_strategies": ",".join(ensemble_strategies),
        "ensemble_profiles_json": json.dumps(ensemble_profiles, separators=(",", ":")),
        "ensemble_weights_json": json.dumps(ensemble_weights, separators=(",", ":")),
        "ensemble_config_json": json.dumps(ensemble_config, separators=(",", ":")),
        "capital": format(capital, "f"),
        "split": split,
        "max_open": max_open,
        "batch_mode": batch_mode,
        "trades_per_signal": trades_per_signal,
        "split_delay": split_delay,
        "compound": _boolean(payload.get("compound", False)),
        "loss_policy": loss_policy,
        "loss_count": loss_count,
        "profit_target": format(profit_target, "f"),
        "loss_limit": format(loss_limit, "f"),
        "win_target": win_target,
        "take_profit_mode": take_profit_mode,
        "take_profit_unit": take_profit_unit,
        "take_profit": format(tp_min, "f"),
        "take_profit_max": format(tp_max, "f"),
        "take_profit_ticks": take_profit_ticks,
        "take_profit_ticks_max": take_profit_ticks_max,
        "tick_tp_server_backup": format(tick_tp_server_backup, "f"),
        "smart_activation": format(smart_activation, "f"),
        "smart_profit_floor": format(smart_profit_floor, "f"),
        "smart_trail_percent": format(smart_trail_percent, "f"),
        "smart_min_distance": format(smart_min_distance, "f"),
        "smart_activation_ticks": smart_activation_ticks,
        "smart_profit_floor_ticks": smart_profit_floor_ticks,
        "smart_min_distance_ticks": smart_min_distance_ticks,
        "smart_adverse_ticks": smart_adverse_ticks,
        "smart_adverse_window": smart_adverse_window,
        "smart_opposite_signal": _boolean(payload.get("smart_opposite_signal", True)),
        "smart_server_take_profit": format(smart_server_take_profit, "f"),
        "server_stop_loss": format(server_stop_loss, "f"),
        "smart_loss_enabled": smart_loss_enabled,
        "smart_loss_soft_trigger": format(smart_loss_soft_trigger, "f"),
        "smart_loss_reset_level": format(smart_loss_reset_level, "f"),
        "smart_loss_hard_cap": format(smart_loss_hard_cap, "f"),
        "smart_loss_grace_ticks": smart_loss_grace_ticks,
        "smart_loss_max_watch_ticks": smart_loss_max_watch_ticks,
        "smart_loss_adverse_ticks": smart_loss_adverse_ticks,
        "smart_loss_adverse_window": smart_loss_adverse_window,
        "smart_loss_opposite_signal": smart_loss_opposite_signal,
        "smart_loss_rescue_profit": format(smart_loss_rescue_profit, "f"),
        "multiplier": multiplier,
        "live_confirmation": live_confirmation,
        "stake_per_trade": format(stake, "f"),
    }


def build_bot_command(settings: Dict[str, Any], python_executable: str = sys.executable) -> list[str]:
    command = [
        python_executable, "-u", str(BOT_PATH), "--yes",
        "--strategy", settings["strategy"],
        "--capital", settings["capital"],
        "--split", str(settings["split"]),
        "--max-open", str(settings["max_open"]),
        "--batch-mode", "one" if settings["trades_per_signal"] == 1 else "all",
        "--trades-per-signal", str(settings["trades_per_signal"]),
        "--split-delay", str(settings["split_delay"]),
        "--loss-policy", settings["loss_policy"],
        "--loss-count", str(settings["loss_count"]),
        "--profit-target", settings["profit_target"],
        "--loss-limit", settings["loss_limit"],
        "--win-target", str(settings["win_target"]),
        "--take-profit-mode", settings["take_profit_mode"],
        "--take-profit-unit", settings.get("take_profit_unit","amount"),
        "--take-profit", settings["take_profit"],
        "--take-profit-max", settings["take_profit_max"],
        "--take-profit-ticks", str(settings.get("take_profit_ticks",3)),
        "--take-profit-ticks-max", str(settings.get("take_profit_ticks_max",8)),
        "--tick-tp-server-backup", settings.get("tick_tp_server_backup","2.00"),
        "--smart-activation", settings["smart_activation"],
        "--smart-profit-floor", settings["smart_profit_floor"],
        "--smart-trail-percent", settings["smart_trail_percent"],
        "--smart-min-distance", settings["smart_min_distance"],
        "--smart-activation-ticks", str(settings.get("smart_activation_ticks", 3)),
        "--smart-profit-floor-ticks", str(settings.get("smart_profit_floor_ticks", 1)),
        "--smart-min-distance-ticks", str(settings.get("smart_min_distance_ticks", 1)),
        "--smart-adverse-ticks", str(settings["smart_adverse_ticks"]),
        "--smart-adverse-window", str(settings["smart_adverse_window"]),
        "--smart-server-take-profit", settings["smart_server_take_profit"],
        "--multiplier", str(settings["multiplier"]),
    ]
    command.extend(["--server-stop-loss", settings.get("server_stop_loss", "0")])
    command.extend([
        "--smart-loss-soft-trigger", settings.get("smart_loss_soft_trigger", "0.60"),
        "--smart-loss-reset-level", settings.get("smart_loss_reset_level", "0.20"),
        "--smart-loss-hard-cap", settings.get("smart_loss_hard_cap", "2.50"),
        "--smart-loss-grace-ticks", str(settings.get("smart_loss_grace_ticks", 8)),
        "--smart-loss-max-watch-ticks", str(settings.get("smart_loss_max_watch_ticks", 30)),
        "--smart-loss-adverse-ticks", str(settings.get("smart_loss_adverse_ticks", 4)),
        "--smart-loss-adverse-window", str(settings.get("smart_loss_adverse_window", 6)),
        "--smart-loss-rescue-profit", settings.get("smart_loss_rescue_profit", "0.02"),
    ])
    command.append("--smart-loss" if settings.get("smart_loss_enabled") else "--no-smart-loss")
    command.append(
        "--smart-loss-opposite-signal"
        if settings.get("smart_loss_opposite_signal", True)
        else "--no-smart-loss-opposite-signal"
    )
    if settings.get("strategy") == "ensemble":
        command.extend([
            "--ensemble-preset", settings.get("ensemble_preset", "custom"),
            "--ensemble-strategies", settings.get("ensemble_strategies", ""),
            "--ensemble-policy", settings.get("ensemble_policy", "regime"),
            "--ensemble-min-votes", str(settings.get("ensemble_min_votes", 2)),
            "--ensemble-memory-ticks", str(settings.get("ensemble_memory_ticks", 8)),
            "--ensemble-confirmations", str(settings.get("ensemble_confirmations", 1)),
            "--ensemble-cooldown-ticks", str(settings.get("ensemble_cooldown_ticks", 3)),
            "--ensemble-regime-lookback", str(settings.get("ensemble_regime_lookback", 24)),
            "--ensemble-trend-threshold", str(settings.get("ensemble_trend_threshold", 0.45)),
            "--ensemble-weight-threshold", str(settings.get("ensemble_weight_threshold", 1.25)),
            "--ensemble-conflict-margin", str(settings.get("ensemble_conflict_margin", 0.20)),
            "--ensemble-weights-json", settings.get("ensemble_weights_json", "{}"),
            "--ensemble-config-json", settings.get("ensemble_config_json", "{}"),
        ])

    command.append(
        "--smart-opposite-signal"
        if settings["smart_opposite_signal"]
        else "--no-smart-opposite-signal"
    )
    command.append("--compound" if settings["compound"] else "--fixed")
    # Web UI launches are allowed alongside already-open account positions.
    # Each process manages only contracts it opens itself.
    command.append("--allow-existing-positions")
    if settings["mode"] == "signals":
        command.append("--dry-run")
    elif settings["mode"] == "live":
        command.append("--live")
    return command



def load_config() -> Dict[str, Any]:
    return normalize_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))



def normalize_config(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object")
    result = dict(data)
    result.setdefault("symbol_display_names", ["Step 100 Index", "Step Index 100", "Step 100"])
    result.setdefault("multiplier", 750)
    result.setdefault("take_profit", 0.10)
    result.setdefault("dashboard_seconds", 3)
    supplied = result.get("strategies")
    strategies: Dict[str, Dict[str, Any]] = {}
    if isinstance(supplied, dict):
        for name, values in supplied.items():
            if isinstance(values, dict):
                strategies[str(name)] = dict(values)
    if "ema_rsi" not in strategies and isinstance(result.get("strategy"), dict):
        strategies["ema_rsi"] = dict(result["strategy"])
    if "hammer" not in strategies and isinstance(result.get("hammer_strategy"), dict):
        strategies["hammer"] = dict(result["hammer_strategy"])
    for strategy_id in STRATEGY_IDS:
        base = dict(STRATEGY_PROFILES[strategy_id]["balanced"])
        base.update(strategies.get(strategy_id, {}))
        strategies[strategy_id] = base
    result["strategies"] = strategies
    # Keep legacy fields synchronized for older launchers and readable backups.
    result["strategy"] = dict(strategies["ema_rsi"])
    result["hammer_strategy"] = dict(strategies["hammer"])
    return result


def _validate_number(profile: Dict[str, Any], name: str, minimum: float, maximum: float) -> None:
    try:
        value = float(profile.get(name))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _validate_int_field(profile: Dict[str, Any], name: str, minimum: int = 1, maximum: int = 100000) -> None:
    _integer(profile.get(name), name, minimum=minimum, maximum=maximum)


def validate_strategy_profile(strategy_id: str, profile: Dict[str, Any]) -> None:
    if strategy_id == "ema_rsi":
        for name in ("ema_fast", "ema_slow", "rsi_period", "momentum_ticks", "momentum_required", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        if int(profile["ema_fast"]) >= int(profile["ema_slow"]):
            raise ValueError("EMA fast must be lower than EMA slow")
        for name in ("up_rsi_min", "up_rsi_max", "down_rsi_min", "down_rsi_max"):
            _validate_number(profile, name, 0, 100)
    elif strategy_id == "hammer":
        for name in ("candle_ticks", "candle_seconds", "context_candles", "breakout_expiry_ticks", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        if str(profile.get("candle_mode", "ticks")) not in {"ticks", "time"}:
            raise ValueError("Candlestick candle_mode must be ticks or time")
        if int(profile.get("candle_seconds", 60)) < 5 or int(profile.get("candle_seconds", 60)) > 3600:
            raise ValueError("Candlestick candle_seconds must be between 5 and 3600")
        for name in ("min_long_wick_body_ratio", "max_opposite_wick_body_ratio", "min_body_range_ratio", "max_body_range_ratio"):
            _validate_number(profile, name, 0, 20)
        allowed=CANDLE_PATTERN_IDS;enabled=profile.get("enabled_patterns",[])
        if not isinstance(enabled,list) or not enabled or any(str(x) not in allowed for x in enabled):raise ValueError("Candlestick engine must have at least one valid enabled pattern")
        if str(profile.get("confirmation_mode","breakout")) not in {"breakout","close"}:raise ValueError("Candlestick confirmation mode must be breakout or close")
        if str(profile.get("shape_mode","percent")) not in {"percent","ratio"}:raise ValueError("Candlestick shape_mode must be percent or ratio")
        if str(profile.get("context_mode","trend")) not in {"off","trend","strict"}:raise ValueError("Candlestick context_mode must be off, trend, or strict")
        if str(profile.get("pattern_conflict_policy","block")) not in {"block","highest_score"}:raise ValueError("Candlestick pattern_conflict_policy must be block or highest_score")
        _validate_number(profile,"min_pattern_score",0,1);_validate_number(profile,"doji_body_ratio",.001,.35);_validate_number(profile,"marubozu_body_ratio",.40,.999)
        for name in ("min_body_pct","max_body_pct","min_long_wick_pct","max_opposite_wick_pct","body_edge_zone_pct","flat_wick_max_pct","rejection_min_body_pct","rejection_max_body_pct","rejection_min_wick_pct","rejection_max_opposite_wick_pct","context_agreement_pct","doji_body_pct","marubozu_body_pct"):
            _validate_number(profile,name,0,100)
        if float(profile["min_body_pct"]) > float(profile["max_body_pct"]): raise ValueError("Candlestick min body % cannot exceed max body %")
        if float(profile["rejection_min_body_pct"]) > float(profile["rejection_max_body_pct"]): raise ValueError("Candlestick rejection min body % cannot exceed max body %")
    elif strategy_id == "macd":
        for name in ("fast_period", "slow_period", "signal_period", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        if int(profile["fast_period"]) >= int(profile["slow_period"]):
            raise ValueError("MACD fast period must be lower than slow period")
        _validate_number(profile, "histogram_threshold", 0, 100000)
    elif strategy_id in {"bollinger_reversion", "bollinger_breakout"}:
        for name in ("period", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        _validate_number(profile, "stddev_multiplier", 0.1, 10)
        if strategy_id == "bollinger_reversion":
            _validate_int_field(profile, "rsi_period")
            _validate_number(profile, "oversold", 0, 100)
            _validate_number(profile, "overbought", 0, 100)
        else:
            _validate_int_field(profile, "momentum_ticks")
            _validate_int_field(profile, "momentum_required")
    elif strategy_id == "stochastic":
        for name in ("lookback_period", "smooth_k", "smooth_d", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        _validate_number(profile, "oversold", 0, 100)
        _validate_number(profile, "overbought", 0, 100)
    elif strategy_id == "donchian":
        for name in ("period", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        _validate_number(profile, "breakout_buffer", 0, 100000)
    elif strategy_id == "rsi_pullback":
        for name in ("trend_ema", "pullback_ema", "rsi_period", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        if int(profile["pullback_ema"]) >= int(profile["trend_ema"]):
            raise ValueError("Pullback EMA must be lower than trend EMA")
        for name in ("up_rsi_min", "up_rsi_max", "down_rsi_min", "down_rsi_max"):
            _validate_number(profile, name, 0, 100)
    elif strategy_id == "tick_momentum":
        for name in ("lookback_ticks", "required_directional_ticks", "confirmations", "cooldown_ticks", "history_count"):
            _validate_int_field(profile, name, 0 if name == "cooldown_ticks" else 1)
        if int(profile["required_directional_ticks"]) > int(profile["lookback_ticks"]):
            raise ValueError("Required directional ticks cannot exceed lookback ticks")
        _validate_number(profile, "min_net_change", 0, 100000)
    else:
        raise ValueError(f"Unknown strategy configuration: {strategy_id}")


def validate_config(data: Dict[str, Any]) -> Dict[str, Any]:
    result = normalize_config(data)
    multiplier = _integer(result["multiplier"], "Multiplier", minimum=1, maximum=7500)
    if multiplier not in STEP100_ALLOWED_MULTIPLIERS:
        allowed_text = ", ".join(str(value) for value in STEP100_ALLOWED_MULTIPLIERS)
        raise ValueError(f"Step 100 multiplier must be one of: {allowed_text}")
    _decimal(result["take_profit"], "Take profit", minimum=Decimal("0.01"), allow_zero=False)
    _integer(result["dashboard_seconds"], "Dashboard seconds", minimum=1, maximum=3600)
    if not isinstance(result.get("symbol_display_names"), list) or not result["symbol_display_names"]:
        raise ValueError("symbol_display_names must be a non-empty list")
    for strategy_id in STRATEGY_IDS:
        profile = result["strategies"].get(strategy_id)
        if not isinstance(profile, dict):
            raise ValueError(f"Missing strategy configuration: {strategy_id}")
        validate_strategy_profile(strategy_id, profile)
    return result


def apply_strategy_profile(strategy_id: str, profile_name: str) -> Dict[str, Any]:
    config = validate_config(load_config())
    if profile_name == "custom":
        return config
    try:
        values = STRATEGY_PROFILES[strategy_id][profile_name]
    except KeyError as exc:
        raise ValueError(f"Unknown {strategy_id} profile: {profile_name}") from exc
    config["strategies"][strategy_id] = dict(values)
    config["strategy"] = dict(config["strategies"]["ema_rsi"])
    config["hammer_strategy"] = dict(config["strategies"]["hammer"])
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


@dataclass
class ProcessState:
    process: Optional[subprocess.Popen[str]] = None
    started_at: float = 0.0
    command: list[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    exit_code: Optional[int] = None
    stop_requested: bool = False
    lines: Deque[tuple[int, str]] = field(default_factory=lambda: deque(maxlen=LOG_LIMIT))
    next_line_id: int = 1
    dashboard_lines: list[str] = field(default_factory=list)
    dashboard_capture: list[str] = field(default_factory=list)
    last_error: str = ""
    stop_file: str = ""
    live_control_file: str = ""
    telemetry_file: str = ""
    live_revision: int = 0
    run_id: str = ""
    diagnostic_path: str = ""
    ensemble_log_path: str = ""
    runtime_banner: str = ""
    build_source: str = ""


class BotProcessManager:
    def __init__(self) -> None:
        self.state = ProcessState()
        self.lock = threading.RLock()
        self.reader_thread: Optional[threading.Thread] = None

    def _append_line(self, raw_line: str) -> None:
        line = ANSI_RE.sub("", raw_line.rstrip("\r\n"))
        with self.lock:
            line_id = self.state.next_line_id
            self.state.next_line_id += 1
            self.state.lines.append((line_id, line))
            if "ERROR" in line:
                self.state.last_error = line
            if line.startswith("DR EXPLOIT CONTROL V2."):
                self.state.runtime_banner = line
                self.state.dashboard_capture = [line]
            elif line.startswith("Build source:"):
                self.state.build_source = line.split(":", 1)[1].strip() if ":" in line else line
            elif self.state.dashboard_capture:
                self.state.dashboard_capture.append(line)
                if line == "=" * 72 and len(self.state.dashboard_capture) > 3:
                    self.state.dashboard_lines = list(self.state.dashboard_capture)
                    self.state.dashboard_capture = []

    def start(
        self,
        settings: Dict[str, Any],
        *,
        env_overrides: Optional[Dict[str, str]] = None,
        log_prefix: str = "WEB UI",
    ) -> None:
        with self.lock:
            if self.state.process is not None and self.state.process.poll() is None:
                raise RuntimeError("The bot is already running")
            command = build_bot_command(settings)
            child_env = os.environ.copy()
            child_env["PYTHONUNBUFFERED"] = "1"
            if env_overrides:
                child_env.update({str(k): str(v) for k, v in env_overrides.items()})
            run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
            instance_name = str((env_overrides or {}).get("DR_EXPLOIT_INSTANCE_ID", "main") or "main")
            safe_instance = re.sub(r"[^A-Za-z0-9_-]+", "-", instance_name).strip("-") or "main"
            diagnostic_path = DIAGNOSTIC_DIR / f"{run_id}_{safe_instance}_entry.jsonl"
            ensemble_log_path = DIAGNOSTIC_DIR / f"{run_id}_{safe_instance}_ensemble.csv"
            if not (env_overrides or {}).get("DR_EXPLOIT_DIAGNOSTIC_LOG_PATH"):
                child_env["DR_EXPLOIT_DIAGNOSTIC_LOG_PATH"] = str(diagnostic_path)
            else:
                diagnostic_path = Path(str((env_overrides or {})["DR_EXPLOIT_DIAGNOSTIC_LOG_PATH"]))
            if not (env_overrides or {}).get("DR_EXPLOIT_ENSEMBLE_LOG_PATH"):
                child_env["DR_EXPLOIT_ENSEMBLE_LOG_PATH"] = str(ensemble_log_path)
            else:
                ensemble_log_path = Path(str((env_overrides or {})["DR_EXPLOIT_ENSEMBLE_LOG_PATH"]))
            if settings["mode"] == "live":
                child_env["DERIV_LIVE_CONFIRM"] = "YES_I_ACCEPT_REAL_MONEY_RISK"
            else:
                child_env.pop("DERIV_LIVE_CONFIRM", None)
            stop_file = CONTROL_DIR / f"stop_{secrets.token_hex(12)}.flag"
            stop_file.unlink(missing_ok=True)
            child_env["DR_EXPLOIT_STOP_FILE"] = str(stop_file)
            live_control_file = CONTROL_DIR / f"live_{secrets.token_hex(12)}.json"
            telemetry_file = CONTROL_DIR / f"telemetry_{secrets.token_hex(12)}.json"
            live_control_file.write_text(json.dumps({"revision": 0}), encoding="utf-8")
            telemetry_file.unlink(missing_ok=True)
            child_env["DR_EXPLOIT_LIVE_CONTROL_FILE"] = str(live_control_file)
            child_env["DR_EXPLOIT_TELEMETRY_FILE"] = str(telemetry_file)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=child_env,
                creationflags=creationflags,
            )
            self.state = ProcessState(
                process=process, started_at=time.time(), command=command, settings=dict(settings),
                stop_file=str(stop_file), live_control_file=str(live_control_file), telemetry_file=str(telemetry_file),
                live_revision=0, run_id=run_id,
                diagnostic_path=str(diagnostic_path), ensemble_log_path=str(ensemble_log_path),
            )
            self._append_line(f"{log_prefix} | Bot process started")
            self.reader_thread = threading.Thread(target=self._reader, args=(process,), daemon=True)
            self.reader_thread.start()

    def _reader(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                self._append_line(line)
        finally:
            exit_code = process.wait()
            with self.lock:
                self.state.exit_code = exit_code
                stop_file_value = self.state.stop_file
            if stop_file_value:
                try:
                    Path(stop_file_value).unlink(missing_ok=True)
                except OSError:
                    pass
            self._append_line(f"WEB UI | Bot process exited with code {exit_code}")

    def stop(self) -> None:
        """Disable new entries and let managed positions finish naturally.

        This deliberately does NOT terminate the child process. The core bot
        remains connected, keeps applying Smart/Hybrid exits, reconnects if
        needed, and exits only when all positions it manages are closed.
        """
        with self.lock:
            process = self.state.process
            if process is None or process.poll() is not None:
                raise RuntimeError("The bot is not running")
            if self.state.stop_requested:
                return
            self.state.stop_requested = True
            stop_file_value = self.state.stop_file
            self._append_line(
                "WEB UI | STOP ENTRIES requested — no new MULTUP/MULTDOWN orders; "
                "open positions will keep running until their normal exit"
            )
        if not stop_file_value:
            raise RuntimeError("Graceful stop control file is unavailable")
        stop_path = Path(stop_file_value)
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.write_text(
            f"graceful-entry-stop {time.time():.6f}\n", encoding="utf-8"
        )

    def _read_telemetry_locked(self) -> Dict[str, Any]:
        path = Path(self.state.telemetry_file) if self.state.telemetry_file else None
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def apply_live_controls(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            if self.state.process is None or self.state.process.poll() is not None:
                raise RuntimeError("The bot is not running")
            if self.state.stop_requested:
                raise RuntimeError("The session is draining after Stop new entries; live resume/tuning is disabled")
            control_path = Path(self.state.live_control_file) if self.state.live_control_file else None
            if control_path is None:
                raise RuntimeError("Live runtime-control channel is unavailable")
            action = str(payload.get("action", "apply") or "apply").lower()
            if action not in {"apply", "pause", "resume"}:
                raise ValueError("Unknown live control action")
            outgoing: Dict[str, Any] = {"action": action, "label": str(payload.get("label", "Advanced dashboard update"))[:120]}
            raw_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            if raw_settings:
                # Translate Web UI names to the bot's dataclass names. Values were already validated when practical.
                mapping = {"split":"split_count", "max_open":"max_open_trades", "split_delay":"split_delay_seconds", "strategy":"strategy_mode"}
                allowed = {
                    "capital","split","max_open","trades_per_signal","split_delay","compound","loss_policy","loss_count",
                    "profit_target","loss_limit","win_target","take_profit_mode","take_profit_unit","take_profit","take_profit_max",
                    "take_profit_ticks","take_profit_ticks_max","tick_tp_server_backup","smart_activation","smart_profit_floor",
                    "smart_trail_percent","smart_min_distance","smart_activation_ticks","smart_profit_floor_ticks","smart_min_distance_ticks",
                    "smart_adverse_ticks","smart_adverse_window","smart_opposite_signal","smart_server_take_profit","server_stop_loss",
                    "smart_loss_enabled","smart_loss_soft_trigger","smart_loss_reset_level","smart_loss_hard_cap","smart_loss_grace_ticks",
                    "smart_loss_max_watch_ticks","smart_loss_adverse_ticks","smart_loss_adverse_window","smart_loss_opposite_signal",
                    "smart_loss_rescue_profit","multiplier","ensemble_policy","ensemble_min_votes","ensemble_memory_ticks",
                    "ensemble_confirmations","ensemble_cooldown_ticks","ensemble_regime_lookback","ensemble_trend_threshold",
                    "ensemble_weight_threshold","ensemble_conflict_margin"
                }
                outgoing["settings"] = {mapping.get(k,k): v for k,v in raw_settings.items() if k in allowed}
                if "strategy" in raw_settings:
                    outgoing["strategy"] = str(raw_settings["strategy"])
            if isinstance(payload.get("config"), dict):
                outgoing["config"] = validate_config(payload["config"])
            if payload.get("strategy"):
                sid = str(payload["strategy"]).strip().lower()
                if sid != "ensemble" and sid not in STRATEGY_IDS:
                    raise ValueError("Unknown strategy")
                outgoing["strategy"] = sid
            self.state.live_revision += 1
            outgoing["revision"] = self.state.live_revision
            outgoing["issued_at"] = datetime.now(timezone.utc).isoformat()
            tmp = control_path.with_suffix(control_path.suffix + ".tmp")
            tmp.write_text(json.dumps(outgoing, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, control_path)
            self._append_line(f"WEB UI | LIVE CONTROL revision {self.state.live_revision}: {outgoing['label']}")
            return {"revision": self.state.live_revision, "request": outgoing}

    def running(self) -> bool:
        with self.lock:
            return self.state.process is not None and self.state.process.poll() is None

    def logs_after(self, after: int) -> Dict[str, Any]:
        with self.lock:
            lines = [{"id": line_id, "text": text} for line_id, text in self.state.lines if line_id > after]
            last_id = self.state.lines[-1][0] if self.state.lines else after
            return {"lines": lines, "last_id": last_id}

    def status(self) -> Dict[str, Any]:
        with self.lock:
            running = self.state.process is not None and self.state.process.poll() is None
            process_id = self.state.process.pid if self.state.process is not None else None
            return {
                "running": running,
                "pid": process_id,
                "exit_code": self.state.exit_code,
                "started_at": self.state.started_at,
                "runtime_seconds": max(0, int(time.time() - self.state.started_at)) if self.state.started_at else 0,
                "settings": self.state.settings,
                "dashboard": parse_dashboard(self.state.dashboard_lines),
                "last_error": self.state.last_error,
                "stop_requested": self.state.stop_requested,
                "run_id": self.state.run_id,
                "diagnostic_path": self.state.diagnostic_path,
                "ensemble_log_path": self.state.ensemble_log_path,
                "runtime_banner": self.state.runtime_banner,
                "build_source": self.state.build_source,
                "telemetry": self._read_telemetry_locked(),
                "live_control_revision": self.state.live_revision,
            }


class RiseFallProcessManager:
    """Owns one dedicated Rise/Fall subprocess.

    The main Rise/Fall engine and every Rise/Fall Lab tester get separate
    manager instances, telemetry files, stop/control channels and trade CSVs.
    Nothing here shares multiplier trade/config state.
    """
    def __init__(self) -> None:
        self.state = ProcessState()
        self.lock = threading.RLock()
        self.reader_thread = None
        self.trades_path = RISE_FALL_TRADES_PATH
        self.log_prefix = ""

    def _append(self, raw: str) -> None:
        line = ANSI_RE.sub("", raw.rstrip("\r\n"))
        if self.log_prefix and not line.startswith(self.log_prefix):
            line = f"{self.log_prefix} | {line}"
        with self.lock:
            lid = self.state.next_line_id
            self.state.next_line_id += 1
            self.state.lines.append((lid, line))
            if "ERROR" in line:
                self.state.last_error = line
            if "DR EXPLOIT CONTROL V2." in line:
                self.state.runtime_banner = line

    def start(
        self,
        settings: Dict[str, Any],
        *,
        env_overrides: Optional[Dict[str, str]] = None,
        instance_id: str = "rise-fall-main",
        log_prefix: str = "",
    ) -> None:
        with self.lock:
            if self.state.process is not None and self.state.process.poll() is None:
                raise RuntimeError("Rise/Fall is already running")
            if not RISE_FALL_BOT_PATH.exists():
                raise RuntimeError("rise_fall_bot.py is missing")
            command = build_rise_fall_command(settings)
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            if settings["mode"] == "live":
                env["DERIV_LIVE_CONFIRM"] = "YES_I_ACCEPT_REAL_MONEY_RISK"
            else:
                env.pop("DERIV_LIVE_CONFIRM", None)
            if env_overrides:
                env.update({str(k): str(v) for k, v in env_overrides.items()})
            stop = CONTROL_DIR / f"rf_stop_{secrets.token_hex(10)}.flag"
            control = CONTROL_DIR / f"rf_live_{secrets.token_hex(10)}.json"
            telem = CONTROL_DIR / f"rf_telemetry_{secrets.token_hex(10)}.json"
            stop.unlink(missing_ok=True)
            telem.unlink(missing_ok=True)
            control.write_text(json.dumps({"revision": 0}), encoding="utf-8")
            env["DR_EXPLOIT_RF_STOP_FILE"] = str(stop)
            env["DR_EXPLOIT_RF_CONTROL_FILE"] = str(control)
            env["DR_EXPLOIT_RF_TELEMETRY_FILE"] = str(telem)
            env["DR_EXPLOIT_RF_INSTANCE_ID"] = instance_id
            self.trades_path = Path(env.get("DR_EXPLOIT_RF_TRADES_PATH", str(RISE_FALL_TRADES_PATH)))
            self.trades_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_prefix = str(log_prefix or "")
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            proc = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
            self.state = ProcessState(
                process=proc,
                started_at=time.time(),
                command=command,
                settings=dict(settings),
                stop_file=str(stop),
                live_control_file=str(control),
                telemetry_file=str(telem),
                run_id=time.strftime("rf_%Y%m%d_%H%M%S"),
            )
            self._append("process started")
            self.reader_thread = threading.Thread(target=self._reader, args=(proc,), daemon=True)
            self.reader_thread.start()

    def _reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._append(line)
        code = proc.wait()
        with self.lock:
            self.state.exit_code = code
        self._append(f"process exited with code {code}")

    def running(self) -> bool:
        with self.lock:
            return self.state.process is not None and self.state.process.poll() is None

    def stop(self) -> None:
        with self.lock:
            if not self.running():
                raise RuntimeError("Rise/Fall is not running")
            self.state.stop_requested = True
            path = Path(self.state.stop_file)
        path.write_text(f"graceful-stop {time.time()}\n", encoding="utf-8")

    def live(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            if not self.running():
                raise RuntimeError("Rise/Fall is not running")
            path = Path(self.state.live_control_file)
            action = str(payload.get("action", "apply"))
            if action not in {"apply", "pause", "resume"}:
                raise ValueError("Unknown Rise/Fall live action")
            settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            if settings:
                merged = dict(self.state.settings)
                merged.update(settings)
                merged["live_confirmation"] = "LIVE" if merged.get("mode") == "live" else ""
                validated = validate_rise_fall_session(merged)
                allowed = {"capital","split","max_open","trades_per_signal","split_delay","compound","duration","duration_unit","decision_mode","direction_policy","min_confidence","min_edge","min_return_pct","cooldown_ticks","quote_refresh_ticks","lookback","trend_weight","momentum_weight","reversal_weight","breakout_weight","loss_policy","loss_count","profit_target","loss_limit","win_target"}
                outgoing = {k: validated[k] for k in allowed if k in validated}
                self.state.settings.update(outgoing)
                if "split" in outgoing:
                    outgoing["split_count"] = outgoing.pop("split")
            else:
                outgoing = {}
            self.state.live_revision += 1
            data = {"revision": self.state.live_revision, "action": action, "settings": outgoing, "issued_at": datetime.now(timezone.utc).isoformat()}
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
            return data

    def _telemetry(self) -> Dict[str, Any]:
        path = Path(self.state.telemetry_file) if self.state.telemetry_file else None
        if not path or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def status(self) -> Dict[str, Any]:
        with self.lock:
            running = self.running()
            return {
                "running": running,
                "pid": self.state.process.pid if self.state.process else None,
                "exit_code": self.state.exit_code,
                "started_at": self.state.started_at,
                "runtime_seconds": max(0, int(time.time() - self.state.started_at)) if self.state.started_at else 0,
                "settings": self.state.settings,
                "stop_requested": self.state.stop_requested,
                "last_error": self.state.last_error,
                "telemetry": self._telemetry(),
                "live_control_revision": self.state.live_revision,
                "history": read_trades(100, self.trades_path),
            }

    def logs_after(self, after: int) -> Dict[str, Any]:
        with self.lock:
            lines = [{"id": i, "text": t} for i, t in self.state.lines if i > after]
            last = self.state.lines[-1][0] if self.state.lines else after
            return {"lines": lines, "last_id": last}

RISE_FALL_MANAGER = RiseFallProcessManager()

MANAGER = BotProcessManager()


def parse_dashboard(lines: Iterable[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"positions": []}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("DR EXPLOIT CONTROL"):
            parts = [part.strip() for part in stripped.split("|")]
            if len(parts) >= 3:
                result["mode"], result["state"] = parts[1], parts[2]
        elif stripped.startswith("Account:"):
            match = re.search(r"Account:\s+(\S+)\s+Balance:\s+([^\s]+)\s+(\S+)", stripped)
            if match:
                result.update(account=match.group(1), balance=match.group(2), currency=match.group(3))
        elif stripped.startswith("Market:"):
            match = re.search(r"Market:\s+(\S+)\s+Quote:\s+(.+)$", stripped)
            if match:
                result.update(market=match.group(1), quote=match.group(2).strip())
        elif stripped.startswith("Capital:"):
            match = re.search(r"Capital:\s+([^\s]+)\s+Effective:\s+([^\s]+)\s+Stake/slot:\s+([^\s]+)", stripped)
            if match:
                result.update(capital=match.group(1), effective_capital=match.group(2), stake_per_slot=match.group(3))
        elif stripped.startswith("Open:"):
            match = re.search(r"Open:\s+(\d+)/(\d+).*Available slots:\s+(\d+).*Split:\s+(\d+)", stripped)
            if match:
                result.update(open_trades=int(match.group(1)), max_open=int(match.group(2)), available_slots=int(match.group(3)), split=int(match.group(4)))
        elif stripped.startswith("Strategy:"):
            result["strategy_line"] = stripped.removeprefix("Strategy:").strip()
        elif stripped.startswith("Take profit:"):
            result["take_profit_line"] = stripped.removeprefix("Take profit:").strip()
        elif stripped.startswith("Wins:"):
            match = re.search(r"Wins:\s+(\d+)\s+Losses:\s+(\d+)\s+Win rate:\s+([^%]+)%\s+Consecutive losses:\s+(\d+)", stripped)
            if match:
                result.update(wins=int(match.group(1)), losses=int(match.group(2)), win_rate=match.group(3), consecutive_losses=int(match.group(4)))
        elif stripped.startswith("Realized P/L:"):
            match = re.search(r"Realized P/L:\s+([^\s]+)\s+Open P/L:\s+([^\s]+)\s+Signal:\s+(.+)$", stripped)
            if match:
                result.update(realized_profit=match.group(1), open_profit=match.group(2), signal=match.group(3).strip())
        elif stripped.startswith("Runtime:"):
            result["runtime_line"] = stripped
            reconnect_match = re.search(r"Reconnects:\s+(\d+)", stripped)
            if reconnect_match:
                result["reconnects"] = int(reconnect_match.group(1))
        elif stripped.startswith("Instance:"):
            match = re.search(r"Instance:\s+(\S+).*External account positions ignored:\s+(\d+).*LINK:\s+(\S+)", stripped)
            if match:
                result.update(instance_id=match.group(1), external_positions=int(match.group(2)), link_state=match.group(3))
        elif stripped.startswith("Candles:"):
            result["candle_status_line"] = stripped
            m = re.search(r"Candles:\s+(\d+)/(\d+) ticks \| Detected:\s+([^|]+)\| score=([0-9.]+) \| Confirm=(\S+) \| Pending=([^:|]+):([^|]+)", stripped)
            if m:
                result.update(candle_mode="ticks", candle_progress=int(m.group(1)), candle_ticks=int(m.group(2)), candle_progress_label=f"{m.group(1)} / {m.group(2)} ticks", candle_detected=m.group(3).strip(), candle_pattern_score=float(m.group(4)), candle_confirmation=m.group(5), candle_pending_pattern=m.group(6).strip(), candle_pending_direction=m.group(7).strip())
            else:
                m = re.search(r"Candles:\s+(\d+)/(\d+) sec \(([^)]*)\) \| Detected:\s+([^|]+)\| score=([0-9.]+) \| Confirm=(\S+) \| Pending=([^:|]+):([^|]+)", stripped)
                if m:
                    result.update(candle_mode="time", candle_elapsed_seconds=int(m.group(1)), candle_seconds=int(m.group(2)), candle_progress_label=f"{m.group(1)} / {m.group(2)} sec · {m.group(3)}", candle_detected=m.group(4).strip(), candle_pattern_score=float(m.group(5)), candle_confirmation=m.group(6), candle_pending_pattern=m.group(7).strip(), candle_pending_direction=m.group(8).strip())
        elif stripped.startswith("LIVE CANDLE"):
            result["candle_live_line"] = stripped
            m = re.search(r"O=([-0-9.]+) H=([-0-9.]+) L=([-0-9.]+) C=([-0-9.]+) body=([0-9.]+)% upper=([0-9.]+)% lower=([0-9.]+)% candidates=(.*)$", stripped)
            if m:
                result.update(candle_live_open=m.group(1), candle_live_high=m.group(2), candle_live_low=m.group(3), candle_live_close=m.group(4), candle_live_body_pct=m.group(5), candle_live_upper_pct=m.group(6), candle_live_lower_pct=m.group(7), candle_live_candidates=m.group(8).strip())
        elif stripped.startswith("CANDLE OHLC"):
            result["candle_ohlc_line"] = stripped
            m = re.search(r"O=([-0-9.]+) H=([-0-9.]+) L=([-0-9.]+) C=([-0-9.]+) body=([0-9.]+)% upper=([0-9.]+)% lower=([0-9.]+)%", stripped)
            if m:
                result.update(candle_open=m.group(1), candle_high=m.group(2), candle_low=m.group(3), candle_close=m.group(4), candle_body_pct=m.group(5), candle_upper_pct=m.group(6), candle_lower_pct=m.group(7))
        elif stripped.startswith("Reconnect guard:"):
            match = re.search(
                r"Reconnect guard:\s+(\S+)\s+\|\s+attempts=(\d+)\s+failures=(\d+)\s+success=(\d+)\s+next=([0-9.]+)s(?:\s+\|\s+last_error=(.*))?$",
                stripped,
            )
            if match:
                result.update(
                    auto_reconnect=match.group(1),
                    reconnect_attempts=int(match.group(2)),
                    reconnect_failures=int(match.group(3)),
                    reconnects=int(match.group(4)),
                    reconnect_next_delay=float(match.group(5)),
                    reconnect_last_error=(match.group(6) or "").strip(),
                )
        elif stripped.startswith("Position sync:"):
            match = re.search(
                r"Position sync:\s+managed=(\d+)\s+subscribed=(\d+)\s+external=(\d+)(?:\s+opening=(\d+))?",
                stripped,
            )
            if match:
                result.update(
                    managed_positions=int(match.group(1)),
                    subscribed_positions=int(match.group(2)),
                    external_positions=int(match.group(3)),
                    opening_positions=int(match.group(4) or 0),
                )
        elif stripped.startswith("#"):
            # V2.18: TP labels can contain spaces (for example
            # "SMART-TICKS a=3/f=1"). Parse the TP field lazily until P/L
            # instead of assuming it is one whitespace-free token.
            match = re.search(
                r"#(\d+)\s+(\S+)\s+stake=([^\s]+)\s+TP=(.+?)\s+"
                r"P/L=([^\s]+)\s+status=(\S+)"
                r"(?:\s+peak=([^\s]+)\s+floor=([^\s]+))?"
                r"(?:\s+worst=([^\s]+))?"
                r"(?:\s+loss_watch=(\S+))?",
                stripped,
            )
            if match:
                result["positions"].append({
                    "contract_id": match.group(1), "direction": match.group(2), "stake": match.group(3),
                    "take_profit": match.group(4).strip(), "profit": match.group(5), "status": match.group(6),
                    "peak_profit": match.group(7) or "0", "trailing_floor": match.group(8) or "0",
                    "worst_profit": match.group(9) or "0", "loss_watch": match.group(10) or "off",
                })
    # Reconciliation metadata lets the UI distinguish an actual flat account
    # from a display/parser mismatch. The authoritative managed count is the
    # Open: X/Y dashboard line produced directly from active_positions.
    result.setdefault("managed_positions", int(result.get("open_trades", len(result["positions"]))))
    result.setdefault("subscribed_positions", len(result["positions"]))
    result["position_rows"] = len(result["positions"])
    result["position_detail_mismatch"] = result["managed_positions"] != result["position_rows"]
    return result


def read_trades(limit: int = 250, path: Path = TRADES_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"rows": [], "summary": {"trades": 0, "wins": 0, "losses": 0, "profit": "0.00", "win_rate": "0.0"}}
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    profit_total = Decimal("0")
    wins = losses = 0
    for row in rows:
        try:
            profit = Decimal(str(row.get("profit", "0")))
        except InvalidOperation:
            profit = Decimal("0")
        profit_total += profit
        if profit > 0:
            wins += 1
        else:
            losses += 1
    total = len(rows)
    win_rate = Decimal(wins) / Decimal(total) * Decimal("100") if total else Decimal("0")
    return {
        "rows": list(reversed(rows[-limit:])),
        "summary": {
            "trades": total, "wins": wins, "losses": losses,
            "profit": format(profit_total.quantize(Decimal("0.01")), "f"),
            "win_rate": format(win_rate.quantize(Decimal("0.1")), "f"),
        },
    }



class StrategyLabManager:
    """Runs multiple isolated DEMO bot processes for side-by-side strategy testing."""

    MAX_BOTS = 8

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.managers: Dict[str, BotProcessManager] = {}
        self.meta: Dict[str, Dict[str, Any]] = {}
        self.run_id = ""

    @staticmethod
    def _safe_id(value: Any, fallback: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
        return (cleaned or fallback)[:40]

    def any_running(self) -> bool:
        with self.lock:
            return any(manager.running() for manager in self.managers.values())

    def _make_settings(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        strategy = str(raw.get("strategy", "ema_rsi")).strip().lower()
        if strategy not in STRATEGY_IDS:
            raise ValueError(f"Unknown Strategy Lab strategy: {strategy}")
        profile = str(raw.get("profile", "balanced")).strip().lower()
        if profile not in STRATEGY_PROFILE_NAMES:
            raise ValueError(f"Unknown Strategy Lab profile: {profile}")
        if profile != "custom" and profile not in STRATEGY_PROFILES.get(strategy, {}):
            raise ValueError(f"Profile {profile} is unavailable for {strategy}")

        stake = _decimal(raw.get("stake", "1.00"), "Lab stake", minimum=Decimal("0.35"))
        max_open = _integer(raw.get("max_open", 1), "Lab maximum open trades", minimum=1, maximum=50)
        raw_tps = raw.get("trades_per_signal", None)
        if raw_tps in (None, ""):
            legacy_batch_mode = str(raw.get("batch_mode", "one")).strip().lower()
            if legacy_batch_mode not in {"one", "all"}:
                raise ValueError("Lab opening mode must be one or all")
            trades_per_signal = 1 if legacy_batch_mode == "one" else max_open
        else:
            trades_per_signal = _integer(raw_tps, "Lab trades per signal", minimum=1, maximum=50)
            if trades_per_signal > max_open:
                raise ValueError("Lab trades per signal cannot exceed maximum open trades")
        batch_mode = "one" if trades_per_signal == 1 else ("all" if trades_per_signal == max_open else "count")
        try:
            split_delay = float(raw.get("split_delay", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Lab split delay must be a number") from exc
        if split_delay < 0 or split_delay > 3600 or split_delay != split_delay:
            raise ValueError("Lab split delay must be between 0 and 3600 seconds")

        # The Lab UI expresses risk as stake-per-trade. The core bot expresses
        # it as capital/split, so make split == max_open and derive capital to
        # preserve the requested stake for every simultaneous slot.
        lab_capital = (stake * Decimal(max_open)).quantize(Decimal("0.01"))

        multiplier = _integer(raw.get("multiplier", 750), "Lab multiplier", minimum=1, maximum=7500)
        if multiplier not in STEP100_ALLOWED_MULTIPLIERS:
            raise ValueError("Lab multiplier must be 750, 2000, 3500, 5500, or 7500")
        exit_mode = str(raw.get("exit_mode", "hybrid")).strip().lower()
        if exit_mode not in {"fixed", "adaptive", "smart", "hybrid"}:
            raise ValueError("Unknown Lab exit mode")
        tp = _decimal(raw.get("take_profit", "0.10"), "Lab take profit", minimum=Decimal("0.10"))
        tp_max = _decimal(raw.get("take_profit_max", "0.30"), "Lab maximum take profit", minimum=Decimal("0.10"))
        if tp_max < tp:
            tp_max = tp

        base = dict(DEFAULT_SESSION)
        base.update({
            "mode": "demo",
            "strategy": strategy,
            "strategy_profile": "custom",  # isolated config file contains the requested profile
            "capital": format(lab_capital, "f"),
            "split": max_open,
            "max_open": max_open,
            "batch_mode": batch_mode,
            "trades_per_signal": trades_per_signal,
            "split_delay": split_delay,
            "compound": False,
            "loss_policy": "continue",
            "loss_count": 100000,
            "profit_target": "0",
            "loss_limit": "0",
            "win_target": 0,
            "take_profit_mode": exit_mode,
            "take_profit_unit": str(raw.get("take_profit_unit", "amount")),
            "take_profit": format(tp, "f"),
            "take_profit_max": format(tp_max, "f"),
            "take_profit_ticks": int(raw.get("take_profit_ticks", 3)),
            "take_profit_ticks_max": int(raw.get("take_profit_ticks_max", 8)),
            "smart_activation": str(raw.get("smart_activation", "0.15")),
            "smart_profit_floor": str(raw.get("smart_profit_floor", "0.10")),
            "smart_activation_ticks": int(raw.get("smart_activation_ticks", 3)),
            "smart_profit_floor_ticks": int(raw.get("smart_profit_floor_ticks", 1)),
            "smart_min_distance_ticks": int(raw.get("smart_min_distance_ticks", 1)),
            "smart_trail_percent": str(raw.get("smart_trail_percent", "30")),
            "smart_min_distance": str(raw.get("smart_min_distance", "0.05")),
            "smart_adverse_ticks": int(raw.get("smart_adverse_ticks", 3)),
            "smart_adverse_window": int(raw.get("smart_adverse_window", 4)),
            "smart_opposite_signal": _boolean(raw.get("smart_opposite_signal", True)),
            "smart_server_take_profit": str(raw.get("smart_server_take_profit", "2.00")),
            "multiplier": multiplier,
        })
        return validate_session(base)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
            return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _streaks(profits: list[float]) -> tuple[int, int]:
        longest_wins = longest_losses = current_wins = current_losses = 0
        for profit in profits:
            if profit > 0:
                current_wins += 1
                current_losses = 0
                longest_wins = max(longest_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                longest_losses = max(longest_losses, current_losses)
        return longest_wins, longest_losses

    @staticmethod
    def _group_trade_stats(rows: list[Dict[str, Any]], key: str) -> Dict[str, Any]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            name = str(row.get(key) or "UNKNOWN")
            item = grouped.setdefault(name, {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0})
            profit = StrategyLabManager._safe_float(row.get("profit"), 0.0)
            item["trades"] += 1
            item["profit"] += profit
            if profit > 0:
                item["wins"] += 1
            else:
                item["losses"] += 1
        for item in grouped.values():
            item["profit"] = round(item["profit"], 4)
            item["win_rate"] = round((item["wins"] / item["trades"] * 100.0) if item["trades"] else 0.0, 2)
        return grouped

    def _optimization_report(self, bot_id: str) -> Dict[str, Any]:
        meta = dict(self.meta.get(bot_id, {}))
        manager = self.managers.get(bot_id)
        process_status = manager.status() if manager else {}
        settings = dict(process_status.get("settings") or {})
        trades_path = Path(meta.get("trades_path", "")) if meta.get("trades_path") else Path("__missing__")
        rows: list[Dict[str, Any]] = []
        if trades_path.is_file():
            with trades_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        profits = [self._safe_float(row.get("profit"), 0.0) for row in rows]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value <= 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        realized = sum(profits)
        cumulative = peak = max_drawdown = 0.0
        for value in profits:
            cumulative += value
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
        longest_wins, longest_losses = self._streaks(profits)
        durations = [self._safe_float(row.get("duration_seconds"), 0.0) for row in rows]
        stakes = [max(0.0, self._safe_float(row.get("stake"), 0.0)) for row in rows]
        peaks = [self._safe_float(row.get("peak_profit"), 0.0) for row in rows]
        capture_values = []
        for profit, peak_profit in zip(profits, peaks):
            if profit > 0 and peak_profit > 0:
                capture_values.append(min(200.0, max(-200.0, profit / peak_profit * 100.0)))

        config: Dict[str, Any] = {}
        config_path = Path(meta.get("config_path", "")) if meta.get("config_path") else Path("__missing__")
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                config = {}
        strategy_id = str(meta.get("strategy") or settings.get("strategy") or "")
        strategy_config = ((config.get("strategies") or {}).get(strategy_id) or {}) if isinstance(config, dict) else {}

        sanitized_trades = []
        allowed_trade_fields = (
            "closed_at_utc", "direction", "stake", "currency", "multiplier", "take_profit",
            "take_profit_unit", "take_profit_ticks", "entry_spot", "max_favorable_ticks", "worst_ticks", "entry_pattern",
            "exit_mode", "exit_reason", "peak_profit", "trailing_floor", "trailing_floor_ticks", "worst_profit",
            "smart_loss_triggered", "smart_loss_trigger_count", "smart_loss_reset_count", "smart_loss_trough",
            "profit", "result",
            "status", "duration_seconds", "session_profit", "split_count", "max_open_trades",
            "compound", "split_delay_seconds", "strategy", "instance_id",
        )
        for row in rows:
            sanitized_trades.append({key: row.get(key, "") for key in allowed_trade_fields})

        trades_count = len(rows)
        win_count = len(wins)
        loss_count = len(losses)
        sample_quality = "very small" if trades_count < 30 else "small" if trades_count < 100 else "medium" if trades_count < 300 else "large"
        flags = []
        if trades_count < 100:
            flags.append("Sample is still small; avoid optimizing heavily from this run alone.")
        if int(meta.get("trades_per_signal") or (meta.get("max_open") if str(meta.get("batch_mode")) == "all" else 1) or 1) > 1:
            flags.append("Multiple trades can come from the same signal, so trade rows are correlated rather than independent samples.")
        reconnects = int((process_status.get("dashboard") or {}).get("reconnects", 0) or 0)
        if reconnects:
            flags.append(f"The tester reconnected {reconnects} time(s); compare execution behavior with runs that had no disconnects.")
        if longest_losses >= 5:
            flags.append(f"Longest losing streak was {longest_losses}; review whether entry filters should be tightened.")
        if gross_loss > 0 and gross_profit / gross_loss < 1:
            flags.append("Profit factor is below 1.0 in this run.")

        report = {
            "report_version": "2.16",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": self.run_id,
            "tester": {
                "id": bot_id,
                "label": meta.get("label", bot_id),
                "strategy": strategy_id,
                "strategy_label": meta.get("strategy_label", strategy_id),
                "profile": meta.get("profile", "custom"),
                "symbol": read_env().get("DERIV_SYMBOL", "stpRNG") or "stpRNG",
            },
            "execution_settings": {
                "stake_per_trade": meta.get("stake"),
                "max_open": meta.get("max_open"),
                "opening_mode": meta.get("batch_mode"),
                "trades_per_signal": meta.get("trades_per_signal"),
                "split_delay_seconds": meta.get("split_delay"),
                "multiplier": meta.get("multiplier"),
                "exit_mode": meta.get("exit_mode"),
                "take_profit": meta.get("take_profit"),
                "take_profit_max": settings.get("take_profit_max"),
                "take_profit_unit": settings.get("take_profit_unit", "amount"),
                "take_profit_ticks": settings.get("take_profit_ticks"),
                "take_profit_ticks_max": settings.get("take_profit_ticks_max"),
                "tick_tp_server_backup": settings.get("tick_tp_server_backup"),
                "smart_activation": settings.get("smart_activation"),
                "smart_profit_floor": settings.get("smart_profit_floor"),
                "smart_trail_percent": settings.get("smart_trail_percent"),
                "smart_min_distance": settings.get("smart_min_distance"),
                "smart_adverse_ticks": settings.get("smart_adverse_ticks"),
                "smart_adverse_window": settings.get("smart_adverse_window"),
                "smart_opposite_signal": settings.get("smart_opposite_signal"),
                "smart_server_take_profit": settings.get("smart_server_take_profit"),
                "smart_activation_ticks": settings.get("smart_activation_ticks"),
                "smart_profit_floor_ticks": settings.get("smart_profit_floor_ticks"),
                "smart_min_distance_ticks": settings.get("smart_min_distance_ticks"),
                "server_stop_loss": settings.get("server_stop_loss"),
                "smart_loss_enabled": settings.get("smart_loss_enabled"),
                "smart_loss_soft_trigger": settings.get("smart_loss_soft_trigger"),
                "smart_loss_reset_level": settings.get("smart_loss_reset_level"),
                "smart_loss_hard_cap": settings.get("smart_loss_hard_cap"),
                "smart_loss_grace_ticks": settings.get("smart_loss_grace_ticks"),
                "smart_loss_max_watch_ticks": settings.get("smart_loss_max_watch_ticks"),
                "smart_loss_adverse_ticks": settings.get("smart_loss_adverse_ticks"),
                "smart_loss_adverse_window": settings.get("smart_loss_adverse_window"),
                "smart_loss_opposite_signal": settings.get("smart_loss_opposite_signal"),
                "smart_loss_rescue_profit": settings.get("smart_loss_rescue_profit"),
            },
            "strategy_config": strategy_config,
            "performance": {
                "trades": trades_count,
                "wins": win_count,
                "losses": loss_count,
                "win_rate_percent": round((win_count / trades_count * 100.0) if trades_count else 0.0, 2),
                "realized_profit": round(realized, 4),
                "average_trade": round(statistics.mean(profits), 4) if profits else 0.0,
                "median_trade": round(statistics.median(profits), 4) if profits else 0.0,
                "average_win": round(statistics.mean(wins), 4) if wins else 0.0,
                "average_loss": round(statistics.mean(losses), 4) if losses else 0.0,
                "gross_profit": round(gross_profit, 4),
                "gross_loss": round(gross_loss, 4),
                "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
                "best_trade": round(max(profits), 4) if profits else 0.0,
                "worst_trade": round(min(profits), 4) if profits else 0.0,
                "max_drawdown": round(max_drawdown, 4),
                "longest_win_streak": longest_wins,
                "longest_loss_streak": longest_losses,
                "average_duration_seconds": round(statistics.mean(durations), 3) if durations else 0.0,
                "total_stake_turnover": round(sum(stakes), 4),
                "profit_per_100_staked": round((realized / sum(stakes) * 100.0), 4) if sum(stakes) > 0 else 0.0,
                "average_winner_capture_percent": round(statistics.mean(capture_values), 2) if capture_values else None,
                "runtime_seconds": int(process_status.get("runtime_seconds", 0) or 0),
                "reconnects": reconnects,
                "sample_quality": sample_quality,
            },
            "breakdowns": {
                "direction": self._group_trade_stats(rows, "direction"),
                "exit_reason": self._group_trade_stats(rows, "exit_reason"),
                "exit_mode": self._group_trade_stats(rows, "exit_mode"),
                "entry_pattern": self._group_trade_stats([row for row in rows if str(row.get("entry_pattern") or "").strip()], "entry_pattern"),
            },
            "analysis_flags": flags,
            "sanitized_trades": sanitized_trades,
            "privacy": "API token, App ID, demo account ID, and contract IDs are intentionally omitted from this report.",
        }
        return report

    @staticmethod
    def _report_markdown(report: Dict[str, Any]) -> str:
        tester = report["tester"]
        perf = report["performance"]
        settings = report["execution_settings"]
        lines = [
            f"# Strategy Lab Optimization Report — {tester['label']}",
            "",
            f"- Run: `{report['run_id']}`",
            f"- Strategy: **{tester['strategy_label']}** (`{tester['strategy']}`)",
            f"- Profile: **{tester['profile']}**",
            f"- Symbol: `{tester['symbol']}`",
            f"- Stake/trade: **{settings.get('stake_per_trade')}**",
            f"- Max open: **{settings.get('max_open')}**",
            f"- Opening mode: **{settings.get('opening_mode')}**",
            f"- Stagger: **{settings.get('split_delay_seconds')} sec**",
            f"- Multiplier: **x{settings.get('multiplier')}**",
            f"- Exit mode: **{settings.get('exit_mode')}**",
            "",
            "## Performance",
            "",
            f"- Trades: **{perf['trades']}**",
            f"- Wins / losses: **{perf['wins']} / {perf['losses']}**",
            f"- Win rate: **{perf['win_rate_percent']}%**",
            f"- Realized P/L: **{perf['realized_profit']}**",
            f"- Average trade: **{perf['average_trade']}**",
            f"- Average win: **{perf['average_win']}**",
            f"- Average loss: **{perf['average_loss']}**",
            f"- Profit factor: **{perf['profit_factor'] if perf['profit_factor'] is not None else 'N/A (no gross loss)'}**",
            f"- Max drawdown: **{perf['max_drawdown']}**",
            f"- Best / worst: **{perf['best_trade']} / {perf['worst_trade']}**",
            f"- Longest W/L streak: **{perf['longest_win_streak']} / {perf['longest_loss_streak']}**",
            f"- Average duration: **{perf['average_duration_seconds']} sec**",
            f"- Profit per $100 staked: **{perf['profit_per_100_staked']}**",
            f"- Winner capture of peak: **{perf['average_winner_capture_percent'] if perf['average_winner_capture_percent'] is not None else 'N/A'}%**",
            f"- Reconnects: **{perf['reconnects']}**",
            f"- Sample quality: **{perf['sample_quality']}**",
            "",
            "## Exact strategy configuration",
            "",
            "```json",
            json.dumps(report.get("strategy_config", {}), indent=2),
            "```",
            "",
            "## Exit-reason breakdown",
            "",
        ]
        for name, item in (report.get("breakdowns", {}).get("exit_reason", {}) or {}).items():
            lines.append(f"- `{name}`: {item['trades']} trades, {item['win_rate']}% win rate, P/L {item['profit']}")
        pattern_breakdown = report.get("breakdowns", {}).get("entry_pattern", {}) or {}
        if pattern_breakdown:
            lines.extend(["", "## Candlestick entry-pattern breakdown", ""])
            for name, item in pattern_breakdown.items():
                lines.append(f"- `{name}`: {item['trades']} trades, {item['win_rate']}% win rate, P/L {item['profit']}")
        lines.extend(["", "## Analysis flags", ""])
        flags = report.get("analysis_flags") or []
        if flags:
            lines.extend(f"- {flag}" for flag in flags)
        else:
            lines.append("- No automatic flags for this run.")
        lines.extend([
            "",
            "## Privacy",
            "",
            report.get("privacy", "Credentials are omitted."),
            "",
            "Upload this `.md` or the matching `.json` file to ChatGPT when you want the tester compared and tuned.",
            "",
        ])
        return "\n".join(lines)

    def _write_optimization_report(self, bot_id: str) -> None:
        with self.lock:
            meta = self.meta.get(bot_id)
            if not meta:
                return
            report = self._optimization_report(bot_id)
            run_dir = LAB_DIR / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            json_path = run_dir / f"{bot_id}_optimization.json"
            md_path = run_dir / f"{bot_id}_optimization.md"
            json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            md_path.write_text(self._report_markdown(report), encoding="utf-8")
            meta["report_json_path"] = str(json_path)
            meta["report_md_path"] = str(md_path)
            meta["report_ready"] = True

            manager = self.managers.get(bot_id)
            if manager is not None:
                log_data = manager.logs_after(0)
                console_path = run_dir / f"{bot_id}_console.log"
                console_path.write_text("\n".join(str(line.get("text", "")) for line in log_data.get("lines", [])) + "\n", encoding="utf-8")
                meta["console_log_path"] = str(console_path)

            # Refresh an aggregate run summary every time a tester finishes.
            summaries = []
            for other_id, other_meta in self.meta.items():
                path = Path(other_meta.get("report_json_path", "")) if other_meta.get("report_json_path") else None
                if path and path.is_file():
                    try:
                        item = json.loads(path.read_text(encoding="utf-8"))
                        summaries.append({
                            "id": other_id,
                            "label": item["tester"]["label"],
                            "strategy": item["tester"]["strategy"],
                            "profile": item["tester"]["profile"],
                            **item["performance"],
                        })
                    except Exception:
                        pass
            summaries.sort(key=lambda item: float(item.get("realized_profit", 0.0)), reverse=True)
            run_summary = {
                "report_version": "2.16",
                "run_id": self.run_id,
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "testers": summaries,
                "note": "Ranking by realized P/L is descriptive only. Compare sample size, drawdown, profit factor, and correlated multi-open settings before optimizing.",
            }
            (run_dir / "RUN_OPTIMIZATION_SUMMARY.json").write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")
            summary_md = [f"# Strategy Lab Run Summary — {self.run_id}", "", "| Rank | Tester | Strategy | Profile | Trades | Win rate | P/L | Profit factor | Drawdown |", "|---:|---|---|---|---:|---:|---:|---:|---:|"]
            for index, item in enumerate(summaries, start=1):
                pf = item.get("profit_factor")
                summary_md.append(f"| {index} | {item.get('label','')} | {item.get('strategy','')} | {item.get('profile','')} | {item.get('trades',0)} | {item.get('win_rate_percent',0)}% | {item.get('realized_profit',0)} | {pf if pf is not None else 'N/A'} | {item.get('max_drawdown',0)} |")
            summary_md += ["", run_summary["note"], ""]
            (run_dir / "RUN_OPTIMIZATION_SUMMARY.md").write_text("\n".join(summary_md), encoding="utf-8")

    def _watch_and_finalize(self, bot_id: str) -> None:
        manager = self.managers.get(bot_id)
        if manager is None:
            return
        while manager.running():
            time.sleep(0.25)
        time.sleep(0.25)
        try:
            self._write_optimization_report(bot_id)
        except Exception as exc:
            with self.lock:
                meta = self.meta.get(bot_id)
                if meta is not None:
                    meta["report_ready"] = False
                    meta["report_error"] = str(exc)

    def get_report(self, bot_id: str, report_format: str = "json") -> Dict[str, Any]:
        with self.lock:
            meta = self.meta.get(bot_id)
            if meta is None:
                raise ValueError("Strategy Lab bot not found")
            key = "report_md_path" if report_format == "md" else "report_json_path"
            path_value = meta.get(key)
            if not path_value:
                if self.managers.get(bot_id) and self.managers[bot_id].running():
                    raise RuntimeError("Optimization report is created after this tester stops")
                self._write_optimization_report(bot_id)
                path_value = self.meta[bot_id].get(key)
            path = Path(str(path_value))
            if not path.is_file():
                raise RuntimeError("Optimization report is not available yet")
            content = path.read_text(encoding="utf-8")
            return {
                "bot_id": bot_id,
                "format": report_format,
                "filename": path.name,
                "content": content,
            }

    def get_run_summary(self, report_format: str = "md") -> Dict[str, Any]:
        with self.lock:
            if not self.run_id:
                raise RuntimeError("No Strategy Lab run exists yet")
            run_dir = LAB_DIR / self.run_id
            filename = "RUN_OPTIMIZATION_SUMMARY.json" if report_format == "json" else "RUN_OPTIMIZATION_SUMMARY.md"
            path = run_dir / filename
            if not path.is_file():
                # Generate any stopped tester reports that may not have completed their watcher yet.
                for bot_id, manager in self.managers.items():
                    if not manager.running():
                        try:
                            self._write_optimization_report(bot_id)
                        except Exception:
                            pass
            if not path.is_file():
                raise RuntimeError("Run summary is not available until at least one tester has stopped")
            return {"format": report_format, "filename": path.name, "content": path.read_text(encoding="utf-8")}

    def start(self, payload: Dict[str, Any]) -> None:
        values = read_env()
        account_id = values.get("DERIV_ACCOUNT_ID", "").strip()
        if not account_id.upper().startswith(("DOT", "VRTC", "DEMO")):
            raise ValueError("Strategy Lab is DEMO-only. Save a demo Options account ID first.")
        if not values.get("DERIV_API_TOKEN") or not values.get("DERIV_APP_ID"):
            raise ValueError("Save API token, App ID, and demo account ID first")

        bots = payload.get("bots")
        if not isinstance(bots, list) or not bots:
            raise ValueError("Add at least one Strategy Lab bot")
        if len(bots) > self.MAX_BOTS:
            raise ValueError(f"Strategy Lab supports up to {self.MAX_BOTS} bots at once")

        with self.lock:
            if self.any_running():
                raise RuntimeError("Stop the current Strategy Lab run before starting another")
            self.managers = {}
            self.meta = {}
            self.run_id = time.strftime("%Y%m%d_%H%M%S")
            run_dir = LAB_DIR / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            started: list[BotProcessManager] = []
            used_ids: set[str] = set()
            try:
                for index, raw in enumerate(bots, start=1):
                    if not isinstance(raw, dict):
                        raise ValueError("Each Strategy Lab bot must be an object")
                    settings = self._make_settings(raw)
                    strategy = settings["strategy"]
                    profile = str(raw.get("profile", "balanced")).strip().lower()
                    label = str(raw.get("label") or f"Bot {index}").strip()[:60]
                    bot_id = self._safe_id(raw.get("id"), f"bot-{index}")
                    if bot_id in used_ids:
                        bot_id = f"{bot_id}-{index}"
                    used_ids.add(bot_id)

                    config = validate_config(load_config())
                    if profile != "custom":
                        config["strategies"][strategy] = dict(STRATEGY_PROFILES[strategy][profile])
                    config["strategy"] = dict(config["strategies"]["ema_rsi"])
                    config["hammer_strategy"] = dict(config["strategies"]["hammer"])
                    config["multiplier"] = settings["multiplier"]
                    config["take_profit"] = float(Decimal(settings["take_profit"]))

                    config_path = run_dir / f"{bot_id}_config.json"
                    trades_path = run_dir / f"{bot_id}_trades.csv"
                    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

                    manager = BotProcessManager()
                    manager.start(
                        settings,
                        env_overrides={
                            "DR_EXPLOIT_CONFIG_PATH": str(config_path),
                            "DR_EXPLOIT_TRADES_PATH": str(trades_path),
                            "DR_EXPLOIT_INSTANCE_ID": bot_id,
                        },
                        log_prefix=f"LAB {bot_id}",
                    )
                    started.append(manager)
                    self.managers[bot_id] = manager
                    self.meta[bot_id] = {
                        "id": bot_id,
                        "label": label,
                        "strategy": strategy,
                        "strategy_label": next((item["label"] for item in STRATEGY_CATALOG if item["id"] == strategy), strategy),
                        "profile": profile,
                        "stake": settings["stake_per_trade"],
                        "capital": settings["capital"],
                        "max_open": settings["max_open"],
                        "batch_mode": settings["batch_mode"],
                        "trades_per_signal": settings["trades_per_signal"],
                        "split_delay": settings["split_delay"],
                        "multiplier": settings["multiplier"],
                        "exit_mode": settings["take_profit_mode"],
                        "take_profit": settings["take_profit"],
                        "config_path": str(config_path),
                        "trades_path": str(trades_path),
                        "report_ready": False,
                        "report_error": "",
                    }
                    threading.Thread(target=self._watch_and_finalize, args=(bot_id,), daemon=True).start()
                    # Avoid bursting authenticated WebSocket URL requests for parallel testers.
                    time.sleep(2.0)
            except Exception:
                for manager in started:
                    if manager.running():
                        try:
                            manager.stop()
                        except Exception:
                            pass
                raise

    def stop(self, bot_id: str = "") -> None:
        with self.lock:
            if bot_id:
                manager = self.managers.get(bot_id)
                if manager is None:
                    raise ValueError("Strategy Lab bot not found")
                if manager.running():
                    manager.stop()
                return
            found = False
            for manager in self.managers.values():
                if manager.running():
                    found = True
                    manager.stop()
            if not found:
                raise RuntimeError("No Strategy Lab bot is running")

    def status(self) -> Dict[str, Any]:
        with self.lock:
            bots = []
            for bot_id, manager in self.managers.items():
                process_status = manager.status()
                meta = dict(self.meta.get(bot_id, {}))
                trades_path = Path(meta.get("trades_path", "")) if meta.get("trades_path") else Path("__missing__")
                # Do not expose local filesystem paths through the browser status API.
                for private_key in ("config_path", "trades_path", "report_json_path", "report_md_path", "console_log_path"):
                    meta.pop(private_key, None)
                trade_data = read_trades(100000, trades_path)
                summary = trade_data.get("summary", {})
                dashboard = process_status.get("dashboard", {})
                profit = summary.get("profit", dashboard.get("realized_profit", "0.00"))
                wins = summary.get("wins", dashboard.get("wins", 0))
                losses = summary.get("losses", dashboard.get("losses", 0))
                total = summary.get("trades", int(wins or 0) + int(losses or 0))
                bots.append({
                    **meta,
                    "running": process_status.get("running", False),
                    "stop_requested": process_status.get("stop_requested", False),
                    "exit_code": process_status.get("exit_code"),
                    "runtime_seconds": process_status.get("runtime_seconds", 0),
                    "last_error": process_status.get("last_error", ""),
                    "dashboard": dashboard,
                    "trades": total,
                    "wins": wins,
                    "losses": losses,
                    "profit": profit,
                    "win_rate": summary.get("win_rate", dashboard.get("win_rate", "0.0")),
                    "open_profit": dashboard.get("open_profit", "0.00"),
                    "signal": dashboard.get("signal", "WAITING"),
                    "open_trades": dashboard.get("open_trades", 0),
                    "reconnects": dashboard.get("reconnects", 0),
                    "link_state": dashboard.get("link_state", "ONLINE" if process_status.get("running") else "OFFLINE"),
                })
            return {
                "run_id": self.run_id,
                "running": any(bot.get("running") for bot in bots),
                "count": len(bots),
                "max_bots": self.MAX_BOTS,
                "bots": bots,
            }

    def logs_after(self, bot_id: str, after: int) -> Dict[str, Any]:
        with self.lock:
            manager = self.managers.get(bot_id)
            if manager is None:
                return {"lines": [], "last_id": after}
            return manager.logs_after(after)


LAB_MANAGER = StrategyLabManager()


class RiseFallLabManager:
    """Parallel DEMO-only configuration lab for the dedicated Rise/Fall engine."""

    MAX_BOTS = 4

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.managers: Dict[str, RiseFallProcessManager] = {}
        self.meta: Dict[str, Dict[str, Any]] = {}
        self.run_id = ""
        self.auto_thread: Optional[threading.Thread] = None
        self.auto_stop_event = threading.Event()
        self.auto_results: list[Dict[str, Any]] = []
        self.auto_state: Dict[str, Any] = {
            "enabled": False, "running": False, "stop_requested": False,
            "stage": "IDLE", "round": 0, "max_rounds": 0, "target_trades": 0,
            "screen_trades": 30, "refine_trades": 50, "validation_trades": 100,
            "max_minutes_per_round": 30, "started_at": "", "completed_at": "",
            "best_candidate": "", "best_score": None, "message": "Auto Lab idle.",
        }

    @staticmethod
    def _safe_id(value: Any, fallback: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
        return (cleaned or fallback)[:40]

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            value = float(value)
            return value if value == value and value not in (float("inf"), float("-inf")) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _wilson_lower(wins: int, trades: int, z: float = 1.96) -> float:
        if trades <= 0:
            return 0.0
        p = wins / trades
        z2 = z * z
        denom = 1.0 + z2 / trades
        center = p + z2 / (2.0 * trades)
        margin = z * ((p * (1.0 - p) / trades + z2 / (4.0 * trades * trades)) ** 0.5)
        return max(0.0, (center - margin) / denom)

    @staticmethod
    def _streaks(profits: list[float]) -> tuple[int, int]:
        max_w = max_l = cur_w = cur_l = 0
        for profit in profits:
            if profit > 0:
                cur_w += 1; cur_l = 0; max_w = max(max_w, cur_w)
            else:
                cur_l += 1; cur_w = 0; max_l = max(max_l, cur_l)
        return max_w, max_l

    @staticmethod
    def _confidence_band(value: float) -> str:
        if value < 0: return "unavailable"
        if value < .60: return "<60%"
        if value < .65: return "60-65%"
        if value < .70: return "65-70%"
        if value < .75: return "70-75%"
        return "75%+"

    @staticmethod
    def _edge_band(value: float) -> str:
        if value < 0: return "unavailable"
        if value < .10: return "<10%"
        if value < .20: return "10-20%"
        if value < .30: return "20-30%"
        if value < .40: return "30-40%"
        return "40%+"

    @staticmethod
    def _duration_integrity(rows: list[Dict[str, Any]], settings: Dict[str, Any]) -> tuple[list[Dict[str, Any]], int]:
        """Keep only trades whose recorded expiry matches this tester's configured expiry."""
        try:
            expected_duration = int(settings.get("duration", 2))
        except (TypeError, ValueError):
            expected_duration = 2
        expected_unit = str(settings.get("duration_unit", "t") or "t").strip().lower()
        valid: list[Dict[str, Any]] = []
        invalid = 0
        for row in rows:
            try:
                row_duration = int(float(str(row.get("duration", "")).strip()))
            except (TypeError, ValueError):
                invalid += 1
                continue
            row_unit = str(row.get("duration_unit", "") or "").strip().lower()
            if row_duration == expected_duration and row_unit == expected_unit:
                valid.append(row)
            else:
                invalid += 1
        return valid, invalid

    @staticmethod
    def _auto_candidate_key(settings: Dict[str, Any]) -> str:
        keys = ("duration","duration_unit","decision_mode","direction_policy","min_confidence","min_edge",
                "cooldown_ticks","quote_refresh_ticks","lookback","trend_weight","momentum_weight",
                "reversal_weight","breakout_weight")
        parts = []
        for key in keys:
            value = settings.get(key)
            if isinstance(value, float): value = round(value, 4)
            parts.append(f"{key}={value}")
        return "|".join(parts)

    @classmethod
    def _auto_rank_score(cls, item: Dict[str, Any]) -> float:
        p = item.get("performance") or item
        n = int(p.get("trades") or 0)
        if n <= 0:
            return -9999.0
        edge = cls._safe_float(p.get("edge_vs_break_even_pct"), -50.0)
        conservative = cls._safe_float(p.get("conservative_edge_95_pct"), edge - 20.0)
        pf_value = p.get("profit_factor")
        pf = 2.5 if pf_value is None and cls._safe_float(p.get("profit"), 0.0) > 0 else cls._safe_float(pf_value, 0.0)
        roi = cls._safe_float(p.get("return_on_stake_pct"), 0.0)
        dd = cls._safe_float(p.get("max_drawdown_pct_of_stake"), 0.0)
        sample = min(1.0, n / 100.0)
        return round((edge * (0.35 + 0.65 * sample)) + (conservative * sample * 0.8) +
                     ((pf - 1.0) * 8.0) + (roi * 0.10) - (dd * 0.08), 4)

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _auto_seed(self, label: str, stake: str, **overrides: Any) -> Dict[str, Any]:
        seed: Dict[str, Any] = {
            "label": label, "stake": stake, "max_open": 1, "trades_per_signal": 1, "split_delay": 0,
            "duration": 2, "duration_unit": "t", "decision_mode": "best_odds", "direction_policy": "auto",
            "min_confidence": .66, "min_edge": .30, "min_return_pct": 0, "cooldown_ticks": 4,
            "quote_refresh_ticks": 2, "lookback": 20, "trend_weight": .45, "momentum_weight": 2.0,
            "reversal_weight": .05, "breakout_weight": .30,
        }
        seed.update(overrides)
        seed["duration"] = 2
        seed["duration_unit"] = "t"
        return seed

    def _auto_leaderboard(self) -> list[Dict[str, Any]]:
        rows = [dict(x) for x in self.auto_results]
        for item in rows:
            item["auto_score"] = self._auto_rank_score(item)
        rows.sort(key=lambda x: (x.get("performance", {}).get("trades", 0) >= 30, x.get("auto_score", -9999)), reverse=True)
        return rows

    def _auto_generate_candidates(self, round_no: int, stage: str, stake: str) -> list[Dict[str, Any]]:
        if stage == "EXPLORE":
            return [
                self._auto_seed("2T BO Base", stake, decision_mode="best_odds", min_confidence=.64, min_edge=.28, cooldown_ticks=3, lookback=20),
                self._auto_seed("2T BO Selective", stake, decision_mode="best_odds", min_confidence=.68, min_edge=.32, cooldown_ticks=5, lookback=20),
                self._auto_seed("2T Consensus", stake, decision_mode="consensus", min_confidence=.66, min_edge=.30, cooldown_ticks=4, lookback=20, trend_weight=.70, momentum_weight=1.80, reversal_weight=.10, breakout_weight=.40),
                self._auto_seed("2T Reversal", stake, decision_mode="reversal", min_confidence=.66, min_edge=.30, cooldown_ticks=4, lookback=20),
            ]
        leaders = self._auto_leaderboard()
        if stage == "VALIDATE":
            chosen: list[Dict[str, Any]] = []
            seen: set[str] = set()
            for leader in leaders:
                settings = dict(leader.get("settings") or {})
                key = self._auto_candidate_key(settings)
                if key in seen: continue
                seen.add(key)
                chosen.append(self._auto_seed(f"2T Validate {len(chosen)+1}", stake, **settings))
                if len(chosen) >= self.MAX_BOTS: break
            return chosen or [self._auto_seed("2T Validate Base", stake)]
        best = dict(leaders[0].get("settings") or {}) if leaders else self._auto_seed("base", stake)
        conf = self._safe_float(best.get("min_confidence"), .66)
        edge = self._safe_float(best.get("min_edge"), .30)
        lb = int(self._safe_float(best.get("lookback"), 20))
        cd = int(self._safe_float(best.get("cooldown_ticks"), 4))
        mode = str(best.get("decision_mode") or "best_odds")
        common = {k: best[k] for k in ("decision_mode","direction_policy","trend_weight","momentum_weight","reversal_weight","breakout_weight","quote_refresh_ticks") if k in best}
        common["decision_mode"] = mode
        return [
            self._auto_seed(f"2T R{round_no} Looser", stake, **common, min_confidence=round(self._clamp(conf-.02,.50,.90),2), min_edge=round(self._clamp(edge-.02,0,.80),2), lookback=max(6,lb-4), cooldown_ticks=max(1,cd-1)),
            self._auto_seed(f"2T R{round_no} Tighter", stake, **common, min_confidence=round(self._clamp(conf+.02,.50,.90),2), min_edge=round(self._clamp(edge+.02,0,.80),2), lookback=min(80,lb+4), cooldown_ticks=min(20,cd+1)),
            self._auto_seed(f"2T R{round_no} Fast", stake, **common, min_confidence=round(conf,2), min_edge=round(edge,2), lookback=max(8,lb-6), cooldown_ticks=max(1,cd-2)),
            self._auto_seed(f"2T R{round_no} Stable", stake, **common, min_confidence=round(self._clamp(conf+.01,.50,.90),2), min_edge=round(self._clamp(edge+.03,0,.80),2), lookback=min(80,lb+6), cooldown_ticks=min(20,cd+2)),
        ]

    @classmethod
    def _group_stats(cls, rows: list[Dict[str, Any]], key_fn) -> Dict[str, Any]:
        groups: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(key_fn(row))
            item = groups.setdefault(key, {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0, "stake": 0.0})
            profit = cls._safe_float(row.get("profit"), 0.0)
            stake = max(0.0, cls._safe_float(row.get("stake"), 0.0))
            item["trades"] += 1; item["profit"] += profit; item["stake"] += stake
            if profit > 0: item["wins"] += 1
            else: item["losses"] += 1
        for item in groups.values():
            n = item["trades"]
            item["profit"] = round(item["profit"], 4)
            item["win_rate"] = round(item["wins"] / n * 100.0 if n else 0.0, 2)
            item["return_on_stake_pct"] = round(item["profit"] / item["stake"] * 100.0 if item["stake"] else 0.0, 2)
            item.pop("stake", None)
        return groups

    def any_running(self) -> bool:
        with self.lock:
            return bool(self.auto_state.get("running")) or any(manager.running() for manager in self.managers.values())

    def _make_settings(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        stake = _decimal(raw.get("stake", "1.00"), "Rise/Fall Lab stake", minimum=Decimal("0.35"))
        max_open = _integer(raw.get("max_open", 1), "Rise/Fall Lab max open", minimum=1, maximum=10)
        trades_per_signal = _integer(raw.get("trades_per_signal", 1), "Rise/Fall Lab trades per signal", minimum=1, maximum=max_open)
        capital = (stake * Decimal(max_open)).quantize(Decimal("0.01"))
        base = dict(DEFAULT_RISE_FALL_SESSION)
        base.update({
            "mode": "demo",
            "capital": format(capital, "f"),
            "split": max_open,
            "max_open": max_open,
            "trades_per_signal": trades_per_signal,
            "split_delay": raw.get("split_delay", 0),
            "compound": False,
            "duration": raw.get("duration", 2),
            "duration_unit": raw.get("duration_unit", "t"),
            "decision_mode": raw.get("decision_mode", "best_odds"),
            "direction_policy": raw.get("direction_policy", "auto"),
            "min_confidence": raw.get("min_confidence", .64),
            "min_edge": raw.get("min_edge", .28),
            "min_return_pct": raw.get("min_return_pct", 0),
            "cooldown_ticks": raw.get("cooldown_ticks", 4),
            "quote_refresh_ticks": raw.get("quote_refresh_ticks", 2),
            "lookback": raw.get("lookback", 20),
            "trend_weight": raw.get("trend_weight", .45),
            "momentum_weight": raw.get("momentum_weight", 2.0),
            "reversal_weight": raw.get("reversal_weight", .05),
            "breakout_weight": raw.get("breakout_weight", .30),
            "loss_policy": "continue",
            "loss_count": 100000,
            "profit_target": "0",
            "loss_limit": "0",
            "win_target": 0,
            "live_confirmation": "",
        })
        return validate_rise_fall_session(base)

    def _read_rows(self, path: Path) -> list[Dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            with path.open("r", newline="", encoding="utf-8") as handle:
                return list(csv.DictReader(handle))
        except OSError:
            return []

    def _performance(self, rows: list[Dict[str, Any]]) -> Dict[str, Any]:
        profits = [self._safe_float(row.get("profit"), 0.0) for row in rows]
        stakes = [max(0.0, self._safe_float(row.get("stake"), 0.0)) for row in rows]
        wins = sum(1 for p in profits if p > 0)
        losses = len(profits) - wins
        gross_profit = sum(p for p in profits if p > 0)
        gross_loss = abs(sum(p for p in profits if p <= 0))
        total_profit = sum(profits)
        total_stake = sum(stakes)
        cumulative = peak = max_drawdown = 0.0
        for profit in profits:
            cumulative += profit
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
        win_streak, loss_streak = self._streaks(profits)
        break_evens = [self._safe_float(row.get("break_even_pct"), -1.0) for row in rows]
        break_evens = [v for v in break_evens if v >= 0]
        avg_break_even = statistics.fmean(break_evens) if break_evens else None
        n = len(rows)
        win_rate = wins / n * 100.0 if n else 0.0
        wilson = self._wilson_lower(wins, n) * 100.0 if n else 0.0
        edge_vs_break_even = win_rate - avg_break_even if avg_break_even is not None else None
        conservative_edge = wilson - avg_break_even if avg_break_even is not None else None
        pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        roi = total_profit / total_stake * 100.0 if total_stake else 0.0
        dd_pct = max_drawdown / total_stake * 100.0 if total_stake else 0.0
        screen_score = roi - 0.35 * dd_pct + (edge_vs_break_even or 0.0) * 0.6
        if n < 30:
            grade = "INSUFFICIENT"
            quality = "early (<30 trades)"
        elif n < 100:
            quality = "screening (30-99 trades)"
            if pf >= 1.05 and (edge_vs_break_even or -999) > 0: grade = "WATCH"
            elif pf < .90 or (edge_vs_break_even or 0) < -3: grade = "WEAK"
            else: grade = "MIXED"
        else:
            quality = "validation (100+ trades)"
            if pf >= 1.10 and conservative_edge is not None and conservative_edge > 0: grade = "PROMISING"
            elif pf < .95 or (edge_vs_break_even or 0) < 0: grade = "WEAK"
            else: grade = "MIXED"
        return {
            "trades": n, "wins": wins, "losses": losses,
            "win_rate": round(win_rate, 2), "wilson_lower_95": round(wilson, 2),
            "profit": round(total_profit, 4), "gross_profit": round(gross_profit, 4), "gross_loss": round(gross_loss, 4),
            "profit_factor": None if pf >= 999 else round(pf, 4),
            "average_trade": round(total_profit / n, 4) if n else 0.0,
            "total_staked": round(total_stake, 4), "return_on_stake_pct": round(roi, 2),
            "max_drawdown": round(max_drawdown, 4), "max_drawdown_pct_of_stake": round(dd_pct, 2),
            "longest_win_streak": win_streak, "longest_loss_streak": loss_streak,
            "average_break_even_pct": round(avg_break_even, 3) if avg_break_even is not None else None,
            "edge_vs_break_even_pct": round(edge_vs_break_even, 3) if edge_vs_break_even is not None else None,
            "conservative_edge_95_pct": round(conservative_edge, 3) if conservative_edge is not None else None,
            "screen_score": round(screen_score, 3), "grade": grade, "sample_quality": quality,
        }

    def _report(self, bot_id: str) -> Dict[str, Any]:
        meta = dict(self.meta.get(bot_id, {}))
        if not meta:
            raise ValueError("Rise/Fall Lab tester not found")
        path = Path(meta["trades_path"])
        rows = self._read_rows(path)
        settings = dict(meta.get("settings") or {})
        valid_rows, invalid_duration_trades = self._duration_integrity(rows, settings)
        perf = self._performance(valid_rows)
        sanitized = []
        allowed = ("closed_utc","opened_utc","direction","result","stake","profit","duration","duration_unit","decision_mode","direction_policy","signal_confidence","signal_edge","proposal_return_pct","break_even_pct","combined_score","rsi","momentum_up","momentum_down","range_position","volatility_step","lookback","min_confidence","min_edge","min_return_pct","trend_weight","momentum_weight","reversal_weight","breakout_weight")
        for row in valid_rows[-500:]:
            sanitized.append({k: row.get(k, "") for k in allowed})
        flags = []
        if invalid_duration_trades:
            flags.append(f"Excluded {invalid_duration_trades} trade(s) because recorded duration did not match configured {settings.get('duration')}{settings.get('duration_unit')}. These rows do not affect ranking.")
        if perf["trades"] < 30: flags.append("Sample is too small for configuration selection; collect at least 30 trades for screening and preferably 100+ for validation.")
        if perf["edge_vs_break_even_pct"] is not None and perf["edge_vs_break_even_pct"] < 0: flags.append("Observed win rate is below the average proposal break-even rate.")
        if perf["conservative_edge_95_pct"] is not None and perf["conservative_edge_95_pct"] <= 0: flags.append("The 95% Wilson lower win-rate bound does not clear proposal break-even; observed edge is not statistically secure.")
        if perf["longest_loss_streak"] >= 8: flags.append("Long loss streak observed; keep compounding off and use strict session risk limits outside the lab.")
        return {
            "report_type": "rise_fall_lab_optimization",
            "report_version": WEB_UI_VERSION,
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tester": {k: meta.get(k) for k in ("id","label")},
            "settings": settings,
            "performance": perf,
            "duration_integrity": {"expected": f"{settings.get('duration')}{settings.get('duration_unit')}", "valid_trades": len(valid_rows), "invalid_trades": invalid_duration_trades},
            "breakdowns": {
                "direction": self._group_stats(valid_rows, lambda r: r.get("direction") or "UNKNOWN"),
                "duration": self._group_stats(valid_rows, lambda r: f"{r.get('duration','?')}{r.get('duration_unit','') or ''}"),
                "decision_mode": self._group_stats(valid_rows, lambda r: r.get("decision_mode") or "UNKNOWN"),
                "confidence_band": self._group_stats(valid_rows, lambda r: self._confidence_band(self._safe_float(r.get("signal_confidence"), -1.0))),
                "edge_band": self._group_stats(valid_rows, lambda r: self._edge_band(self._safe_float(r.get("signal_edge"), -1.0))),
            },
            "analysis_flags": flags,
            "recent_sanitized_trades": sanitized,
            "privacy": "API token, App ID, account ID and Deriv contract IDs are omitted. Multiplier trades/configuration are not included.",
        }

    @staticmethod
    def _markdown(report: Dict[str, Any]) -> str:
        p = report["performance"]; s = report["settings"]; t = report["tester"]
        lines = [
            f"# Rise/Fall Lab Optimization — {t.get('label') or t.get('id')}", "",
            f"- Run: `{report['run_id']}`", f"- Duration: **{s.get('duration')}{s.get('duration_unit')}**", f"- Decision mode: **{s.get('decision_mode')}**", f"- Direction: **{s.get('direction_policy')}**",
            f"- Minimum confidence / edge: **{s.get('min_confidence')} / {s.get('min_edge')}**", f"- Lookback / cooldown: **{s.get('lookback')} / {s.get('cooldown_ticks')} ticks**", "",
            "## Performance", "", f"- Trades: **{p['trades']}**", f"- Wins / losses: **{p['wins']} / {p['losses']}**", f"- Win rate: **{p['win_rate']}%**", f"- Average proposal break-even: **{p['average_break_even_pct']}%**", f"- Observed edge vs break-even: **{p['edge_vs_break_even_pct']} pp**", f"- 95% Wilson lower edge vs break-even: **{p['conservative_edge_95_pct']} pp**", f"- P/L: **{p['profit']}**", f"- Profit factor: **{p['profit_factor']}**", f"- Return on total stake: **{p['return_on_stake_pct']}%**", f"- Max drawdown: **{p['max_drawdown']}**", f"- Longest W/L streak: **{p['longest_win_streak']} / {p['longest_loss_streak']}**", f"- Lab grade: **{p['grade']}**", f"- Sample quality: **{p['sample_quality']}**", "",
            "## Exact Rise/Fall configuration", "", "```json", json.dumps(s, indent=2), "```", "",
            "## Analysis flags", "",
        ]
        lines += [f"- {x}" for x in report.get("analysis_flags") or ["No automatic flags."]]
        lines += ["", "## Breakdowns", "", "```json", json.dumps(report.get("breakdowns", {}), indent=2), "```", "", "## Privacy", "", report.get("privacy", ""), ""]
        return "\n".join(lines)

    def _write_report(self, bot_id: str) -> None:
        with self.lock:
            if bot_id not in self.meta: return
            report = self._report(bot_id)
            run_dir = RF_LAB_DIR / self.run_id; run_dir.mkdir(parents=True, exist_ok=True)
            jp = run_dir / f"{bot_id}_rf_optimization.json"; mp = run_dir / f"{bot_id}_rf_optimization.md"
            jp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            mp.write_text(self._markdown(report), encoding="utf-8")
            self.meta[bot_id]["report_json_path"] = str(jp); self.meta[bot_id]["report_md_path"] = str(mp); self.meta[bot_id]["report_ready"] = True
            self._write_summary()

    def _write_summary(self) -> None:
        if not self.run_id: return
        run_dir = RF_LAB_DIR / self.run_id; run_dir.mkdir(parents=True, exist_ok=True)
        testers = []
        for bot_id, meta in self.meta.items():
            rows = self._read_rows(Path(meta["trades_path"]))
            settings = dict(meta.get("settings") or {})
            valid_rows, invalid_duration_trades = self._duration_integrity(rows, settings)
            testers.append({"id": bot_id, "label": meta.get("label"), "settings": settings, "invalid_duration_trades": invalid_duration_trades, **self._performance(valid_rows)})
        testers.sort(key=lambda x: (x.get("trades", 0) >= 30, x.get("screen_score", -999)), reverse=True)
        eligible = [x for x in testers if x.get("trades", 0) >= 30]
        recommendation = {
            "status": "COLLECT_MORE_DATA" if not eligible else "SCREENING_ONLY",
            "top_candidate": eligible[0]["id"] if eligible else "",
            "message": "No tester has 30 trades yet." if not eligible else "Top candidate is descriptive only. Prefer 100+ trades and a positive conservative edge before treating a setup as validated.",
        }
        summary = {"report_type":"rise_fall_lab_run_summary","report_version":WEB_UI_VERSION,"run_id":self.run_id,"generated_at":datetime.now(timezone.utc).isoformat(),"testers":testers,"recommendation":recommendation,"note":"Rise/Fall Lab only. Multiplier Strategy Lab data is intentionally excluded."}
        (run_dir/"RISE_FALL_LAB_SUMMARY.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
        lines=[f"# Rise/Fall Lab Run Summary — {self.run_id}","","| Rank | Tester | Duration | Mode | Direction | Trades | Invalid Dur | Win % | BE % | Edge pp | PF | P/L | DD | Grade |","|---:|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for i,x in enumerate(testers,1):
            st=x.get("settings") or {}; lines.append(f"| {i} | {x.get('label','')} | {st.get('duration')}{st.get('duration_unit','')} | {st.get('decision_mode','')} | {st.get('direction_policy','')} | {x.get('trades',0)} | {x.get('invalid_duration_trades',0)} | {x.get('win_rate',0)} | {x.get('average_break_even_pct','')} | {x.get('edge_vs_break_even_pct','')} | {x.get('profit_factor','')} | {x.get('profit',0)} | {x.get('max_drawdown',0)} | {x.get('grade','')} |")
        lines += ["", recommendation["message"], "", summary["note"], ""]
        (run_dir/"RISE_FALL_LAB_SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")

    def _watch(self, bot_id: str) -> None:
        manager = self.managers.get(bot_id)
        if manager is None: return
        while manager.running(): time.sleep(.25)
        time.sleep(.25)
        try: self._write_report(bot_id)
        except Exception as exc:
            with self.lock:
                if bot_id in self.meta:
                    self.meta[bot_id]["report_error"] = str(exc); self.meta[bot_id]["report_ready"] = False

    def _verify_lab_ready(self) -> None:
        values = read_env(); account = values.get("DERIV_ACCOUNT_ID", "").strip()
        if not account.upper().startswith(("DOT", "VRTC", "DEMO")):
            raise ValueError("Rise/Fall Lab is DEMO-only. Save a demo Options account ID first.")
        if not values.get("DERIV_API_TOKEN") or not values.get("DERIV_APP_ID"):
            raise ValueError("Save API token, App ID, and demo account ID first")
        if RISE_FALL_MANAGER.running():
            raise RuntimeError("Stop the main Rise/Fall engine before starting Rise/Fall Lab. The two Rise/Fall runtimes are intentionally isolated.")

    def _launch_auto_batch(self, candidates: list[Dict[str, Any]], round_no: int, stage: str) -> list[str]:
        run_dir = RF_LAB_DIR / self.run_id; run_dir.mkdir(parents=True, exist_ok=True)
        started_ids: list[str] = []
        started_managers: list[RiseFallProcessManager] = []
        try:
            for index, raw in enumerate(candidates[:self.MAX_BOTS], 1):
                if self.auto_stop_event.is_set(): break
                settings = self._make_settings(raw)
                if int(settings.get("duration", 0)) != 2 or settings.get("duration_unit") != "t":
                    raise ValueError("Auto Lab candidate rejected: Auto Lab is locked to 2 ticks")
                label = str(raw.get("label") or f"Auto R{round_no} Test {index}").strip()[:60]
                slug = self._safe_id(label, f"candidate-{index}")
                bot_id = self._safe_id(f"auto-r{round_no}-{index}-{slug}", f"auto-r{round_no}-{index}")
                suffix = 2
                while bot_id in self.managers:
                    bot_id = self._safe_id(f"auto-r{round_no}-{index}-{slug}-{suffix}", f"auto-r{round_no}-{index}-{suffix}")
                    suffix += 1
                trades = run_dir / f"{bot_id}_trades.csv"
                manager = RiseFallProcessManager()
                manager.start(settings, env_overrides={"DR_EXPLOIT_RF_TRADES_PATH":str(trades)}, instance_id=f"rf-auto-{bot_id}", log_prefix=f"RF AUTO {bot_id}")
                with self.lock:
                    self.managers[bot_id] = manager
                    self.meta[bot_id] = {"id":bot_id,"label":label,"settings":dict(settings),"trades_path":str(trades),"report_ready":False,"report_error":"","auto":True,"auto_round":round_no,"auto_stage":stage}
                started_ids.append(bot_id); started_managers.append(manager)
                threading.Thread(target=self._watch, args=(bot_id,), daemon=True).start()
                time.sleep(2.0)
        except Exception:
            for manager in started_managers:
                if manager.running():
                    try: manager.stop()
                    except Exception: pass
            raise
        return started_ids

    def _collect_auto_round(self, bot_ids: list[str]) -> None:
        existing = {str(x.get("id")) for x in self.auto_results}
        for bot_id in bot_ids:
            if bot_id in existing: continue
            meta = dict(self.meta.get(bot_id) or {})
            if not meta: continue
            settings = dict(meta.get("settings") or {})
            rows = self._read_rows(Path(meta.get("trades_path", "")))
            valid_rows, invalid_duration_trades = self._duration_integrity(rows, settings)
            perf = self._performance(valid_rows)
            item = {"id":bot_id,"label":meta.get("label"),"round":meta.get("auto_round"),"stage":meta.get("auto_stage"),"settings":settings,"invalid_duration_trades":invalid_duration_trades,"performance":perf}
            item["auto_score"] = self._auto_rank_score(item)
            self.auto_results.append(item)

    def _best_auto_result(self) -> Optional[Dict[str, Any]]:
        board = self._auto_leaderboard()
        if not board: return None
        screened = [x for x in board if int((x.get("performance") or {}).get("trades") or 0) >= int(self.auto_state.get("screen_trades") or 30)]
        return screened[0] if screened else board[0]

    def _write_auto_artifacts(self) -> None:
        if not self.run_id: return
        run_dir = RF_LAB_DIR / self.run_id; run_dir.mkdir(parents=True, exist_ok=True)
        board = self._auto_leaderboard(); best = self._best_auto_result()
        if best:
            self.auto_state["best_candidate"] = best.get("id", ""); self.auto_state["best_score"] = best.get("auto_score")
        state_payload = {"report_type":"rise_fall_auto_lab","report_version":WEB_UI_VERSION,"run_id":self.run_id,"generated_at":datetime.now(timezone.utc).isoformat(),"auto":dict(self.auto_state),"leaderboard":board,"note":"DEMO-only 2T optimizer. Duration-mismatched trades are excluded."}
        (run_dir / "AUTO_LAB_STATE.json").write_text(json.dumps(state_payload, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
        lines=[f"# Rise/Fall 2T Auto Lab — {self.run_id}","",f"- Stage: **{self.auto_state.get('stage')}**",f"- Round: **{self.auto_state.get('round')} / {self.auto_state.get('max_rounds')}**",f"- Best candidate: **{self.auto_state.get('best_candidate') or 'Need data'}**","","| Rank | Candidate | Round | Stage | Trades | Invalid Dur | Win % | BE % | Edge pp | 95% Edge | PF | P/L | DD | Auto Score |","|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for i,item in enumerate(board,1):
            perf=item.get("performance") or {}; lines.append(f"| {i} | {item.get('label','')} | {item.get('round','')} | {item.get('stage','')} | {perf.get('trades',0)} | {item.get('invalid_duration_trades',0)} | {perf.get('win_rate',0)} | {perf.get('average_break_even_pct','')} | {perf.get('edge_vs_break_even_pct','')} | {perf.get('conservative_edge_95_pct','')} | {perf.get('profit_factor','')} | {perf.get('profit',0)} | {perf.get('max_drawdown',0)} | {item.get('auto_score','')} |")
        lines += ["", "Auto Lab is DEMO-only. A top-ranked candidate is not treated as validated unless it clears the validation sample and conservative-edge checks.", ""]
        (run_dir / "AUTO_LAB_LEADERBOARD.md").write_text("\n".join(lines), encoding="utf-8")
        if best:
            settings=dict(best.get("settings") or {}); settings["mode"]="demo"; settings["duration"]=2; settings["duration_unit"]="t"; settings["compound"]=False
            perf=best.get("performance") or {}
            validated=(int(perf.get("trades") or 0)>=int(self.auto_state.get("validation_trades") or 100) and self._safe_float(perf.get("conservative_edge_95_pct"),-999)>0 and self._safe_float(perf.get("profit_factor"),0)>=1.10)
            preset={"format":"control-nexus-rise-fall-preset","version":1,"app_version":WEB_UI_VERSION,"exported_at":datetime.now(timezone.utc).isoformat(),"name":"RF Auto Lab Best 2T","category":"2T","priority":"Validated Auto Lab candidate" if validated else "Auto Lab research candidate","description":f"Auto Lab 2T candidate from {self.run_id}. Trades={perf.get('trades',0)}, PF={perf.get('profit_factor')}, edge={perf.get('edge_vs_break_even_pct')} pp. {'Validation checks passed.' if validated else 'Keep in DEMO until validation checks pass.'}","preset":{"saved_at":datetime.now(timezone.utc).isoformat(),"settings":settings},"research":{"auto_score":best.get("auto_score"),"performance":perf,"validated":validated},"privacy":"Credentials, API tokens, account IDs and contract IDs are not included."}
            (run_dir / "RF_AUTO_LAB_BEST_2T.json").write_text(json.dumps(preset, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    def _auto_loop(self, config: Dict[str, Any]) -> None:
        completed_normally=False
        try:
            max_rounds=int(config["max_rounds"]); stake=str(config["stake"])
            for round_no in range(1,max_rounds+1):
                if self.auto_stop_event.is_set(): break
                stage="EXPLORE" if round_no==1 else ("VALIDATE" if round_no==max_rounds else "REFINE")
                target=int(config["screen_trades"] if stage=="EXPLORE" else config["validation_trades"] if stage=="VALIDATE" else config["refine_trades"])
                with self.lock: self.auto_state.update({"stage":stage,"round":round_no,"target_trades":target,"message":f"{stage.title()} round {round_no}: collecting {target} valid 2T trades per candidate."})
                bot_ids=self._launch_auto_batch(self._auto_generate_candidates(round_no,stage,stake),round_no,stage)
                deadline=time.time()+float(config["max_minutes_per_round"])*60.0
                while bot_ids and not self.auto_stop_event.is_set():
                    all_finished=True
                    for bot_id in bot_ids:
                        manager=self.managers.get(bot_id); meta=self.meta.get(bot_id) or {}
                        if manager is None: continue
                        settings=dict(meta.get("settings") or {}); rows=self._read_rows(Path(meta.get("trades_path",""))); valid_rows,invalid_duration=self._duration_integrity(rows,settings)
                        if invalid_duration>0 and manager.running() and not manager.state.stop_requested:
                            try: manager.stop()
                            except Exception: pass
                            meta["duration_error"]=f"Excluded {invalid_duration} mismatched-duration trade(s); tester stopped."
                        elif len(valid_rows)>=target and manager.running() and not manager.state.stop_requested:
                            try: manager.stop()
                            except Exception: pass
                        if manager.running(): all_finished=False
                    if all_finished: break
                    if time.time()>=deadline:
                        for bot_id in bot_ids:
                            manager=self.managers.get(bot_id)
                            if manager and manager.running():
                                try: manager.stop()
                                except Exception: pass
                        with self.lock: self.auto_state["message"]=f"{stage.title()} round {round_no} reached its time cap; draining open 2T contracts."
                        break
                    time.sleep(2.0)
                drain_deadline=time.time()+90.0
                while time.time()<drain_deadline and any(self.managers.get(i) and self.managers[i].running() for i in bot_ids): time.sleep(1.0)
                self._collect_auto_round(bot_ids)
                try: self._write_summary()
                except Exception: pass
                self._write_auto_artifacts()
            completed_normally=not self.auto_stop_event.is_set()
        except Exception as exc:
            with self.lock: self.auto_state["message"]=f"Auto Lab stopped on error: {exc}"; self.auto_state["error"]=str(exc)
        finally:
            for manager in list(self.managers.values()):
                if manager.running():
                    try: manager.stop()
                    except Exception: pass
            with self.lock:
                self.auto_state["running"]=False; self.auto_state["stage"]="COMPLETE" if completed_normally else "STOPPED"; self.auto_state["completed_at"]=datetime.now(timezone.utc).isoformat()
                self.auto_state["message"]="Auto Lab complete. Best 2T preset and leaderboard are ready." if completed_normally else (self.auto_state.get("message") or "Auto Lab stopped by user.")
            try: self._write_auto_artifacts()
            except Exception: pass

    def start_auto(self, payload: Dict[str, Any]) -> None:
        self._verify_lab_ready()
        stake=_decimal(payload.get("stake","1.00"),"Auto Lab stake",minimum=Decimal("0.35")); max_rounds=_integer(payload.get("max_rounds",4),"Auto Lab rounds",minimum=3,maximum=8)
        screen_trades=_integer(payload.get("screen_trades",30),"Auto Lab screen trades",minimum=30,maximum=300); refine_trades=_integer(payload.get("refine_trades",50),"Auto Lab refine trades",minimum=screen_trades,maximum=500); validation_trades=_integer(payload.get("validation_trades",100),"Auto Lab validation trades",minimum=refine_trades,maximum=1000); max_minutes=_integer(payload.get("max_minutes_per_round",30),"Auto Lab max minutes per round",minimum=5,maximum=240)
        config={"stake":format(stake,"f"),"max_rounds":max_rounds,"screen_trades":screen_trades,"refine_trades":refine_trades,"validation_trades":validation_trades,"max_minutes_per_round":max_minutes}
        with self.lock:
            if self.any_running(): raise RuntimeError("Stop the current Rise/Fall Lab run before starting Auto Lab")
            self.managers={}; self.meta={}; self.auto_results=[]; self.auto_stop_event.clear(); self.run_id="auto_"+time.strftime("%Y%m%d_%H%M%S"); (RF_LAB_DIR/self.run_id).mkdir(parents=True,exist_ok=True)
            self.auto_state={"enabled":True,"running":True,"stop_requested":False,"stage":"STARTING","round":0,"max_rounds":max_rounds,"target_trades":screen_trades,"screen_trades":screen_trades,"refine_trades":refine_trades,"validation_trades":validation_trades,"max_minutes_per_round":max_minutes,"started_at":datetime.now(timezone.utc).isoformat(),"completed_at":"","best_candidate":"","best_score":None,"message":"Starting 2T Auto Lab...","error":""}
            self.auto_thread=threading.Thread(target=self._auto_loop,args=(config,),daemon=True,name="rf-auto-lab"); self.auto_thread.start()

    def stop_auto(self) -> None:
        with self.lock:
            if not self.auto_state.get("running") and not any(m.running() for m in self.managers.values()): raise RuntimeError("Auto Lab is not running")
            self.auto_stop_event.set(); self.auto_state["stop_requested"]=True; self.auto_state["message"]="Stopping Auto Lab; draining open 2T contracts."; managers=list(self.managers.values())
        for manager in managers:
            if manager.running():
                try: manager.stop()
                except Exception: pass

    def get_auto_best_preset(self) -> Dict[str, Any]:
        with self.lock:
            if not self.run_id: raise RuntimeError("No Rise/Fall Auto Lab run exists yet")
            self._write_auto_artifacts(); path=RF_LAB_DIR/self.run_id/"RF_AUTO_LAB_BEST_2T.json"
            if not path.is_file(): raise RuntimeError("Auto Lab does not have enough data to create a best preset yet")
            return {"filename":path.name,"content":path.read_text(encoding="utf-8")}

    def start(self, payload: Dict[str, Any]) -> None:
        self._verify_lab_ready()
        bots = payload.get("bots")
        if not isinstance(bots, list) or not bots: raise ValueError("Add at least one Rise/Fall Lab tester")
        if len(bots) > self.MAX_BOTS: raise ValueError(f"Rise/Fall Lab supports up to {self.MAX_BOTS} simultaneous testers")
        with self.lock:
            if self.any_running(): raise RuntimeError("Stop the current Rise/Fall Lab run before starting another")
            self.auto_stop_event.clear(); self.auto_results = []
            self.auto_state = {
                "enabled": False, "running": False, "stop_requested": False, "stage": "IDLE", "round": 0, "max_rounds": 0,
                "target_trades": 0, "screen_trades": 30, "refine_trades": 50, "validation_trades": 100, "max_minutes_per_round": 30,
                "started_at": "", "completed_at": "", "best_candidate": "", "best_score": None, "message": "Auto Lab idle.",
            }
            self.managers = {}; self.meta = {}; self.run_id = time.strftime("%Y%m%d_%H%M%S")
            run_dir = RF_LAB_DIR / self.run_id; run_dir.mkdir(parents=True, exist_ok=True)
            started=[]; used=set()
            try:
                for index, raw in enumerate(bots, 1):
                    if not isinstance(raw, dict): raise ValueError("Each Rise/Fall Lab tester must be an object")
                    settings = self._make_settings(raw)
                    bot_id = self._safe_id(raw.get("id"), f"rf-{index}")
                    if bot_id in used: bot_id=f"{bot_id}-{index}"
                    used.add(bot_id); label=str(raw.get("label") or f"RF Tester {index}").strip()[:60]
                    trades = run_dir / f"{bot_id}_trades.csv"
                    manager = RiseFallProcessManager()
                    manager.start(settings, env_overrides={"DR_EXPLOIT_RF_TRADES_PATH":str(trades)}, instance_id=f"rf-lab-{bot_id}", log_prefix=f"RF LAB {bot_id}")
                    started.append(manager); self.managers[bot_id]=manager
                    self.meta[bot_id]={"id":bot_id,"label":label,"settings":dict(settings),"trades_path":str(trades),"report_ready":False,"report_error":""}
                    threading.Thread(target=self._watch,args=(bot_id,),daemon=True).start()
                    # Avoid bursting several authenticated-URL requests at the same instant.
                    time.sleep(2.0)
            except Exception:
                for manager in started:
                    if manager.running():
                        try: manager.stop()
                        except Exception: pass
                raise

    def stop(self, bot_id: str = "") -> None:
        with self.lock:
            if not bot_id and self.auto_state.get("running"):
                self.auto_stop_event.set(); self.auto_state["stop_requested"] = True
            if bot_id:
                manager=self.managers.get(bot_id)
                if manager is None: raise ValueError("Rise/Fall Lab tester not found")
                if manager.running(): manager.stop()
                return
            found=False
            for manager in self.managers.values():
                if manager.running(): found=True; manager.stop()
            if not found: raise RuntimeError("No Rise/Fall Lab tester is running")

    def status(self) -> Dict[str, Any]:
        with self.lock:
            bots=[]
            for bot_id, manager in self.managers.items():
                meta=dict(self.meta.get(bot_id,{})); path=Path(meta.get("trades_path","")); rows=self._read_rows(path); settings=dict(meta.get("settings") or {}); valid_rows,invalid_duration_trades=self._duration_integrity(rows,settings); perf=self._performance(valid_rows); ps=manager.status(); telem=ps.get("telemetry") or {}
                for key in ("trades_path","report_json_path","report_md_path"): meta.pop(key,None)
                bots.append({**meta,**perf,"invalid_duration_trades":invalid_duration_trades,"running":ps.get("running",False),"stop_requested":ps.get("stop_requested",False),"exit_code":ps.get("exit_code"),"runtime_seconds":ps.get("runtime_seconds",0),"last_error":ps.get("last_error",""),"open_trades":len(telem.get("positions") or []),"current_signal":(telem.get("decision") or {}).get("direction") or (telem.get("decision") or {}).get("action") or "WAIT","quote":telem.get("quote")})
            eligible=[b for b in bots if b.get("trades",0)>=30]
            eligible.sort(key=lambda b:b.get("screen_score",-999),reverse=True)
            auto=dict(self.auto_state); board=self._auto_leaderboard()[:10] if auto.get("enabled") else []; top=auto.get("best_candidate") if auto.get("enabled") else (eligible[0].get("id") if eligible else "")
            return {"run_id":self.run_id,"running":bool(auto.get("running")) or any(b.get("running") for b in bots),"count":len(bots),"max_bots":self.MAX_BOTS,"bots":bots,"top_candidate":top or "","auto":auto,"auto_leaderboard":board,"data_message":auto.get("message") if auto.get("enabled") else "Need 30+ trades per tester for screening; 100+ preferred for validation."}

    def logs_after(self, bot_id: str, after: int) -> Dict[str, Any]:
        with self.lock:
            manager=self.managers.get(bot_id)
            return manager.logs_after(after) if manager else {"lines":[],"last_id":after}

    def get_report(self, bot_id: str, report_format: str = "md") -> Dict[str, Any]:
        with self.lock:
            if bot_id not in self.meta: raise ValueError("Rise/Fall Lab tester not found")
            key="report_json_path" if report_format=="json" else "report_md_path"
            path_value=self.meta[bot_id].get(key)
            if not path_value:
                if self.managers.get(bot_id) and self.managers[bot_id].running(): raise RuntimeError("Stop this tester before downloading its final optimization report")
                self._write_report(bot_id); path_value=self.meta[bot_id].get(key)
            path=Path(str(path_value))
            return {"bot_id":bot_id,"format":report_format,"filename":path.name,"content":path.read_text(encoding="utf-8")}

    def get_run_summary(self, report_format: str = "md") -> Dict[str, Any]:
        with self.lock:
            if not self.run_id: raise RuntimeError("No Rise/Fall Lab run exists yet")
            self._write_summary(); run_dir=RF_LAB_DIR/self.run_id
            name="RISE_FALL_LAB_SUMMARY.json" if report_format=="json" else "RISE_FALL_LAB_SUMMARY.md"; path=run_dir/name
            return {"format":report_format,"filename":name,"content":path.read_text(encoding="utf-8")}


RF_LAB_MANAGER = RiseFallLabManager()

class AIOptimizerManager:
    """Shared Ollama-assisted optimizer for Rise/Fall and Multiplier research/live selection.

    The model only proposes bounded configuration changes or selects an already
    validated configuration. The local Python process validates everything and
    remains the only component allowed to start/stop testers or trading engines.
    """

    ENGINES = ("rise_fall", "multiplier")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings = load_optimizer_settings(AI_OPTIMIZER_SETTINGS_PATH)
        self.states: Dict[str, Dict[str, Any]] = {engine: self._fresh_state(engine) for engine in self.ENGINES}
        self.threads: Dict[str, Optional[threading.Thread]] = {engine: None for engine in self.ENGINES}
        self.stop_events: Dict[str, threading.Event] = {engine: threading.Event() for engine in self.ENGINES}
        self.last_plans: Dict[str, Dict[str, Any]] = {engine: {} for engine in self.ENGINES}
        self.history = self._load_history()
        self.ollama_models: list[str] = []

    @staticmethod
    def _fresh_state(engine: str) -> Dict[str, Any]:
        return {
            "engine": engine,
            "running": False,
            "mode": "advisor",
            "stage": "IDLE",
            "round": 0,
            "target_trades": 0,
            "started_at": "",
            "completed_at": "",
            "message": "AI Optimizer idle.",
            "last_error": "",
            "last_analysis_at": "",
            "last_response_id": "",
            "last_summary": "",
            "current_best_id": "",
            "current_best_label": "",
            "current_score": None,
            "adaptive_selected_id": "",
            "live_selected_id": "",
            "live_session_trades": 0,
            "live_session_profit": 0.0,
            "api_calls": 0,
        }

    def _load_history(self) -> Dict[str, Any]:
        try:
            data = json.loads(AI_OPTIMIZER_HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            data = {}
        result = {"rise_fall": [], "multiplier": []}
        for engine in self.ENGINES:
            values = data.get(engine) if isinstance(data.get(engine), list) else []
            result[engine] = [dict(x) for x in values if isinstance(x, dict)][-1000:]
        return result

    def _save_history(self) -> None:
        payload = {engine: self.history.get(engine, [])[-1000:] for engine in self.ENGINES}
        tmp = AI_OPTIMIZER_HISTORY_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, AI_OPTIMIZER_HISTORY_PATH)

    @staticmethod
    def _is_demo_account() -> bool:
        account_id = str(read_env().get("DERIV_ACCOUNT_ID", "") or "").upper()
        return account_id.startswith(("DOT", "VRTC", "DEMO"))

    @staticmethod
    def _safe_number(value: Any, default: float = 0.0) -> float:
        try:
            v = float(value)
            if v == v and v not in (float("inf"), float("-inf")):
                return v
        except (TypeError, ValueError):
            pass
        return default

    def _public_history(self, engine: str, limit: int = 100) -> list[Dict[str, Any]]:
        rows = []
        for item in self.history.get(engine, [])[-max(1, limit):]:
            row = dict(item)
            row.pop("raw_report", None)
            rows.append(row)
        return rows

    def _validated_history(self, engine: str) -> list[Dict[str, Any]]:
        return [dict(x) for x in self.history.get(engine, []) if bool(x.get("validated"))]

    def status(self) -> Dict[str, Any]:
        with self.lock:
            provider = ollama_provider_status(str(self.settings.get("ollama_url") or "http://localhost:11434"))
            provider["models"] = list(self.ollama_models)
            return {
                "settings": deepcopy(self.settings),
                "provider": provider,
                "engines": {engine: dict(self.states[engine]) for engine in self.ENGINES},
                "validated_counts": {engine: len(self._validated_history(engine)) for engine in self.ENGINES},
                "live_eligible_counts": {engine: len(self._live_eligible(engine)) for engine in self.ENGINES},
                "last_plans": {engine: dict(self.last_plans.get(engine) or {}) for engine in self.ENGINES},
                "history": {engine: self._public_history(engine, 20) for engine in self.ENGINES},
            }

    def configure(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            incoming = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
            merged = deepcopy(self.settings)
            for key, value in incoming.items():
                if key in {"rise_fall", "multiplier"} and isinstance(value, dict):
                    merged[key] = {**merged.get(key, {}), **value}
                else:
                    merged[key] = value
            self.settings = save_optimizer_settings(AI_OPTIMIZER_SETTINGS_PATH, merged)
            return self.status()

    def _rf_snapshot(self, purpose: str) -> Dict[str, Any]:
        lab = RF_LAB_MANAGER.status()
        current = load_rise_fall_session()
        main = RISE_FALL_MANAGER.status()
        bots = []
        for raw in lab.get("bots") or []:
            bots.append({
                "id": raw.get("id"), "label": raw.get("label"), "trades": raw.get("trades"),
                "wins": raw.get("wins"), "losses": raw.get("losses"), "win_rate": raw.get("win_rate"),
                "profit": raw.get("profit"), "profit_factor": raw.get("profit_factor"),
                "max_drawdown": raw.get("max_drawdown"), "edge_vs_break_even_pct": raw.get("edge_vs_break_even_pct"),
                "conservative_edge_95_pct": raw.get("conservative_edge_95_pct"),
                "average_break_even_pct": raw.get("average_break_even_pct"),
                "invalid_duration_trades": raw.get("invalid_duration_trades"), "settings": raw.get("settings") or {},
            })
        return {
            "engine": "rise_fall", "purpose": purpose, "hard_constraints": {
                "duration": 2, "duration_unit": "t", "auto_lab_demo_only": True, "max_open": 1,
                "compound": False, "stake_is_not_ai_controlled": True,
            },
            "current_session": {k: current.get(k) for k in (
                "mode", "duration", "duration_unit", "decision_mode", "direction_policy", "min_confidence", "min_edge",
                "cooldown_ticks", "quote_refresh_ticks", "lookback", "trend_weight", "momentum_weight",
                "reversal_weight", "breakout_weight")},
            "main_running": bool(main.get("running")), "lab": {"run_id": lab.get("run_id"), "bots": bots},
            "recent_optimizer_history": self._public_history("rise_fall", 60),
            "validated_presets": self._validated_history("rise_fall")[-20:],
            "thresholds": {"min_profit_factor": self.settings["min_profit_factor"], "min_edge_pp": self.settings["min_edge_pp"]},
        }

    def _mult_snapshot(self, purpose: str) -> Dict[str, Any]:
        lab = LAB_MANAGER.status()
        current = load_last_session()
        main = MANAGER.status()
        bots = []
        for raw in lab.get("bots") or []:
            bots.append({
                "id": raw.get("id"), "label": raw.get("label"), "strategy": raw.get("strategy"),
                "profile": raw.get("profile"), "trades": raw.get("trades"), "wins": raw.get("wins"),
                "losses": raw.get("losses"), "win_rate": raw.get("win_rate"), "profit": raw.get("profit"),
                "multiplier": raw.get("multiplier"), "exit_mode": raw.get("exit_mode"), "take_profit": raw.get("take_profit"),
            })
        return {
            "engine": "multiplier", "purpose": purpose, "hard_constraints": {
                "auto_lab_demo_only": True, "stake_is_not_ai_controlled": True, "capital_is_not_ai_controlled": True,
                "allowed_multipliers": list(STEP100_ALLOWED_MULTIPLIERS), "max_concurrent_ai_tests": 4,
            },
            "current_session": {k: current.get(k) for k in (
                "mode", "strategy", "strategy_profile", "multiplier", "take_profit_mode", "take_profit_unit", "take_profit",
                "take_profit_max", "take_profit_ticks", "take_profit_ticks_max", "smart_activation", "smart_profit_floor",
                "smart_activation_ticks", "smart_profit_floor_ticks", "smart_min_distance_ticks", "smart_server_take_profit")},
            "strategy_catalog": [{"id": x.get("id"), "label": x.get("label")} for x in STRATEGY_CATALOG],
            "main_running": bool(main.get("running")), "lab": {"run_id": lab.get("run_id"), "bots": bots},
            "recent_optimizer_history": self._public_history("multiplier", 60),
            "validated_presets": self._validated_history("multiplier")[-20:],
            "thresholds": {"min_profit_factor": self.settings["min_profit_factor"]},
        }

    def snapshot(self, engine: str, purpose: str = "advisor") -> Dict[str, Any]:
        if engine == "rise_fall":
            return self._rf_snapshot(purpose)
        if engine == "multiplier":
            return self._mult_snapshot(purpose)
        raise ValueError("Unknown AI Optimizer engine")

    def _call(self, engine: str, operation_mode: str, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        settings = deepcopy(self.settings)
        plan = call_ollama_optimizer(
            ollama_url=str(settings.get("ollama_url") or "http://localhost:11434"),
            model=str(settings.get("model") or "qwen3:8b"),
            thinking=str(settings.get("thinking") or "low"),
            keep_alive=str(settings.get("keep_alive") or "10m"),
            engine=engine,
            operation_mode=operation_mode,
            snapshot=snapshot or self.snapshot(engine, operation_mode),
            strategy_ids=STRATEGY_IDS,
            max_candidates=int(settings.get("max_concurrent") or 4),
        )
        with self.lock:
            self.last_plans[engine] = dict(plan)
            state = self.states[engine]
            state["api_calls"] = int(state.get("api_calls") or 0) + 1
            state["last_analysis_at"] = datetime.now(timezone.utc).isoformat()
            state["last_response_id"] = ""
            state["last_summary"] = str(plan.get("summary") or "")
        return plan

    def analyze(self, engine: str) -> Dict[str, Any]:
        if engine not in self.ENGINES:
            raise ValueError("Unknown AI Optimizer engine")
        plan = self._call(engine, "advisor")
        return {"plan": plan, "optimizer": self.status()}

    def test_connection(self) -> Dict[str, Any]:
        result = list_ollama_models(str(self.settings.get("ollama_url") or "http://localhost:11434"))
        self.ollama_models = list(result.get("models") or [])
        configured = str(self.settings.get("model") or "").strip()
        result["model"] = configured
        result["model_installed"] = (configured in self.ollama_models) if configured else False
        return result

    def _live_eligible(self, engine: str) -> list[Dict[str, Any]]:
        required = int(self.settings.get("live_min_validated_trades") or 150)
        min_pf = float(self.settings.get("live_min_profit_factor") or 1.25)
        min_edge = float(self.settings.get("live_min_edge_pp") or 2.0)
        rows = []
        for item in self._validated_history(engine):
            perf = item.get("performance") if isinstance(item.get("performance"), dict) else {}
            if int(perf.get("trades") or 0) < required:
                continue
            if self._safe_number(perf.get("profit_factor"), 0.0) < min_pf:
                continue
            if engine == "rise_fall":
                if self._safe_number(perf.get("edge_vs_break_even_pct"), -999.0) < min_edge:
                    continue
                if self._safe_number(perf.get("profit"), 0.0) <= 0:
                    continue
            else:
                if self._safe_number(perf.get("realized_profit"), 0.0) <= 0:
                    continue
            rows.append(dict(item))
        rows.sort(key=lambda x: self._safe_number(x.get("score"), -9999), reverse=True)
        return rows

    def _rf_score(self, performance: Dict[str, Any]) -> float:
        n = int(performance.get("trades") or 0)
        if n <= 0:
            return -9999.0
        pf = self._safe_number(performance.get("profit_factor"), 0.0)
        edge = self._safe_number(performance.get("edge_vs_break_even_pct"), -50.0)
        conservative = self._safe_number(performance.get("conservative_edge_95_pct"), edge - 20.0)
        profit = self._safe_number(performance.get("profit"), 0.0)
        dd = self._safe_number(performance.get("max_drawdown"), 0.0)
        sample = min(1.0, n / max(100.0, float(self.settings.get("validate_trades") or 150)))
        return round((pf - 1.0) * 12.0 + edge * 0.7 + conservative * 0.8 * sample + profit * 0.04 - dd * 0.03, 5)

    def _mult_score(self, performance: Dict[str, Any]) -> float:
        n = int(performance.get("trades") or 0)
        if n <= 0:
            return -9999.0
        pf = self._safe_number(performance.get("profit_factor"), 0.0)
        p100 = self._safe_number(performance.get("profit_per_100_staked"), 0.0)
        profit = self._safe_number(performance.get("realized_profit"), 0.0)
        drawdown = self._safe_number(performance.get("max_drawdown"), 0.0)
        turnover = max(1.0, self._safe_number(performance.get("total_stake_turnover"), 0.0))
        dd_pct = drawdown / turnover * 100.0
        sample = min(1.0, n / max(100.0, float(self.settings.get("validate_trades") or 150)))
        return round((pf - 1.0) * 12.0 + p100 * 0.55 + profit * 0.03 - dd_pct * 0.30 + sample * 2.0, 5)

    def _validated(self, engine: str, performance: Dict[str, Any], stage: str) -> bool:
        required = int(self.settings.get("validate_trades") or 150)
        if stage == "FINAL":
            required = int(self.settings.get("final_trades") or 250)
        if int(performance.get("trades") or 0) < required:
            return False
        pf = self._safe_number(performance.get("profit_factor"), 0.0)
        if pf < float(self.settings.get("min_profit_factor") or 1.2):
            return False
        if engine == "rise_fall":
            if self._safe_number(performance.get("profit"), 0.0) <= 0:
                return False
            if self._safe_number(performance.get("conservative_edge_95_pct"), -999.0) <= 0:
                return False
            if self._safe_number(performance.get("edge_vs_break_even_pct"), -999.0) < float(self.settings.get("min_edge_pp") or 3.0):
                return False
            return True
        return self._safe_number(performance.get("realized_profit"), 0.0) > 0 and self._safe_number(performance.get("profit_per_100_staked"), -999.0) > 0

    def _append_history(self, engine: str, entries: list[Dict[str, Any]]) -> None:
        with self.lock:
            self.history.setdefault(engine, []).extend(entries)
            self.history[engine] = self.history[engine][-1000:]
            self._save_history()

    def _prepare_plan_candidates(self, engine: str, plan: Dict[str, Any]) -> list[Dict[str, Any]]:
        max_count = int(self.settings.get("max_concurrent") or 4)
        block = self.settings[engine]
        candidates = []
        for raw in (plan.get("candidates") or [])[:max_count]:
            if not isinstance(raw, dict):
                continue
            if engine == "rise_fall":
                candidates.append(sanitize_rf_candidate(raw, stake=str(block.get("stake") or "1.00")))
            else:
                candidates.append(sanitize_multiplier_candidate(
                    raw, strategy_ids=STRATEGY_IDS, stake=str(block.get("stake") or "1.00"),
                    max_open=int(block.get("max_open") or 1), trades_per_signal=int(block.get("trades_per_signal") or 1),
                    split_delay=float(block.get("split_delay") or 0.5),
                ))
        return candidates

    def _initial_candidates(self, engine: str) -> list[Dict[str, Any]]:
        block = self.settings[engine]
        if engine == "rise_fall":
            return initial_rf_candidates(str(block.get("stake") or "1.00"))[: int(self.settings.get("max_concurrent") or 4)]
        return initial_multiplier_candidates(
            STRATEGY_IDS, stake=str(block.get("stake") or "1.00"), max_open=int(block.get("max_open") or 1),
            trades_per_signal=int(block.get("trades_per_signal") or 1), split_delay=float(block.get("split_delay") or 0.5),
        )[: int(self.settings.get("max_concurrent") or 4)]

    def _refine_fallback(self, engine: str, leader: Dict[str, Any]) -> list[Dict[str, Any]]:
        block = self.settings[engine]
        settings = dict(leader.get("settings") or {})
        if engine == "rise_fall":
            return fallback_refine_rf(settings, stake=str(block.get("stake") or "1.00"))[: int(self.settings.get("max_concurrent") or 4)]
        return fallback_refine_multiplier(
            settings, strategy_ids=STRATEGY_IDS, stake=str(block.get("stake") or "1.00"),
            max_open=int(block.get("max_open") or 1), trades_per_signal=int(block.get("trades_per_signal") or 1),
            split_delay=float(block.get("split_delay") or 0.5),
        )[: int(self.settings.get("max_concurrent") or 4)]

    def _stage_candidates(self, engine: str, stage: str, accumulated: list[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if stage == "EXPLORE" or not accumulated:
            return self._initial_candidates(engine), None
        sorted_results = sorted(accumulated, key=lambda x: self._safe_number(x.get("score"), -9999), reverse=True)
        max_count = int(self.settings.get("max_concurrent") or 4)
        if stage in {"VALIDATE", "FINAL"}:
            # Validation must continue candidates that survived the immediately previous stage,
            # rather than resurrecting a lucky tiny-sample Explore result.
            preferred_stage = "REFINE" if stage == "VALIDATE" else "VALIDATE"
            stage_results = [x for x in sorted_results if str(x.get("stage") or "").upper() == preferred_stage]
            source_results = stage_results or sorted_results
            chosen, seen = [], set()
            for item in source_results:
                settings = dict(item.get("settings") or {})
                key = json.dumps(settings, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(settings)
                if len(chosen) >= max_count:
                    break
            return chosen, None
        snapshot = self.snapshot(engine, "auto_lab_refine")
        snapshot["stage"] = stage
        snapshot["leaderboard"] = sorted_results[:20]
        try:
            plan = self._call(engine, "auto_lab", snapshot)
            candidates = self._prepare_plan_candidates(engine, plan)
            if candidates:
                return candidates, plan
        except Exception as exc:
            with self.lock:
                self.states[engine]["message"] = f"AI refine call failed; using bounded local fallback: {exc}"
                self.states[engine]["last_error"] = str(exc)
        return self._refine_fallback(engine, sorted_results[0]), None

    def _collect_rf_batch(self, bot_ids: list[str], candidate_map: Dict[str, Dict[str, Any]], stage: str, run_id: str) -> list[Dict[str, Any]]:
        status = RF_LAB_MANAGER.status()
        by_id = {str(x.get("id")): x for x in status.get("bots") or []}
        entries = []
        for bot_id in bot_ids:
            row = by_id.get(bot_id) or {}
            perf = {k: row.get(k) for k in (
                "trades", "wins", "losses", "win_rate", "profit", "profit_factor", "max_drawdown",
                "average_break_even_pct", "edge_vs_break_even_pct", "conservative_edge_95_pct", "return_on_stake_pct")}
            settings = dict(candidate_map.get(bot_id) or row.get("settings") or {})
            score = self._rf_score(perf)
            entries.append({
                "id": f"{run_id}:{bot_id}", "bot_id": bot_id, "engine": "rise_fall", "stage": stage,
                "label": row.get("label") or settings.get("label") or bot_id, "settings": settings, "performance": perf,
                "score": score, "validated": self._validated("rise_fall", perf, stage),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        return entries

    def _collect_mult_batch(self, bot_ids: list[str], candidate_map: Dict[str, Dict[str, Any]], stage: str, run_id: str) -> list[Dict[str, Any]]:
        entries = []
        for bot_id in bot_ids:
            try:
                report = LAB_MANAGER._optimization_report(bot_id)
            except Exception:
                report = {"performance": {}, "tester": {}}
            perf = dict(report.get("performance") or {})
            settings = dict(candidate_map.get(bot_id) or {})
            score = self._mult_score(perf)
            entries.append({
                "id": f"{run_id}:{bot_id}", "bot_id": bot_id, "engine": "multiplier", "stage": stage,
                "label": (report.get("tester") or {}).get("label") or settings.get("label") or bot_id,
                "settings": settings, "performance": perf, "score": score,
                "validated": self._validated("multiplier", perf, stage), "created_at": datetime.now(timezone.utc).isoformat(),
            })
        return entries

    def _run_batch(self, engine: str, candidates: list[Dict[str, Any]], stage: str, target: int, run_id: str) -> list[Dict[str, Any]]:
        stop_event = self.stop_events[engine]
        run_dir = AI_OPTIMIZER_RUN_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        candidate_map: Dict[str, Dict[str, Any]] = {}
        payload_bots = []
        for index, raw in enumerate(candidates[: int(self.settings.get("max_concurrent") or 4)], 1):
            item = dict(raw)
            bot_id = re.sub(r"[^A-Za-z0-9_-]+", "-", f"ai-{stage.lower()}-{index}-{item.get('label','candidate')}").strip("-")[:40]
            item["id"] = bot_id
            candidate_map[bot_id] = dict(item)
            payload_bots.append(item)
        if not payload_bots:
            raise RuntimeError("AI Optimizer did not produce any valid candidates")
        if engine == "rise_fall":
            if RISE_FALL_MANAGER.running():
                raise RuntimeError("Stop the main Rise/Fall engine before AI Auto Lab")
            if RF_LAB_MANAGER.any_running():
                raise RuntimeError("Stop the current Rise/Fall Lab before AI Auto Lab")
            RF_LAB_MANAGER.start({"bots": payload_bots})
        else:
            if MANAGER.running():
                raise RuntimeError("Stop the main Multiplier bot before AI Auto Lab")
            if LAB_MANAGER.any_running():
                raise RuntimeError("Stop the current Multiplier Strategy Lab before AI Auto Lab")
            LAB_MANAGER.start({"bots": payload_bots})

        bot_ids = list(candidate_map)
        deadline = time.time() + float(self.settings.get("max_minutes_per_stage") or 60) * 60.0
        while not stop_event.is_set():
            lab = RF_LAB_MANAGER.status() if engine == "rise_fall" else LAB_MANAGER.status()
            by_id = {str(x.get("id")): x for x in lab.get("bots") or []}
            all_done = True
            for bot_id in bot_ids:
                row = by_id.get(bot_id) or {}
                running = bool(row.get("running"))
                trades = int(row.get("trades") or 0)
                if running and trades >= target and not row.get("stop_requested"):
                    try:
                        (RF_LAB_MANAGER if engine == "rise_fall" else LAB_MANAGER).stop(bot_id)
                    except Exception:
                        pass
                if running:
                    all_done = False
            if all_done:
                break
            if time.time() >= deadline:
                with self.lock:
                    self.states[engine]["message"] = f"{stage.title()} time cap reached; draining open contracts."
                for bot_id in bot_ids:
                    try:
                        (RF_LAB_MANAGER if engine == "rise_fall" else LAB_MANAGER).stop(bot_id)
                    except Exception:
                        pass
                break
            time.sleep(2.0)
        if stop_event.is_set():
            try:
                (RF_LAB_MANAGER if engine == "rise_fall" else LAB_MANAGER).stop()
            except Exception:
                pass
        drain_deadline = time.time() + 180.0
        while time.time() < drain_deadline:
            lab = RF_LAB_MANAGER.status() if engine == "rise_fall" else LAB_MANAGER.status()
            if not any(bool(x.get("running")) for x in lab.get("bots") or []):
                break
            time.sleep(1.0)
        entries = self._collect_rf_batch(bot_ids, candidate_map, stage, run_id) if engine == "rise_fall" else self._collect_mult_batch(bot_ids, candidate_map, stage, run_id)
        stage_path = run_dir / f"{stage.lower()}_results.json"
        stage_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return entries

    def _write_best_preset(self, engine: str, run_id: str, accumulated: list[Dict[str, Any]]) -> Optional[Path]:
        if not accumulated:
            return None
        # Prefer genuinely validated candidates. If the run ended early, prefer the
        # deepest completed stage before falling back to screening results.
        validated = [x for x in accumulated if bool(x.get("validated"))]
        if validated:
            pool = validated
        else:
            pool = []
            for preferred_stage in ("FINAL", "VALIDATE", "REFINE", "EXPLORE"):
                pool = [x for x in accumulated if str(x.get("stage") or "").upper() == preferred_stage]
                if pool:
                    break
            pool = pool or accumulated
        best = max(pool, key=lambda x: self._safe_number(x.get("score"), -9999))
        run_dir = AI_OPTIMIZER_RUN_DIR / run_id
        now = datetime.now(timezone.utc).isoformat()
        settings = dict(best.get("settings") or {})
        if engine == "rise_fall":
            validated_settings = RF_LAB_MANAGER._make_settings(settings)
            validated_settings["mode"] = "demo"; validated_settings["duration"] = 2; validated_settings["duration_unit"] = "t"; validated_settings["compound"] = False
            payload = {
                "format": "control-nexus-rise-fall-preset", "version": 1, "app_version": WEB_UI_VERSION,
                "exported_at": now, "name": "RF AI Optimizer Best 2T", "category": "2T",
                "priority": "Validated AI candidate" if best.get("validated") else "AI research candidate",
                "description": f"AI Optimizer candidate from {run_id}. Score={best.get('score')}. Keep DEMO until validated.",
                "preset": {"saved_at": now, "settings": validated_settings},
                "research": {"validated": bool(best.get("validated")), "performance": best.get("performance"), "score": best.get("score")},
                "privacy": "Credentials, API tokens, account IDs and contract IDs are not included.",
            }
            path = run_dir / "RF_AI_OPTIMIZER_BEST_2T.json"
        else:
            raw = settings
            lab_settings = LAB_MANAGER._make_settings(raw)
            session = dict(DEFAULT_SESSION)
            session.update({
                "mode": "demo", "strategy": raw.get("strategy", "ema_rsi"), "strategy_profile": raw.get("profile", "balanced"),
                "capital": lab_settings.get("capital"), "split": lab_settings.get("split"), "max_open": lab_settings.get("max_open"),
                "trades_per_signal": lab_settings.get("trades_per_signal"), "split_delay": lab_settings.get("split_delay"), "compound": False,
                "multiplier": lab_settings.get("multiplier"), "take_profit_mode": lab_settings.get("take_profit_mode"),
                "take_profit_unit": lab_settings.get("take_profit_unit"), "take_profit": lab_settings.get("take_profit"),
                "take_profit_max": lab_settings.get("take_profit_max"), "take_profit_ticks": lab_settings.get("take_profit_ticks"),
                "take_profit_ticks_max": lab_settings.get("take_profit_ticks_max"), "smart_activation": lab_settings.get("smart_activation"),
                "smart_profit_floor": lab_settings.get("smart_profit_floor"), "smart_activation_ticks": lab_settings.get("smart_activation_ticks"),
                "smart_profit_floor_ticks": lab_settings.get("smart_profit_floor_ticks"), "smart_min_distance_ticks": lab_settings.get("smart_min_distance_ticks"),
                "smart_server_take_profit": lab_settings.get("smart_server_take_profit"), "loss_policy": "continue", "loss_count": 100000,
                "profit_target": "0", "loss_limit": "0", "win_target": 0,
            })
            session = validate_preset_session(session)
            config = validate_config(load_config())
            strategy = str(raw.get("strategy") or "ema_rsi")
            profile = str(raw.get("profile") or "balanced")
            if profile in STRATEGY_PROFILES.get(strategy, {}):
                config["strategies"][strategy] = dict(STRATEGY_PROFILES[strategy][profile])
            payload = {
                "format": "control-nexus-full-preset", "version": 1, "app_version": WEB_UI_VERSION, "exported_at": now,
                "name": "Multiplier AI Optimizer Best", "preset": {"saved_at": now, "session": session, "config": config},
                "research": {"validated": bool(best.get("validated")), "performance": best.get("performance"), "score": best.get("score")},
                "privacy": "Credentials, API tokens, account IDs and contract IDs are not included.",
            }
            path = run_dir / "MULTIPLIER_AI_OPTIMIZER_BEST.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def _auto_loop(self, engine: str) -> None:
        stop_event = self.stop_events[engine]
        run_id = f"{engine}_ai_{time.strftime('%Y%m%d_%H%M%S')}"
        run_dir = AI_OPTIMIZER_RUN_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        accumulated: list[Dict[str, Any]] = []
        stages = [
            ("EXPLORE", int(self.settings.get("explore_trades") or 30)),
            ("REFINE", int(self.settings.get("refine_trades") or 75)),
            ("VALIDATE", int(self.settings.get("validate_trades") or 150)),
            ("FINAL", int(self.settings.get("final_trades") or 250)),
        ]
        try:
            if not self._is_demo_account():
                raise RuntimeError("AI Auto Lab is DEMO-only. Save a demo Options account first.")
            for round_no, (stage, target) in enumerate(stages, 1):
                if stop_event.is_set():
                    break
                with self.lock:
                    self.states[engine].update({"stage": stage, "round": round_no, "target_trades": target, "message": f"{stage.title()}: targeting {target} trades per candidate."})
                candidates, plan = self._stage_candidates(engine, stage, accumulated)
                if plan:
                    (run_dir / f"{stage.lower()}_ai_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                results = self._run_batch(engine, candidates, stage, target, run_id)
                accumulated.extend(results)
                self._append_history(engine, results)
                if accumulated:
                    best = max(accumulated, key=lambda x: self._safe_number(x.get("score"), -9999))
                    with self.lock:
                        self.states[engine].update({"current_best_id": best.get("id", ""), "current_best_label": best.get("label", ""), "current_score": best.get("score")})
                (run_dir / "leaderboard.json").write_text(json.dumps(sorted(accumulated, key=lambda x: self._safe_number(x.get("score"), -9999), reverse=True), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self._write_best_preset(engine, run_id, accumulated)
            with self.lock:
                self.states[engine]["message"] = "AI Auto Lab complete. Best research preset is ready." if not stop_event.is_set() else "AI Auto Lab stopped."
        except Exception as exc:
            with self.lock:
                self.states[engine]["last_error"] = str(exc)
                self.states[engine]["message"] = f"AI Auto Lab stopped: {exc}"
        finally:
            with self.lock:
                self.states[engine]["running"] = False
                self.states[engine]["stage"] = "COMPLETE" if not stop_event.is_set() else "STOPPED"
                self.states[engine]["completed_at"] = datetime.now(timezone.utc).isoformat()

    def _adaptive_trade_count(self, engine: str) -> int:
        path = RISE_FALL_TRADES_PATH if engine == "rise_fall" else TRADES_PATH
        if not path.is_file():
            return 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return max(0, sum(1 for _ in handle) - 1)
        except OSError:
            return 0

    def _apply_validated(self, engine: str, selected: Dict[str, Any], *, allow_live: bool = False) -> None:
        settings = dict(selected.get("settings") or {})
        if engine == "rise_fall":
            if not RISE_FALL_MANAGER.running():
                raise RuntimeError("Rise/Fall main engine is not running")
            current = RISE_FALL_MANAGER.status().get("settings") or {}
            if str(current.get("mode") or "").lower() == "live" and not allow_live:
                raise RuntimeError("Adaptive auto-apply is disabled for LIVE mode; use AI Trade with explicit LIVE authorization")
            allowed = {k: settings[k] for k in (
                "duration", "duration_unit", "decision_mode", "direction_policy", "min_confidence", "min_edge",
                "cooldown_ticks", "quote_refresh_ticks", "lookback", "trend_weight", "momentum_weight",
                "reversal_weight", "breakout_weight") if k in settings}
            allowed["duration"] = 2; allowed["duration_unit"] = "t"
            RISE_FALL_MANAGER.live({"action": "apply", "settings": allowed})
        else:
            if not MANAGER.running():
                raise RuntimeError("Multiplier main bot is not running")
            current = MANAGER.status().get("settings") or {}
            if str(current.get("mode") or "").lower() == "live" and not allow_live:
                raise RuntimeError("Adaptive auto-apply is disabled for LIVE mode; use AI Trade with explicit LIVE authorization")
            strategy = str(settings.get("strategy") or current.get("strategy") or "ema_rsi")
            profile = str(settings.get("profile") or "balanced")
            config = validate_config(load_config())
            if profile in STRATEGY_PROFILES.get(strategy, {}):
                config["strategies"][strategy] = dict(STRATEGY_PROFILES[strategy][profile])
            live_settings = {
                "multiplier": settings.get("multiplier"), "take_profit_mode": settings.get("exit_mode"),
                "take_profit_unit": settings.get("take_profit_unit"), "take_profit": settings.get("take_profit"),
                "take_profit_max": settings.get("take_profit_max"), "take_profit_ticks": settings.get("take_profit_ticks"),
                "take_profit_ticks_max": settings.get("take_profit_ticks_max"), "smart_activation": settings.get("smart_activation"),
                "smart_profit_floor": settings.get("smart_profit_floor"), "smart_activation_ticks": settings.get("smart_activation_ticks"),
                "smart_profit_floor_ticks": settings.get("smart_profit_floor_ticks"), "smart_min_distance_ticks": settings.get("smart_min_distance_ticks"),
                "smart_server_take_profit": settings.get("smart_server_take_profit"),
            }
            live_settings = {k: v for k, v in live_settings.items() if v is not None}
            MANAGER.apply_live_controls({"action": "apply", "label": "AI Adaptive validated preset", "settings": live_settings, "strategy": strategy, "config": config})

    def _trade_totals(self, engine: str) -> tuple[int, float]:
        path = RISE_FALL_TRADES_PATH if engine == "rise_fall" else TRADES_PATH
        if not path.is_file():
            return 0, 0.0
        count = 0
        profit = 0.0
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle):
                    count += 1
                    for key in ("profit", "realized_profit", "pnl", "net_profit"):
                        if row.get(key) not in (None, ""):
                            profit += self._safe_number(row.get(key), 0.0)
                            break
        except OSError:
            return 0, 0.0
        return count, round(profit, 8)

    def _stop_main_engine(self, engine: str) -> None:
        try:
            if engine == "rise_fall":
                if RISE_FALL_MANAGER.running():
                    RISE_FALL_MANAGER.stop()
            else:
                if MANAGER.running():
                    MANAGER.stop()
        except Exception:
            pass

    def _start_ai_trade_engine(self, engine: str, selected: Dict[str, Any]) -> bool:
        """Start the main engine if needed, preserving user-controlled exposure."""
        demo = self._is_demo_account()
        candidate = dict(selected.get("settings") or {})
        if engine == "rise_fall":
            if RF_LAB_MANAGER.any_running():
                raise RuntimeError("Stop Rise/Fall Lab before AI Trade")
            if RISE_FALL_MANAGER.running():
                return False
            session = dict(load_rise_fall_session())
            session.update({k: candidate[k] for k in (
                "decision_mode", "direction_policy", "min_confidence", "min_edge", "cooldown_ticks",
                "quote_refresh_ticks", "lookback", "trend_weight", "momentum_weight", "reversal_weight", "breakout_weight"
            ) if k in candidate})
            session.update({"mode": "demo" if demo else "live", "duration": 2, "duration_unit": "t", "compound": False})
            # Duplicate the AI Trade session caps into the native engine so the
            # deterministic bot can stop entries even if the optimizer thread is delayed.
            session["loss_limit"] = f"{float(self.settings.get('live_max_loss') or 10.0):.2f}"
            live_target = float(self.settings.get("live_profit_target") or 0.0)
            session["profit_target"] = f"{live_target:.2f}" if live_target > 0 else "0"
            if not demo:
                session["live_confirmation"] = "LIVE"
            settings = validate_rise_fall_session(session)
            RISE_FALL_MANAGER.start(settings)
            save_rise_fall_session(settings)
            return True
        if LAB_MANAGER.any_running():
            raise RuntimeError("Stop Multiplier Strategy Lab before AI Trade")
        if MANAGER.running():
            return False
        session = dict(load_last_session())
        strategy = str(candidate.get("strategy") or session.get("strategy") or "ema_rsi")
        profile = str(candidate.get("profile") or session.get("strategy_profile") or "balanced")
        session.update({
            "mode": "demo" if demo else "live", "compound": False,
            "strategy_engine": "single", "strategy": strategy, "strategy_profile": profile,
            "multiplier": candidate.get("multiplier", session.get("multiplier")),
            "take_profit_mode": candidate.get("exit_mode", session.get("take_profit_mode")),
            "take_profit_unit": candidate.get("take_profit_unit", session.get("take_profit_unit")),
            "take_profit": candidate.get("take_profit", session.get("take_profit")),
            "take_profit_max": candidate.get("take_profit_max", session.get("take_profit_max")),
            "take_profit_ticks": candidate.get("take_profit_ticks", session.get("take_profit_ticks")),
            "take_profit_ticks_max": candidate.get("take_profit_ticks_max", session.get("take_profit_ticks_max")),
            "smart_activation": candidate.get("smart_activation", session.get("smart_activation")),
            "smart_profit_floor": candidate.get("smart_profit_floor", session.get("smart_profit_floor")),
            "smart_activation_ticks": candidate.get("smart_activation_ticks", session.get("smart_activation_ticks")),
            "smart_profit_floor_ticks": candidate.get("smart_profit_floor_ticks", session.get("smart_profit_floor_ticks")),
            "smart_min_distance_ticks": candidate.get("smart_min_distance_ticks", session.get("smart_min_distance_ticks")),
            "smart_server_take_profit": candidate.get("smart_server_take_profit", session.get("smart_server_take_profit")),
        })
        session["loss_limit"] = f"{float(self.settings.get('live_max_loss') or 10.0):.2f}"
        live_target = float(self.settings.get("live_profit_target") or 0.0)
        session["profit_target"] = f"{live_target:.2f}" if live_target > 0 else "0"
        if not demo:
            session["live_confirmation"] = "LIVE"
        if profile in STRATEGY_PROFILES.get(strategy, {}):
            apply_strategy_profile(strategy, profile)
        settings = validate_session(session)
        MANAGER.start(settings)
        save_last_session(settings)
        return True

    def _live_trade_loop(self, engine: str) -> None:
        stop_event = self.stop_events[engine]
        started_main = False
        try:
            if not bool(self.settings.get("allow_live_trading")):
                raise RuntimeError("Enable 'Allow AI Trade' before starting AI Trade mode")
            eligible = self._live_eligible(engine)
            if not eligible:
                raise RuntimeError("AI Trade needs at least one validated candidate that meets the live thresholds")
            selected = eligible[0]
            started_main = self._start_ai_trade_engine(engine, selected)
            if not started_main:
                self._apply_validated(engine, selected, allow_live=True)
            baseline_count, baseline_profit = self._trade_totals(engine)
            last_analysis_count = baseline_count
            with self.lock:
                self.states[engine].update({
                    "stage": "AI_TRADE", "live_selected_id": str(selected.get("id") or ""),
                    "message": f"AI Trade active with validated preset: {selected.get('label') or selected.get('id')}",
                })
            while not stop_event.is_set():
                running = RISE_FALL_MANAGER.running() if engine == "rise_fall" else MANAGER.running()
                if not running:
                    with self.lock:
                        self.states[engine]["message"] = "AI Trade stopped because the trading engine stopped."
                    break
                count, total_profit = self._trade_totals(engine)
                session_trades = max(0, count - baseline_count)
                session_profit = round(total_profit - baseline_profit, 8)
                with self.lock:
                    self.states[engine]["live_session_trades"] = session_trades
                    self.states[engine]["live_session_profit"] = session_profit
                max_loss = float(self.settings.get("live_max_loss") or 10.0)
                profit_target = float(self.settings.get("live_profit_target") or 0.0)
                max_trades = int(self.settings.get("live_max_trades") or 100)
                if session_profit <= -max_loss:
                    self._stop_main_engine(engine)
                    with self.lock:
                        self.states[engine]["message"] = f"AI Trade safety stop: session loss limit reached ({session_profit:.2f})."
                    break
                if profit_target > 0 and session_profit >= profit_target:
                    self._stop_main_engine(engine)
                    with self.lock:
                        self.states[engine]["message"] = f"AI Trade stopped at session profit target ({session_profit:.2f})."
                    break
                if session_trades >= max_trades:
                    self._stop_main_engine(engine)
                    with self.lock:
                        self.states[engine]["message"] = f"AI Trade stopped at {session_trades} session trades."
                    break
                if count - last_analysis_count >= int(self.settings.get("analyze_every") or 25):
                    eligible = self._live_eligible(engine)
                    snapshot = self.snapshot(engine, "live_trade")
                    snapshot["validated_presets"] = eligible[-30:]
                    snapshot["live_session"] = {"trades": session_trades, "profit": session_profit}
                    try:
                        plan = self._call(engine, "live_trade", snapshot)
                        selected_id = str(plan.get("selected_id") or "")
                        next_selected = next((x for x in eligible if str(x.get("id")) == selected_id), None)
                        if plan.get("action") == "SELECT_PRESET" and next_selected is not None:
                            self._apply_validated(engine, next_selected, allow_live=True)
                            with self.lock:
                                self.states[engine]["live_selected_id"] = selected_id
                                self.states[engine]["message"] = f"AI Trade selected validated preset: {next_selected.get('label') or selected_id}"
                        else:
                            with self.lock:
                                self.states[engine]["message"] = str(plan.get("summary") or "AI Trade held the current validated preset.")
                    except Exception as exc:
                        if bool(self.settings.get("live_fail_closed", True)):
                            self._stop_main_engine(engine)
                            raise RuntimeError(f"Ollama optimizer failed; fail-closed stopped trading: {exc}") from exc
                        with self.lock:
                            self.states[engine]["message"] = f"Ollama unavailable; holding current validated preset: {exc}"
                    last_analysis_count = count
                stop_event.wait(2.0)
        except Exception as exc:
            with self.lock:
                self.states[engine]["last_error"] = str(exc)
                self.states[engine]["message"] = f"AI Trade stopped: {exc}"
        finally:
            if stop_event.is_set():
                self._stop_main_engine(engine)
            with self.lock:
                self.states[engine]["running"] = False
                self.states[engine]["stage"] = "STOPPED"
                self.states[engine]["completed_at"] = datetime.now(timezone.utc).isoformat()

    def _adaptive_loop(self, engine: str) -> None:
        stop_event = self.stop_events[engine]
        last_count = self._adaptive_trade_count(engine)
        try:
            if not self._is_demo_account():
                raise RuntimeError("Adaptive mode is DEMO-only for automatic changes")
            while not stop_event.is_set():
                running = RISE_FALL_MANAGER.running() if engine == "rise_fall" else MANAGER.running()
                if not running:
                    with self.lock:
                        self.states[engine]["message"] = "Adaptive mode is armed; start the DEMO main engine to begin selection."
                    stop_event.wait(5.0)
                    continue
                count = self._adaptive_trade_count(engine)
                delta = count - last_count
                if delta >= int(self.settings.get("analyze_every") or 25):
                    validated = self._validated_history(engine)
                    if not validated:
                        with self.lock:
                            self.states[engine]["message"] = "Adaptive mode needs at least one validated AI Lab candidate."
                        last_count = count
                        continue
                    snapshot = self.snapshot(engine, "adaptive")
                    snapshot["validated_presets"] = validated[-30:]
                    plan = self._call(engine, "adaptive", snapshot)
                    selected_id = str(plan.get("selected_id") or "")
                    selected = next((x for x in validated if str(x.get("id")) == selected_id), None)
                    if plan.get("action") == "SELECT_PRESET" and selected is not None:
                        self._apply_validated(engine, selected)
                        with self.lock:
                            self.states[engine]["adaptive_selected_id"] = selected_id
                            self.states[engine]["message"] = f"Adaptive selected validated preset: {selected.get('label') or selected_id}"
                    else:
                        with self.lock:
                            self.states[engine]["message"] = str(plan.get("summary") or "Adaptive held the current preset.")
                    last_count = count
                stop_event.wait(2.0)
        except Exception as exc:
            with self.lock:
                self.states[engine]["last_error"] = str(exc)
                self.states[engine]["message"] = f"Adaptive stopped: {exc}"
        finally:
            with self.lock:
                self.states[engine]["running"] = False
                self.states[engine]["stage"] = "STOPPED"
                self.states[engine]["completed_at"] = datetime.now(timezone.utc).isoformat()

    def start(self, engine: str, mode: str, confirmation: str = "") -> Dict[str, Any]:
        if engine not in self.ENGINES:
            raise ValueError("Unknown AI Optimizer engine")
        mode = str(mode or self.settings.get(engine, {}).get("mode") or "advisor").strip().lower()
        if mode not in {"advisor", "auto_lab", "adaptive", "live_trade"}:
            raise ValueError("AI Optimizer mode must be advisor, auto_lab, adaptive, or live_trade")
        if mode == "advisor":
            return self.analyze(engine)
        if mode == "live_trade":
            if str(confirmation or "").strip().upper() != "LIVE":
                raise RuntimeError("Type LIVE in the AI Trade confirmation field before starting automated trading")
            if not bool(self.settings.get("allow_live_trading")):
                raise RuntimeError("Enable Allow AI Trade in optimizer settings first")
        with self.lock:
            if mode == "auto_lab":
                other = "multiplier" if engine == "rise_fall" else "rise_fall"
                if self.states[other].get("running") and self.states[other].get("mode") == "auto_lab":
                    raise RuntimeError("Run one AI Auto Lab engine at a time to reduce Deriv/Cloudflare rate-limit bursts")
            if self.states[engine].get("running"):
                raise RuntimeError(f"{engine} AI Optimizer is already running")
            self.stop_events[engine].clear()
            self.states[engine] = self._fresh_state(engine)
            self.states[engine].update({"running": True, "mode": mode, "stage": "STARTING", "started_at": datetime.now(timezone.utc).isoformat(), "message": f"Starting {mode.replace('_',' ')}..."})
            target = self._auto_loop if mode == "auto_lab" else self._live_trade_loop if mode == "live_trade" else self._adaptive_loop
            thread = threading.Thread(target=target, args=(engine,), daemon=True, name=f"ai-optimizer-{engine}-{mode}")
            self.threads[engine] = thread
            thread.start()
            return self.status()

    def stop(self, engine: str) -> Dict[str, Any]:
        if engine not in self.ENGINES:
            raise ValueError("Unknown AI Optimizer engine")
        with self.lock:
            self.stop_events[engine].set()
            self.states[engine]["message"] = "Stopping AI Optimizer..."
        if self.states[engine].get("mode") == "auto_lab":
            try:
                (RF_LAB_MANAGER if engine == "rise_fall" else LAB_MANAGER).stop()
            except Exception:
                pass
        elif self.states[engine].get("mode") == "live_trade":
            self._stop_main_engine(engine)
        return self.status()

    def best_preset(self, engine: str) -> Dict[str, Any]:
        if engine not in self.ENGINES:
            raise ValueError("Unknown AI Optimizer engine")
        pattern = "RF_AI_OPTIMIZER_BEST_2T.json" if engine == "rise_fall" else "MULTIPLIER_AI_OPTIMIZER_BEST.json"
        paths = sorted(AI_OPTIMIZER_RUN_DIR.glob(f"*/{pattern}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not paths:
            raise RuntimeError("No AI Optimizer best preset has been generated yet")
        path = paths[0]
        return {"filename": path.name, "content": path.read_text(encoding="utf-8")}


AI_OPTIMIZER = AIOptimizerManager()


def _read_jsonl_tail(path_value: str, limit: int = 1200) -> list[Dict[str, Any]]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.is_file():
        return []
    wanted = max(1, int(limit))
    # Read backwards in chunks so the 1.5-second dashboard poll does not scan
    # an hour-long diagnostic file from the beginning on every refresh.
    chunks: list[bytes] = []
    newline_count = 0
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            while position > 0 and newline_count <= wanted:
                size = min(65536, position)
                position -= size
                handle.seek(position)
                chunk = handle.read(size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
                if sum(len(part) for part in chunks) >= 8 * 1024 * 1024:
                    break
        text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[Dict[str, Any]] = []
    for line in text.splitlines()[-wanted:]:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _read_csv_tail(path_value: str, limit: int = 100) -> list[Dict[str, Any]]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.is_file():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            return list(deque(csv.DictReader(handle), maxlen=max(1, int(limit))))
    except OSError:
        return []


def _redact_console_line(line: str) -> str:
    text = str(line)
    account_id = read_env().get("DERIV_ACCOUNT_ID", "").strip()
    if account_id:
        text = text.replace(account_id, "[ACCOUNT_REDACTED]")
    text = re.sub(r"contract_id=\d+", "contract_id=[REDACTED]", text)
    text = re.sub(r"#\d+\s+(MULTUP|MULTDOWN)", r"#[REDACTED] \1", text)
    return text


def _selected_strategy_configs(settings: Dict[str, Any]) -> Dict[str, Any]:
    try:
        base_config = normalize_config(load_config())
    except Exception:
        base_config = {"strategies": {}}
    if settings.get("strategy") != "ensemble":
        sid = str(settings.get("strategy") or "")
        return {sid: dict(base_config.get("strategies", {}).get(sid, {}))} if sid else {}
    selected = [item.strip() for item in str(settings.get("ensemble_strategies", "")).split(",") if item.strip()]
    try:
        overrides = json.loads(str(settings.get("ensemble_config_json") or "{}"))
    except json.JSONDecodeError:
        overrides = {}
    result: Dict[str, Any] = {}
    for sid in selected:
        value = overrides.get(sid) if isinstance(overrides, dict) else None
        result[sid] = dict(value) if isinstance(value, dict) else dict(base_config.get("strategies", {}).get(sid, {}))
    return result


def main_diagnostic_snapshot(event_limit: int = 1200, recent_limit: int = 20) -> Dict[str, Any]:
    status = MANAGER.status()
    settings = dict(status.get("settings") or {})
    events = _read_jsonl_tail(str(status.get("diagnostic_path") or ""), event_limit)
    event_counts = Counter(str(item.get("event") or "unknown") for item in events)
    reason_counts = Counter(str(item.get("reason") or "unspecified") for item in events)
    last_event = events[-1] if events else {}
    last_ensemble = {}
    for item in reversed(events):
        if isinstance(item.get("ensemble"), dict):
            last_ensemble = dict(item["ensemble"])
            break

    runtime_seconds = int(status.get("runtime_seconds") or 0)
    ready = int(event_counts.get("signal_ready", 0))
    scheduled = int(event_counts.get("open_scheduled", 0))
    proposal_ok = int(event_counts.get("proposal_ok", 0))
    buy_success = int(event_counts.get("buy_success", 0))
    warnings: list[str] = []
    banner = str(status.get("runtime_banner") or "")
    if banner and "V2.22" not in banner:
        warnings.append(f"Version mismatch: the running bot banner is '{banner}'. Expected V2.22.x.")
    mode_value = str(settings.get("mode") or "").lower()
    if mode_value == "signals":
        warnings.append(
            "SIGNAL-ONLY mode is active: valid entries are logged as signal_would_open, but no Deriv proposal or buy request is intentionally sent."
        )
    dashboard_state = status.get("dashboard") or {}
    if str(dashboard_state.get("link_state") or "").upper() == "RECONNECTING":
        warnings.append(
            "Deriv link is currently reconnecting. New entries are blocked until streams are restored; managed open trades will be re-subscribed after recovery."
        )
    reconnect_failures = int(dashboard_state.get("reconnect_failures") or 0)
    if reconnect_failures:
        warnings.append(f"Automatic reconnect has recorded {reconnect_failures} failed attempt(s) in this session and will continue retrying while the bot remains running.")
    if status.get("running") and runtime_seconds >= 900 and buy_success == 0:
        if settings.get("strategy") == "ensemble":
            if ready == 0:
                warnings.append("No ensemble signal has passed the final strategy gate in the sampled diagnostic window.")
            elif scheduled == 0:
                warnings.append("Signals passed the strategy gate, but no opening task was scheduled. This points to an entry-routing block.")
            elif proposal_ok == 0:
                warnings.append("An opening task was scheduled, but no proposal succeeded. Check proposal/API errors.")
            else:
                warnings.append("Proposal activity exists but no contract opened. Check buy errors and connection events.")
        else:
            warnings.append("No successful opening appears in the sampled diagnostic window.")
    if settings.get("strategy") == "ensemble" and settings.get("ensemble_policy") == "regime":
        warnings.append("REGIME mode gates on eligible vote count; Weighted score threshold is not the entry threshold in this policy.")
    if event_counts.get("proposal_error") or event_counts.get("buy_error") or event_counts.get("batch_error"):
        warnings.append("Order-path errors were recorded. Review the recent diagnostic events and console section.")

    block_reasons = [
        {"reason": reason, "count": count}
        for reason, count in reason_counts.most_common(12)
        if reason not in {"strategy_gate_passed", "opening_task_created", "proposal_received", "sending_proposal", "sending_buy", "contract_opened", "position_finalized"}
    ]

    attribution: Dict[str, Dict[str, Any]] = {}
    for item in events:
        if item.get("event") != "trade_closed":
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        source = str(extra.get("entry_sources") or "UNKNOWN")
        bucket = attribution.setdefault(source, {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0, "regimes": {}})
        profit = float(extra.get("profit") or 0.0)
        bucket["trades"] += 1
        bucket["profit"] += profit
        if profit > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        regime = str(extra.get("entry_regime") or "UNKNOWN")
        bucket["regimes"][regime] = bucket["regimes"].get(regime, 0) + 1
    for bucket in attribution.values():
        bucket["profit"] = round(float(bucket["profit"]), 4)
        bucket["win_rate"] = round((bucket["wins"] / bucket["trades"] * 100.0) if bucket["trades"] else 0.0, 2)
    safe_source_folder = ""
    build_source = str(status.get("build_source") or "")
    if build_source:
        try:
            safe_source_folder = Path(build_source).parent.name
        except Exception:
            safe_source_folder = ""

    safe_dashboard = dict(status.get("dashboard") or {})
    safe_dashboard.pop("account", None)
    # Contract IDs are removed from the position objects in the support report.
    if isinstance(safe_dashboard.get("positions"), list):
        safe_positions = []
        for item in safe_dashboard.get("positions") or []:
            clean = dict(item) if isinstance(item, dict) else {}
            clean.pop("contract_id", None)
            safe_positions.append(clean)
        safe_dashboard["positions"] = safe_positions

    return {
        "report_version": WEB_UI_VERSION,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": status.get("run_id", ""),
        "running": bool(status.get("running")),
        "runtime_seconds": runtime_seconds,
        "runtime_banner": banner,
        "source_folder": safe_source_folder,
        "exit_code": status.get("exit_code"),
        "last_error": _redact_console_line(str(status.get("last_error") or "")),
        "stop_requested": bool(status.get("stop_requested")),
        "settings": {k: v for k, v in settings.items() if k not in {"live_confirmation"}},
        "dashboard": safe_dashboard,
        "event_counts": dict(event_counts),
        "block_reasons": block_reasons,
        "last_event": last_event,
        "last_ensemble": last_ensemble,
        "recent_events": events[-max(1, recent_limit):],
        "warnings": warnings,
        "strategy_configs": _selected_strategy_configs(settings),
        "ensemble_trade_attribution": attribution,
    }


def _session_trade_rows(started_at: float, limit: int = 100) -> list[Dict[str, Any]]:
    if not TRADES_PATH.is_file():
        return []
    cutoff = datetime.fromtimestamp(started_at, timezone.utc) if started_at else None
    rows: list[Dict[str, Any]] = []
    try:
        with TRADES_PATH.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if cutoff:
                    try:
                        closed = datetime.fromisoformat(str(row.get("closed_at_utc") or "").replace("Z", "+00:00"))
                        if closed < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
                safe = dict(row)
                safe.pop("account", None)
                safe.pop("contract_id", None)
                rows.append(safe)
    except OSError:
        return []
    return rows[-limit:]


def _diagnostic_report_markdown(data: Dict[str, Any]) -> str:
    settings = data.get("settings") or {}
    dash = data.get("dashboard") or {}
    ens = data.get("last_ensemble") or {}
    lines = [
        f"# Dr Exploit Trade Session Diagnostic — {data.get('generated_at_utc', '')}",
        "",
        "> Upload this file to ChatGPT when a session does not trade or when you want configuration optimization. Credentials, account IDs, and contract IDs are intentionally omitted.",
        "",
        "## Runtime verification",
        f"- Expected build: **V{WEB_UI_VERSION}**",
        f"- Running banner: `{data.get('runtime_banner') or 'not captured'}`",
        f"- Package folder: `{data.get('source_folder') or 'unknown'}`",
        f"- Running: **{data.get('running')}** | Runtime: **{data.get('runtime_seconds', 0)} sec** | Exit code: `{data.get('exit_code')}`",
        f"- Last error: `{data.get('last_error') or 'none'}`",
        "",
        "## Session settings",
        "```json",
        json.dumps(settings, indent=2, sort_keys=True),
        "```",
        "",
        "## Current dashboard / entry state",
        "```json",
        json.dumps(dash, indent=2, sort_keys=True),
        "```",
        "",
        "## Reconnect guard",
        f"- Auto reconnect: **{dash.get('auto_reconnect', 'AUTO')}**",
        f"- Link state: **{dash.get('link_state', 'UNKNOWN')}**",
        f"- Successful reconnects: **{dash.get('reconnects', 0)}**",
        f"- Reconnect attempts: **{dash.get('reconnect_attempts', 0)}**",
        f"- Failed attempts: **{dash.get('reconnect_failures', 0)}**",
        f"- Next retry delay: **{dash.get('reconnect_next_delay', 0)} sec**",
        f"- Last reconnect error: `{dash.get('reconnect_last_error') or 'none'}`",
        "",
        "## Current ensemble gate",
        "```json",
        json.dumps(ens, indent=2, sort_keys=True),
        "```",
        "",
        "## Automatic findings",
    ]
    warnings = data.get("warnings") or []
    lines.extend([f"- {item}" for item in warnings] if warnings else ["- No automatic warning was detected."])
    lines += ["", "## Entry-event counts", "```json", json.dumps(data.get("event_counts") or {}, indent=2, sort_keys=True), "```", "", "## Main no-trade blockers"]
    blockers = data.get("block_reasons") or []
    if blockers:
        lines += ["| Reason | Count |", "|---|---:|"] + [f"| {item.get('reason')} | {item.get('count')} |" for item in blockers]
    else:
        lines.append("No blocker events recorded yet.")
    lines += ["", "## Ensemble trade attribution", "```json", json.dumps(data.get("ensemble_trade_attribution") or {}, indent=2, sort_keys=True), "```", "", "## Exact selected strategy configurations", "```json", json.dumps(data.get("strategy_configs") or {}, indent=2, sort_keys=True), "```", "", "## Recent entry decisions"]
    events = data.get("recent_events") or []
    if events:
        lines += ["| UTC | Event | Reason | Signal | Regime | Votes / score |", "|---|---|---|---|---|---|"]
        for event in events[-30:]:
            e = event.get("ensemble") or {}
            votes = f"UP {e.get('up_votes', 0)}/{e.get('up_score', 0)} · DOWN {e.get('down_votes', 0)}/{e.get('down_score', 0)}" if e else "—"
            lines.append(f"| {event.get('timestamp_utc','')} | {event.get('event','')} | {event.get('reason','')} | {event.get('signal','') or '—'} | {e.get('regime','') or '—'} | {votes} |")
    else:
        lines.append("No entry diagnostics recorded yet.")
    ensemble_rows = data.get("recent_ensemble_decisions") or []
    lines += ["", "## Recent accepted ensemble decisions"]
    if ensemble_rows:
        lines += ["```json", json.dumps(ensemble_rows[-20:], indent=2), "```"]
    else:
        lines.append("No accepted ensemble decision rows recorded for this run.")
    trades = data.get("session_trades") or []
    lines += ["", "## Trades closed during this session", "```json", json.dumps(trades[-50:], indent=2), "```", "", "## Recent sanitized console", "```text"]
    lines.extend(data.get("recent_console") or ["No console lines captured."])
    lines += ["```", ""]
    return "\n".join(lines)


def build_main_diagnostic_report(report_format: str = "md") -> Dict[str, Any]:
    data = main_diagnostic_snapshot(event_limit=5000, recent_limit=80)
    status = MANAGER.status()
    data["recent_ensemble_decisions"] = _read_csv_tail(str(status.get("ensemble_log_path") or ""), 100)
    data["session_trades"] = _session_trade_rows(float(status.get("started_at") or 0), 100)
    with MANAGER.lock:
        console = [text for _, text in list(MANAGER.state.lines)[-220:]]
    data["recent_console"] = [_redact_console_line(line) for line in console]
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    SUPPORT_REPORT_DIR.mkdir(exist_ok=True)
    if report_format == "json":
        content = json.dumps(data, indent=2, ensure_ascii=False)
        filename = f"TRADE_SESSION_DIAGNOSTIC_{stamp}.json"
    else:
        content = _diagnostic_report_markdown(data)
        filename = f"TRADE_SESSION_DIAGNOSTIC_{stamp}.md"
    path = SUPPORT_REPORT_DIR / filename
    path.write_text(content, encoding="utf-8")
    return {"filename": filename, "content": content, "data": data}



def _account_activity_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return only dashboard-useful statement fields; never credentials."""
    return {
        "action_type": item.get("action_type") or item.get("action") or item.get("type") or "activity",
        "amount": item.get("amount", item.get("profit", 0)),
        "balance_after": item.get("balance_after", item.get("balance")),
        "description": item.get("description") or item.get("longcode") or "",
        "time": item.get("transaction_time") or item.get("purchase_time") or item.get("sell_time") or item.get("date") or "",
        "transaction_id": item.get("transaction_id") or item.get("contract_id") or "",
    }


def _extract_list(block: Any, *keys: str) -> list[Dict[str, Any]]:
    if isinstance(block, list):
        return [x for x in block if isinstance(x, dict)]
    if not isinstance(block, dict):
        return []
    for key in keys:
        value = block.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


_ACCOUNT_INSIGHTS_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_ACCOUNT_INSIGHTS_TTL_SECONDS = 300.0


def fetch_account_insights() -> Dict[str, Any]:
    now = time.monotonic()
    cached = _ACCOUNT_INSIGHTS_CACHE.get("data")
    cached_at = float(_ACCOUNT_INSIGHTS_CACHE.get("at") or 0.0)
    if isinstance(cached, dict) and now - cached_at < _ACCOUNT_INSIGHTS_TTL_SECONDS:
        result = dict(cached)
        result["cached"] = True
        result["cache_age_seconds"] = round(now - cached_at, 1)
        return result

    values = read_env()
    token = values.get("DERIV_API_TOKEN", "")
    account_id = values.get("DERIV_ACCOUNT_ID", "")
    if not token or not account_id:
        raise ValueError("Save the Deriv API token and account ID first")

    async def fetch() -> Dict[str, Any]:
        import bot as bot_module
        client = await bot_module.connect_authenticated_client(token, FIXED_DERIV_APP_ID, account_id, attempts=2)
        errors: Dict[str, str] = {}

        async def safe(name: str, payload: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            try:
                return await client.request(payload, timeout=18.0)
            except Exception as exc:
                if fallback is not None:
                    try:
                        return await client.request(fallback, timeout=18.0)
                    except Exception as fallback_exc:
                        exc = fallback_exc
                msg = str(exc).replace(token, "[REDACTED]")
                errors[name] = msg[:240]
                return {}

        try:
            balance_response = await safe("balance", {"balance": 1})
            portfolio_response = await safe("portfolio", {"portfolio": 1})
            statement_response = await safe("statement", {"statement": 1, "limit": 30}, {"statement": 1})
            profit_response = await safe("profit_table", {"profit_table": 1, "limit": 30}, {"profit_table": 1})
        finally:
            await client.close()

        balance_block = balance_response.get("balance") if isinstance(balance_response.get("balance"), dict) else {}
        portfolio_block = portfolio_response.get("portfolio", {})
        statement_block = statement_response.get("statement", {})
        profit_block = profit_response.get("profit_table", {})
        portfolio = _extract_list(portfolio_block, "contracts", "positions")
        statement = _extract_list(statement_block, "transactions", "statement")
        profits = _extract_list(profit_block, "transactions", "contracts", "profit_table")

        activity = [_account_activity_row(row) for row in statement[:20]]
        deposit_total = Decimal("0")
        withdrawal_total = Decimal("0")
        for row in statement:
            action = str(row.get("action_type") or row.get("action") or "").lower()
            try:
                amount = Decimal(str(row.get("amount", "0") or "0"))
            except (InvalidOperation, ValueError):
                amount = Decimal("0")
            if action == "deposit":
                deposit_total += abs(amount)
            elif action == "withdrawal":
                withdrawal_total += abs(amount)

        last_activity = ""
        if activity:
            last_activity = str(activity[0].get("time") or activity[0].get("description") or "Recent activity")
        return {
            "account_id": account_id,
            "balance": balance_block.get("balance"),
            "currency": balance_block.get("currency") or "USD",
            "portfolio_count": len(portfolio),
            "statement_count": len(statement),
            "profit_count": len(profits),
            "deposit_total": float(deposit_total),
            "withdrawal_total": float(withdrawal_total),
            "last_activity": last_activity,
            "activity": activity,
            "errors": errors,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    result = asyncio.run(fetch())
    if isinstance(result, dict):
        result["cached"] = False
        _ACCOUNT_INSIGHTS_CACHE["at"] = time.monotonic()
        _ACCOUNT_INSIGHTS_CACHE["data"] = dict(result)
    return result


def credential_status() -> Dict[str, Any]:
    values = read_env()
    token = values.get("DERIV_API_TOKEN", "")
    return {
        "env_exists": ENV_PATH.exists(),
        "token_saved": bool(token),
        "token_hint": (token[:4] + "…" + token[-4:]) if len(token) >= 10 else ("saved" if token else ""),
        "app_id": values.get("DERIV_APP_ID", ""),
        "account_id": values.get("DERIV_ACCOUNT_ID", ""),
        "symbol": values.get("DERIV_SYMBOL", "stpRNG") or "stpRNG",
    }


def api_bootstrap() -> Dict[str, Any]:
    return {
        "ok": True,
        "app_version": WEB_UI_VERSION,
        "session": load_last_session(),
        "strategy_catalog": STRATEGY_CATALOG,
        "strategy_profiles": STRATEGY_PROFILES,
        "config": load_config(),
        "presets": user_preset_summaries(),
        "credentials": credential_status(),
        "status": MANAGER.status(),
        "trades": read_trades(100),
        "lab": LAB_MANAGER.status(),
        "rise_fall_lab": RF_LAB_MANAGER.status(),
        "rise_fall": {"session": load_rise_fall_session(), "status": RISE_FALL_MANAGER.status(), "presets": rise_fall_preset_summaries()},
        "ai_optimizer": AI_OPTIMIZER.status(),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DrExploitControlWebUI/2.27"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path.startswith("/api/status") or self.path.startswith("/api/logs"):
            return
        super().log_message(fmt, *args)

    def _headers(self, status: int, content_type: str, length: Optional[int] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length")
        if length > 1024 * 1024:
            raise ValueError("Request is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-WebUI-Token", ""), WEB_UI_SECRET)

    def _require_auth(self) -> bool:
        if not self._authorized():
            self._error("Invalid local dashboard token", HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            html = TEMPLATE_PATH.read_text(encoding="utf-8").replace("{{ ui_token }}", WEB_UI_SECRET)
            body = html.encode("utf-8")
            self._headers(200, "text/html; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        if path == "/service-worker.js":
            worker = STATIC_DIR / "service-worker.js"
            if not worker.exists():
                self._error("Not found", 404)
                return
            data = worker.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/static/"):
            relative = Path(path.removeprefix("/static/"))
            if relative.is_absolute() or ".." in relative.parts:
                self._error("Not found", 404)
                return
            file_path = STATIC_DIR / relative
            if not file_path.is_file():
                self._error("Not found", 404)
                return
            body = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self._headers(200, content_type, len(body))
            self.wfile.write(body)
            return
        if path == "/api/bootstrap":
            self._json(api_bootstrap())
            return
        if path == "/api/status":
            self._json({"ok": True, "status": MANAGER.status(), "trades": read_trades(100), "lab": LAB_MANAGER.status(), "rise_fall_lab": RF_LAB_MANAGER.status(), "ai_optimizer": AI_OPTIMIZER.status()})
            return
        if path == "/api/ai-optimizer/status":
            self._json({"ok": True, "optimizer": AI_OPTIMIZER.status()})
            return
        if path == "/api/ai-optimizer/best-preset":
            query = urllib.parse.parse_qs(parsed.query)
            engine = str(query.get("engine", ["rise_fall"])[0]).strip().lower()
            try:
                self._json({"ok": True, "preset": AI_OPTIMIZER.best_preset(engine)})
            except Exception as exc:
                self._error(str(exc), 400)
            return
        if path == "/api/presets":
            self._json({"ok": True, "presets": user_preset_summaries()})
            return
        if path == "/api/presets/export":
            query = urllib.parse.parse_qs(parsed.query)
            name = str(query.get("name", [""])[0]).strip()
            try:
                self._json({"ok": True, "export": export_full_preset(name)})
            except Exception as exc:
                self._error(str(exc), 400)
            return
        if path == "/api/lab/status":
            self._json({"ok": True, "lab": LAB_MANAGER.status()})
            return
        if path == "/api/lab/report":
            query = urllib.parse.parse_qs(parsed.query)
            bot_id = str(query.get("bot_id", [""])[0]).strip()
            report_format = str(query.get("format", ["json"])[0]).strip().lower()
            if report_format not in {"json", "md"}:
                self._error("Report format must be json or md", 400)
                return
            try:
                self._json({"ok": True, "report": LAB_MANAGER.get_report(bot_id, report_format)})
            except Exception as exc:
                self._error(str(exc), 400)
            return
        if path == "/api/lab/run-summary":
            query = urllib.parse.parse_qs(parsed.query)
            report_format = str(query.get("format", ["md"])[0]).strip().lower()
            if report_format not in {"json", "md"}:
                self._error("Report format must be json or md", 400)
                return
            try:
                self._json({"ok": True, "report": LAB_MANAGER.get_run_summary(report_format)})
            except Exception as exc:
                self._error(str(exc), 400)
            return
        if path == "/api/lab/logs":
            query = urllib.parse.parse_qs(parsed.query)
            bot_id = str(query.get("bot_id", [""])[0])
            try:
                after = int(query.get("after", ["0"])[0])
            except ValueError:
                after = 0
            self._json({"ok": True, **LAB_MANAGER.logs_after(bot_id, after)})
            return
        if path == "/api/rise-fall/lab/status":
            self._json({"ok": True, "lab": RF_LAB_MANAGER.status()})
            return
        if path == "/api/rise-fall/lab/report":
            query = urllib.parse.parse_qs(parsed.query)
            bot_id = str(query.get("bot_id", [""])[0]).strip()
            report_format = str(query.get("format", ["md"])[0]).strip().lower()
            if report_format not in {"json", "md"}:
                self._error("Report format must be json or md", 400); return
            try: self._json({"ok": True, "report": RF_LAB_MANAGER.get_report(bot_id, report_format)})
            except Exception as exc: self._error(str(exc), 400)
            return
        if path == "/api/rise-fall/lab/run-summary":
            query = urllib.parse.parse_qs(parsed.query)
            report_format = str(query.get("format", ["md"])[0]).strip().lower()
            if report_format not in {"json", "md"}:
                self._error("Report format must be json or md", 400); return
            try: self._json({"ok": True, "report": RF_LAB_MANAGER.get_run_summary(report_format)})
            except Exception as exc: self._error(str(exc), 400)
            return
        if path == "/api/rise-fall/lab/logs":
            query = urllib.parse.parse_qs(parsed.query)
            bot_id = str(query.get("bot_id", [""])[0]).strip()
            try: after = int(query.get("after", ["0"])[0])
            except ValueError: after = 0
            self._json({"ok": True, **RF_LAB_MANAGER.logs_after(bot_id, after)})
            return
        if path == "/api/rise-fall/lab/auto-best-preset":
            try: self._json({"ok": True, "preset": RF_LAB_MANAGER.get_auto_best_preset()})
            except Exception as exc: self._error(str(exc), 400)
            return
        if path == "/api/rise-fall/presets":
            self._json({"ok": True, "presets": rise_fall_preset_summaries()})
            return
        if path == "/api/rise-fall/presets/export":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                self._json({"ok": True, "export": export_rise_fall_preset(str(query.get("name", [""])[0]))})
            except Exception as exc:
                self._error(str(exc), 400)
            return
        if path == "/api/rise-fall/optimization":
            query = urllib.parse.parse_qs(parsed.query)
            report_format = str(query.get("format", ["md"])[0]).strip().lower()
            try:
                self._json({"ok": True, "report": build_rise_fall_optimization_report(report_format)})
            except Exception as exc:
                self._error(str(exc), 400)
            return
        if path == "/api/rise-fall/status":
            self._json({"ok": True, "status": RISE_FALL_MANAGER.status(), "session": load_rise_fall_session()})
            return
        if path == "/api/rise-fall/logs":
            query = urllib.parse.parse_qs(parsed.query)
            try: after = int(query.get("after", ["0"])[0])
            except ValueError: after = 0
            self._json({"ok": True, **RISE_FALL_MANAGER.logs_after(after)})
            return
        if path == "/api/logs":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                after = int(query.get("after", ["0"])[0])
            except ValueError:
                after = 0
            self._json({"ok": True, **MANAGER.logs_after(after)})
            return
        self._error("Not found", 404)

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/ai-optimizer/config":
                self._json({"ok": True, "optimizer": AI_OPTIMIZER.configure(payload)})
                return
            if path == "/api/ai-optimizer/test":
                self._json({"ok": True, "result": AI_OPTIMIZER.test_connection(), "optimizer": AI_OPTIMIZER.status()})
                return
            if path == "/api/ai-optimizer/analyze":
                engine = str(payload.get("engine", "rise_fall")).strip().lower()
                self._json({"ok": True, **AI_OPTIMIZER.analyze(engine)})
                return
            if path == "/api/ai-optimizer/start":
                engine = str(payload.get("engine", "rise_fall")).strip().lower()
                mode = str(payload.get("mode", "auto_lab")).strip().lower()
                self._json({"ok": True, "optimizer": AI_OPTIMIZER.start(engine, mode, str(payload.get("confirmation", "")))})
                return
            if path == "/api/ai-optimizer/stop":
                engine = str(payload.get("engine", "rise_fall")).strip().lower()
                self._json({"ok": True, "optimizer": AI_OPTIMIZER.stop(engine)})
                return
            if path == "/api/presets/save":
                item = save_full_preset(
                    str(payload.get("name", "")),
                    payload.get("session") if isinstance(payload.get("session"), dict) else {},
                    payload.get("config") if isinstance(payload.get("config"), dict) else {},
                )
                self._json({"ok": True, "preset": item, "presets": user_preset_summaries()})
                return
            if path == "/api/presets/apply":
                loaded = apply_full_preset(str(payload.get("name", "")))
                self._json({"ok": True, **loaded, "presets": user_preset_summaries()})
                return
            if path == "/api/presets/delete":
                delete_full_preset(str(payload.get("name", "")))
                self._json({"ok": True, "presets": user_preset_summaries()})
                return
            if path == "/api/presets/import":
                imported = import_full_preset(
                    payload.get("preset_file") if isinstance(payload.get("preset_file"), dict) else {},
                    overwrite=bool(payload.get("overwrite", False)),
                )
                self._json({"ok": True, **imported, "presets": user_preset_summaries()})
                return
            if path == "/api/lab/start":
                LAB_MANAGER.start(payload)
                self._json({"ok": True, "lab": LAB_MANAGER.status()})
                return
            if path == "/api/lab/stop":
                LAB_MANAGER.stop(str(payload.get("bot_id", "")).strip())
                self._json({"ok": True, "lab": LAB_MANAGER.status()})
                return
            if path == "/api/rise-fall/lab/start":
                RF_LAB_MANAGER.start(payload)
                self._json({"ok": True, "lab": RF_LAB_MANAGER.status()}); return
            if path == "/api/rise-fall/lab/auto-start":
                RF_LAB_MANAGER.start_auto(payload)
                self._json({"ok": True, "lab": RF_LAB_MANAGER.status()}); return
            if path == "/api/rise-fall/lab/auto-stop":
                RF_LAB_MANAGER.stop_auto()
                self._json({"ok": True, "lab": RF_LAB_MANAGER.status()}); return
            if path == "/api/rise-fall/lab/stop":
                RF_LAB_MANAGER.stop(str(payload.get("bot_id", "")).strip())
                self._json({"ok": True, "lab": RF_LAB_MANAGER.status()}); return
            if path == "/api/rise-fall/presets/save":
                item = save_rise_fall_preset(str(payload.get("name", "")), payload.get("settings") if isinstance(payload.get("settings"), dict) else {})
                self._json({"ok": True, "preset": item, "presets": rise_fall_preset_summaries()}); return
            if path == "/api/rise-fall/presets/apply":
                loaded = apply_rise_fall_preset(str(payload.get("name", "")))
                self._json({"ok": True, **loaded, "presets": rise_fall_preset_summaries()}); return
            if path == "/api/rise-fall/presets/delete":
                delete_rise_fall_preset(str(payload.get("name", "")))
                self._json({"ok": True, "presets": rise_fall_preset_summaries()}); return
            if path == "/api/rise-fall/presets/import":
                imported = import_rise_fall_preset(payload.get("preset_file") if isinstance(payload.get("preset_file"), dict) else {}, overwrite=bool(payload.get("overwrite", False)))
                self._json({"ok": True, **imported, "presets": rise_fall_preset_summaries()}); return
            if path == "/api/rise-fall/start":
                if RF_LAB_MANAGER.any_running(): raise RuntimeError("Stop Rise/Fall Lab before deploying the main Rise/Fall engine")
                settings = validate_rise_fall_session(payload)
                creds = credential_status()
                missing = [name for ok,name in ((creds["token_saved"],"API token"),(bool(creds["app_id"]),"App ID"),(bool(creds["account_id"]),"Account ID")) if not ok]
                if missing: raise ValueError("Save credentials first: " + ", ".join(missing))
                RISE_FALL_MANAGER.start(settings); save_rise_fall_session(settings)
                self._json({"ok":True,"status":RISE_FALL_MANAGER.status(),"session":settings}); return
            if path == "/api/rise-fall/stop":
                RISE_FALL_MANAGER.stop(); self._json({"ok":True}); return
            if path == "/api/rise-fall/live-controls":
                result=RISE_FALL_MANAGER.live(payload); self._json({"ok":True,"request":result,"status":RISE_FALL_MANAGER.status()}); return
            if path == "/api/rise-fall/save":
                candidate=dict(payload)
                if str(candidate.get("mode","")).lower()=="live": candidate["live_confirmation"]="LIVE"
                settings=validate_rise_fall_session(candidate); settings["live_confirmation"]=""; save_rise_fall_session(settings); self._json({"ok":True,"session":settings}); return
            if path == "/api/start":
                settings = validate_session(payload)
                creds = credential_status()
                missing = []
                if not creds["token_saved"]:
                    missing.append("API token")
                if not creds["app_id"]:
                    missing.append("App ID")
                if not creds["account_id"]:
                    missing.append("Account ID")
                if missing:
                    raise ValueError("Save credentials first: " + ", ".join(missing))
                if settings.get("strategy") == "ensemble":
                    active_config = normalize_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
                else:
                    active_config = apply_strategy_profile(settings["strategy"], settings.get("strategy_profile", "custom"))
                MANAGER.start(settings)
                save_last_session(settings)
                self._json({"ok": True, "status": MANAGER.status(), "config": active_config})
                return
            if path == "/api/stop":
                MANAGER.stop()
                self._json({"ok": True})
                return
            if path == "/api/live-controls":
                result = MANAGER.apply_live_controls(payload)
                self._json({"ok": True, **result, "status": MANAGER.status()})
                return
            if path == "/api/account-insights":
                self._json({"ok": True, "insights": fetch_account_insights()})
                return
            if path == "/api/config":
                if MANAGER.running() or LAB_MANAGER.any_running():
                    raise ValueError("Stop the main bot and Strategy Lab before changing strategy config")
                data = validate_config(payload)
                if CONFIG_PATH.exists():
                    shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_suffix(".json.bak"))
                CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                self._json({"ok": True, "config": data})
                return
            if path == "/api/credentials":
                if MANAGER.running() or LAB_MANAGER.any_running() or RISE_FALL_MANAGER.running() or RF_LAB_MANAGER.any_running():
                    raise ValueError("Stop Multiplier, Rise/Fall, Multiplier Strategy Lab, and Rise/Fall Lab before changing credentials")
                existing = read_env()
                token = str(payload.get("token", "")).strip() or existing.get("DERIV_API_TOKEN", "")
                app_id = str(payload.get("app_id", "")).strip()
                account_id = str(payload.get("account_id", "")).strip()
                symbol = str(payload.get("symbol", "stpRNG")).strip() or "stpRNG"
                if not token:
                    raise ValueError("API token is required")
                if not app_id:
                    raise ValueError("App ID is required")
                if not account_id:
                    raise ValueError("Account ID is required")
                if any(char.isspace() for char in app_id + account_id + symbol):
                    raise ValueError("IDs and symbol cannot contain spaces")
                write_env({"DERIV_API_TOKEN": token, "DERIV_ACCOUNT_ID": account_id, "DERIV_SYMBOL": symbol})
                self._json({"ok": True, "credentials": credential_status()})
                return
            if path == "/api/test-connection":
                if MANAGER.running() or LAB_MANAGER.any_running() or RISE_FALL_MANAGER.running() or RF_LAB_MANAGER.any_running():
                    raise ValueError("Stop Multiplier, Rise/Fall, Multiplier Strategy Lab, and Rise/Fall Lab before testing the account connection")
                values = read_env()
                token = values.get("DERIV_API_TOKEN", "")
                app_id = FIXED_DERIV_APP_ID
                account_id = values.get("DERIV_ACCOUNT_ID", "")
                if not token or not app_id or not account_id:
                    raise ValueError("Save token, App ID, and account ID first")

                async def probe() -> Dict[str, Any]:
                    import bot as bot_module
                    client = await bot_module.connect_authenticated_client(token, app_id, account_id, attempts=2)
                    try:
                        balance, currency = await bot_module.get_initial_balance(client)
                        return {"account_id": account_id, "balance": str(balance), "currency": currency}
                    finally:
                        await client.close()

                try:
                    result = asyncio.run(probe())
                except Exception as exc:
                    raise ValueError(str(exc).replace(token, "[REDACTED]")) from exc
                self._json({"ok": True, "connection": result})
                return
            if path == "/api/clear-history":
                if MANAGER.running() or LAB_MANAGER.any_running():
                    raise ValueError("Stop the main bot and Strategy Lab before clearing trade history")
                if TRADES_PATH.exists():
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    TRADES_PATH.rename(ROOT / f"trades_v2_backup_{timestamp}.csv")
                self._json({"ok": True, "trades": read_trades(100)})
                return
            self._error("Not found", 404)
        except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            self._error(str(exc), 400)
        except Exception as exc:
            self._error(f"Unexpected server error: {exc}", 500)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dr Exploit Control local web dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; keep 127.0.0.1 for safety")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: The dashboard is being exposed beyond this computer.")
        print("Do not expose it to the internet. Credentials and trade controls are sensitive.")
    enforce_app_id()
    threading.Thread(target=_app_id_watch_loop, name="env-app-id-watch", daemon=True).start()
    if args.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dr Exploit Control Web UI: http://127.0.0.1:{args.port}")
    print("Keep this window open. Press Ctrl+C to stop the dashboard.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping web dashboard...")
    finally:
        if MANAGER.running():
            try:
                MANAGER.stop()
            except RuntimeError:
                pass
        if RISE_FALL_MANAGER.running():
            try:
                RISE_FALL_MANAGER.stop()
            except RuntimeError:
                pass
        if LAB_MANAGER.any_running():
            try: LAB_MANAGER.stop()
            except RuntimeError: pass
        if RF_LAB_MANAGER.any_running():
            try: RF_LAB_MANAGER.stop()
            except RuntimeError: pass
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
