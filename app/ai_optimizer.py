from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

DEFAULT_SETTINGS: Dict[str, Any] = {
    "provider": "ollama",
    "ollama_url": "http://localhost:11434",
    "model": "qwen3:8b",
    "thinking": "low",
    "keep_alive": "10m",
    "analyze_every": 25,
    "explore_trades": 30,
    "refine_trades": 75,
    "validate_trades": 150,
    "final_trades": 250,
    "max_concurrent": 4,
    "max_minutes_per_stage": 60,
    "min_profit_factor": 1.20,
    "min_edge_pp": 3.0,
    "allow_adaptive_demo": True,
    "allow_live_trading": False,
    "live_max_loss": 10.0,
    "live_profit_target": 20.0,
    "live_max_trades": 100,
    "live_min_validated_trades": 150,
    "live_min_profit_factor": 1.25,
    "live_min_edge_pp": 2.0,
    "live_fail_closed": True,
    "rise_fall": {
        "enabled": True,
        "mode": "advisor",
        "stake": "1.00",
        "duration": 2,
        "duration_unit": "t",
    },
    "multiplier": {
        "enabled": True,
        "mode": "advisor",
        "stake": "1.00",
        "max_open": 1,
        "trades_per_signal": 1,
        "split_delay": 0.5,
    },
}


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError
    except (TypeError, ValueError):
        parsed = default
    return max(lo, min(hi, parsed))


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lo, min(hi, parsed))


def sanitize_optimizer_settings(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    src = dict(raw or {})
    out = deepcopy(DEFAULT_SETTINGS)
    out["provider"] = "ollama"
    ollama_url = str(src.get("ollama_url", out["ollama_url"]) or out["ollama_url"]).strip()
    if not ollama_url:
        ollama_url = "http://localhost:11434"
    if not re.match(r"^https?://", ollama_url, flags=re.I):
        ollama_url = "http://" + ollama_url
    ollama_url = ollama_url.rstrip("/")
    if ollama_url.lower().endswith("/api"):
        ollama_url = ollama_url[:-4]
    out["ollama_url"] = ollama_url[:300]
    model = str(src.get("model", out["model"]) or out["model"]).strip()
    # Migrate V2.25 OpenAI settings to a useful local default instead of
    # preserving a cloud-only model name that Ollama will not have installed.
    legacy_cloud = str(src.get("provider", "")).lower() != "ollama" and ("reasoning_mode" in src or "reasoning_effort" in src)
    if legacy_cloud and model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
        model = "qwen3:8b"
    out["model"] = model[:120] or "qwen3:8b"
    thinking = str(src.get("thinking", src.get("reasoning_effort", out["thinking"])) or out["thinking"]).strip().lower()
    out["thinking"] = thinking if thinking in {"none", "low", "medium", "high"} else "low"
    keep_alive = str(src.get("keep_alive", out["keep_alive"]) or out["keep_alive"]).strip()
    out["keep_alive"] = (keep_alive[:32] or "10m")
    out["analyze_every"] = _clamp_int(src.get("analyze_every"), 5, 1000, 25)
    out["explore_trades"] = _clamp_int(src.get("explore_trades"), 20, 1000, 30)
    out["refine_trades"] = _clamp_int(src.get("refine_trades"), out["explore_trades"], 2000, 75)
    out["validate_trades"] = _clamp_int(src.get("validate_trades"), out["refine_trades"], 5000, 150)
    out["final_trades"] = _clamp_int(src.get("final_trades"), out["validate_trades"], 10000, 250)
    out["max_concurrent"] = _clamp_int(src.get("max_concurrent"), 1, 4, 4)
    out["max_minutes_per_stage"] = _clamp_int(src.get("max_minutes_per_stage"), 5, 1440, 60)
    out["min_profit_factor"] = round(_clamp_float(src.get("min_profit_factor"), 0.5, 5.0, 1.20), 3)
    out["min_edge_pp"] = round(_clamp_float(src.get("min_edge_pp"), -20.0, 30.0, 3.0), 3)
    out["allow_adaptive_demo"] = bool(src.get("allow_adaptive_demo", True))
    out["allow_live_trading"] = bool(src.get("allow_live_trading", False))
    out["live_max_loss"] = round(_clamp_float(src.get("live_max_loss"), 0.35, 1000000.0, 10.0), 2)
    out["live_profit_target"] = round(_clamp_float(src.get("live_profit_target"), 0.0, 1000000.0, 20.0), 2)
    out["live_max_trades"] = _clamp_int(src.get("live_max_trades"), 1, 1000000, 100)
    out["live_min_validated_trades"] = _clamp_int(src.get("live_min_validated_trades"), 30, 100000, 150)
    out["live_min_profit_factor"] = round(_clamp_float(src.get("live_min_profit_factor"), 1.0, 10.0, 1.25), 3)
    out["live_min_edge_pp"] = round(_clamp_float(src.get("live_min_edge_pp"), -10.0, 30.0, 2.0), 3)
    out["live_fail_closed"] = bool(src.get("live_fail_closed", True))

    for engine in ("rise_fall", "multiplier"):
        block = src.get(engine) if isinstance(src.get(engine), dict) else {}
        out[engine].update(block)
        out[engine]["enabled"] = bool(out[engine].get("enabled", True))
        emode = str(out[engine].get("mode", "advisor") or "advisor").strip().lower()
        out[engine]["mode"] = emode if emode in {"advisor", "auto_lab", "adaptive", "live_trade"} else "advisor"
        try:
            stake = float(out[engine].get("stake", "1.00"))
        except (TypeError, ValueError):
            stake = 1.0
        out[engine]["stake"] = f"{max(0.35, min(1000.0, stake)):.2f}"
    out["rise_fall"]["duration"] = 2
    out["rise_fall"]["duration_unit"] = "t"
    out["multiplier"]["max_open"] = _clamp_int(out["multiplier"].get("max_open"), 1, 10, 1)
    out["multiplier"]["trades_per_signal"] = _clamp_int(
        out["multiplier"].get("trades_per_signal"), 1, out["multiplier"]["max_open"], 1
    )
    out["multiplier"]["split_delay"] = round(_clamp_float(out["multiplier"].get("split_delay"), 0.0, 60.0, 0.5), 2)
    return out


def load_optimizer_settings(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    return sanitize_optimizer_settings(raw)


def save_optimizer_settings(path: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    clean = sanitize_optimizer_settings(settings)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return clean



def normalize_ollama_url(value: str) -> str:
    url = str(value or "http://localhost:11434").strip() or "http://localhost:11434"
    if not re.match(r"^https?://", url, flags=re.I):
        url = "http://" + url
    url = url.rstrip("/")
    if url.lower().endswith("/api"):
        url = url[:-4]
    return url


def list_ollama_models(ollama_url: str, timeout: float = 10.0) -> Dict[str, Any]:
    base = normalize_ollama_url(ollama_url)
    request = urllib.request.Request(
        base + "/api/tags",
        headers={"Accept": "application/json", "User-Agent": "ControlNexus-Ollama-Optimizer/2.28"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama connection failed: {exc.reason}") from exc
    models = []
    for item in data.get("models") or []:
        if isinstance(item, dict) and item.get("name"):
            models.append(str(item.get("name")))
    return {"connected": True, "url": base, "models": models, "count": len(models)}


def ollama_provider_status(ollama_url: str) -> Dict[str, Any]:
    base = normalize_ollama_url(ollama_url)
    return {"provider": "ollama", "url": base, "local": base.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]"))}

def _rf_candidate_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "rationale", "decision_mode", "direction_policy", "min_confidence", "min_edge", "cooldown_ticks", "quote_refresh_ticks", "lookback", "trend_weight", "momentum_weight", "reversal_weight", "breakout_weight"],
        "properties": {
            "label": {"type": "string", "minLength": 1, "maxLength": 60},
            "rationale": {"type": "string", "maxLength": 500},
            "decision_mode": {"type": "string", "enum": ["best_odds", "consensus", "momentum", "reversal", "trend"]},
            "direction_policy": {"type": "string", "enum": ["auto", "rise_only", "fall_only"]},
            "min_confidence": {"type": "number", "minimum": 0.50, "maximum": 0.85},
            "min_edge": {"type": "number", "minimum": 0.05, "maximum": 0.65},
            "cooldown_ticks": {"type": "integer", "minimum": 1, "maximum": 20},
            "quote_refresh_ticks": {"type": "integer", "minimum": 1, "maximum": 10},
            "lookback": {"type": "integer", "minimum": 8, "maximum": 80},
            "trend_weight": {"type": "number", "minimum": 0.0, "maximum": 3.0},
            "momentum_weight": {"type": "number", "minimum": 0.0, "maximum": 3.5},
            "reversal_weight": {"type": "number", "minimum": 0.0, "maximum": 2.0},
            "breakout_weight": {"type": "number", "minimum": 0.0, "maximum": 2.0},
        },
    }


def _mult_candidate_schema(strategy_ids: Iterable[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "rationale", "strategy", "profile", "multiplier", "exit_mode", "take_profit_unit", "take_profit", "take_profit_max", "take_profit_ticks", "take_profit_ticks_max", "smart_activation", "smart_profit_floor", "smart_activation_ticks", "smart_profit_floor_ticks", "smart_min_distance_ticks", "smart_server_take_profit"],
        "properties": {
            "label": {"type": "string", "minLength": 1, "maxLength": 60},
            "rationale": {"type": "string", "maxLength": 500},
            "strategy": {"type": "string", "enum": list(strategy_ids)},
            "profile": {"type": "string", "enum": ["balanced", "fast", "strict"]},
            "multiplier": {"type": "integer", "enum": [750, 2000, 3500, 5500, 7500]},
            "exit_mode": {"type": "string", "enum": ["fixed", "adaptive", "smart", "hybrid"]},
            "take_profit_unit": {"type": "string", "enum": ["amount", "ticks"]},
            "take_profit": {"type": "number", "minimum": 0.10, "maximum": 20.0},
            "take_profit_max": {"type": "number", "minimum": 0.10, "maximum": 50.0},
            "take_profit_ticks": {"type": "integer", "minimum": 1, "maximum": 100},
            "take_profit_ticks_max": {"type": "integer", "minimum": 1, "maximum": 200},
            "smart_activation": {"type": "number", "minimum": 0.10, "maximum": 20.0},
            "smart_profit_floor": {"type": "number", "minimum": 0.10, "maximum": 20.0},
            "smart_activation_ticks": {"type": "integer", "minimum": 1, "maximum": 100},
            "smart_profit_floor_ticks": {"type": "integer", "minimum": 1, "maximum": 100},
            "smart_min_distance_ticks": {"type": "integer", "minimum": 1, "maximum": 100},
            "smart_server_take_profit": {"type": "number", "minimum": 0.10, "maximum": 100.0},
        },
    }


def optimizer_plan_schema(engine: str, strategy_ids: Iterable[str]) -> Dict[str, Any]:
    candidate = _rf_candidate_schema() if engine == "rise_fall" else _mult_candidate_schema(strategy_ids)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "summary", "confidence", "selected_id", "candidates", "reject_ids", "warnings"],
        "properties": {
            "action": {"type": "string", "enum": ["HOLD", "ADVISE", "SPAWN_CHALLENGERS", "SELECT_PRESET", "ROLLBACK"]},
            "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "selected_id": {"type": "string", "maxLength": 120},
            "candidates": {"type": "array", "maxItems": 4, "items": candidate},
            "reject_ids": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 120}},
            "warnings": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 500}},
        },
    }



def _ollama_think_value(model: str, thinking: str) -> Any:
    level = str(thinking or "none").lower()
    if level == "none":
        return False
    # GPT-OSS supports named reasoning levels. Many other Ollama thinking models
    # accept a boolean, so use True for broad compatibility.
    if "gpt-oss" in str(model).lower() and level in {"low", "medium", "high"}:
        return level
    return True


def call_ollama_optimizer(
    *,
    ollama_url: str,
    model: str,
    thinking: str,
    keep_alive: str,
    engine: str,
    operation_mode: str,
    snapshot: Dict[str, Any],
    strategy_ids: Iterable[str],
    max_candidates: int = 4,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    base = normalize_ollama_url(ollama_url)
    if not str(model or "").strip():
        raise RuntimeError("Choose an Ollama model before using AI Optimizer")
    engine_label = "Rise/Fall 2-tick" if engine == "rise_fall" else "Step 100 Multiplier"
    system = (
        "You are the configuration research optimizer inside a trading automation application. "
        "Never promise profit. Never change stake, account credentials, capital, or order count. "
        "Prefer controlled one-variable or two-variable experiments and large samples. Reject tiny-sample overfitting. "
        "In adaptive or live_trade mode, only select an ID already listed as validated; never invent a live configuration. "
        "Rise/Fall candidates are always 2 ticks. Return only the requested JSON schema."
    )
    user = {
        "task": f"Analyze {engine_label} in {operation_mode} mode and propose the next bounded action.",
        "max_candidates": max(1, min(4, int(max_candidates))),
        "snapshot": snapshot,
    }
    body: Dict[str, Any] = {
        "model": str(model).strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
        ],
        "format": optimizer_plan_schema(engine, strategy_ids),
        "stream": False,
        "keep_alive": str(keep_alive or "10m"),
        "think": _ollama_think_value(model, thinking),
        "options": {"temperature": 0.1},
    }

    def do_request(payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            base + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "ControlNexus-Ollama-Optimizer/2.28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(detail)
                message = parsed.get("error") or detail
            except Exception:
                message = str(exc)
            raise RuntimeError(f"Ollama HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama connection failed: {exc.reason}") from exc

    try:
        data = do_request(body)
    except RuntimeError as first_error:
        # Some models do not expose thinking controls. Retry once without `think`
        # rather than making the entire optimizer unusable with those models.
        if "think" not in str(first_error).lower() and "thinking" not in str(first_error).lower():
            raise
        retry = dict(body)
        retry.pop("think", None)
        data = do_request(retry)

    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise RuntimeError("Ollama returned no optimizer plan")
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama optimizer returned unreadable structured output") from exc
    if not isinstance(plan, dict):
        raise RuntimeError("Ollama optimizer plan was not an object")
    plan["response_id"] = ""
    input_tokens = data.get("prompt_eval_count")
    output_tokens = data.get("eval_count")
    plan["usage"] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (input_tokens + output_tokens) if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None,
    }
    plan["provider"] = "ollama"
    plan["model"] = str(data.get("model") or model)
    return plan

def sanitize_rf_candidate(candidate: Dict[str, Any], *, stake: str = "1.00") -> Dict[str, Any]:
    c = dict(candidate or {})
    mode = str(c.get("decision_mode", "best_odds")).lower()
    if mode not in {"best_odds", "consensus", "momentum", "reversal", "trend"}:
        mode = "best_odds"
    direction = str(c.get("direction_policy", "auto")).lower()
    if direction not in {"auto", "rise_only", "fall_only"}:
        direction = "auto"
    return {
        "label": str(c.get("label") or "AI RF Candidate")[:60],
        "stake": stake,
        "max_open": 1,
        "trades_per_signal": 1,
        "split_delay": 0,
        "duration": 2,
        "duration_unit": "t",
        "decision_mode": mode,
        "direction_policy": direction,
        "min_confidence": round(_clamp_float(c.get("min_confidence"), 0.50, 0.85, 0.66), 3),
        "min_edge": round(_clamp_float(c.get("min_edge"), 0.05, 0.65, 0.30), 3),
        "min_return_pct": 0.0,
        "cooldown_ticks": _clamp_int(c.get("cooldown_ticks"), 1, 20, 4),
        "quote_refresh_ticks": _clamp_int(c.get("quote_refresh_ticks"), 1, 10, 2),
        "lookback": _clamp_int(c.get("lookback"), 8, 80, 20),
        "trend_weight": round(_clamp_float(c.get("trend_weight"), 0.0, 3.0, 0.45), 3),
        "momentum_weight": round(_clamp_float(c.get("momentum_weight"), 0.0, 3.5, 2.0), 3),
        "reversal_weight": round(_clamp_float(c.get("reversal_weight"), 0.0, 2.0, 0.05), 3),
        "breakout_weight": round(_clamp_float(c.get("breakout_weight"), 0.0, 2.0, 0.30), 3),
        "ai_rationale": str(c.get("rationale") or "")[:500],
    }


def sanitize_multiplier_candidate(
    candidate: Dict[str, Any], *, strategy_ids: Iterable[str], stake: str = "1.00", max_open: int = 1,
    trades_per_signal: int = 1, split_delay: float = 0.5,
) -> Dict[str, Any]:
    c = dict(candidate or {})
    strategies = tuple(strategy_ids)
    strategy = str(c.get("strategy", strategies[0] if strategies else "ema_rsi")).lower()
    if strategy not in strategies:
        strategy = strategies[0] if strategies else "ema_rsi"
    profile = str(c.get("profile", "balanced")).lower()
    if profile not in {"balanced", "fast", "strict"}:
        profile = "balanced"
    multiplier = _clamp_int(c.get("multiplier"), 750, 7500, 750)
    if multiplier not in {750, 2000, 3500, 5500, 7500}:
        multiplier = min((750, 2000, 3500, 5500, 7500), key=lambda x: abs(x - multiplier))
    exit_mode = str(c.get("exit_mode", "hybrid")).lower()
    if exit_mode not in {"fixed", "adaptive", "smart", "hybrid"}:
        exit_mode = "hybrid"
    tp_unit = str(c.get("take_profit_unit", "amount")).lower()
    if tp_unit not in {"amount", "ticks"}:
        tp_unit = "amount"
    tp = round(_clamp_float(c.get("take_profit"), 0.10, 20.0, 0.10), 2)
    tp_max = round(max(tp, _clamp_float(c.get("take_profit_max"), 0.10, 50.0, 0.30)), 2)
    tp_ticks = _clamp_int(c.get("take_profit_ticks"), 1, 100, 3)
    tp_ticks_max = max(tp_ticks, _clamp_int(c.get("take_profit_ticks_max"), 1, 200, 8))
    smart_activation = round(_clamp_float(c.get("smart_activation"), 0.10, 20.0, 0.15), 2)
    smart_floor = round(min(smart_activation, _clamp_float(c.get("smart_profit_floor"), 0.10, 20.0, 0.10)), 2)
    return {
        "label": str(c.get("label") or "AI Mult Candidate")[:60],
        "strategy": strategy,
        "profile": profile,
        "stake": stake,
        "max_open": max_open,
        "trades_per_signal": min(max_open, max(1, trades_per_signal)),
        "split_delay": split_delay,
        "multiplier": multiplier,
        "exit_mode": exit_mode,
        "take_profit_unit": tp_unit,
        "take_profit": f"{tp:.2f}",
        "take_profit_max": f"{tp_max:.2f}",
        "take_profit_ticks": tp_ticks,
        "take_profit_ticks_max": tp_ticks_max,
        "smart_activation": f"{smart_activation:.2f}",
        "smart_profit_floor": f"{smart_floor:.2f}",
        "smart_activation_ticks": _clamp_int(c.get("smart_activation_ticks"), 1, 100, 3),
        "smart_profit_floor_ticks": _clamp_int(c.get("smart_profit_floor_ticks"), 1, 100, 1),
        "smart_min_distance_ticks": _clamp_int(c.get("smart_min_distance_ticks"), 1, 100, 1),
        "smart_server_take_profit": f"{_clamp_float(c.get('smart_server_take_profit'), 0.10, 100.0, 2.0):.2f}",
        "ai_rationale": str(c.get("rationale") or "")[:500],
    }


def initial_rf_candidates(stake: str) -> list[Dict[str, Any]]:
    seeds = [
        {"label":"AI RF BO Selective","rationale":"Repeat the strongest selective best-odds region.","decision_mode":"best_odds","direction_policy":"auto","min_confidence":.68,"min_edge":.32,"cooldown_ticks":5,"quote_refresh_ticks":2,"lookback":20,"trend_weight":.45,"momentum_weight":2.0,"reversal_weight":.05,"breakout_weight":.30},
        {"label":"AI RF Consensus Loose","rationale":"Retest the promising consensus family with moderate gates.","decision_mode":"consensus","direction_policy":"auto","min_confidence":.64,"min_edge":.28,"cooldown_ticks":3,"quote_refresh_ticks":2,"lookback":16,"trend_weight":.70,"momentum_weight":1.80,"reversal_weight":.10,"breakout_weight":.40},
        {"label":"AI RF BO Stable","rationale":"Slightly slower best-odds control candidate.","decision_mode":"best_odds","direction_policy":"auto","min_confidence":.69,"min_edge":.34,"cooldown_ticks":6,"quote_refresh_ticks":2,"lookback":24,"trend_weight":.45,"momentum_weight":2.0,"reversal_weight":.05,"breakout_weight":.30},
        {"label":"AI RF BO Fast","rationale":"Faster lookback/cooldown challenger around best-odds.","decision_mode":"best_odds","direction_policy":"auto","min_confidence":.66,"min_edge":.30,"cooldown_ticks":3,"quote_refresh_ticks":1,"lookback":14,"trend_weight":.45,"momentum_weight":2.0,"reversal_weight":.05,"breakout_weight":.30},
    ]
    return [sanitize_rf_candidate(x, stake=stake) for x in seeds]


def initial_multiplier_candidates(strategy_ids: Iterable[str], *, stake: str, max_open: int, trades_per_signal: int, split_delay: float) -> list[Dict[str, Any]]:
    strategies = list(strategy_ids)
    preferred = [x for x in ("ema_rsi", "hammer", "donchian", "tick_momentum") if x in strategies]
    while len(preferred) < 4 and strategies:
        item = strategies[len(preferred) % len(strategies)]
        if item not in preferred:
            preferred.append(item)
        else:
            break
    seeds = []
    for idx, strategy in enumerate(preferred[:4], 1):
        seeds.append({
            "label": f"AI M{idx} {strategy}", "rationale": "Broad first-stage strategy/profile screening.",
            "strategy": strategy, "profile": "balanced" if idx % 2 else "strict", "multiplier": 750,
            "exit_mode": "hybrid", "take_profit_unit": "amount", "take_profit": .10, "take_profit_max": .30,
            "take_profit_ticks": 3, "take_profit_ticks_max": 8, "smart_activation": .15, "smart_profit_floor": .10,
            "smart_activation_ticks": 3, "smart_profit_floor_ticks": 1, "smart_min_distance_ticks": 1,
            "smart_server_take_profit": 2.0,
        })
    return [sanitize_multiplier_candidate(x, strategy_ids=strategies, stake=stake, max_open=max_open, trades_per_signal=trades_per_signal, split_delay=split_delay) for x in seeds]


def fallback_refine_rf(leader: Dict[str, Any], *, stake: str) -> list[Dict[str, Any]]:
    base = sanitize_rf_candidate(leader, stake=stake)
    values = []
    for name, dc, de, dlb, dcd in [
        ("Conf+", .02, 0, 0, 0), ("Edge+", 0, .02, 0, 0), ("LB+", 0, 0, 4, 0), ("Cool+", 0, 0, 0, 1)
    ]:
        item = dict(base)
        item["label"] = f"{str(base.get('label','Leader'))[:38]} {name}"
        item["min_confidence"] = round(_clamp_float(item["min_confidence"] + dc, .50, .85, .66), 3)
        item["min_edge"] = round(_clamp_float(item["min_edge"] + de, .05, .65, .30), 3)
        item["lookback"] = _clamp_int(item["lookback"] + dlb, 8, 80, 20)
        item["cooldown_ticks"] = _clamp_int(item["cooldown_ticks"] + dcd, 1, 20, 4)
        values.append(item)
    return values


def fallback_refine_multiplier(leader: Dict[str, Any], *, strategy_ids: Iterable[str], stake: str, max_open: int, trades_per_signal: int, split_delay: float) -> list[Dict[str, Any]]:
    base = sanitize_multiplier_candidate(leader, strategy_ids=strategy_ids, stake=stake, max_open=max_open, trades_per_signal=trades_per_signal, split_delay=split_delay)
    results = []
    profiles = [base["profile"], "fast", "strict", "balanced"]
    multipliers = [base["multiplier"], 2000 if base["multiplier"] == 750 else 750, base["multiplier"], base["multiplier"]]
    modes = [base["exit_mode"], base["exit_mode"], "smart", "hybrid"]
    for i in range(4):
        item = dict(base)
        item["label"] = f"{str(base.get('label','Leader'))[:36]} R{i+1}"
        item["profile"] = profiles[i]
        item["multiplier"] = multipliers[i]
        item["exit_mode"] = modes[i]
        if i == 2:
            item["smart_activation_ticks"] = max(1, int(item.get("smart_activation_ticks", 3)) - 1)
        if i == 3:
            item["take_profit_max"] = f"{max(float(item['take_profit']), min(2.0, float(item['take_profit_max']) + .10)):.2f}"
        results.append(item)
    return results
