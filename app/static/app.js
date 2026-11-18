const UI_TOKEN = document.body.dataset.uiToken;
const APP_ID_DISPLAY_KEY = "control-nexus-app-id-display";
let bootstrapData = null;
let lastLogId = 0;
let currentConfig = null;
let pollTimer = null;
let clockTimer = null;
let strategyCatalog = [];
let strategyProfiles = {};
let equitySeries = [];
let savedFullPresets = [];
let refreshIntervalMs = 1500;
let riseFallLogId = 0;
let riseFallLastStatus = null;
let multiplierLastStatus = null;
let savedRiseFallPresets = [];
let rfLabCounter = 0;
let rfLabLastStatus = { bots: [], running: false };
let rfLabLogIds = {};
let rfLabSelectedConsole = "";
const ENGINE_UI_KEY = "controlNexusEngineUiV223";
const quoteHistory = [];
let latestAdvancedStatus = {};
let latestAdvancedTrades = { rows: [], summary: {} };
let advancedLayoutEditing = false;
let advancedControlsDirty = false;
let advancedStrategyDirty = false;
const ADV_DASH_MODE_KEY = "controlNexusDashboardModeV219";
const ADV_LAYOUT_KEY = "controlNexusAdvancedLayoutV219";
const ADV_LAYOUT_PRESETS_KEY = "controlNexusAdvancedLayoutPresetsV219";
const BEST_MIX_STRATEGIES = ["bollinger_reversion", "stochastic", "hammer", "ema_rsi", "donchian", "tick_momentum"];


// V2.18.7 — merged Ideas UI: full theme engine, portable Full Presets, direct Deploy controls, streamlined risk + commission disclosure, app-close backend lifecycle, adjustable refresh speed, UI themes/images.
const RISK_COMMISSION_ACCEPT_KEY = "controlNexusRiskCommissionAcceptedV1";
let pendingDeployAfterRiskAcceptance = false;
// V2.18.5 — configurable exact trades-per-signal control on mobile/desktop and Strategy Lab.
// V2.18.4 — miniature definition-candle diagrams for every Candlestick Studio subtype (mobile fit corrected).
// Values are [openY, closeY, highY, lowY, tone] on a 0-100 SVG coordinate system.
// Smaller Y values are visually higher on the candle.
const CANDLE_ICON_RECIPES = {
  hammer: [[42, 30, 22, 92, "neutral"]],
  bullish_hammer: [[44, 28, 20, 94, "bull"]],
  bearish_hammer: [[28, 44, 20, 94, "bear"]],
  inverted_hammer: [[70, 82, 8, 90, "neutral"]],
  bullish_inverted_hammer: [[82, 68, 8, 91, "bull"]],
  bearish_inverted_hammer: [[68, 82, 8, 91, "bear"]],
  hanging_man: [[34, 47, 25, 94, "neutral"]],
  bullish_hanging_man: [[47, 33, 24, 94, "bull"]],
  bearish_hanging_man: [[32, 47, 24, 94, "bear"]],
  shooting_star: [[70, 83, 7, 91, "neutral"]],
  bullish_shooting_star: [[84, 69, 7, 92, "bull"]],
  bearish_shooting_star: [[68, 84, 7, 92, "bear"]],
  bullish_upper_wick_rejection: [[75, 48, 7, 86, "bull"]],
  bearish_upper_wick_rejection: [[48, 76, 7, 86, "bear"]],
  bullish_lower_wick_rejection: [[56, 30, 18, 94, "bull"]],
  bearish_lower_wick_rejection: [[30, 57, 18, 94, "bear"]],
  flat_bottom_bullish_rejection: [[83, 48, 8, 84, "bull"]],
  flat_bottom_bearish_rejection: [[48, 83, 8, 84, "bear"]],
  bullish_belt_hold: [[86, 22, 16, 88, "bull"]],
  bearish_belt_hold: [[20, 84, 14, 88, "bear"]],

  standard_doji: [[50, 51, 24, 78, "neutral"]],
  dragonfly_doji: [[28, 29, 24, 93, "neutral"]],
  gravestone_doji: [[74, 75, 7, 80, "neutral"]],
  long_legged_doji: [[50, 51, 7, 94, "neutral"]],
  rickshaw_man: [[50, 50, 8, 92, "neutral"]],
  four_price_doji: [[50, 50, 49, 51, "neutral"]],
  spinning_top: [[58, 42, 18, 83, "bull"]],
  high_wave: [[54, 47, 5, 96, "neutral"]],

  bullish_engulfing: [
    [32, 66, 24, 75, "bear"],
    [75, 23, 17, 82, "bull"]
  ],
  bearish_engulfing: [
    [67, 31, 23, 74, "bull"],
    [22, 76, 15, 83, "bear"]
  ],
  bullish_harami: [
    [25, 77, 18, 84, "bear"],
    [61, 43, 37, 67, "bull"]
  ],
  bearish_harami: [
    [78, 24, 17, 84, "bull"],
    [42, 61, 36, 68, "bear"]
  ],
  piercing_line: [
    [24, 78, 17, 85, "bear"],
    [84, 42, 35, 90, "bull"]
  ],
  dark_cloud_cover: [
    [78, 24, 17, 84, "bull"],
    [18, 58, 12, 65, "bear"]
  ],
  tweezer_bottom: [
    [31, 70, 23, 88, "bear"],
    [70, 38, 31, 88, "bull"]
  ],
  tweezer_top: [
    [70, 34, 13, 77, "bull"],
    [34, 68, 13, 75, "bear"]
  ],

  morning_star: [
    [23, 76, 16, 83, "bear"],
    [72, 66, 58, 82, "neutral"],
    [70, 27, 21, 78, "bull"]
  ],
  evening_star: [
    [77, 24, 17, 84, "bull"],
    [29, 35, 19, 45, "neutral"],
    [28, 72, 20, 80, "bear"]
  ],
  morning_doji_star: [
    [23, 76, 16, 83, "bear"],
    [69, 70, 57, 82, "neutral"],
    [70, 27, 21, 78, "bull"]
  ],
  evening_doji_star: [
    [77, 24, 17, 84, "bull"],
    [31, 32, 18, 45, "neutral"],
    [28, 72, 20, 80, "bear"]
  ],

  bullish_marubozu: [[86, 14, 11, 89, "bull"]],
  bearish_marubozu: [[14, 86, 11, 89, "bear"]],
  three_white_soldiers: [
    [78, 56, 50, 84, "bull"],
    [62, 36, 30, 68, "bull"],
    [43, 16, 10, 49, "bull"]
  ],
  three_black_crows: [
    [20, 43, 14, 50, "bear"],
    [36, 62, 30, 69, "bear"],
    [56, 82, 50, 89, "bear"]
  ]
};


// V2.18.3 — make the exact trade direction of every Candlestick Studio subtype explicit.
// "context" patterns can resolve to MULTUP or MULTDOWN depending on the prior candle trend.
const CANDLE_TRADE_DIRECTIONS = {
  hammer: { side: "up", label: "BUY UP", detail: "Either body → UP" },
  bullish_hammer: { side: "up", label: "BUY UP", detail: "Green body → UP" },
  bearish_hammer: { side: "up", label: "BUY UP", detail: "Red body → UP" },
  inverted_hammer: { side: "up", label: "BUY UP", detail: "Either body → UP" },
  bullish_inverted_hammer: { side: "up", label: "BUY UP", detail: "Green body → UP" },
  bearish_inverted_hammer: { side: "up", label: "BUY UP", detail: "Red body → UP" },
  hanging_man: { side: "down", label: "BUY DOWN", detail: "Either body → DOWN" },
  bullish_hanging_man: { side: "down", label: "BUY DOWN", detail: "Green body → DOWN" },
  bearish_hanging_man: { side: "down", label: "BUY DOWN", detail: "Red body → DOWN" },
  shooting_star: { side: "down", label: "BUY DOWN", detail: "Either body → DOWN" },
  bullish_shooting_star: { side: "down", label: "BUY DOWN", detail: "Green body → DOWN" },
  bearish_shooting_star: { side: "down", label: "BUY DOWN", detail: "Red body → DOWN" },
  bullish_upper_wick_rejection: { side: "up", label: "BUY UP", detail: "Green body → UP" },
  bearish_upper_wick_rejection: { side: "down", label: "BUY DOWN", detail: "Red body → DOWN" },
  bullish_lower_wick_rejection: { side: "up", label: "BUY UP", detail: "Green body → UP" },
  bearish_lower_wick_rejection: { side: "down", label: "BUY DOWN", detail: "Red body → DOWN" },
  flat_bottom_bullish_rejection: { side: "up", label: "BUY UP", detail: "Green body → UP" },
  flat_bottom_bearish_rejection: { side: "down", label: "BUY DOWN", detail: "Red body → DOWN" },
  bullish_belt_hold: { side: "up", label: "BUY UP", detail: "Bullish continuation" },
  bearish_belt_hold: { side: "down", label: "BUY DOWN", detail: "Bearish continuation" },

  standard_doji: { side: "context", label: "UP / DOWN", detail: "Prior trend decides" },
  dragonfly_doji: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  gravestone_doji: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },
  long_legged_doji: { side: "context", label: "UP / DOWN", detail: "Prior trend decides" },
  rickshaw_man: { side: "context", label: "UP / DOWN", detail: "Prior trend decides" },
  four_price_doji: { side: "context", label: "UP / DOWN", detail: "Prior trend decides" },
  spinning_top: { side: "context", label: "UP / DOWN", detail: "Prior trend decides" },
  high_wave: { side: "context", label: "UP / DOWN", detail: "Prior trend decides" },

  bullish_engulfing: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  bearish_engulfing: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },
  bullish_harami: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  bearish_harami: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },
  piercing_line: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  dark_cloud_cover: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },
  tweezer_bottom: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  tweezer_top: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },

  morning_star: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  evening_star: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },
  morning_doji_star: { side: "up", label: "BUY UP", detail: "Bullish reversal" },
  evening_doji_star: { side: "down", label: "BUY DOWN", detail: "Bearish reversal" },

  bullish_marubozu: { side: "up", label: "BUY UP", detail: "Bullish momentum" },
  bearish_marubozu: { side: "down", label: "BUY DOWN", detail: "Bearish momentum" },
  three_white_soldiers: { side: "up", label: "BUY UP", detail: "Bullish momentum" },
  three_black_crows: { side: "down", label: "BUY DOWN", detail: "Bearish momentum" }
};

function candleIconSvg(patternId) {
  const candles = CANDLE_ICON_RECIPES[patternId] || [[65, 35, 18, 84, "neutral"]];
  const count = candles.length;
  // Keep every recipe inside a generous horizontal safe area. V2.18.3 placed
  // single-candle bodies too far to the right, which could look clipped on narrow
  // Android cards. These coordinates center 1/2/3-candle patterns consistently.
  const positions = count === 1 ? [26.5] : count === 2 ? [14, 43] : [5, 30, 55];
  const bodyWidth = count === 1 ? 19 : count === 2 ? 15 : 12;
  const toneClass = (tone) => `pattern-svg-${tone === "bull" ? "bull" : tone === "bear" ? "bear" : "neutral"}`;
  const parts = candles.map((c, index) => {
    const [openY, closeY, highY, lowY, tone] = c;
    const x = positions[index] ?? (14 + index * 22);
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(2.5, Math.abs(closeY - openY));
    const center = x + bodyWidth / 2;
    const cls = toneClass(tone);
    return `<g class="${cls}"><line class="pattern-svg-wick" x1="${center}" y1="${highY}" x2="${center}" y2="${lowY}"/><rect class="pattern-svg-body" x="${x}" y="${bodyTop}" width="${bodyWidth}" height="${bodyHeight}" rx="1.5"/></g>`;
  }).join("");
  return `<svg viewBox="0 0 72 100" preserveAspectRatio="xMidYMid meet" role="img" focusable="false" aria-hidden="true">${parts}</svg>`;
}

function initCandlestickSubtypeIcons() {
  $$(".hammer-pattern-toggle").forEach((input) => {
    const label = input.closest("label");
    if (!label || label.querySelector(".pattern-visual")) return;
    const copy = label.querySelector(":scope > span");
    if (copy) copy.classList.add("pattern-copy");

    const info = CANDLE_TRADE_DIRECTIONS[input.value] || { side: "context", label: "UP / DOWN", detail: "Context decides" };
    const visual = document.createElement("span");
    visual.className = "pattern-visual";
    visual.dataset.patternVisual = input.value;

    const icon = document.createElement("span");
    icon.className = "pattern-candle-icon";
    icon.dataset.patternIcon = input.value;
    icon.innerHTML = candleIconSvg(input.value);

    const badge = document.createElement("span");
    badge.className = `pattern-direction pattern-direction-${info.side}`;
    badge.dataset.patternDirection = input.value;
    badge.innerHTML = `<strong>${info.label}</strong><small>${info.detail}</small>`;
    badge.title = `${info.label} — ${info.detail}`;

    visual.append(icon, badge);
    input.insertAdjacentElement("afterend", visual);
  });
}
let labBotCounter = 0;
let labLastStatus = { bots: [], running: false };
const labLogIds = {};
let labSelectedConsoleBot = "";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function toast(message, type = "success") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast visible ${type}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.className = "toast"; }, 4200);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["X-WebUI-Token"] = UI_TOKEN;
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let data;
  try { data = await response.json(); } catch { data = { ok: false, error: `HTTP ${response.status}` }; }
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

const RF_UI_TABS = new Set(["rise-fall", "rf-lab"]);
const MULT_UI_TABS = new Set(["dashboard", "session", "strategy", "lab", "history"]);
function switchEngine(engine, chooseDefault = true) {
  engine = engine === "rise-fall" ? "rise-fall" : "multiplier";
  document.body.dataset.engineUi = engine;
  localStorage.setItem(ENGINE_UI_KEY, engine);
  $$(".engine-tab").forEach((button) => button.classList.toggle("active", button.dataset.engine === engine));
  $$('[data-engine-nav]').forEach((nav) => nav.classList.toggle("hidden", nav.dataset.engineNav !== engine));
  if (!chooseDefault) return;
  const active = document.querySelector(".tab-panel.active")?.id?.replace("tab-", "") || "";
  if (engine === "rise-fall" && !RF_UI_TABS.has(active) && active !== "credentials") switchTab("rise-fall", false);
  if (engine === "multiplier" && !MULT_UI_TABS.has(active) && active !== "credentials") switchTab("dashboard", false);
  syncActiveEngineTopbar();
}
function switchTab(name, syncEngine = true) {
  if (syncEngine && RF_UI_TABS.has(name)) switchEngine("rise-fall", false);
  if (syncEngine && MULT_UI_TABS.has(name)) switchEngine("multiplier", false);
  if (typeof syncMobileNav === "function") syncMobileNav(name);
  $$(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === name));
  $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
  document.body.classList.toggle("mobile-config-active", name === "strategy");
  document.querySelector("#mobileConfigDock")?.setAttribute("aria-hidden", name === "strategy" ? "false" : "true");
  if (name === "dashboard") requestAnimationFrame(drawCurrentCharts);
  if (name === "rise-fall") requestAnimationFrame(()=>drawRiseFallChart(riseFallLastStatus?.telemetry?.recent_prices||[]));
}

function formToObject(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  form.querySelectorAll('input[type="checkbox"]').forEach((input) => { data[input.name] = input.checked; });
  return data;
}

function setFormValues(form, values) {
  Object.entries(values || {}).forEach(([key, value]) => {
    const input = form.elements.namedItem(key);
    if (!input) return;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
  });
}

function setNested(target, path, value) {
  const parts = path.split(".");
  let cursor = target;
  parts.slice(0, -1).forEach((part) => {
    if (!cursor[part] || typeof cursor[part] !== "object") cursor[part] = {};
    cursor = cursor[part];
  });
  cursor[parts.at(-1)] = value;
}

function getNested(target, path) {
  return path.split(".").reduce((value, part) => value?.[part], target);
}

function numericValue(input) {
  if (input.value === "") return "";
  return Number(input.value);
}

function toNumber(value, fallback = 0) {
  if (typeof value === "number") return Number.isFinite(value) ? value : fallback;
  const cleaned = String(value ?? "").replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  const parsed = cleaned ? Number(cleaned[0]) : NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatNumber(value, digits = 2) {
  const number = toNumber(value, 0);
  return number.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// ===== V2.18.6 full presets =====
function renderFullPresets(presets = [], selectedName = "") {
  savedFullPresets = Array.isArray(presets) ? presets : [];
  const select = $("#fullPresetSelect");
  if (!select) return;
  const previous = selectedName || select.value;
  select.innerHTML = '<option value="">Choose a preset…</option>' + savedFullPresets.map((item) => {
    const detail = [item.strategy_engine === "single" ? item.strategy : item.strategy_engine, item.take_profit_mode].filter(Boolean).join(" · ");
    return `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}${detail ? ` — ${escapeHtml(detail)}` : ""}</option>`;
  }).join("");
  if (savedFullPresets.some((item) => item.name === previous)) select.value = previous;
  $("#presetCountBadge").textContent = `${savedFullPresets.length} saved`;
  $("#deleteFullPresetButton").disabled = !select.value;
  $("#downloadFullPresetButton").disabled = !select.value;
}

function applyLoadedPresetToForms(result) {
  const session = result.session || {};
  const config = result.config || {};
  setFormValues($("#sessionForm"), session);
  const liveConfirm = $("#sessionForm")?.elements?.namedItem("live_confirmation");
  if (liveConfirm) liveConfirm.value = "";
  renderEnsembleStrategyGrid(session);
  applyEnsembleMode(false);
  if (session.strategy && session.strategy !== "ensemble") {
    $("#sessionStrategy").value = session.strategy;
    $("#configStrategySelect").value = session.strategy;
  }
  populateConfigForm(config);
  updateSessionPreview();
}

async function saveFullPreset() {
  const selected = $("#fullPresetSelect")?.value || "";
  const name = String($("#fullPresetName")?.value || selected).trim();
  if (!name) { toast("Enter a preset name first", "error"); $("#fullPresetName")?.focus(); return; }
  if (savedFullPresets.some((item) => item.name === name) && !confirm(`Overwrite saved preset “${name}”?`)) return;
  try {
    syncEnsembleHiddenInputs();
    const session = formToObject($("#sessionForm"));
    session.live_confirmation = "";
    const config = readConfigForm();
    const result = await api("/api/presets/save", { method: "POST", body: JSON.stringify({ name, session, config }) });
    renderFullPresets(result.presets || [], name);
    $("#fullPresetName").value = name;
    toast(`Preset “${name}” saved with Session + Strategy settings`, "success");
  } catch (error) { toast(error.message, "error"); }
}

async function loadFullPreset(name = "") {
  name = String(name || $("#fullPresetSelect")?.value || "").trim();
  if (!name) return;
  try {
    const result = await api("/api/presets/apply", { method: "POST", body: JSON.stringify({ name }) });
    applyLoadedPresetToForms(result);
    renderFullPresets(result.presets || [], name);
    $("#fullPresetName").value = name;
    toast(`Preset “${name}” loaded — Session and Strategy config restored`, "success");
  } catch (error) {
    toast(error.message, "error");
    renderFullPresets(savedFullPresets, "");
  }
}

async function deleteFullPreset() {
  const name = String($("#fullPresetSelect")?.value || "").trim();
  if (!name) return;
  if (!confirm(`Delete saved preset “${name}”?`)) return;
  try {
    const result = await api("/api/presets/delete", { method: "POST", body: JSON.stringify({ name }) });
    renderFullPresets(result.presets || [], "");
    if ($("#fullPresetName").value === name) $("#fullPresetName").value = "";
    toast(`Preset “${name}” deleted`, "success");
  } catch (error) { toast(error.message, "error"); }
}

function safePresetFilename(name) {
  const cleaned = String(name || "Control Nexus Preset")
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return `${cleaned || "Control Nexus Preset"}.json`;
}

async function downloadFullPreset() {
  const name = String($("#fullPresetSelect")?.value || "").trim();
  if (!name) { toast("Choose a saved preset first", "error"); return; }
  try {
    const result = await api(`/api/presets/export?name=${encodeURIComponent(name)}`);
    const payload = JSON.stringify(result.export, null, 2);
    const blob = new Blob([payload + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = safePresetFilename(name);
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Preset “${name}” downloaded`, "success");
  } catch (error) { toast(error.message, "error"); }
}

async function importFullPresetFile(file) {
  if (!file) return;
  try {
    const raw = await file.text();
    let presetFile;
    try { presetFile = JSON.parse(raw); }
    catch { throw new Error("That file is not valid JSON"); }
    if (!presetFile || typeof presetFile !== "object" || Array.isArray(presetFile)) throw new Error("Preset file must contain a JSON object");
    const importedName = String(presetFile.name || "").trim();
    if (!importedName) throw new Error("Preset file does not contain a preset name");
    const exists = savedFullPresets.some((item) => item.name === importedName);
    let overwrite = false;
    if (exists) {
      overwrite = confirm(`Preset “${importedName}” already exists. Overwrite it with the imported configuration?`);
      if (!overwrite) return;
    }
    const result = await api("/api/presets/import", {
      method: "POST",
      body: JSON.stringify({ preset_file: presetFile, overwrite }),
    });
    applyLoadedPresetToForms(result);
    renderFullPresets(result.presets || [], result.name || importedName);
    $("#fullPresetName").value = result.name || importedName;
    toast(`Preset “${result.name || importedName}” imported and loaded`, "success");
  } catch (error) { toast(error.message, "error"); }
}

// ===== V3 UI settings — theme engine (Glass / Wood / Forest / Cyber / Water / Gamer) =====
const UI_PREFS_KEY = "control-nexus-ui-v3";
const UI_THEMES = {
  glass:  { label: "Glass",  accent: "#6ea8ff", accent2: "#8b5cf6", highlight: "#7ffcf1", background: "#0a0f1a" },
  wood:   { label: "Wood",   accent: "#d4a24c", accent2: "#8a4b25", highlight: "#f2d9a0", background: "#170d07" },
  forest: { label: "Forest", accent: "#5fbf6a", accent2: "#2e6b4f", highlight: "#ffd166", background: "#081a10" },
  cyber:  { label: "Cyber",  accent: "#ff3490", accent2: "#8c2cff", highlight: "#31e7d0", background: "#030407" },
  water:  { label: "Water",  accent: "#2fb8e6", accent2: "#0e6ba8", highlight: "#7be0c9", background: "#03141d" },
  gamer:  { label: "Gamer",  accent: "#ff2d55", accent2: "#5e5ce6", highlight: "#30d158", background: "#030303" },
};
// Ambient particle recipe per theme style: class name + how many to spawn.
const FX_PARTICLES = {
  glass:  { cls: "fx-mote",    count: 10 },
  wood:   { cls: "fx-ember",   count: 9  },
  forest: { cls: "fx-firefly", count: 14 },
  cyber:  { cls: "fx-glitch",  count: 7  },
  water:  { cls: "fx-bubble",  count: 16 },
  gamer:  { cls: "fx-led",     count: 5  },
};
const DEFAULT_UI_PREFS = { theme: "glass", style: "glass", refreshMs: 1500, ...UI_THEMES["glass"], heroImage: "", backgroundImage: "", backgroundImageOpacity: 20 };
let uiPrefs = { ...DEFAULT_UI_PREFS };
let appliedFxStyle = null;

function hexRgb(hex) {
  const raw = String(hex || "").replace("#", "");
  const normalized = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw.padEnd(6, "0").slice(0, 6);
  return [parseInt(normalized.slice(0,2),16)||0, parseInt(normalized.slice(2,4),16)||0, parseInt(normalized.slice(4,6),16)||0];
}
function rgba(hex, alpha) { const [r,g,b] = hexRgb(hex); return `rgba(${r},${g},${b},${alpha})`; }

function loadUiPrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(UI_PREFS_KEY) || "{}");
    if (parsed && typeof parsed === "object") uiPrefs = { ...DEFAULT_UI_PREFS, ...parsed };
  } catch { uiPrefs = { ...DEFAULT_UI_PREFS }; }
  refreshIntervalMs = Math.max(500, Math.min(10000, Number(uiPrefs.refreshMs || 1500)));
}
function persistUiPrefs() {
  try { localStorage.setItem(UI_PREFS_KEY, JSON.stringify(uiPrefs)); } catch { toast("UI image is too large for local storage", "error"); }
}

function applyUiPrefs({ restart = true } = {}) {
  const root = document.documentElement;
  const style = uiPrefs.style && UI_THEMES[uiPrefs.style] ? uiPrefs.style : "glass";
  root.dataset.theme = style;
  root.style.setProperty("--accent", uiPrefs.accent);
  root.style.setProperty("--accent-2", uiPrefs.accent2);
  root.style.setProperty("--cyan", uiPrefs.highlight);
  root.style.setProperty("--bg", uiPrefs.background);
  root.style.setProperty("--bg-deep", uiPrefs.background);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", uiPrefs.background);
  root.style.setProperty("--ui-accent-soft", rgba(uiPrefs.accent, .18));
  root.style.setProperty("--ui-accent-border", rgba(uiPrefs.accent, .42));
  const hero = $("#heroArtwork");
  if (hero) hero.src = uiPrefs.heroImage || "/static/dr_exploit_hero.png";
  const strength = Math.max(0, Math.min(70, Number(uiPrefs.backgroundImageOpacity || 0)));
  if (uiPrefs.backgroundImage) {
    // A user-uploaded background image takes precedence over the theme's own
    // ambient gradient/texture — composite it with an accent-tinted overlay.
    const overlay = 1 - (strength / 100);
    document.body.style.backgroundImage = `linear-gradient(${rgba(uiPrefs.background, overlay)}, ${rgba(uiPrefs.background, overlay)}), url("${uiPrefs.backgroundImage}")`;
    document.body.style.backgroundSize = "cover, cover";
    document.body.style.backgroundPosition = "center, center";
    document.body.style.backgroundAttachment = "fixed, fixed";
  } else {
    // No custom image: clear the inline override so the theme's own CSS
    // background (grain / canopy / grid / caustics / aurora) shows through.
    document.body.style.backgroundImage = "";
    document.body.style.backgroundSize = "";
    document.body.style.backgroundPosition = "";
    document.body.style.backgroundAttachment = "";
  }
  ensureThemeFx(style);
  refreshIntervalMs = Math.max(500, Math.min(10000, Number(uiPrefs.refreshMs || 1500)));
  const label = $("#refreshSpeedLabel");
  if (label) label.textContent = refreshIntervalMs < 1000 ? `${refreshIntervalMs} ms` : `${(refreshIntervalMs / 1000).toFixed(refreshIntervalMs % 1000 ? 2 : 0).replace(/0+$/,'').replace(/\.$/,'')} sec`;
  syncUiSettingsControls();
  if (restart && pollTimer) restartPolling();
}

// Builds (and rebuilds only on change) the ambient particle layer for the
// active theme style — fireflies, bubbles, embers, glitch flecks, LEDs, or
// drifting light motes. Respects prefers-reduced-motion by spawning nothing.
function ensureThemeFx(style) {
  const fx = $("#themeFx");
  if (!fx || appliedFxStyle === style) return;
  appliedFxStyle = style;
  fx.querySelectorAll(".fx-particle").forEach((el) => el.remove());
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const spec = FX_PARTICLES[style];
  if (!spec || !spec.count || reduceMotion) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < spec.count; i += 1) {
    const el = document.createElement("div");
    el.className = `fx-particle ${spec.cls}`;
    const duration = (10 + Math.random() * 14).toFixed(2);
    const delay = (Math.random() * -duration).toFixed(2);
    const drift = (Math.random() * 40 - 20).toFixed(1);
    const size = spec.cls === "fx-bubble" ? (4 + Math.random() * 10).toFixed(1)
      : spec.cls === "fx-led" ? "6"
      : (2 + Math.random() * 3).toFixed(1);
    el.style.animationDelay = `${delay}s`;
    el.style.animationDuration = `${duration}s`;
    el.style.setProperty("--drift", `${drift}px`);
    el.style.setProperty("--size", `${size}px`);
    if (spec.cls === "fx-led") {
      el.style.top = `${Math.random() * 100}%`;
      el.style.left = i % 2 === 0 ? "10px" : "calc(100% - 10px)";
    } else if (spec.cls === "fx-glitch") {
      el.style.top = `${Math.random() * 100}%`;
      el.style.left = `${Math.random() * 100}%`;
    } else {
      el.style.left = `${Math.random() * 100}%`;
    }
    frag.appendChild(el);
  }
  fx.appendChild(frag);
}

function syncUiSettingsControls() {
  if (!$("#uiSettingsDialog")) return;
  const activePreset = UI_THEMES[uiPrefs.theme] ? uiPrefs.theme : null;
  $$(".theme-swatch").forEach((btn) => btn.classList.toggle("active", btn.dataset.themeChoice === activePreset));
  $("#uiRefreshSpeed").value = String(refreshIntervalMs);
  $("#uiAccentColor").value = uiPrefs.accent;
  $("#uiAccent2Color").value = uiPrefs.accent2;
  $("#uiHighlightColor").value = uiPrefs.highlight;
  $("#uiBackgroundColor").value = uiPrefs.background;
  $("#uiBackgroundImageOpacity").value = String(uiPrefs.backgroundImageOpacity || 0);
  $("#uiBackgroundImageOpacityLabel").textContent = `${uiPrefs.backgroundImageOpacity || 0}%`;
}
function restartPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(poll, refreshIntervalMs);
}
function openUiSettings() {
  syncUiSettingsControls();
  const dialog = $("#uiSettingsDialog");
  if (dialog?.showModal) dialog.showModal();
}
function setThemePreset(name) {
  if (!UI_THEMES[name]) return;
  uiPrefs = { ...uiPrefs, theme: name, style: name, ...UI_THEMES[name] };
  persistUiPrefs();
  applyUiPrefs();
}
function markCustomThemeAndApply() {
  uiPrefs.theme = "custom";
  uiPrefs.accent = $("#uiAccentColor").value;
  uiPrefs.accent2 = $("#uiAccent2Color").value;
  uiPrefs.highlight = $("#uiHighlightColor").value;
  uiPrefs.background = $("#uiBackgroundColor").value;
  persistUiPrefs();
  applyUiPrefs();
}
function fileToResizedDataUrl(file, maxWidth, maxHeight, quality = .82) {
  return new Promise((resolve, reject) => {
    if (!file) { resolve(""); return; }
    if (!String(file.type || "").startsWith("image/")) { reject(new Error("Choose an image file")); return; }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read image"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("Could not decode image"));
      img.onload = () => {
        const scale = Math.min(1, maxWidth / img.width, maxHeight / img.height);
        const width = Math.max(1, Math.round(img.width * scale));
        const height = Math.max(1, Math.round(img.height * scale));
        const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
        canvas.getContext("2d").drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL("image/webp", quality));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function populateStrategySelectors() {
  const options = strategyCatalog.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join("");
  $("#sessionStrategy").innerHTML = options;
  $("#configStrategySelect").innerHTML = options;
}

function parseJsonObject(value) {
  try {
    const parsed = JSON.parse(String(value || "{}"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch { return {}; }
}

function renderEnsembleStrategyGrid(session = {}) {
  const grid = $("#ensembleStrategyGrid");
  if (!grid) return;
  const selected = new Set(String(session.ensemble_strategies || "").split(",").map(v => v.trim()).filter(Boolean));
  const profiles = parseJsonObject(session.ensemble_profiles_json);
  const weights = parseJsonObject(session.ensemble_weights_json);
  grid.innerHTML = strategyCatalog.map((item) => {
    const sid = item.id;
    const profile = profiles[sid] || "balanced";
    const weight = Number(weights[sid] ?? 1);
    const checked = selected.has(sid);
    return `<article class="ensemble-strategy-card" data-ensemble-strategy="${escapeHtml(sid)}">
      <div class="ensemble-strategy-title">
        <label class="ensemble-check"><input class="ensemble-enabled" type="checkbox" ${checked ? "checked" : ""}><span>${escapeHtml(item.label)}</span></label>
        <span class="micro-badge neutral">${escapeHtml(sid)}</span>
      </div>
      <p>${escapeHtml(item.description || "Independent strategy engine")}</p>
      <div class="ensemble-card-controls">
        <label class="field"><span>Profile</span><select class="ensemble-profile"><option value="balanced" ${profile === "balanced" ? "selected" : ""}>Balanced</option><option value="fast" ${profile === "fast" ? "selected" : ""}>Fast</option><option value="strict" ${profile === "strict" ? "selected" : ""}>Strict</option><option value="custom" ${profile === "custom" ? "selected" : ""}>Custom saved</option></select></label>
        <label class="field"><span>Vote weight</span><input class="ensemble-weight" type="number" min="0.05" max="10" step="0.05" value="${Number.isFinite(weight) ? weight : 1}"></label>
      </div>
    </article>`;
  }).join("");
  applyEnsembleMode(false);
}

function syncEnsembleHiddenInputs() {
  const selected = [];
  const profiles = {};
  const weights = {};
  $$(".ensemble-strategy-card").forEach((card) => {
    const sid = card.dataset.ensembleStrategy;
    const enabled = card.querySelector(".ensemble-enabled")?.checked;
    if (enabled) selected.push(sid);
    profiles[sid] = card.querySelector(".ensemble-profile")?.value || "balanced";
    weights[sid] = Number(card.querySelector(".ensemble-weight")?.value || 1);
  });
  $("#ensembleStrategiesHidden").value = selected.join(",");
  $("#ensembleProfilesHidden").value = JSON.stringify(profiles);
  $("#ensembleWeightsHidden").value = JSON.stringify(weights);
}

function applyEnsembleMode(resetSelection = true) {
  const engine = $("#sessionStrategyEngine")?.value || "single";
  const cards = $$(".ensemble-strategy-card");
  if (resetSelection && engine === "best") {
    cards.forEach((card) => { card.querySelector(".ensemble-enabled").checked = BEST_MIX_STRATEGIES.includes(card.dataset.ensembleStrategy); });
  } else if (resetSelection && engine === "all") {
    cards.forEach((card) => { card.querySelector(".ensemble-enabled").checked = true; });
  }
  cards.forEach((card) => {
    const checkbox = card.querySelector(".ensemble-enabled");
    if (checkbox) checkbox.disabled = engine === "best" || engine === "all";
    card.classList.toggle("selected", Boolean(checkbox?.checked));
  });
  const badge = $("#ensembleModeBadge");
  if (badge) badge.textContent = engine === "best" ? "BEST MIX" : engine === "all" ? "ALL" : "CUSTOM";
  syncEnsembleHiddenInputs();
  updateSessionPreview();
}

function strategyItem(strategyId) {
  return strategyCatalog.find((item) => item.id === strategyId) || { id: strategyId, label: strategyId, description: "" };
}

function showStrategyPanel(strategyId) {
  $$(".strategy-config-panel").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.strategyPanel !== strategyId));
  const item = strategyItem(strategyId);
  $("#strategyConfigDescription").textContent = `${item.label}: ${item.description || "Exclusive saved configuration."}`;
  $("#configStrategySelect").value = strategyId;
}

function populateConfigForm(config) {
  currentConfig = structuredClone(config);
  $$('[data-config]').forEach((input) => { input.value = getNested(config, input.dataset.config) ?? ""; });
  $$('[data-config-string]').forEach((input) => { input.value = getNested(config, input.dataset.configString) ?? ""; });
  const enabledPatterns = new Set(config?.strategies?.hammer?.enabled_patterns || []);
  $$(".hammer-pattern-toggle").forEach((input) => { input.checked = enabledPatterns.has(input.value); });
  updatePatternSelectionUi();
  $("#configMultiplierPreview").textContent = `×${config.multiplier ?? "—"}`;
  const selected = $("#configStrategySelect").value || $("#sessionStrategy").value || strategyCatalog[0]?.id || "ema_rsi";
  showStrategyPanel(selected);
}

function readConfigForm() {
  const result = structuredClone(currentConfig || {});
  $$('[data-config]').forEach((input) => { setNested(result, input.dataset.config, numericValue(input)); });
  $$('[data-config-string]').forEach((input) => { setNested(result, input.dataset.configString, input.value); });
  if (!result.strategies) result.strategies = {}; if (!result.strategies.hammer) result.strategies.hammer = {};
  result.strategies.hammer.enabled_patterns = $$(".hammer-pattern-toggle:checked").map((input) => input.value);
  result.strategies.hammer.doji_body_ratio = Number(result.strategies.hammer.doji_body_pct || 8) / 100;
  result.strategies.hammer.marubozu_body_ratio = Number(result.strategies.hammer.marubozu_body_pct || 80) / 100;
  if (result.strategies?.ema_rsi) result.strategy = structuredClone(result.strategies.ema_rsi);
  if (result.strategies?.hammer) result.hammer_strategy = structuredClone(result.strategies.hammer);
  return result;
}

function loadStrategyProfileIntoForm(strategyId, profileName) {
  const values = strategyProfiles?.[strategyId]?.[profileName];
  if (!values) throw new Error(`No ${profileName} configuration exists for ${strategyItem(strategyId).label}`);
  if (!currentConfig.strategies) currentConfig.strategies = {};
  currentConfig.strategies[strategyId] = structuredClone(values);
  Object.entries(values).forEach(([key, value]) => { const input=document.querySelector(`[data-config="strategies.${strategyId}.${key}"]`); if(input) input.value=value; const sinput=document.querySelector(`[data-config-string="strategies.${strategyId}.${key}"]`); if(sinput) sinput.value=value; });
  if (strategyId === "hammer") { const enabled=new Set(values.enabled_patterns || []); $$(".hammer-pattern-toggle").forEach((input)=>{input.checked=enabled.has(input.value);}); updatePatternSelectionUi(); }
  if (strategyId === "ema_rsi") currentConfig.strategy = structuredClone(values);
  if (strategyId === "hammer") currentConfig.hammer_strategy = structuredClone(values);
}

function applySelectedStrategyProfile() {
  try {
    const strategyId = $("#configStrategySelect").value;
    const profileName = $("#configStrategyProfileSelect").value;
    loadStrategyProfileIntoForm(strategyId, profileName);
    toast(`${strategyItem(strategyId).label} loaded with ${profileName} settings. Save to keep it.`, "success");
  } catch (error) { toast(error.message, "error"); }
}

function applyPatternSet(name) {
  const hammer = ["hammer","bullish_hammer","bearish_hammer","inverted_hammer","bullish_inverted_hammer","bearish_inverted_hammer","hanging_man","bullish_hanging_man","bearish_hanging_man","shooting_star","bullish_shooting_star","bearish_shooting_star","bullish_upper_wick_rejection","bearish_upper_wick_rejection","bullish_lower_wick_rejection","bearish_lower_wick_rejection","flat_bottom_bullish_rejection","flat_bottom_bearish_rejection","bullish_belt_hold","bearish_belt_hold"];
  const doji = ["standard_doji","dragonfly_doji","gravestone_doji","long_legged_doji","rickshaw_man","four_price_doji","spinning_top","high_wave"];
  const body = ["bullish_engulfing","bearish_engulfing","bullish_harami","bearish_harami","piercing_line","dark_cloud_cover","tweezer_bottom","tweezer_top"];
  const stars = ["morning_star","evening_star","morning_doji_star","evening_doji_star"];
  const momentum = ["bullish_marubozu","bearish_marubozu","three_white_soldiers","three_black_crows"];
  const screenshot = ["bullish_upper_wick_rejection","bearish_upper_wick_rejection","flat_bottom_bullish_rejection","flat_bottom_bearish_rejection","bullish_inverted_hammer","bearish_shooting_star"];
  const all = $$(".hammer-pattern-toggle").map((x)=>x.value);
  const bullish = all.filter((x)=>x.startsWith("bullish_") || ["hammer","inverted_hammer","dragonfly_doji","piercing_line","morning_star","morning_doji_star","tweezer_bottom","three_white_soldiers"].includes(x));
  const bearish = all.filter((x)=>x.startsWith("bearish_") || ["hanging_man","shooting_star","gravestone_doji","dark_cloud_cover","evening_star","evening_doji_star","tweezer_top","three_black_crows"].includes(x));
  const sets={screenshot,hammer_family:hammer,doji,engulfing:body,stars,multi:momentum,bullish,bearish,all,none:[]};
  const enabled=new Set(sets[name]||[]); $$(".hammer-pattern-toggle").forEach((input)=>{input.checked=enabled.has(input.value);}); updatePatternSelectionUi();
}
function updatePatternSelectionUi(){const all=$$(".hammer-pattern-toggle");const selected=all.filter((x)=>x.checked).length;const el=$("#patternSelectedCount");if(el)el.textContent=`${selected} / ${all.length} selected`;}
function applyCandleShapePreset(name){const presets={loose:{min_body_pct:3,max_body_pct:55,min_long_wick_pct:25,max_opposite_wick_pct:18,body_edge_zone_pct:50,flat_wick_max_pct:8,rejection_min_body_pct:12,rejection_max_body_pct:88,rejection_min_wick_pct:12,rejection_max_opposite_wick_pct:10,doji_body_pct:10,marubozu_body_pct:72},balanced:{min_body_pct:5,max_body_pct:45,min_long_wick_pct:35,max_opposite_wick_pct:12,body_edge_zone_pct:45,flat_wick_max_pct:5,rejection_min_body_pct:20,rejection_max_body_pct:82,rejection_min_wick_pct:15,rejection_max_opposite_wick_pct:6,doji_body_pct:8,marubozu_body_pct:80},strict:{min_body_pct:6,max_body_pct:35,min_long_wick_pct:45,max_opposite_wick_pct:8,body_edge_zone_pct:38,flat_wick_max_pct:3,rejection_min_body_pct:25,rejection_max_body_pct:72,rejection_min_wick_pct:22,rejection_max_opposite_wick_pct:4,doji_body_pct:5,marubozu_body_pct:85}};const values=presets[name];if(!values)return;Object.entries(values).forEach(([key,value])=>{const input=document.querySelector(`[data-config="strategies.hammer.${key}"]`);if(input)input.value=value;});toast(`Candlestick ${name} geometry loaded. Save config to keep it.`,"success");}


function updateSessionPreview() {
  const form = $("#sessionForm");
  const values = formToObject(form);
  const capital = Number(values.capital || 0);
  const split = Math.max(1, Number(values.split || 1));
  const maxOpen = Math.max(0, Number(values.max_open || 0));
  const tradesPerSignalInput = form.elements.namedItem("trades_per_signal");
  if (tradesPerSignalInput) {
    tradesPerSignalInput.max = String(Math.max(1, maxOpen || 1));
    const requested = Math.max(1, Number(tradesPerSignalInput.value || 1));
    tradesPerSignalInput.classList.toggle("risk-active", maxOpen > 0 && requested > maxOpen);
  }
  const stake = capital / split;
  $("#stakePreview").textContent = Number.isFinite(stake) ? `$${stake.toFixed(2)}` : "—";
  $("#exposurePreview").textContent = Number.isFinite(stake) ? `$${(stake * Math.min(split, maxOpen)).toFixed(2)}` : "—";
  const engine = values.strategy_engine || "single";
  const ensembleActive = engine !== "single";
  $$(".single-strategy-field").forEach((field) => field.classList.toggle("hidden", ensembleActive));
  $("#ensemblePanel")?.classList.toggle("hidden", !ensembleActive);
  if (ensembleActive) syncEnsembleHiddenInputs();
  const weightedPolicy = ensembleActive && (values.ensemble_policy || "regime") === "weighted";
  $$(".weighted-policy-only").forEach((field) => field.classList.toggle("hidden", !weightedPolicy));
  const stopLoss = Number(values.server_stop_loss || 0);
  const stopLossInput = form.elements.namedItem("server_stop_loss");
  if (stopLossInput) stopLossInput.classList.toggle("risk-active", stopLoss > 0);
  const smartLossEnabled = Boolean(values.smart_loss_enabled);
  $$(".smart-loss-field").forEach((field) => field.classList.toggle("hidden", !smartLossEnabled));

  const exitMode=values.take_profit_mode || "fixed"; const isAdaptive=exitMode==="adaptive"; const isSmart=exitMode==="smart" || exitMode==="hybrid"; const tpUnit=values.take_profit_unit || "amount";
  $("#tpUnitField").classList.remove("hidden");
  $$(".tp-amount-field").forEach((field)=>field.classList.toggle("hidden",isSmart || tpUnit!=="amount"));
  $("#tpMaxField").classList.toggle("hidden",isSmart || tpUnit!=="amount" || !isAdaptive);
  $$(".tp-tick-field").forEach((field)=>field.classList.toggle("hidden",isSmart || tpUnit!=="ticks"));
  $("#tpTicksMaxField").classList.toggle("hidden",isSmart || tpUnit!=="ticks" || !isAdaptive);
  $$(".smart-exit-field").forEach((field) => field.classList.toggle("hidden", !isSmart));
  $$(".smart-amount-field").forEach((field) => field.classList.toggle("hidden", !isSmart || tpUnit!=="amount"));
  $$(".smart-tick-field").forEach((field) => field.classList.toggle("hidden", !isSmart || tpUnit!=="ticks"));
  $$(".hybrid-only").forEach((field) => field.classList.toggle("hidden", exitMode !== "hybrid"));
  $("#liveConfirmationField").classList.toggle("hidden", values.mode !== "live");
}

function formatRuntime(seconds) {
  const h = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function updateClock() {
  const clock = $("#dashboardClock");
  if (clock) clock.textContent = new Date().toLocaleTimeString([], { hour12: false });
}

function setApiState(state, text) {
  const dot = $("#apiStatusDot");
  const label = $("#apiStatusText");
  if (!dot || !label) return;
  dot.className = `status-dot${state ? ` ${state}` : ""}`;
  label.textContent = text;
}

function setMetricTone(element, value) {
  const card = element.closest(".metric-card");
  if (!card) return;
  card.classList.toggle("positive", value > 0);
  card.classList.toggle("negative", value < 0);
}

function updateQuoteSeries(rawQuote) {
  const quote = toNumber(rawQuote, NaN);
  if (!Number.isFinite(quote)) return;
  quoteHistory.push(quote);
  if (quoteHistory.length > 54) quoteHistory.shift();

  $("#liveQuoteValue").textContent = String(rawQuote);
  const previous = quoteHistory.at(-2);
  const trend = $("#quoteTrend");
  let direction = "NEUTRAL";
  if (Number.isFinite(previous) && quote > previous) {
    trend.textContent = "Rising";
    trend.className = "micro-badge up";
    direction = "UP";
  } else if (Number.isFinite(previous) && quote < previous) {
    trend.textContent = "Falling";
    trend.className = "micro-badge down";
    direction = "DOWN";
  } else {
    trend.textContent = "Flat";
    trend.className = "micro-badge neutral";
  }
  $("#signalDirection").textContent = direction;
  $("#quoteEmpty").classList.toggle("hidden", quoteHistory.length > 1);
  drawQuoteChart();
}

function syncActiveEngineTopbar() {
  const isRf = document.body.dataset.engineUi === "rise-fall";
  const badge = $("#processBadge"), start = $("#startButton"), stop = $("#stopButton");
  if (!badge || !start || !stop) return;
  if (isRf) {
    const status = riseFallLastStatus || {}; const running = Boolean(status.running), draining = Boolean(status.stop_requested);
    badge.textContent = running ? (draining ? "RF DRAINING" : "RF Running") : "RF Stopped";
    badge.className = `badge ${running ? "running" : "stopped"}`;
    start.textContent = "Deploy Rise/Fall"; start.disabled = running || Boolean(rfLabLastStatus?.running);
    stop.textContent = draining ? "Waiting for RF contracts…" : "Stop RF entries"; stop.disabled = !running || draining;
  } else {
    const status = multiplierLastStatus || {}; const running = Boolean(status.running), draining = Boolean(status.stop_requested);
    badge.textContent = running ? (draining ? "MULT DRAINING" : "MULT Running") : "MULT Stopped";
    badge.className = `badge ${running ? "running" : "stopped"}`;
    start.textContent = "Deploy Multiplier"; start.disabled = running;
    stop.textContent = draining ? "Waiting for open trades…" : "Stop MULT entries"; stop.disabled = !running || draining;
  }
}
function deployActiveEngine(){return document.body.dataset.engineUi === "rise-fall" ? startRiseFall() : startBot();}
function stopActiveEngine(){return document.body.dataset.engineUi === "rise-fall" ? stopRiseFall() : stopBot();}

function renderStatus(status = {}) {
  multiplierLastStatus = status;
  if (document.body.dataset.engineUi !== "rise-fall") syncMobileDeploy(status);
  const running = Boolean(status.running);
  if (document.body.dataset.engineUi !== "rise-fall") syncActiveEngineTopbar();
  $("#navDeployButton") && ($("#navDeployButton").disabled = running);
  $("#sessionStartButton").disabled = running;

  const d = status.dashboard || {};
  const s = status.settings || {};
  const balanceText = d.balance !== undefined && d.balance !== null && d.balance !== ""
    ? `${d.balance} ${d.currency || ""}`.trim()
    : "—";
  $("#metricBalance").textContent = balanceText;
  $("#metricAccount").textContent = d.account || s.mode || "No account data";
  $("#metricOpen").textContent = `${d.open_trades ?? 0} / ${d.max_open ?? s.max_open ?? 0}`;
  $("#metricSlots").textContent = `${d.available_slots ?? 0} available`;
  $("#metricProfit").textContent = d.realized_profit ?? "0.00";
  $("#metricOpenProfit").textContent = `Open P/L ${d.open_profit ?? "0.00"}`;
  $("#metricWinRate").textContent = `${d.win_rate ?? "0.0"}%`;
  $("#metricRecord").textContent = `${d.wins ?? 0} wins · ${d.losses ?? 0} losses`;
  $("#metricSignal").textContent = d.signal || "WAITING";
  $("#metricQuote").textContent = `Quote ${d.quote ?? "—"}`;
  $("#metricRuntime").textContent = d.runtime_line?.match(/Runtime:\s+(\S+)/)?.[1] || formatRuntime(status.runtime_seconds || 0);
  $("#metricMode").textContent = d.mode ? `${d.mode} · ${d.state || ""}` : (running ? s.mode || "Running" : "Stopped");

  const realizedProfit = toNumber(d.realized_profit, 0);
  setMetricTone($("#metricProfit"), realizedProfit);

  const signal = String(d.signal || "WAITING").toUpperCase();
  const signalCard = $("#metricSignal").closest(".signal-metric");
  signalCard.classList.remove("signal-up", "signal-down", "signal-waiting");
  if (/UP|BUY|CALL|RISE/.test(signal)) signalCard.classList.add("signal-up");
  else if (/DOWN|SELL|PUT|FALL/.test(signal)) signalCard.classList.add("signal-down");
  else signalCard.classList.add("signal-waiting");
  $("#signalState").textContent = signal;

  const candleCard = $("#candleWatchCard");
  const hasCandle = d.candle_status_line || d.candle_ohlc_line || d.candle_live_line;
  if (candleCard) candleCard.classList.toggle("hidden", !hasCandle);
  if (hasCandle) {
    $("#candleWatchProgress").textContent = d.candle_progress_label || (d.candle_mode === "time" ? `${d.candle_elapsed_seconds ?? "—"} / ${d.candle_seconds ?? "—"} sec` : `${d.candle_progress ?? "—"} / ${d.candle_ticks ?? "—"} ticks`);
    $("#candleWatchDetected").textContent = d.candle_detected || "NONE";
    $("#candleWatchCandidates").textContent = d.candle_live_candidates || "NONE";
    $("#candleWatchScore").textContent = d.candle_pattern_score !== undefined ? Number(d.candle_pattern_score).toFixed(2) : "—";
    $("#candleWatchConfirm").textContent = d.candle_confirmation || "—";
    $("#candleWatchPending").textContent = `${d.candle_pending_pattern || "NONE"}${d.candle_pending_direction && d.candle_pending_direction !== "NONE" ? ` · ${d.candle_pending_direction}` : ""}`;
    const live = d.candle_live_open !== undefined;
    $("#candleWatchOhlc").textContent = live ? `O ${d.candle_live_open} · H ${d.candle_live_high} · L ${d.candle_live_low} · C ${d.candle_live_close}` : d.candle_open !== undefined ? `O ${d.candle_open} · H ${d.candle_high} · L ${d.candle_low} · C ${d.candle_close}` : "—";
    $("#candleBodyPct").textContent = live ? `${d.candle_live_body_pct}%` : d.candle_body_pct !== undefined ? `${d.candle_body_pct}%` : "—";
    $("#candleUpperPct").textContent = live ? `${d.candle_live_upper_pct}%` : d.candle_upper_pct !== undefined ? `${d.candle_upper_pct}%` : "—";
    $("#candleLowerPct").textContent = live ? `${d.candle_live_lower_pct}%` : d.candle_lower_pct !== undefined ? `${d.candle_lower_pct}%` : "—";
    const bodyPct = Number(live ? d.candle_live_body_pct : d.candle_body_pct); const upperPct = Number(live ? d.candle_live_upper_pct : d.candle_upper_pct); const lowerPct = Number(live ? d.candle_live_lower_pct : d.candle_lower_pct);
    if ($("#candleBodyBar")) $("#candleBodyBar").style.width = `${Math.max(0,Math.min(100,Number.isFinite(bodyPct)?bodyPct:0))}%`;
    if ($("#candleUpperBar")) $("#candleUpperBar").style.width = `${Math.max(0,Math.min(100,Number.isFinite(upperPct)?upperPct:0))}%`;
    if ($("#candleLowerBar")) $("#candleLowerBar").style.width = `${Math.max(0,Math.min(100,Number.isFinite(lowerPct)?lowerPct:0))}%`;
    $("#candleWatchBadge").textContent = d.candle_detected && d.candle_detected !== "NONE" ? "PATTERN" : "READING";
    $("#candleWatchBadge").className = `micro-badge ${d.candle_detected && d.candle_detected !== "NONE" ? "up" : "neutral"}`;
  }

  if (d.quote !== undefined && d.quote !== null && d.quote !== "—") updateQuoteSeries(d.quote);

  const positionsBody = $("#positionsBody");
  const positions = Array.isArray(d.positions) ? d.positions : [];
  const managedPositions = Math.max(0, toNumber(d.managed_positions ?? d.open_trades, positions.length));
  const subscribedPositions = Math.max(0, toNumber(d.subscribed_positions, positions.length));
  const externalPositions = Math.max(0, toNumber(d.external_positions, 0));
  const detailMismatch = managedPositions !== positions.length;

  if (positions.length) {
    positionsBody.innerHTML = positions.map((p) => {
      const profit = toNumber(p.profit, 0);
      const profitClass = profit > 0 ? "positive" : profit < 0 ? "negative" : "";
      return `<tr><td>#${escapeHtml(p.contract_id)}</td><td>${escapeHtml(p.direction)}</td><td>${escapeHtml(p.stake)}</td><td>${escapeHtml(p.take_profit)}</td><td class="${profitClass}">${escapeHtml(p.profit)}</td><td>${escapeHtml(p.peak_profit || "0")}</td><td>${escapeHtml(p.trailing_floor || "0")}</td><td>${escapeHtml(p.status)}</td></tr>`;
    }).join("");
  } else if (managedPositions > 0) {
    positionsBody.innerHTML = `<tr><td colspan="8" class="empty position-warning">${managedPositions} managed trade${managedPositions === 1 ? " is" : "s are"} active, but detailed rows are resynchronizing. Trading management is still running.</td></tr>`;
  } else {
    positionsBody.innerHTML = '<tr><td colspan="8" class="empty">No open positions</td></tr>';
  }

  const positionBadge = $("#positionCountBadge");
  positionBadge.textContent = `${managedPositions} active`;
  positionBadge.className = `micro-badge ${managedPositions ? "up" : "neutral"}`;
  if ($("#positionManagedCount")) $("#positionManagedCount").textContent = String(managedPositions);
  if ($("#positionSubscribedCount")) $("#positionSubscribedCount").textContent = String(subscribedPositions);
  if ($("#positionExternalCount")) $("#positionExternalCount").textContent = String(externalPositions);
  const syncState = $("#positionSyncState");
  if (syncState) {
    const reconnecting = String(d.link_state || "").toUpperCase() === "RECONNECTING";
    const healthy = !detailMismatch && (!managedPositions || subscribedPositions >= managedPositions);
    syncState.textContent = reconnecting ? "reconnecting" : healthy ? "synced" : "resyncing";
    syncState.className = `position-sync-state ${reconnecting || !healthy ? "warning" : "ok"}`;
  }

  $("#activeSession").innerHTML = `
    <div><dt>Strategy</dt><dd>${escapeHtml(d.strategy_line || s.strategy || "—")}</dd></div>
    <div><dt>Engine</dt><dd>${escapeHtml(s.strategy_engine || "single")}${s.strategy === "ensemble" ? ` · ${escapeHtml(s.ensemble_policy || "regime")}` : ""}</dd></div>
    <div><dt>Capital</dt><dd>${escapeHtml(s.capital || d.capital || "—")}</dd></div>
    <div><dt>Split / max open</dt><dd>${escapeHtml(String(s.split ?? d.split ?? "—"))} / ${escapeHtml(String(s.max_open ?? d.max_open ?? "—"))}</dd></div>
    <div><dt>Trades per signal</dt><dd>${escapeHtml(String(s.trades_per_signal ?? (s.batch_mode === "one" ? 1 : (s.max_open ?? "—"))))}</dd></div>
    <div><dt>Take profit</dt><dd>${escapeHtml(d.take_profit_line || `${s.take_profit_mode || "—"} ${s.take_profit || ""}${s.take_profit_max ? `–${s.take_profit_max}` : ""}`)}</dd></div>
    <div><dt>Multiplier</dt><dd>×${escapeHtml(s.multiplier ?? currentConfig?.multiplier ?? "—")}</dd></div>
    <div><dt>Emergency server loss cap</dt><dd>${toNumber(s.server_stop_loss, 0) > 0 ? `$${escapeHtml(s.server_stop_loss)}` : "OFF"}</dd></div>
    <div><dt>Smart Loss Guard</dt><dd>${s.smart_loss_enabled ? `ON · soft -$${escapeHtml(s.smart_loss_soft_trigger)} · hard -$${escapeHtml(s.smart_loss_hard_cap)} · rescue +$${escapeHtml(s.smart_loss_rescue_profit)}` : "OFF"}</dd></div>
    <div><dt>Loss policy</dt><dd>${escapeHtml(String(s.loss_policy || "—").replaceAll("_", " "))}</dd></div>`;

  const capital = Math.max(0, toNumber(s.capital ?? d.capital, 0));
  const split = Math.max(1, toNumber(s.split ?? d.split, 1));
  const fallbackStake = capital / split;
  const allocated = positions.reduce((sum, position) => sum + Math.max(0, toNumber(position.stake, fallbackStake)), 0);
  const allocationPercent = capital > 0 ? (allocated / capital) * 100 : 0;
  $("#allocationText").textContent = `${Math.round(allocationPercent)}%`;
  $("#allocationBar").style.width = `${Math.min(100, allocationPercent)}%`;
  $("#allocationDetail").textContent = capital > 0
    ? `$${formatNumber(allocated)} of $${formatNumber(capital)} currently allocated`
    : "No capital currently allocated";

  const heroStatus = $("#heroStatusText");
  if (running) {
    heroStatus.textContent = status.stop_requested
      ? `DRAINING · new entries blocked · ${positions.length} managed open trade${positions.length === 1 ? "" : "s"} still running to normal exit.`
      : `${signal} signal · ${positions.length} open position${positions.length === 1 ? "" : "s"} · ${s.mode || d.mode || "active"} mode.`;
  } else if (status.last_error) {
    heroStatus.textContent = `Bot stopped: ${status.last_error}`;
  } else {
    heroStatus.textContent = "System idle. Configure a session or test the Deriv connection.";
  }

  if (d.account || d.balance !== undefined) setApiState("online", "Connected");
  else if (running) setApiState("online", "Active");

  if (status.last_error && !running) {
    $("#processBadge").title = status.last_error;
  } else {
    $("#processBadge").removeAttribute("title");
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function orderedTradeRows(rows) {
  const copy = [...rows];
  const hasDates = copy.some((row) => !Number.isNaN(Date.parse(row.closed_at_utc || row.closed_at || row.timestamp || "")));
  if (hasDates) {
    copy.sort((a, b) => Date.parse(a.closed_at_utc || a.closed_at || a.timestamp || 0) - Date.parse(b.closed_at_utc || b.closed_at || b.timestamp || 0));
    return copy;
  }
  return copy.reverse();
}

function renderTradeAnalytics(rows, summary) {
  const ordered = orderedTradeRows(rows);
  const profits = ordered.map((row) => toNumber(row.profit, 0));
  let runningTotal = 0;
  equitySeries = profits.map((profit) => {
    runningTotal += profit;
    return runningTotal;
  });

  const gains = profits.filter((value) => value > 0);
  const losses = profits.filter((value) => value < 0);
  const grossGain = gains.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  const average = profits.length ? profits.reduce((sum, value) => sum + value, 0) / profits.length : 0;
  const best = profits.length ? Math.max(...profits) : 0;
  const worst = profits.length ? Math.min(...profits) : 0;

  let peak = 0;
  let maxDrawdown = 0;
  equitySeries.forEach((value) => {
    peak = Math.max(peak, value);
    maxDrawdown = Math.max(maxDrawdown, peak - value);
  });

  $("#statProfitFactor").textContent = grossLoss > 0 ? (grossGain / grossLoss).toFixed(2) : grossGain > 0 ? "∞" : "—";
  $("#statAverageTrade").textContent = formatNumber(average);
  $("#statDrawdown").textContent = formatNumber(maxDrawdown);
  $("#statBestWorst").textContent = `${formatNumber(best)} / ${formatNumber(worst)}`;
  $("#equityEmpty").classList.toggle("hidden", equitySeries.length > 0);

  const wins = toNumber(summary.wins, gains.length);
  const lossCount = toNumber(summary.losses, losses.length);
  const totalDecisions = wins + lossCount;
  const winRate = totalDecisions > 0 ? (wins / totalDecisions) * 100 : toNumber(summary.win_rate, 0);
  $("#outcomeRing").style.setProperty("--win-rate", `${Math.max(0, Math.min(100, winRate))}%`);
  $("#ringWinRate").textContent = `${Math.round(winRate)}%`;
  $("#outcomeRecord").textContent = `${wins} wins / ${lossCount} losses`;

  const latest = ordered.at(-1);
  if (latest) {
    const latestProfit = toNumber(latest.profit, 0);
    const result = latest.result || latest.status || (latestProfit > 0 ? "Win" : latestProfit < 0 ? "Loss" : "Flat");
    $("#lastTradeResult").textContent = `Last: ${result} · ${formatNumber(latestProfit)}`;
  } else {
    $("#lastTradeResult").textContent = "No completed trades";
  }

  drawEquityChart();
}

function tradeCell(column, value) {
  let className = "";
  if (column === "profit") {
    const profit = toNumber(value, 0);
    className = profit > 0 ? "positive" : profit < 0 ? "negative" : "";
  }
  if (column === "result") {
    const result = String(value || "").toLowerCase();
    className = /win|won|profit/.test(result) ? "positive" : /loss|lost/.test(result) ? "negative" : "";
  }
  return `<td class="${className}">${escapeHtml(value)}</td>`;
}

function renderTrades(trades) {
  const summary = trades?.summary || {};
  $("#historyTrades").textContent = summary.trades ?? 0;
  $("#historyWins").textContent = summary.wins ?? 0;
  $("#historyLosses").textContent = summary.losses ?? 0;
  $("#historyProfit").textContent = summary.profit ?? "0.00";
  $("#historyWinRate").textContent = `${summary.win_rate ?? "0.0"}%`;

  const rows = trades?.rows || [];
  renderTradeAnalytics(rows, summary);

  const patternGroups = new Map();
  rows.forEach((row) => {
    const pattern = String(row.entry_pattern || "").trim();
    if (!pattern) return;
    const item = patternGroups.get(pattern) || { trades: 0, wins: 0, losses: 0, profit: 0 };
    const profit = toNumber(row.profit, 0);
    item.trades += 1; item.profit += profit;
    if (profit > 0) item.wins += 1; else item.losses += 1;
    patternGroups.set(pattern, item);
  });
  const patternBody = $("#patternPerformanceBody");
  if (patternBody) {
    const ranked = [...patternGroups.entries()].sort((a,b)=>b[1].profit-a[1].profit);
    patternBody.innerHTML = ranked.length ? ranked.map(([name,item]) => {
      const winRate = item.trades ? item.wins / item.trades * 100 : 0;
      const avg = item.trades ? item.profit / item.trades : 0;
      return `<tr><td><strong>${escapeHtml(name.replaceAll("_"," "))}</strong></td><td>${item.trades}</td><td>${item.wins} / ${item.losses}</td><td>${winRate.toFixed(1)}%</td><td class="${item.profit>0?"positive":item.profit<0?"negative":""}">${formatNumber(item.profit)}</td><td>${formatNumber(avg)}</td></tr>`;
    }).join("") : '<tr><td colspan="6" class="empty">No candle-attributed trades yet.</td></tr>';
  }

  const head = $("#historyHead");
  const body = $("#historyBody");
  if (!rows.length) {
    head.innerHTML = "";
    body.innerHTML = '<tr><td class="empty">No trades recorded</td></tr>';
    return;
  }
  const preferred = ["closed_at_utc", "direction", "entry_pattern", "stake", "take_profit_unit", "take_profit_ticks", "take_profit", "max_favorable_ticks", "worst_ticks", "profit", "result", "exit_reason"];
  const available = Object.keys(rows[0]);
  const columns = preferred.filter((key) => available.includes(key));
  available.forEach((key) => { if (!columns.includes(key) && columns.length < 12) columns.push(key); });
  head.innerHTML = `<tr>${columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("")}</tr>`;
  body.innerHTML = rows.map((row) => `<tr>${columns.map((column) => tradeCell(column, row[column])).join("")}</tr>`).join("");
}

function renderCredentials(credentials) {
  const form = $("#credentialsForm");
  form.elements.app_id.value = localStorage.getItem(APP_ID_DISPLAY_KEY) || credentials.app_id || "";
  form.elements.account_id.value = credentials.account_id || "";
  form.elements.symbol.value = credentials.symbol || "stpRNG";
  form.elements.token.value = "";
  $("#heroSymbol").textContent = credentials.symbol || "stpRNG";
  const badge = $("#credentialBadge");
  if (credentials.token_saved && credentials.app_id && credentials.account_id) {
    badge.textContent = `Configured · ${credentials.token_hint}`;
    badge.className = "badge running";
  } else {
    badge.textContent = "Not configured";
    badge.className = "badge neutral";
  }
}

function prepareCanvas(canvas) {
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(rect.width * dpr);
  const height = Math.round(rect.height * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  return { context, width: rect.width, height: rect.height };
}

function drawLineChart(canvas, values, options = {}) {
  const prepared = prepareCanvas(canvas);
  if (!prepared || !values.length) return;
  const { context: ctx, width, height } = prepared;
  const pad = { top: 15, right: 14, bottom: 22, left: 42 };
  const chartWidth = Math.max(1, width - pad.left - pad.right);
  const chartHeight = Math.max(1, height - pad.top - pad.bottom);
  const includeZero = options.includeZero !== false;
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (includeZero) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  if (min === max) {
    const offset = Math.max(Math.abs(min) * .02, 1);
    min -= offset;
    max += offset;
  }
  const range = max - min;
  const xFor = (index) => pad.left + (values.length === 1 ? chartWidth / 2 : (index / (values.length - 1)) * chartWidth);
  const yFor = (value) => pad.top + ((max - value) / range) * chartHeight;

  ctx.lineWidth = 1;
  ctx.font = "10px ui-monospace, SFMono-Regular, Consolas, monospace";
  ctx.fillStyle = "rgba(154, 173, 186, .58)";
  ctx.strokeStyle = "rgba(115, 151, 172, .12)";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i += 1) {
    const ratio = i / 4;
    const y = pad.top + ratio * chartHeight;
    const label = max - ratio * range;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(formatNumber(label, Math.abs(range) < 10 ? 2 : 0), pad.left - 7, y);
  }

  const baselineY = yFor(0);
  if (baselineY >= pad.top && baselineY <= height - pad.bottom) {
    ctx.strokeStyle = "rgba(255,255,255,.14)";
    ctx.beginPath();
    ctx.moveTo(pad.left, baselineY);
    ctx.lineTo(width - pad.right, baselineY);
    ctx.stroke();
  }

  const gradient = ctx.createLinearGradient(0, pad.top, 0, height - pad.bottom);
  gradient.addColorStop(0, options.fillTop || "rgba(255,52,144,.26)");
  gradient.addColorStop(1, options.fillBottom || "rgba(49,231,208,0)");
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = xFor(index);
    const y = yFor(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.lineTo(xFor(values.length - 1), height - pad.bottom);
  ctx.lineTo(xFor(0), height - pad.bottom);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  const lineGradient = ctx.createLinearGradient(pad.left, 0, width - pad.right, 0);
  lineGradient.addColorStop(0, options.lineStart || "#ff3490");
  lineGradient.addColorStop(1, options.lineEnd || "#31e7d0");
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = xFor(index);
    const y = yFor(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = lineGradient;
  ctx.shadowColor = options.shadow || "rgba(49,231,208,.34)";
  ctx.shadowBlur = 8;
  ctx.stroke();
  ctx.shadowBlur = 0;

  const lastX = xFor(values.length - 1);
  const lastY = yFor(values.at(-1));
  ctx.beginPath();
  ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = options.lineEnd || "#31e7d0";
  ctx.fill();
}

function drawEquityChart() {
  const canvas = $("#equityChart");
  const values = equitySeries.length ? equitySeries : [0];
  drawLineChart(canvas, values, { includeZero: true });
}

function drawQuoteChart() {
  const canvas = $("#quoteChart");
  if (!quoteHistory.length) {
    const prepared = prepareCanvas(canvas);
    if (prepared) prepared.context.clearRect(0, 0, prepared.width, prepared.height);
    return;
  }
  const rising = quoteHistory.length < 2 || quoteHistory.at(-1) >= quoteHistory[0];
  drawLineChart(canvas, quoteHistory, {
    includeZero: false,
    lineStart: rising ? "#ff3490" : "#ff7a92",
    lineEnd: rising ? "#31e7d0" : "#ff5475",
    fillTop: rising ? "rgba(49,231,208,.18)" : "rgba(255,84,117,.18)",
    fillBottom: "rgba(0,0,0,0)",
    shadow: rising ? "rgba(49,231,208,.34)" : "rgba(255,84,117,.34)",
  });
}

function drawCurrentCharts() {
  drawEquityChart();
  drawQuoteChart();
}

function appendLogsV219Base(lines) {
  if (!lines.length) return;
  const terminal = $("#consoleOutput");
  const atBottom = terminal.scrollTop + terminal.clientHeight >= terminal.scrollHeight - 40;
  const prefix = terminal.textContent.includes("Control online") ? "" : `${terminal.textContent}\n`;
  terminal.textContent = prefix + lines.map((line) => line.text).join("\n");
  if (terminal.textContent.length > 220000) terminal.textContent = terminal.textContent.slice(-180000);
  if (atBottom) terminal.scrollTop = terminal.scrollHeight;
}


function labStrategyOptions(selected = "") {
  return strategyCatalog.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}

function labProfileOptions(selected = "balanced") {
  return ["balanced", "fast", "strict", "custom"].map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value[0].toUpperCase() + value.slice(1)}</option>`).join("");
}

function labDefaultValue(selector, fallback) {
  const el = $(selector);
  return el ? (el.value || fallback) : fallback;
}

function addLabBot(initial = {}) {
  if ($$(".lab-bot-card").length >= 8) {
    toast("Strategy Lab supports up to 8 bots at once", "error");
    return;
  }
  labBotCounter += 1;
  const id = initial.id || `lab-${labBotCounter}`;
  const strategy = initial.strategy || strategyCatalog[Math.min(labBotCounter - 1, Math.max(0, strategyCatalog.length - 1))]?.id || "ema_rsi";
  const profile = initial.profile || "balanced";
  const card = document.createElement("article");
  card.className = "card lab-bot-card";
  card.dataset.labId = id;
  card.innerHTML = `
    <div class="lab-bot-header">
      <div><span class="lab-index">TEST ${String(labBotCounter).padStart(2, "0")}</span><h3>${escapeHtml(initial.label || `Tester ${labBotCounter}`)}</h3></div>
      <button type="button" class="button secondary small lab-remove" title="Remove tester">Remove</button>
    </div>
    <div class="lab-bot-grid">
      <label class="field"><span>Label</span><input data-lab-field="label" value="${escapeHtml(initial.label || `Tester ${labBotCounter}`)}"></label>
      <label class="field"><span>Strategy</span><select data-lab-field="strategy">${labStrategyOptions(strategy)}</select></label>
      <label class="field"><span>Configuration</span><select data-lab-field="profile">${labProfileOptions(profile)}</select></label>
      <label class="field"><span>Stake / trade</span><input data-lab-field="stake" type="number" min="0.35" step="0.01" value="${escapeHtml(initial.stake || labDefaultValue("#labDefaultStake", "1.00"))}"></label>
      <label class="field"><span>Max open</span><input data-lab-field="max_open" type="number" min="1" max="50" step="1" value="${escapeHtml(initial.max_open || labDefaultValue("#labDefaultMaxOpen", "3"))}"></label>
      <label class="field"><span>Trades per signal</span><input data-lab-field="trades_per_signal" type="number" min="1" max="50" step="1" value="${escapeHtml(initial.trades_per_signal || (initial.batch_mode === "all" ? (initial.max_open || labDefaultValue("#labDefaultMaxOpen", "3")) : labDefaultValue("#labDefaultTradesPerSignal", "1")))}"><small>Exact maximum entries from each fresh signal.</small></label>
      <label class="field"><span>Stagger delay</span><input data-lab-field="split_delay" type="number" min="0" max="3600" step="0.1" value="${escapeHtml(initial.split_delay ?? labDefaultValue("#labDefaultSplitDelay", "0.5"))}"><small>Seconds between fill-slot entries.</small></label>
      <label class="field"><span>Multiplier</span><select data-lab-field="multiplier">
        ${[750,2000,3500,5500,7500].map(v => `<option value="${v}" ${String(v) === String(initial.multiplier || labDefaultValue("#labDefaultMultiplier", "750")) ? "selected" : ""}>×${v}</option>`).join("")}
      </select></label>
      <label class="field"><span>Exit</span><select data-lab-field="exit_mode">
        ${[["fixed","Fixed TP"],["adaptive","Adaptive TP"],["smart","Smart trailing"],["hybrid","Hybrid"]].map(([v,l]) => `<option value="${v}" ${v === (initial.exit_mode || labDefaultValue("#labDefaultExit", "hybrid")) ? "selected" : ""}>${l}</option>`).join("")}
      </select></label>
      <label class="field"><span>TP unit</span><select data-lab-field="take_profit_unit">
        ${[["amount","Amount"],["ticks","Ticks"]].map(([v,l]) => `<option value="${v}" ${v === (initial.take_profit_unit || labDefaultValue("#labDefaultTpUnit", "amount")) ? "selected" : ""}>${l}</option>`).join("")}
      </select></label>
      <label class="field"><span>TP ticks min/fixed</span><input data-lab-field="take_profit_ticks" type="number" min="1" step="1" value="${escapeHtml(initial.take_profit_ticks || "3")}"></label>
      <label class="field"><span>TP ticks max</span><input data-lab-field="take_profit_ticks_max" type="number" min="1" step="1" value="${escapeHtml(initial.take_profit_ticks_max || "8")}"></label>
      <label class="field"><span>TP min/fixed</span><input data-lab-field="take_profit" type="number" min="0.10" step="0.01" value="${escapeHtml(initial.take_profit || labDefaultValue("#labDefaultTp", "0.10"))}"></label>
      <label class="field"><span>TP max</span><input data-lab-field="take_profit_max" type="number" min="0.10" step="0.01" value="${escapeHtml(initial.take_profit_max || labDefaultValue("#labDefaultTpMax", "0.30"))}"></label>
    </div>
    <div class="lab-bot-foot"><span class="lab-open-foot">Up to ${escapeHtml(initial.max_open || labDefaultValue("#labDefaultMaxOpen", "3"))} open</span><span>Demo only</span><span>Independent config</span></div>`;
  $("#labBotBuilder").appendChild(card);
  updateLabBotFoot(card);
}

function updateLabBotFoot(card) {
  if (!card) return;
  const maxOpen = Math.max(1, Number(card.querySelector('[data-lab-field="max_open"]')?.value || 1));
  const tradesInput = card.querySelector('[data-lab-field="trades_per_signal"]');
  const tradesPerSignal = Math.max(1, Number(tradesInput?.value || 1));
  if (tradesInput) { tradesInput.max = String(maxOpen); tradesInput.classList.toggle("risk-active", tradesPerSignal > maxOpen); }
  const delay = Math.max(0, Number(card.querySelector('[data-lab-field="split_delay"]')?.value || 0));
  const foot = card.querySelector(".lab-open-foot");
  if (foot) foot.textContent = `Up to ${maxOpen} open · ${Math.min(tradesPerSignal, maxOpen)} per signal${tradesPerSignal > 1 ? ` · ${delay}s stagger` : ""}`;
}

function gatherLabBots() {
  return $$(".lab-bot-card").map((card) => {
    const value = (name) => card.querySelector(`[data-lab-field="${name}"]`)?.value;
    return {
      id: card.dataset.labId,
      label: value("label") || card.dataset.labId,
      strategy: value("strategy"),
      profile: value("profile"),
      stake: value("stake"),
      max_open: value("max_open"),
      trades_per_signal: value("trades_per_signal"),
      batch_mode: Number(value("trades_per_signal") || 1) === 1 ? "one" : (Number(value("trades_per_signal") || 1) >= Number(value("max_open") || 1) ? "all" : "count"),
      split_delay: value("split_delay"),
      multiplier: value("multiplier"),
      exit_mode: value("exit_mode"),
      take_profit_unit: value("take_profit_unit") || labDefaultValue("#labDefaultTpUnit", "amount"),
      take_profit: value("take_profit"),
      take_profit_max: value("take_profit_max"),
      take_profit_ticks: value("take_profit_ticks") || "3",
      take_profit_ticks_max: value("take_profit_ticks_max") || "8",
      smart_activation: labDefaultValue("#labSmartActivation", "0.15"),
      smart_profit_floor: labDefaultValue("#labSmartFloor", "0.10"),
      smart_activation_ticks: labDefaultValue("#labSmartActivationTicks", "3"),
      smart_profit_floor_ticks: labDefaultValue("#labSmartFloorTicks", "1"),
      smart_min_distance_ticks: labDefaultValue("#labSmartMinDistanceTicks", "1"),
      smart_trail_percent: "30",
      smart_min_distance: "0.05",
      smart_adverse_ticks: 3,
      smart_adverse_window: 4,
      smart_opposite_signal: true,
      smart_server_take_profit: labDefaultValue("#labServerTp", "2.00"),
    };
  });
}

async function startStrategyLab() {
  try {
    const bots = gatherLabBots();
    if (bots.length < 1) throw new Error("Add at least one Strategy Lab tester");
    const result = await api("/api/lab/start", { method: "POST", body: JSON.stringify({ bots }) });
    renderStrategyLab(result.lab);
    toast(`Strategy Lab started ${bots.length} demo bot${bots.length === 1 ? "" : "s"}`, "success");
  } catch (error) { toast(error.message, "error"); }
}

async function stopStrategyLab(botId = "") {
  try {
    const result = await api("/api/lab/stop", { method: "POST", body: JSON.stringify({ bot_id: botId }) });
    renderStrategyLab(result.lab);
    toast(botId ? `New entries stopped for ${botId}; open trades are still being managed` : "New entries stopped for all lab bots; open trades are still being managed", "success");
  } catch (error) { toast(error.message, "error"); }
}

function downloadTextFile(filename, content, mime = "text/plain") {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function downloadLabReport(botId, format = "md") {
  try {
    const data = await api(`/api/lab/report?bot_id=${encodeURIComponent(botId)}&format=${encodeURIComponent(format)}`);
    const report = data.report || {};
    downloadTextFile(report.filename || `${botId}_optimization.${format}`, report.content || "", format === "json" ? "application/json" : "text/markdown");
    toast(`Optimization ${format.toUpperCase()} downloaded for ${botId}`, "success");
  } catch (error) { toast(error.message, "error"); }
}

async function downloadLabRunSummary(format = "md") {
  try {
    const data = await api(`/api/lab/run-summary?format=${encodeURIComponent(format)}`);
    const report = data.report || {};
    downloadTextFile(report.filename || `RUN_OPTIMIZATION_SUMMARY.${format}`, report.content || "", format === "json" ? "application/json" : "text/markdown");
    toast(`Strategy Lab run summary ${format.toUpperCase()} downloaded`, "success");
  } catch (error) { toast(error.message, "error"); }
}

function renderStrategyLab(lab = {}) {
  labLastStatus = lab || { bots: [], running: false };
  const bots = [...(lab.bots || [])];
  const runningCount = bots.filter((bot) => bot.running).length;
  const badge = $("#labRunBadge");
  if (badge) {
    badge.textContent = runningCount ? `${runningCount} running` : (bots.length ? "Run complete" : "Lab idle");
    badge.className = `badge ${runningCount ? "running" : "neutral"}`;
  }
  $("#labStartAllButton").disabled = runningCount > 0;
  const stoppableCount = bots.filter((bot) => bot.running && !bot.stop_requested).length;
  $("#labStopAllButton").disabled = stoppableCount === 0;
  $("#labStopAllButton").textContent = stoppableCount ? "Stop all entries" : (runningCount ? "Draining…" : "Stop all entries");
  $("#labAddBotButton").disabled = runningCount > 0;
  $$(".lab-remove").forEach((button) => button.disabled = runningCount > 0);
  $$(".lab-bot-card input, .lab-bot-card select").forEach((input) => input.disabled = runningCount > 0);

  const combined = bots.reduce((sum, bot) => sum + toNumber(bot.profit, 0), 0);
  $("#labActiveCount").textContent = `${runningCount} active / ${bots.length} loaded`;
  $("#labCombinedProfit").textContent = formatNumber(combined);
  $("#labCombinedProfit").className = combined > 0 ? "positive" : combined < 0 ? "negative" : "";
  const anyReportReady = bots.some((bot) => bot.report_ready);
  $("#labDownloadSummaryMd").disabled = !anyReportReady;
  $("#labDownloadSummaryJson").disabled = !anyReportReady;

  const sorted = [...bots].sort((a, b) => toNumber(b.profit, 0) - toNumber(a.profit, 0));
  const body = $("#labResultsBody");
  body.innerHTML = sorted.length ? sorted.map((bot, index) => {
    const profit = toNumber(bot.profit, 0);
    const state = bot.running
      ? (bot.stop_requested ? "DRAINING" : (bot.link_state === "RECONNECTING" ? "RECONNECTING" : "RUNNING"))
      : (bot.exit_code === null || bot.exit_code === undefined ? "READY" : "STOPPED");
    const statusClass = state === "RUNNING" ? "up" : (state === "RECONNECTING" || state === "DRAINING") ? "warn" : "neutral";
    return `<tr>
      <td><strong class="lab-rank">#${index + 1}</strong> ${escapeHtml(bot.label || bot.id)}</td>
      <td><strong>${escapeHtml(bot.strategy_label || bot.strategy)}</strong><small class="table-sub">${escapeHtml(bot.profile)} · $${escapeHtml(bot.stake)} each · ×${escapeHtml(bot.multiplier)} · ${escapeHtml(bot.exit_mode)} · ${escapeHtml(String(bot.trades_per_signal || (bot.batch_mode === "all" ? bot.max_open : 1)))} / signal</small></td>
      <td><span class="micro-badge ${statusClass}">${escapeHtml(state)}</span></td>
      <td>${escapeHtml(bot.trades)}</td>
      <td>${escapeHtml(bot.wins)} / ${escapeHtml(bot.losses)}</td>
      <td>${escapeHtml(bot.win_rate)}%</td>
      <td class="${profit > 0 ? "positive" : profit < 0 ? "negative" : ""}">${escapeHtml(bot.profit)}</td>
      <td>${escapeHtml(bot.open_profit)}</td>
      <td>${escapeHtml(bot.signal)}</td>
      <td>${escapeHtml(bot.open_trades)} / ${escapeHtml(bot.max_open || 1)}</td>
      <td>${escapeHtml(bot.reconnects || 0)}</td>
      <td><div class="lab-row-actions"><button type="button" class="button danger small lab-stop-one" data-bot-id="${escapeHtml(bot.id)}" ${bot.running && !bot.stop_requested ? "" : "disabled"}>${bot.stop_requested && bot.running ? "Draining…" : "Stop entries"}</button>${bot.report_ready ? `<button type="button" class="button secondary small lab-report-one" data-bot-id="${escapeHtml(bot.id)}" data-format="md">Report</button><button type="button" class="button secondary small lab-report-one" data-bot-id="${escapeHtml(bot.id)}" data-format="json">JSON</button>` : (!bot.running && bot.exit_code !== null && bot.exit_code !== undefined ? `<span class="table-sub">${escapeHtml(bot.report_error || "Building report…")}</span>` : "")}</div></td>
    </tr>`;
  }).join("") : '<tr><td colspan="12" class="empty">Add two or more test bots to begin a comparison.</td></tr>';

  const select = $("#labConsoleSelect");
  const previous = select.value || labSelectedConsoleBot;
  select.innerHTML = '<option value="">Select tester…</option>' + bots.map((bot) => `<option value="${escapeHtml(bot.id)}">${escapeHtml(bot.label || bot.id)} · ${escapeHtml(bot.strategy_label || bot.strategy)} (${escapeHtml(bot.profile)})</option>`).join("");
  if (bots.some((bot) => bot.id === previous)) select.value = previous;
  else if (bots.length) select.value = bots[0].id;
  labSelectedConsoleBot = select.value;
}

async function pollLabConsole() {
  const botId = $("#labConsoleSelect")?.value || "";
  if (!botId) return;
  if (labSelectedConsoleBot !== botId) {
    labSelectedConsoleBot = botId;
    $("#labConsoleOutput").textContent = "";
  }
  const after = labLogIds[botId] || 0;
  try {
    const data = await api(`/api/lab/logs?bot_id=${encodeURIComponent(botId)}&after=${after}`);
    if (data.lines?.length) {
      const terminal = $("#labConsoleOutput");
      const atBottom = terminal.scrollTop + terminal.clientHeight >= terminal.scrollHeight - 40;
      terminal.textContent += (terminal.textContent ? "\n" : "") + data.lines.map((line) => line.text).join("\n");
      if (terminal.textContent.length > 180000) terminal.textContent = terminal.textContent.slice(-150000);
      if (atBottom) terminal.scrollTop = terminal.scrollHeight;
    }
    labLogIds[botId] = data.last_id || after;
  } catch (_) {}
}

async function poll() {
  try {
    const [statusData, logData] = await Promise.all([
      api("/api/status"),
      api(`/api/logs?after=${lastLogId}`),
    ]);
    renderStatus(statusData.status);
    renderTrades(statusData.trades);
    renderAdvancedOps(statusData.status, statusData.trades);
    renderStrategyLab(statusData.lab || {});
    renderRfLab(statusData.rise_fall_lab || {});
    renderAIOptimizer(statusData.ai_optimizer || {});
    pollLabConsole();
    pollRfLabConsole();
    pollRiseFall();
    appendLogs(logData.lines || []);
    lastLogId = logData.last_id || lastLogId;
  } catch (error) {
    $("#processBadge").textContent = "UI offline";
    $("#processBadge").className = "badge stopped";
    setApiState("error", "Offline");
  }
}

async function startBot() {
  if (localStorage.getItem(RISK_COMMISSION_ACCEPT_KEY) !== "accepted") {
    pendingDeployAfterRiskAcceptance = true;
    openRiskNotice();
    toast("Review and accept the risk and commission notice before deployment.", "info");
    return;
  }
  try {
    const settings = formToObject($("#sessionForm"));
    const result = await api("/api/start", { method: "POST", body: JSON.stringify(settings) });
    if (result.config) populateConfigForm(result.config);
    renderStatus(result.status);
    switchTab("dashboard");
    toast("Bot started", "success");
  } catch (error) { toast(error.message, "error"); }
}

async function stopBot() {
  try {
    await api("/api/stop", { method: "POST", body: "{}" });
    toast("New entries stopped. Open contracts stay managed until they close normally.", "success");
  } catch (error) { toast(error.message, "error"); }
}

async function saveCredentials(event) {
  event.preventDefault();
  try {
    const payload = formToObject(event.currentTarget);
    const displayAppId = String(payload.app_id || "").trim();
    if (displayAppId) localStorage.setItem(APP_ID_DISPLAY_KEY, displayAppId);
    const result = await api("/api/credentials", { method: "POST", body: JSON.stringify(payload) });
    renderCredentials(result.credentials);
    if (displayAppId) event.currentTarget.elements.app_id.value = displayAppId;
    toast("Credentials saved locally", "success");
  } catch (error) { toast(error.message, "error"); }
}

async function testConnection() {
  const button = $("#testConnectionButton");
  button.disabled = true;
  button.textContent = "Testing…";
  try {
    const result = await api("/api/test-connection", { method: "POST", body: "{}" });
    const c = result.connection;
    $("#connectionBadge").textContent = `${c.account_id} · ${c.balance} ${c.currency}`;
    $("#connectionBadge").className = "badge running";
    setApiState("online", "Connected");
    toast("Connection successful", "success");
  } catch (error) {
    $("#connectionBadge").textContent = "Connection failed";
    $("#connectionBadge").className = "badge stopped";
    setApiState("error", "Connection failed");
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Test connection and balance";
  }
}


async function saveConfig() {
  try {
    const result = await api("/api/config", { method: "POST", body: JSON.stringify(readConfigForm()) });
    populateConfigForm(result.config);
    toast("Strategy config saved", "success");
  } catch (error) { toast(error.message, "error"); }
}

async function clearHistory() {
  if (!confirm("Archive the current CSV and start a new trade history file?")) return;
  try {
    const result = await api("/api/clear-history", { method: "POST", body: "{}" });
    renderTrades(result.trades);
    toast("Trade history archived", "success");
  } catch (error) { toast(error.message, "error"); }
}


function diagnosticReasonMeaning(reason) {
  const meanings = {
    entry_stop: "Graceful stop is active; new orders are intentionally blocked.",
    batch_opening: "A previous opening batch is still being created.",
    cooldown: "Post-signal / post-trade cooldown has not expired yet.",
    awaiting_neutral_rearm: "The bot is waiting for the strategy to return neutral before accepting a new fresh signal.",
    neutral_seen: "Neutral state was seen; the next fresh signal may arm again.",
    no_strategy_signal: "The selected single strategy did not produce a trade signal on this tick.",
    ensemble_no_eligible_votes: "No selected strategy currently has an eligible remembered vote for this regime.",
    ensemble_not_enough_agreement: "One or more strategies voted, but the ensemble did not reach the required agreement.",
    ensemble_raw_vote_waiting_confirmation_or_rearm: "The ensemble has enough raw agreement, but its final confirmation/re-arm gate has not emitted a new signal yet.",
    no_available_slots: "The strategy passed, but all allowed simultaneous trade slots are occupied/reserved.",
    strategy_gate_passed: "A final signal passed the strategy gate.",
    opening_task_created: "The order-opening task was scheduled.",
    sending_proposal: "The bot is requesting a Deriv proposal.",
    proposal_received: "Deriv returned a proposal successfully.",
    proposal_request_failed: "The Deriv proposal request failed before a buy could be sent.",
    sending_buy: "The bot is sending the buy request.",
    buy_request_failed: "The buy request failed or was rejected.",
    contract_opened: "A new contract opened successfully.",
    connection_interrupted: "The connection dropped during an opening batch.",
    unexpected_error: "The opening batch failed with an unexpected error.",
    signal_would_open: "Signals-only mode: the bot would have opened a trade but intentionally did not buy."
  };
  return meanings[reason] || "Recorded entry-path state.";
}

function renderDiagnostics(diag = {}) {
  const counts = diag.event_counts || {};
  const ens = diag.last_ensemble || {};
  const last = diag.last_event || {};
  const running = Boolean(diag.running);
  const banner = String(diag.runtime_banner || "");
  const buildMatch = banner.match(/V(\d+(?:\.\d+)+)/i);
  const build = buildMatch ? `V${buildMatch[1]}` : (running ? "Starting…" : "—");
  $("#diagBuild").textContent = build;
  $("#diagFolder").textContent = diag.source_folder ? `Package ${diag.source_folder}` : "Package —";

  const settings = diag.settings || {};
  const dashboard = diag.dashboard || {};
  const signalOnly = String(settings.mode || "").toLowerCase() === "signals";
  $("#diagModeWarning").classList.toggle("hidden", !signalOnly);
  const linkState = String(dashboard.link_state || (running ? "ONLINE" : "OFFLINE")).toUpperCase();
  const reconnecting = linkState === "RECONNECTING";
  const reconnectAttempts = Number(dashboard.reconnect_attempts || 0);
  const reconnectFailures = Number(dashboard.reconnect_failures || 0);
  const reconnectSuccess = Number(dashboard.reconnects || 0);
  const retryDelay = Number(dashboard.reconnect_next_delay || 0);
  $("#diagReconnectState").textContent = reconnecting ? "AUTO / RECONNECTING" : `AUTO / ${linkState}`;
  $("#diagReconnectDetail").textContent = reconnecting
    ? `${reconnectAttempts} attempts · ${reconnectFailures} failures · retry ${formatNumber(retryDelay, 0)}s`
    : `${reconnectAttempts} attempts · ${reconnectFailures} failures · ${reconnectSuccess} recovered`;

  const eventName = String(last.event || (running ? "waiting" : "stopped")).replaceAll("_", " ").toUpperCase();
  $("#diagEntryState").textContent = eventName;
  $("#diagEntryReason").textContent = String(last.reason || (running ? "Collecting entry decisions" : "No running session")).replaceAll("_", " ");
  $("#diagSignalsReady").textContent = counts.signal_ready || 0;
  $("#diagScheduled").textContent = `${counts.open_scheduled || 0} openings scheduled`;
  $("#diagBuySuccess").textContent = counts.buy_success || 0;
  const orderErrors = (counts.proposal_error || 0) + (counts.buy_error || 0) + (counts.batch_error || 0);
  $("#diagOrderErrors").textContent = `${orderErrors} order errors`;
  $("#diagRegime").textContent = ens.regime || "—";
  $("#diagRegimeEfficiency").textContent = ens.regime ? `Efficiency ${formatNumber(ens.regime_efficiency || 0, 2)}` : "Efficiency —";
  $("#diagGate").textContent = ens.gate || "—";
  $("#diagVotes").textContent = ens.policy ? `UP ${ens.up_votes || 0}/${formatNumber(ens.up_score || 0, 2)} · DOWN ${ens.down_votes || 0}/${formatNumber(ens.down_score || 0, 2)}` : "No ensemble vote state";
  $("#diagPolicyBadge").textContent = String(ens.policy || "—").toUpperCase();

  const warnings = diag.warnings || [];
  const mismatch = warnings.some((item) => String(item).toLowerCase().includes("version mismatch"));
  const noTradeWarning = warnings.some((item) => String(item).toLowerCase().includes("no ensemble") || String(item).toLowerCase().includes("no successful") || String(item).toLowerCase().includes("no contract"));
  $("#diagHealthBadge").textContent = mismatch ? "WRONG BUILD" : signalOnly ? "SIGNALS ONLY" : reconnecting ? "RECONNECTING" : noTradeWarning ? "NO-TRADE FOUND" : running ? "DIAGNOSTICS LIVE" : "READY";
  $("#diagHealthBadge").className = `badge ${mismatch || signalOnly || noTradeWarning ? "stopped" : reconnecting ? "neutral" : running ? "running" : "neutral"}`;

  const blockers = diag.block_reasons || [];
  $("#diagBlockerBody").innerHTML = blockers.length ? blockers.map((item) => `<tr><td><strong>${escapeHtml(String(item.reason || "unknown").replaceAll("_", " "))}</strong></td><td>${escapeHtml(item.count || 0)}</td><td>${escapeHtml(diagnosticReasonMeaning(item.reason))}</td></tr>`).join("") : '<tr><td colspan="3" class="empty">No blocker events recorded yet.</td></tr>';

  const selected = Array.isArray(ens.selected) ? ens.selected.join(", ") : "—";
  const eligible = Array.isArray(ens.eligible) ? ens.eligible.join(", ") : "—";
  const memory = ens.memory && typeof ens.memory === "object" ? Object.entries(ens.memory).map(([sid, value]) => `${sid}:${String(value.direction || "").replace("MULT", "")}@${value.age ?? 0}`).join(", ") : "—";
  $("#diagEnsembleInspector").innerHTML = `
    <div><span>Selected engines</span><strong>${escapeHtml(selected || "—")}</strong></div>
    <div><span>Eligible now</span><strong>${escapeHtml(eligible || "—")}</strong></div>
    <div><span>Raw direction</span><strong>${escapeHtml(ens.raw_direction || "—")}</strong></div>
    <div><span>Final output</span><strong>${escapeHtml(ens.output_signal || "—")}</strong></div>
    <div><span>Vote memory</span><strong>${escapeHtml(memory || "—")}</strong></div>
    <div><span>Gate armed</span><strong>${ens.gate_armed === undefined ? "—" : ens.gate_armed ? "YES" : "NO — waiting neutral"}</strong></div>`;

  const attribution = diag.ensemble_trade_attribution || {};
  const attributionRows = Object.entries(attribution).sort((a, b) => toNumber(b[1]?.profit, 0) - toNumber(a[1]?.profit, 0));
  $("#diagAttributionBody").innerHTML = attributionRows.length ? attributionRows.map(([sources, item]) => {
    const profit = toNumber(item.profit, 0);
    const regimes = item.regimes && typeof item.regimes === "object" ? Object.entries(item.regimes).map(([name, count]) => `${name}:${count}`).join(", ") : "—";
    return `<tr><td><strong>${escapeHtml(sources)}</strong></td><td>${escapeHtml(item.trades || 0)}</td><td>${escapeHtml(item.wins || 0)} / ${escapeHtml(item.losses || 0)}</td><td>${escapeHtml(item.win_rate || 0)}%</td><td class="${profit > 0 ? "positive" : profit < 0 ? "negative" : ""}">${escapeHtml(item.profit || 0)}</td><td>${escapeHtml(regimes)}</td></tr>`;
  }).join("") : '<tr><td colspan="6" class="empty">No ensemble trades closed in this session yet.</td></tr>';

  const events = diag.recent_events || [];
  $("#diagEventBody").innerHTML = events.length ? events.slice().reverse().map((event) => {
    const e = event.ensemble || {};
    const votes = e.policy ? `UP ${e.up_votes || 0}/${formatNumber(e.up_score || 0, 2)} · DOWN ${e.down_votes || 0}/${formatNumber(e.down_score || 0, 2)}` : "—";
    const ts = String(event.timestamp_utc || "").replace("T", " ").replace("Z", "").slice(0, 19);
    return `<tr><td>${escapeHtml(ts || "—")}</td><td><strong>${escapeHtml(event.event || "—")}</strong></td><td>${escapeHtml(String(event.reason || "—").replaceAll("_", " "))}</td><td>${escapeHtml(event.signal || "—")}</td><td>${escapeHtml(e.regime || "—")}</td><td>${escapeHtml(votes)}</td></tr>`;
  }).join("") : '<tr><td colspan="6" class="empty">No entry events yet.</td></tr>';

  $("#diagWarnings").innerHTML = warnings.length ? warnings.map((item) => `<div class="diagnostic-finding warn">${escapeHtml(item)}</div>`).join("") : '<div class="diagnostic-finding neutral">No automatic warning detected yet. Keep this tab open while the session runs.</div>';
}

async function getDiagnosticReport(format = "md") {
  return api(`/api/diagnostics?format=${encodeURIComponent(format)}`);
}

async function downloadDiagnosticReport(format = "md") {
  try {
    const result = await getDiagnosticReport(format);
    const report = result.report || {};
    if (!report.content) throw new Error("Diagnostic report is empty");
    downloadTextFile(report.filename || `TRADE_SESSION_DIAGNOSTIC.${format}`, report.content, format === "json" ? "application/json" : "text/markdown");
    if (format === "md") $("#diagReportPreview").value = report.content;
    toast(`Diagnostic ${format.toUpperCase()} created`, "success");
  } catch (error) { toast(error.message, "error"); }
}

async function copyDiagnosticReport() {
  try {
    const result = await getDiagnosticReport("md");
    const report = result.report || {};
    const content = String(report.content || "");
    if (!content) throw new Error("Diagnostic report is empty");
    $("#diagReportPreview").value = content;
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(content);
    else {
      const box = $("#diagReportPreview");
      box.focus(); box.select(); document.execCommand("copy");
    }
    toast("Sanitized troubleshooting report copied", "success");
  } catch (error) { toast(error.message, "error"); }
}

async function refreshDiagnosticsNow() {
  try {
    const data = await api("/api/status");
    renderDiagnostics(data.diagnostics || {});
    toast("Diagnostics refreshed", "success");
  } catch (error) { toast(error.message, "error"); }
}

function openRiskNotice() {
  const dialog = $("#riskNoticeDialog");
  if (!dialog) return;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closeRiskNotice() {
  pendingDeployAfterRiskAcceptance = false;
  const dialog = $("#riskNoticeDialog");
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

function acceptRiskCommissionNotice() {
  localStorage.setItem(RISK_COMMISSION_ACCEPT_KEY, "accepted");
  const shouldDeploy = pendingDeployAfterRiskAcceptance;
  pendingDeployAfterRiskAcceptance = false;
  const dialog = $("#riskNoticeDialog");
  if (dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }
  toast("Risk and markup commission notice accepted.", "success");
  if (shouldDeploy) setTimeout(() => startBot(), 120);
}


function advancedNumber(value, fallback = 0) {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function formatRuntimeSeconds(seconds) {
  const total = Math.max(0, Math.floor(advancedNumber(seconds, 0)));
  const h = Math.floor(total / 3600); const m = Math.floor((total % 3600) / 60); const sec = total % 60;
  return [h,m,sec].map((v)=>String(v).padStart(2,"0")).join(":");
}

function advancedSetProfit(el, value, suffix = "") {
  if (!el) return;
  const n = advancedNumber(value, 0);
  el.textContent = `${formatNumber(n)}${suffix}`;
  el.classList.toggle("positive", n > 0);
  el.classList.toggle("negative", n < 0);
}

function setDashboardMode(mode, persist = true) {
  const advanced = mode === "advanced";
  $("#simpleDashboardView")?.classList.toggle("hidden", advanced);
  $("#advancedDashboardView")?.classList.toggle("hidden", !advanced);
  $("#simpleDashButton")?.classList.toggle("active", !advanced);
  $("#advancedDashButton")?.classList.toggle("active", advanced);
  if (persist) localStorage.setItem(ADV_DASH_MODE_KEY, advanced ? "advanced" : "simple");
  if (advanced) requestAnimationFrame(() => renderAdvancedOps(latestAdvancedStatus, latestAdvancedTrades));
}

function telemetrySettings(status = latestAdvancedStatus) {
  const t = status?.telemetry || {};
  return (t.settings && typeof t.settings === "object") ? t.settings : {};
}

function runtimeSettingsPayloadFromSessionForm() {
  const raw = formToObject($("#sessionForm"));
  const keys = ["capital","split","max_open","trades_per_signal","split_delay","compound","loss_policy","loss_count","profit_target","loss_limit","win_target","take_profit_mode","take_profit_unit","take_profit","take_profit_max","take_profit_ticks","take_profit_ticks_max","tick_tp_server_backup","smart_activation","smart_profit_floor","smart_trail_percent","smart_min_distance","smart_activation_ticks","smart_profit_floor_ticks","smart_min_distance_ticks","smart_adverse_ticks","smart_adverse_window","smart_opposite_signal","smart_server_take_profit","server_stop_loss","smart_loss_enabled","smart_loss_soft_trigger","smart_loss_reset_level","smart_loss_hard_cap","smart_loss_grace_ticks","smart_loss_max_watch_ticks","smart_loss_adverse_ticks","smart_loss_adverse_window","smart_loss_opposite_signal","smart_loss_rescue_profit","multiplier","ensemble_policy","ensemble_min_votes","ensemble_memory_ticks","ensemble_confirmations","ensemble_cooldown_ticks","ensemble_regime_lookback","ensemble_trend_threshold","ensemble_weight_threshold","ensemble_conflict_margin"];
  return Object.fromEntries(keys.filter((key)=>raw[key] !== undefined).map((key)=>[key,raw[key]]));
}

async function sendAdvancedLiveControl(payload, successText = "Live control sent") {
  try {
    const result = await api("/api/live-controls", { method:"POST", body:JSON.stringify(payload) });
    advancedControlsDirty = false;
    if (result.status) renderAdvancedOps(result.status, latestAdvancedTrades);
    toast(successText, "success");
    return result;
  } catch (error) {
    toast(error.message, "error");
    throw error;
  }
}

function loadAdvancedControlFields(status = latestAdvancedStatus, force = false) {
  if (advancedControlsDirty && !force) return;
  const settings = telemetrySettings(status);
  if (!Object.keys(settings).length) return;
  const set = (id, value) => {
    const el = $(id); if (!el || (!force && document.activeElement === el)) return;
    if (el.type === "checkbox") el.checked = Boolean(value); else if (value !== undefined && value !== null) el.value = String(value);
  };
  set("#advCtlMaxOpen", settings.max_open_trades); set("#advCtlTradesPerSignal", settings.trades_per_signal || 1);
  set("#advCtlSplitDelay", settings.split_delay_seconds); set("#advCtlMultiplier", settings.multiplier);
  set("#advCtlTpMode", settings.take_profit_mode); set("#advCtlTpUnit", settings.take_profit_unit);
  set("#advCtlTp", settings.take_profit); set("#advCtlTpMax", settings.take_profit_max);
  set("#advCtlTpTicks", settings.take_profit_ticks); set("#advCtlTpTicksMax", settings.take_profit_ticks_max);
  set("#advCtlServerSl", settings.server_stop_loss); set("#advCtlLossPolicy", settings.loss_policy);
  set("#advCtlLossCount", settings.loss_count); set("#advCtlProfitTarget", settings.profit_target);
  set("#advCtlLossLimit", settings.loss_limit); set("#advCtlWinTarget", settings.win_target);
  set("#advCtlSmartActivation", settings.smart_activation); set("#advCtlSmartFloor", settings.smart_profit_floor);
  set("#advCtlSmartTrail", settings.smart_trail_percent); set("#advCtlSmartDistance", settings.smart_min_distance);
  set("#advCtlSmartLossEnabled", settings.smart_loss_enabled); set("#advCtlSmartLossSoft", settings.smart_loss_soft_trigger);
  set("#advCtlSmartLossReset", settings.smart_loss_reset_level); set("#advCtlSmartLossHard", settings.smart_loss_hard_cap);
  set("#advCtlCapital", settings.capital); set("#advCtlSplit", settings.split_count); set("#advCtlCompound", settings.compound);
  const runtimeStrategy = $("#advRuntimeStrategy");
  if (runtimeStrategy && settings.strategy_mode === "ensemble" && ![...runtimeStrategy.options].some((o)=>o.value === "ensemble")) {
    runtimeStrategy.add(new Option("Strategy Fusion (active ensemble)", "ensemble"));
  }
  if (runtimeStrategy && !advancedStrategyDirty && settings.strategy_mode && [...runtimeStrategy.options].some((o)=>o.value === settings.strategy_mode)) runtimeStrategy.value = settings.strategy_mode;
}

function renderAdvancedPositions(positions = []) {
  const body = $("#advPositionsBody"); if (!body) return;
  if (!positions.length) { body.innerHTML = '<tr><td colspan="12" class="empty">No active positions</td></tr>'; return; }
  body.innerHTML = positions.map((p) => {
    const pl = advancedNumber(p.profit,0); const plClass = pl>0?"positive":pl<0?"negative":"";
    const trail = p.take_profit_unit === "ticks" ? `${advancedNumber(p.trailing_floor_ticks,0).toFixed(2)}t` : formatNumber(p.trailing_floor || 0);
    return `<tr><td>#${escapeHtml(p.contract_id)}</td><td>${escapeHtml(p.direction)}</td><td>${formatNumber(p.stake)}</td><td>${p.entry_spot == null?"—":escapeHtml(p.entry_spot)}</td><td>${p.last_quote == null?"—":escapeHtml(p.last_quote)}</td><td class="${plClass}">${formatNumber(pl)}</td><td>${formatNumber(p.peak_profit||0)}</td><td class="${advancedNumber(p.worst_profit,0)<0?"negative":""}">${formatNumber(p.worst_profit||0)}</td><td>${escapeHtml(trail)}</td><td>${p.protection_active?"ACTIVE":"—"}</td><td>${p.loss_guard_active?`WATCH ${p.loss_guard_ticks||0}`:"—"}</td><td>${escapeHtml(p.closing_requested?(p.closing_reason||"closing"):(p.status||"open"))}</td></tr>`;
  }).join("");
}

function renderAdvancedRecentTrades(trades = latestAdvancedTrades) {
  const host = $("#advRecentTrades"); if (!host) return;
  const rows = [...(trades?.rows || [])].slice(-12).reverse();
  if (!rows.length) { host.innerHTML = '<div class="empty">No trades recorded</div>'; return; }
  host.innerHTML = rows.map((row)=>{
    const pl=advancedNumber(row.profit,0); const cls=pl>0?"positive":pl<0?"negative":"";
    const label=row.entry_pattern||row.strategy||row.direction||"Trade";
    const when=row.closed_utc||row.closed_at||row.timestamp||"";
    return `<div class="advanced-trade-item"><div><strong>${escapeHtml(String(label).replaceAll("_"," "))}</strong><small>${escapeHtml(row.direction||"")} · ${escapeHtml(when)}</small></div><div class="${cls}"><strong>${formatNumber(pl)}</strong><small>${escapeHtml(row.result||"")}</small></div></div>`;
  }).join("");
}

function renderAdvancedOpsV219Base(status = {}, trades = {}) {
  latestAdvancedStatus = status || {};
  latestAdvancedTrades = trades || latestAdvancedTrades;
  const t = status?.telemetry || {};
  const d = status?.dashboard || {};
  const settings = t.settings || {};
  const positions = Array.isArray(t.positions) ? t.positions : [];
  const running = Boolean(status?.running);
  const balance = t.balance ?? d.balance;
  const currency = t.currency || d.currency || "USD";
  const capital = advancedNumber(settings.capital ?? d.capital, 0);
  const effective = advancedNumber(t.effective_capital ?? d.effective_capital, capital);
  const realized = advancedNumber(t.realized_profit ?? d.realized_profit, 0);
  const openPl = advancedNumber(t.open_profit ?? d.open_profit, 0);
  const totalPl = realized + openPl;
  const exposure = positions.reduce((sum,p)=>sum+advancedNumber(p.stake,0),0);
  const openCount = positions.length || advancedNumber(d.open_trades,0);
  const maxOpen = advancedNumber(settings.max_open_trades ?? d.max_open,0);
  $("#advBalance").textContent = balance == null ? "—" : `${formatNumber(balance)} ${currency}`;
  $("#advAccount").textContent = t.account || d.account || "—"; $("#advCapital").textContent = capital ? `${formatNumber(capital)} ${currency}` : "—";
  $("#advEffectiveCapital").textContent = effective ? `${formatNumber(effective)} ${currency}` : "—"; $("#advStakeSlot").textContent = `${formatNumber(t.slot_stake ?? d.stake_per_slot ?? 0)} ${currency}`;
  $("#advAvailableSlots").textContent = String(t.available_slots ?? d.available_slots ?? 0); $("#advExposure").textContent = `${formatNumber(exposure)} ${currency}`;
  $("#advFreeFunds").textContent = balance == null ? "—" : `${formatNumber(Math.max(0, advancedNumber(balance,0)-exposure))} ${currency}`;
  $("#advOpenMax").textContent = `${openCount} / ${maxOpen}`; $("#advCapitalUsage").textContent = capital > 0 ? `${Math.min(999, exposure/capital*100).toFixed(1)}%` : "—";
  advancedSetProfit($("#advRealized"), realized); advancedSetProfit($("#advOpenPl"), openPl); advancedSetProfit($("#advTotalPl"), totalPl);
  const roi = capital > 0 ? totalPl / capital * 100 : 0; advancedSetProfit($("#advRoi"), roi, "%");
  $("#advTrades").textContent = String(t.trades ?? 0); $("#advWinRate").textContent = `${advancedNumber(t.win_rate,0).toFixed(1)}%`; $("#advLossStreak").textContent = String(t.consecutive_losses ?? 0); $("#advRuntime").textContent = formatRuntimeSeconds(t.runtime_seconds ?? status.runtime_seconds);
  const histRows = trades?.rows || []; const histProfits = histRows.map((r)=>advancedNumber(r.profit,0)); const wins=histProfits.filter((v)=>v>0), losses=histProfits.filter((v)=>v<0);
  const grossWin=wins.reduce((a,b)=>a+b,0), grossLoss=Math.abs(losses.reduce((a,b)=>a+b,0)); const pf=grossLoss>0?grossWin/grossLoss:(grossWin>0?Infinity:0);
  let equity=0,peak=0,maxDd=0; histProfits.forEach((v)=>{equity+=v;peak=Math.max(peak,equity);maxDd=Math.max(maxDd,peak-equity);});
  $("#advProfitFactor").textContent = pf===Infinity?"∞":pf?pf.toFixed(2):"—"; $("#advDrawdown").textContent=formatNumber(maxDd); $("#advAvgWin").textContent=formatNumber(wins.length?grossWin/wins.length:0); $("#advAvgLoss").textContent=formatNumber(losses.length?grossLoss/losses.length:0);
  $("#advStrategy").textContent = t.strategy_label || settings.strategy_mode || d.strategy || "—"; $("#advSignal").textContent = t.last_signal || d.signal || "WAITING"; $("#advQuote").textContent = t.quote ?? d.quote ?? "—";
  $("#advCooldown").textContent = String(t.cooldown_remaining ?? 0); $("#advNeutralRearm").textContent = t.neutral_rearm ? "YES" : "No"; $("#advBatchOpening").textContent = t.batch_opening ? `YES · ${t.opening_count||0} opening` : "No";
  const rec=t.reconnect||{}; $("#advLink").textContent = !running?"OFFLINE":rec.active?"RECONNECTING":"ONLINE"; $("#advReconnects").textContent=String(rec.success??0); $("#advReconnectAttempts").textContent=String(rec.attempts??0); $("#advReconnectFailures").textContent=String(rec.failures??0); $("#advReconnectError").textContent=rec.last_error||"None";
  const ctrl=t.live_control||{}; $("#advRevision").textContent=String(ctrl.revision??status.live_control_revision??0); $("#advRevisionState").textContent=String(ctrl.status||"ready").toUpperCase(); $("#advRevisionMessage").textContent=ctrl.message||"No live changes applied";
  $("#advActiveSettings").textContent=JSON.stringify(settings,null,2); $("#advStrategyMetrics").textContent=JSON.stringify(t.metrics||{},null,2);
  const pending=ctrl.pending_flat||{}; const pendingKeys=Object.keys(pending); const pendingNote=$("#advPendingFlat"); if(pendingNote){pendingNote.textContent=pendingKeys.length?`Queued until flat: ${pendingKeys.map((k)=>`${k}=${pending[k]}`).join(" · ")}`:"No queued sizing changes.";pendingNote.classList.toggle("queued",pendingKeys.length>0);}
  const stateBadge=$("#advControlState"); if(stateBadge){stateBadge.textContent=!running?"Bot stopped":t.stop_requested?"Draining":t.paused?"Entries paused":"Live control ready";stateBadge.className=`badge ${running?(t.paused?"paused":"running"):"neutral"}`;}
  if($("#advPauseEntries")) $("#advPauseEntries").disabled=!running||Boolean(t.paused)||Boolean(t.stop_requested); if($("#advResumeEntries")) $("#advResumeEntries").disabled=!running||!Boolean(t.paused)||Boolean(t.stop_requested);
  renderAdvancedPositions(positions); renderAdvancedRecentTrades(trades); loadAdvancedControlFields(status, false);
}

function collectCoreAdvancedControls() {
  return {
    max_open: $("#advCtlMaxOpen").value, trades_per_signal: $("#advCtlTradesPerSignal").value, split_delay: $("#advCtlSplitDelay").value,
    multiplier: $("#advCtlMultiplier").value, take_profit_mode: $("#advCtlTpMode").value, take_profit_unit: $("#advCtlTpUnit").value,
    take_profit: $("#advCtlTp").value, take_profit_max: $("#advCtlTpMax").value, take_profit_ticks: $("#advCtlTpTicks").value,
    take_profit_ticks_max: $("#advCtlTpTicksMax").value, server_stop_loss: $("#advCtlServerSl").value, loss_policy: $("#advCtlLossPolicy").value,
    loss_count: $("#advCtlLossCount").value, profit_target: $("#advCtlProfitTarget").value, loss_limit: $("#advCtlLossLimit").value, win_target: $("#advCtlWinTarget").value,
  };
}

function currentAdvancedLayout() {
  const grid=$("#advancedWidgetGrid"); if(!grid) return [];
  return [...grid.querySelectorAll(".advanced-widget")].map((w)=>({ id:w.dataset.widgetId, span:Number(w.dataset.span||2), height:w.dataset.height||"normal", hidden:w.classList.contains("widget-hidden") }));
}

function applyAdvancedLayout(layout) {
  const grid=$("#advancedWidgetGrid"); if(!grid||!Array.isArray(layout)) return;
  layout.forEach((item)=>{
    const w=grid.querySelector(`[data-widget-id="${CSS.escape(item.id)}"]`); if(!w)return;
    w.dataset.span=String(Math.max(1,Math.min(4,Number(item.span)||2))); w.dataset.height=["compact","normal","tall"].includes(item.height)?item.height:"normal"; w.classList.toggle("widget-hidden",Boolean(item.hidden)); grid.appendChild(w);
  });
  renderAdvancedWidgetLibrary();
}

function persistAdvancedLayout() { localStorage.setItem(ADV_LAYOUT_KEY,JSON.stringify(currentAdvancedLayout())); }
function loadAdvancedLayout() { try { const raw=JSON.parse(localStorage.getItem(ADV_LAYOUT_KEY)||"null"); if(Array.isArray(raw)) applyAdvancedLayout(raw); } catch(_){} }
function advancedLayoutPresets(){try{const raw=JSON.parse(localStorage.getItem(ADV_LAYOUT_PRESETS_KEY)||"{}");return raw&&typeof raw==="object"?raw:{};}catch{return{};}}
function renderAdvancedLayoutPresets(selected="") { const select=$("#advLayoutPreset");if(!select)return;const presets=advancedLayoutPresets();select.innerHTML='<option value="">Current layout</option>'+Object.keys(presets).sort().map((name)=>`<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");if(selected&&presets[selected])select.value=selected; }
function saveAdvancedLayoutPreset(){const name=prompt("Name this Advanced dashboard layout:","My Advanced Layout");if(!name)return;const presets=advancedLayoutPresets();presets[name.trim()]=currentAdvancedLayout();localStorage.setItem(ADV_LAYOUT_PRESETS_KEY,JSON.stringify(presets));renderAdvancedLayoutPresets(name.trim());toast(`Layout “${name.trim()}” saved`,"success");}
function resetAdvancedLayout(){localStorage.removeItem(ADV_LAYOUT_KEY);location.reload();}

function renderAdvancedWidgetLibrary(){const host=$("#advWidgetLibraryPanel");const grid=$("#advancedWidgetGrid");if(!host||!grid)return;host.innerHTML=[...grid.querySelectorAll(".advanced-widget")].map((w)=>`<button type="button" class="${w.classList.contains("widget-hidden")?"":"widget-visible"}" data-show-widget="${escapeHtml(w.dataset.widgetId)}">${w.classList.contains("widget-hidden")?"＋":"✓"} ${escapeHtml(w.querySelector("h3")?.textContent||w.dataset.widgetId)}</button>`).join("");}

function decorateAdvancedWidgets(){
  $$("#advancedWidgetGrid .advanced-widget").forEach((w)=>{
    const controls=w.querySelector(".widget-controls"); if(!controls||controls.dataset.ready)return; controls.dataset.ready="1";
    controls.innerHTML='<button type="button" data-widget-action="width" title="Cycle width">↔</button><button type="button" data-widget-action="height" title="Cycle height">↕</button><button type="button" data-widget-action="hide" title="Hide widget">×</button>';
  });
}

function setAdvancedLayoutEditing(enabled){advancedLayoutEditing=Boolean(enabled);const grid=$("#advancedWidgetGrid");grid?.classList.toggle("editing",advancedLayoutEditing);$$('#advancedWidgetGrid .advanced-widget').forEach((w)=>{w.draggable=advancedLayoutEditing;w.classList.toggle("layout-editing",advancedLayoutEditing);});$("#advLayoutToolbar")?.classList.toggle("hidden",!advancedLayoutEditing);if(advancedLayoutEditing){renderAdvancedWidgetLibrary();renderAdvancedLayoutPresets();}}

function initAdvancedDragDrop(){
  const grid=$("#advancedWidgetGrid"); if(!grid)return; let dragged=null;
  grid.addEventListener("dragstart",(e)=>{const w=e.target.closest(".advanced-widget");if(!advancedLayoutEditing||!w)return;dragged=w;e.dataTransfer.effectAllowed="move";});
  grid.addEventListener("dragover",(e)=>{if(!dragged)return;e.preventDefault();const target=e.target.closest(".advanced-widget");if(target&&target!==dragged)target.classList.add("drag-over");});
  grid.addEventListener("dragleave",(e)=>e.target.closest(".advanced-widget")?.classList.remove("drag-over"));
  grid.addEventListener("drop",(e)=>{if(!dragged)return;e.preventDefault();const target=e.target.closest(".advanced-widget");$$('.advanced-widget.drag-over').forEach((x)=>x.classList.remove("drag-over"));if(target&&target!==dragged){const rect=target.getBoundingClientRect();const after=e.clientY>rect.top+rect.height/2;target.insertAdjacentElement(after?"afterend":"beforebegin",dragged);persistAdvancedLayout();}dragged=null;});
  grid.addEventListener("dragend",()=>{$$('.advanced-widget.drag-over').forEach((x)=>x.classList.remove("drag-over"));dragged=null;});
}

function initAdvancedOpsV219Base(){
  decorateAdvancedWidgets(); loadAdvancedLayout(); renderAdvancedLayoutPresets(); initAdvancedDragDrop();
  setDashboardMode(localStorage.getItem(ADV_DASH_MODE_KEY)==="advanced"?"advanced":"simple",false);
  $("#simpleDashButton")?.addEventListener("click",()=>setDashboardMode("simple")); $("#advancedDashButton")?.addEventListener("click",()=>setDashboardMode("advanced"));
  $("#advPauseEntries")?.addEventListener("click",()=>sendAdvancedLiveControl({action:"pause",label:"Pause new entries"},"New entries paused; open trades remain managed"));
  $("#advResumeEntries")?.addEventListener("click",()=>sendAdvancedLiveControl({action:"resume",label:"Resume new entries"},"New entries resumed"));
  $("#advApplySessionLive")?.addEventListener("click",()=>sendAdvancedLiveControl({action:"apply",label:"Session page live update",settings:runtimeSettingsPayloadFromSessionForm()},"Supported Session settings pushed live"));
  $("#advLiveControlForm")?.addEventListener("input",()=>{advancedControlsDirty=true;}); $("#advProtectionForm")?.addEventListener("input",()=>{advancedControlsDirty=true;});
  ["#advCtlCapital","#advCtlSplit","#advCtlCompound"].forEach((sel)=>$(sel)?.addEventListener("input",()=>{advancedControlsDirty=true;}));
  $("#advRuntimeStrategy")?.addEventListener("change",()=>{advancedStrategyDirty=true;});
  $("#advLoadControls")?.addEventListener("click",()=>{advancedControlsDirty=false;advancedStrategyDirty=false;loadAdvancedControlFields(latestAdvancedStatus,true);toast("Active runtime values loaded","success");});
  $("#advApplyControls")?.addEventListener("click",()=>sendAdvancedLiveControl({action:"apply",label:"Core live controls",settings:collectCoreAdvancedControls()},"Core runtime controls applied"));
  $("#advApplyProtection")?.addEventListener("click",()=>sendAdvancedLiveControl({action:"apply",label:"Smart protection controls",settings:{smart_activation:$("#advCtlSmartActivation").value,smart_profit_floor:$("#advCtlSmartFloor").value,smart_trail_percent:$("#advCtlSmartTrail").value,smart_min_distance:$("#advCtlSmartDistance").value,smart_loss_enabled:$("#advCtlSmartLossEnabled").checked,smart_loss_soft_trigger:$("#advCtlSmartLossSoft").value,smart_loss_reset_level:$("#advCtlSmartLossReset").value,smart_loss_hard_cap:$("#advCtlSmartLossHard").value}},"Smart protection updated live"));
  $("#advApplySizing")?.addEventListener("click",()=>sendAdvancedLiveControl({action:"apply",label:"Capital and stake sizing",settings:{capital:$("#advCtlCapital").value,split:$("#advCtlSplit").value,compound:$("#advCtlCompound").checked}},"Sizing update accepted; it will queue if positions are open"));
  $("#advApplyStrategyLive")?.addEventListener("click",async()=>{try{await sendAdvancedLiveControl({action:"apply",label:"Live strategy configuration",strategy:$("#advRuntimeStrategy").value,config:readConfigForm()},"Strategy engine reloaded live");advancedStrategyDirty=false;}catch(_){}});
  $("#advEditLayout")?.addEventListener("click",()=>setAdvancedLayoutEditing(!advancedLayoutEditing)); $("#advDoneLayout")?.addEventListener("click",()=>{persistAdvancedLayout();setAdvancedLayoutEditing(false);});
  $("#advSaveLayout")?.addEventListener("click",saveAdvancedLayoutPreset); $("#advResetLayout")?.addEventListener("click",resetAdvancedLayout); $("#advWidgetLibrary")?.addEventListener("click",()=>$("#advWidgetLibraryPanel")?.classList.toggle("hidden"));
  $("#advLayoutPreset")?.addEventListener("change",(e)=>{const p=advancedLayoutPresets()[e.target.value];if(p){applyAdvancedLayout(p);persistAdvancedLayout();toast(`Layout “${e.target.value}” loaded`,"success");}});
  $("#advWidgetLibraryPanel")?.addEventListener("click",(e)=>{const b=e.target.closest("[data-show-widget]");if(!b)return;const w=$("#advancedWidgetGrid")?.querySelector(`[data-widget-id="${CSS.escape(b.dataset.showWidget)}"]`);if(w){w.classList.toggle("widget-hidden");persistAdvancedLayout();renderAdvancedWidgetLibrary();}});
  $("#advancedWidgetGrid")?.addEventListener("click",(e)=>{const b=e.target.closest("[data-widget-action]");if(!b||!advancedLayoutEditing)return;const w=b.closest(".advanced-widget");if(!w)return;const action=b.dataset.widgetAction;if(action==="width")w.dataset.span=String((Number(w.dataset.span||2)%4)+1);if(action==="height"){const values=["compact","normal","tall"];w.dataset.height=values[(values.indexOf(w.dataset.height||"normal")+1)%values.length];}if(action==="hide")w.classList.add("widget-hidden");persistAdvancedLayout();renderAdvancedWidgetLibrary();});
}

function populateAdvancedRuntimeStrategies(){
  const select=$("#advRuntimeStrategy"); if(!select)return;
  const previous=select.value; select.innerHTML=(strategyCatalog||[]).map((item)=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.label||item.id)}</option>`).join("");
  if(previous&&[...select.options].some((o)=>o.value===previous))select.value=previous;
  const active=telemetrySettings().strategy_mode;if(active&&[...select.options].some((o)=>o.value===active))select.value=active;
}



// ============================================================
// V2.20 COMMAND DECK — zoned workspace + account intelligence
// ============================================================
const ADV_LAYOUT_V220_KEY = "controlNexusAdvancedLayoutV220";
const ADV_LAYOUT_PRESETS_V220_KEY = "controlNexusAdvancedLayoutPresetsV220";
const ADV_WORKSPACE_PREFS_V220_KEY = "controlNexusAdvancedWorkspacePrefsV220";
const ADV_DECK_TAB_V220_KEY = "controlNexusAdvancedDeckTabV220";
let latestAccountInsightsV220 = null;
let accountInsightsBusyV220 = false;
let lastAccountInsightsFetchV220 = 0;

function advZoneBodyV220(zone) {
  return document.querySelector(`[data-adv-zone="${CSS.escape(zone)}"] .advanced-zone-body`);
}
function advWorkspacePrefsV220() {
  try { return { left:340, right:360, density:"comfortable", showTopRail:true, ...JSON.parse(localStorage.getItem(ADV_WORKSPACE_PREFS_V220_KEY)||"{}") }; }
  catch { return { left:340, right:360, density:"comfortable", showTopRail:true }; }
}
function applyAdvWorkspacePrefsV220(prefs = advWorkspacePrefsV220()) {
  const view = $("#advancedDashboardView"); if (!view) return;
  const left = Math.max(220, Math.min(560, Number(prefs.left)||340));
  const right = Math.max(260, Math.min(640, Number(prefs.right)||360));
  view.style.setProperty("--adv-left", `${left}px`); view.style.setProperty("--adv-right", `${right}px`);
  view.dataset.density = ["comfortable","compact","dense"].includes(prefs.density) ? prefs.density : "comfortable";
  $("#advTelemetryRail")?.classList.toggle("hidden", prefs.showTopRail === false);
  if ($("#advLeftWidth")) $("#advLeftWidth").value = String(left);
  if ($("#advRightWidth")) $("#advRightWidth").value = String(right);
  if ($("#advDensity")) $("#advDensity").value = view.dataset.density;
  if ($("#advShowTopRail")) $("#advShowTopRail").checked = prefs.showTopRail !== false;
}
function saveAdvWorkspacePrefsV220() {
  const prefs = {
    left:Number($("#advLeftWidth")?.value||340), right:Number($("#advRightWidth")?.value||360),
    density:$("#advDensity")?.value||"comfortable", showTopRail:Boolean($("#advShowTopRail")?.checked)
  };
  localStorage.setItem(ADV_WORKSPACE_PREFS_V220_KEY, JSON.stringify(prefs)); applyAdvWorkspacePrefsV220(prefs); persistAdvancedLayout();
}

function currentAdvancedLayout() {
  const grid=$("#advancedWidgetGrid"); if(!grid) return {version:2,widgets:[],prefs:advWorkspacePrefsV220()};
  const widgets=[...grid.querySelectorAll(".advanced-widget")].map((w)=>({
    id:w.dataset.widgetId, zone:w.closest("[data-adv-zone]")?.dataset.advZone||"center",
    span:Number(w.dataset.span||1), height:w.dataset.height||"normal", hidden:w.classList.contains("widget-hidden")
  }));
  return {version:2,widgets,prefs:advWorkspacePrefsV220()};
}
function applyAdvancedLayout(layout) {
  const grid=$("#advancedWidgetGrid"); if(!grid||!layout) return;
  const widgets=Array.isArray(layout)?layout:(Array.isArray(layout.widgets)?layout.widgets:[]);
  widgets.forEach((item)=>{
    const w=grid.querySelector(`[data-widget-id="${CSS.escape(item.id)}"]`); if(!w)return;
    const zone=["top","left","center","right","bottom"].includes(item.zone)?item.zone:(w.closest("[data-adv-zone]")?.dataset.advZone||"center");
    w.dataset.span=String(Math.max(1,Math.min(4,Number(item.span)||1))); w.dataset.height=["compact","normal","tall"].includes(item.height)?item.height:"normal";
    w.classList.toggle("widget-hidden",Boolean(item.hidden)); advZoneBodyV220(zone)?.appendChild(w);
  });
  if(!Array.isArray(layout)&&layout.prefs) { localStorage.setItem(ADV_WORKSPACE_PREFS_V220_KEY,JSON.stringify(layout.prefs)); applyAdvWorkspacePrefsV220(layout.prefs); }
  renderAdvancedWidgetLibrary();
}
function persistAdvancedLayout(){localStorage.setItem(ADV_LAYOUT_V220_KEY,JSON.stringify(currentAdvancedLayout()));}
function loadAdvancedLayout(){try{const raw=JSON.parse(localStorage.getItem(ADV_LAYOUT_V220_KEY)||"null");if(raw)applyAdvancedLayout(raw);else applyAdvWorkspacePrefsV220();}catch(_){applyAdvWorkspacePrefsV220();}}
function advancedLayoutPresets(){try{const raw=JSON.parse(localStorage.getItem(ADV_LAYOUT_PRESETS_V220_KEY)||"{}");return raw&&typeof raw==="object"?raw:{};}catch{return{};}}
function renderAdvancedLayoutPresets(selected=""){const select=$("#advLayoutPreset");if(!select)return;const presets=advancedLayoutPresets();select.innerHTML='<option value="">Current layout</option>'+Object.keys(presets).sort().map((name)=>`<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");if(selected&&presets[selected])select.value=selected;}
function saveAdvancedLayoutPreset(){const name=prompt("Name this Advanced workspace layout:","Command Deck");if(!name)return;const key=name.trim();if(!key)return;const presets=advancedLayoutPresets();presets[key]=currentAdvancedLayout();localStorage.setItem(ADV_LAYOUT_PRESETS_V220_KEY,JSON.stringify(presets));renderAdvancedLayoutPresets(key);toast(`Layout “${key}” saved`,"success");}
function resetAdvancedLayout(){localStorage.removeItem(ADV_LAYOUT_V220_KEY);localStorage.removeItem(ADV_WORKSPACE_PREFS_V220_KEY);location.reload();}

function renderAdvancedWidgetLibrary(){const host=$("#advWidgetLibraryPanel"),grid=$("#advancedWidgetGrid");if(!host||!grid)return;host.innerHTML=[...grid.querySelectorAll(".advanced-widget")].map((w)=>{const zone=w.closest("[data-adv-zone]")?.dataset.advZone||"center";return `<button type="button" class="${w.classList.contains("widget-hidden")?"":"widget-visible"}" data-show-widget="${escapeHtml(w.dataset.widgetId)}">${w.classList.contains("widget-hidden")?"＋":"✓"} ${escapeHtml(w.querySelector("h3")?.textContent||w.dataset.widgetId)} <small>· ${escapeHtml(zone)}</small></button>`;}).join("");}
function decorateAdvancedWidgets(){
  $$("#advancedWidgetGrid .advanced-widget").forEach((w)=>{const controls=w.querySelector(".widget-controls");if(!controls)return;controls.dataset.ready="1";controls.innerHTML='<button type="button" data-widget-action="dock" title="Move to next dock">⧉</button><button type="button" data-widget-action="width" title="Cycle width">↔</button><button type="button" data-widget-action="height" title="Cycle height">↕</button><button type="button" data-widget-action="focus" title="Focus / fullscreen widget">◲</button><button type="button" data-widget-action="hide" title="Hide widget">×</button>';});
}
function setAdvancedLayoutEditing(enabled){advancedLayoutEditing=Boolean(enabled);const grid=$("#advancedWidgetGrid");grid?.classList.toggle("editing",advancedLayoutEditing);$$("#advancedWidgetGrid .advanced-widget").forEach((w)=>{w.draggable=advancedLayoutEditing;w.classList.toggle("layout-editing",advancedLayoutEditing);});$("#advLayoutToolbar")?.classList.toggle("hidden",!advancedLayoutEditing);if(advancedLayoutEditing){renderAdvancedWidgetLibrary();renderAdvancedLayoutPresets();applyAdvWorkspacePrefsV220();}}
function initAdvancedDragDrop(){
  const grid=$("#advancedWidgetGrid");if(!grid||grid.dataset.dragV220)return;grid.dataset.dragV220="1";let dragged=null;
  grid.addEventListener("dragstart",(e)=>{const w=e.target.closest(".advanced-widget");if(!advancedLayoutEditing||!w)return;dragged=w;e.dataTransfer.effectAllowed="move";});
  grid.addEventListener("dragover",(e)=>{if(!dragged)return;const zone=e.target.closest("[data-adv-zone]");if(!zone)return;e.preventDefault();$$("[data-adv-zone].drop-zone-active").forEach((x)=>x.classList.remove("drop-zone-active"));zone.classList.add("drop-zone-active");const target=e.target.closest(".advanced-widget");if(target&&target!==dragged)target.classList.add("drag-over");});
  grid.addEventListener("dragleave",(e)=>{e.target.closest(".advanced-widget")?.classList.remove("drag-over");});
  grid.addEventListener("drop",(e)=>{if(!dragged)return;e.preventDefault();const zone=e.target.closest("[data-adv-zone]");const body=zone?.querySelector(".advanced-zone-body");const target=e.target.closest(".advanced-widget");if(body){if(target&&target!==dragged){const rect=target.getBoundingClientRect();target.insertAdjacentElement(e.clientY>rect.top+rect.height/2?"afterend":"beforebegin",dragged);}else body.appendChild(dragged);persistAdvancedLayout();}$$('.advanced-widget.drag-over').forEach((x)=>x.classList.remove("drag-over"));$$("[data-adv-zone].drop-zone-active").forEach((x)=>x.classList.remove("drop-zone-active"));dragged=null;});
  grid.addEventListener("dragend",()=>{$$('.advanced-widget.drag-over').forEach((x)=>x.classList.remove("drag-over"));$$("[data-adv-zone].drop-zone-active").forEach((x)=>x.classList.remove("drop-zone-active"));dragged=null;});
}
function cycleAdvancedWidgetDockV220(w){const zones=["top","left","center","right","bottom"];const cur=w.closest("[data-adv-zone]")?.dataset.advZone||"center";const next=zones[(zones.indexOf(cur)+1)%zones.length];advZoneBodyV220(next)?.appendChild(w);persistAdvancedLayout();renderAdvancedWidgetLibrary();}
function closeAdvancedFocusV220(){document.querySelectorAll(".advanced-widget.widget-focus").forEach((w)=>w.classList.remove("widget-focus"));document.body.classList.remove("advanced-widget-focused");}

function setAdvancedDeckTabV220(name){const valid=["control","protection","strategy","sizing"];name=valid.includes(name)?name:"control";$$("[data-adv-deck-tab]").forEach((b)=>b.classList.toggle("active",b.dataset.advDeckTab===name));$$("[data-adv-deck-panel]").forEach((p)=>p.classList.toggle("active",p.dataset.advDeckPanel===name));localStorage.setItem(ADV_DECK_TAB_V220_KEY,name);}
function renderAdvancedCandleV220(status={}){
  const d=status?.dashboard||{};const t=status?.telemetry||{};const has=d.candle_status_line||d.candle_ohlc_line||d.candle_live_line||d.candle_live_open!==undefined;
  const live=d.candle_live_open!==undefined;const body=advancedNumber(live?d.candle_live_body_pct:d.candle_body_pct,0);const upper=advancedNumber(live?d.candle_live_upper_pct:d.candle_upper_pct,0);const lower=advancedNumber(live?d.candle_live_lower_pct:d.candle_lower_pct,0);
  const set=(id,v)=>{const el=$(id);if(el)el.textContent=v;};
  set("#advCandleProgress",has?(d.candle_progress_label||(d.candle_mode==="time"?`${d.candle_elapsed_seconds??"—"} / ${d.candle_seconds??"—"} sec`:`${d.candle_progress??"—"} / ${d.candle_ticks??"—"} ticks`)):"Waiting for candle data");
  set("#advCandleDetected",d.candle_detected||"NONE");set("#advCandleCandidates",d.candle_live_candidates||"NONE");set("#advCandleScore",d.candle_pattern_score!==undefined?advancedNumber(d.candle_pattern_score,0).toFixed(2):"—");set("#advCandleConfirm",d.candle_confirmation||"—");set("#advCandlePending",`${d.candle_pending_pattern||"NONE"}${d.candle_pending_direction&&d.candle_pending_direction!=="NONE"?` · ${d.candle_pending_direction}`:""}`);
  set("#advCandleOhlc",live?`O ${d.candle_live_open} · H ${d.candle_live_high} · L ${d.candle_live_low} · C ${d.candle_live_close}`:d.candle_open!==undefined?`O ${d.candle_open} · H ${d.candle_high} · L ${d.candle_low} · C ${d.candle_close}`:"—");
  set("#advCandleBodyPct",has?`${body.toFixed(1)}%`:"—");set("#advCandleUpperPct",has?`${upper.toFixed(1)}%`:"—");set("#advCandleLowerPct",has?`${lower.toFixed(1)}%`:"—");
  [["#advCandleBodyBar",body],["#advCandleUpperBar",upper],["#advCandleLowerBar",lower]].forEach(([id,v])=>{const el=$(id);if(el)el.style.width=`${Math.max(0,Math.min(100,v))}%`;});
  const open=advancedNumber(live?d.candle_live_open:d.candle_open,NaN),close=advancedNumber(live?d.candle_live_close:d.candle_close,NaN);const dir=Number.isFinite(open)&&Number.isFinite(close)?(close>open?"BULLISH":close<open?"BEARISH":"DOJI"):"NEUTRAL";set("#advCandleDirection",dir);set("#advCandleVisualPrice",t.quote??d.quote??"—");
  const visual=$("#advCandleVisual");if(visual){visual.className=`advanced-live-candle ${dir==="BULLISH"?"bull":dir==="BEARISH"?"bear":"neutral"}`;const total=Math.max(1,upper+body+lower);visual.style.setProperty("--upper",`${upper/total*100}%`);visual.style.setProperty("--body",`${body/total*100}%`);visual.style.setProperty("--lower",`${lower/total*100}%`);}
}
function renderAdvancedTopRailV220(status={}){const t=status?.telemetry||{},d=status?.dashboard||{},s=t.settings||{};const running=Boolean(status?.running);const rec=t.reconnect||{};const api=running?(rec.active?"RECONNECTING":"ONLINE"):(d.account?"READY":"OFFLINE");const set=(id,v)=>{const e=$(id);if(e)e.textContent=v;};set("#advTopApi",api);set("#advTopBot",running?(t.stop_requested?"DRAINING":t.paused?"PAUSED":"RUNNING"):"STOPPED");set("#advTopAccount",t.account||d.account||"—");set("#advTopMode",String(t.mode||d.mode||"—").toUpperCase());set("#advTopSymbol",t.symbol_display_name||t.symbol||d.symbol||"—");set("#advTopQuote",t.quote??d.quote??"—");set("#advTopOpen",`${(t.positions||[]).length||d.open_trades||0} / ${s.max_open_trades??d.max_open??0}`);let age="—";if(t.timestamp){const ms=Date.now()-Date.parse(t.timestamp);if(Number.isFinite(ms))age=ms<1000?"LIVE":`${(ms/1000).toFixed(1)}s ago`;}set("#advTopAge",age);const dot=$("#advTopApiDot");if(dot)dot.className=`status-dot ${api==="ONLINE"||api==="READY"?"online":api==="RECONNECTING"?"warn":""}`;}
function renderAdvancedExtrasV220(status={},trades={}){renderAdvancedTopRailV220(status);renderAdvancedCandleV220(status);const t=status?.telemetry||{},d=status?.dashboard||{},settings=t.settings||{};const positions=Array.isArray(t.positions)?t.positions:[];const capital=advancedNumber(settings.capital??d.capital,0);const exposure=positions.reduce((sum,p)=>sum+advancedNumber(p.stake,0),0);const heat=capital>0?Math.min(999,exposure/capital*100):0;if($("#advAllocationHeat"))$("#advAllocationHeat").textContent=`${heat.toFixed(1)}%`;if($("#advAllocationHeatBar"))$("#advAllocationHeatBar").style.width=`${Math.min(100,heat)}%`;const advConsole=$("#advConsoleOutput"),simpleConsole=$("#consoleOutput");if(advConsole&&simpleConsole&&document.activeElement!==advConsole){advConsole.textContent=simpleConsole.textContent;advConsole.scrollTop=advConsole.scrollHeight;}const view=$("#advancedDashboardView");if(view&&!view.classList.contains("hidden")&&Date.now()-lastAccountInsightsFetchV220>300000&&!accountInsightsBusyV220)refreshAccountInsightsV220(false);}
function renderAdvancedOps(status={},trades={}){renderAdvancedOpsV219Base(status,trades);renderAdvancedExtrasV220(status,trades);}
function appendLogs(lines){appendLogsV219Base(lines);if(!lines.length)return;const terminal=$("#advConsoleOutput");if(!terminal)return;const atBottom=terminal.scrollTop+terminal.clientHeight>=terminal.scrollHeight-40;const text=lines.map((line)=>line.text).join("\n");terminal.textContent+=(terminal.textContent?"\n":"")+text;if(terminal.textContent.length>220000)terminal.textContent=terminal.textContent.slice(-180000);if(atBottom)terminal.scrollTop=terminal.scrollHeight;}

function renderAccountInsightsV220(data={}){latestAccountInsightsV220=data;const set=(id,v)=>{const e=$(id);if(e)e.textContent=v;};const curr=data.currency||"USD";set("#advApiBalance",data.balance==null?"—":`${formatNumber(data.balance)} ${curr}`);set("#advApiPortfolioCount",String(data.portfolio_count??"—"));set("#advApiStatementCount",String(data.statement_count??"—"));set("#advApiProfitCount",String(data.profit_count??"—"));set("#advApiDeposits",data.deposit_total==null?"—":`${formatNumber(data.deposit_total)} ${curr}`);set("#advApiWithdrawals",data.withdrawal_total==null?"—":`${formatNumber(data.withdrawal_total)} ${curr}`);set("#advApiLastActivity",data.last_activity||"No activity returned");const state=$("#advAccountApiState");if(state){const errs=Object.entries(data.errors||{});state.textContent=errs.length?`Partial API snapshot · ${errs.map(([k,v])=>`${k}: ${v}`).join(" · ")}`:`Account API refreshed ${data.fetched_at||"now"}`;state.classList.toggle("queued",errs.length>0);}const host=$("#advAccountActivity");if(host){const rows=Array.isArray(data.activity)?data.activity:[];host.innerHTML=rows.length?rows.map((r)=>{const amount=advancedNumber(r.amount,0),cls=amount>0?"positive":amount<0?"negative":"";return `<div class="advanced-activity-row"><div><strong>${escapeHtml(String(r.action_type||r.action||r.description||"activity").replaceAll("_"," "))}</strong><small>${escapeHtml(r.time||r.transaction_time||r.purchase_time||"")}</small></div><div class="activity-amount ${cls}"><strong>${formatNumber(amount)}</strong><small>${escapeHtml(r.balance_after!==undefined?`Bal ${r.balance_after}`:(r.description||""))}</small></div></div>`;}).join(""):'<div class="empty">No statement activity returned</div>';}}
async function refreshAccountInsightsV220(showToast=true){if(accountInsightsBusyV220)return;accountInsightsBusyV220=true;const button=$("#advRefreshAccountApi");if(button){button.disabled=true;button.textContent="Refreshing…";}try{const result=await api("/api/account-insights",{method:"POST",body:"{}"});lastAccountInsightsFetchV220=Date.now();renderAccountInsightsV220(result.insights||{});if(showToast)toast("Account API snapshot refreshed","success");}catch(error){const state=$("#advAccountApiState");if(state){state.textContent=`Account API: ${error.message}`;state.classList.add("queued");}if(showToast)toast(error.message,"error");}finally{accountInsightsBusyV220=false;if(button){button.disabled=false;button.textContent="Refresh account API";}}}
function openDerivFundingV220(){window.open("https://home.deriv.com/","_blank","noopener,noreferrer");toast("Opened Deriv in your browser. Use Wallet → Deposit to add funds.","success");}

function initAdvancedOps(){
  initAdvancedOpsV219Base(); applyAdvWorkspacePrefsV220(); setAdvancedDeckTabV220(localStorage.getItem(ADV_DECK_TAB_V220_KEY)||"control");
  $$("[data-adv-deck-tab]").forEach((b)=>b.addEventListener("click",()=>setAdvancedDeckTabV220(b.dataset.advDeckTab)));
  ["#advLeftWidth","#advRightWidth","#advDensity","#advShowTopRail"].forEach((sel)=>$(sel)?.addEventListener("change",saveAdvWorkspacePrefsV220));
  $("#advRefreshAccountApi")?.addEventListener("click",()=>refreshAccountInsightsV220(true));$("#advOpenFunding")?.addEventListener("click",openDerivFundingV220);
  $("#advancedWidgetGrid")?.addEventListener("click",(e)=>{const b=e.target.closest("[data-widget-action]");if(!b)return;const w=b.closest(".advanced-widget");if(!w)return;const action=b.dataset.widgetAction;if(action==="dock"&&advancedLayoutEditing)cycleAdvancedWidgetDockV220(w);if(action==="focus"){w.classList.toggle("widget-focus");document.body.classList.toggle("advanced-widget-focused",w.classList.contains("widget-focus"));}});
  document.addEventListener("keydown",(e)=>{if(e.key==="Escape")closeAdvancedFocusV220();});
  renderAdvancedExtrasV220(latestAdvancedStatus,latestAdvancedTrades);
}


// ================================================================
// V2.22 — Dedicated Rise/Fall Step 100 presets + optimization exports
// ================================================================
function rfPercent(value) { const n=Number(value); return Number.isFinite(n)?`${(n*100).toFixed(1)}%`:'—'; }
function rfReturn(value) { const n=Number(value); return Number.isFinite(n)?`${n.toFixed(1)}%`:'—'; }
function rfSetConfigTab(name){
  $$('[data-rf-config-tab]').forEach(b=>b.classList.toggle('active',b.dataset.rfConfigTab===name));
  $$('[data-rf-config-page]').forEach(p=>p.classList.toggle('active',p.dataset.rfConfigPage===name));
}
function drawRiseFallChart(prices=[]){
  const canvas=$('#rfChart'), empty=$('#rfChartEmpty'); if(!canvas)return;
  const data=(prices||[]).map(Number).filter(Number.isFinite); empty?.classList.toggle('hidden',data.length>1);
  const rect=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1; canvas.width=Math.max(1,Math.floor(rect.width*dpr));canvas.height=Math.max(1,Math.floor(rect.height*dpr));
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);if(data.length<2)return;
  const lo=Math.min(...data),hi=Math.max(...data),span=Math.max(hi-lo,1e-9),pad=12;
  ctx.lineWidth=1;ctx.globalAlpha=.18;ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted')||'#789';
  for(let i=1;i<4;i++){const y=pad+(rect.height-pad*2)*i/4;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(rect.width-pad,y);ctx.stroke();}
  ctx.globalAlpha=1;ctx.lineWidth=2;ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--accent')||'#49e6aa';ctx.beginPath();
  data.forEach((v,i)=>{const x=pad+(rect.width-pad*2)*i/(data.length-1),y=rect.height-pad-(v-lo)/span*(rect.height-pad*2);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
}
function renderRiseFall(status={}){
  riseFallLastStatus=status; const t=status.telemetry||{}, st=t.stats||{}, m=t.metrics||{}, odds=t.odds||{}, dec=t.decision||{}, settings=t.settings||status.settings||{}, history=status.history||{rows:[],summary:{}};
  const running=!!status.running, paused=!!t.entries_paused, stopping=!!status.stop_requested;
  if(document.body.dataset.engineUi==='rise-fall'){syncActiveEngineTopbar();syncMobileDeploy({running,stop_requested:stopping});}
  const badge=$('#rfEngineBadge'); if(badge){badge.textContent=stopping?'Draining':running?(paused?'Paused':'Running'):'Stopped';badge.className=`badge ${running&&!paused?'running':stopping?'warning':'neutral'}`;}
  $('#rfStartButton').disabled=running; $('#rfDeployInsideButton').disabled=running; $('#rfStopButton').disabled=!running||stopping; $('#rfPauseButton').disabled=!running||paused||stopping; $('#rfResumeButton').disabled=!running||!paused||stopping; $('#rfApplyLiveButton').disabled=!running||stopping;
  $('#rfApiState').textContent=running?'Connected / running':status.last_error?'Error':'Idle'; $('#rfMarket').textContent=t.symbol_name||t.symbol||'Step 100 Index'; $('#rfQuote').textContent=t.quote??'—';
  $('#rfBalance').textContent=t.account_balance!==undefined?`${formatNumber(t.account_balance)} ${t.currency||''}`:'—';$('#rfStake').textContent=t.stake!==undefined?`${formatNumber(t.stake)} ${t.currency||''}`:'—';$('#rfOpen').textContent=t.open??0;
  const profit=Number(st.realized_profit||0);$('#rfProfit').textContent=formatNumber(profit);$('#rfProfit').className=profit>0?'positive':profit<0?'negative':'';$('#rfRecord').textContent=`${st.wins||0} / ${st.losses||0}`;$('#rfCooldown').textContent=t.cooldown??0;
  $('#rfLiveMode').textContent=running?`${String(settings.decision_mode||'').replaceAll('_',' ')} · ${paused?'PAUSED':'ACTIVE'}`:'Stopped';$('#rfRevision').textContent=t.control_revision??status.live_control_revision??0;$('#rfContractSupport').textContent=(t.supported_contracts||[]).length?(t.supported_contracts||[]).join(' / '):'Not checked';
  $('#rfRiseContractCode').textContent=t.contract_mapping?.RISE||'CALL';$('#rfFallContractCode').textContent=t.contract_mapping?.FALL||'PUT';
  const rise=odds.RISE||{},fall=odds.FALL||{};$('#rfRiseConfidence').textContent=rfPercent(m.up_confidence);$('#rfFallConfidence').textContent=rfPercent(m.down_confidence);$('#rfRiseReturn').textContent=rfReturn(rise.return_pct);$('#rfFallReturn').textContent=rfReturn(fall.return_pct);$('#rfRisePayout').textContent=rise.payout!==undefined?formatNumber(rise.payout):'—';$('#rfFallPayout').textContent=fall.payout!==undefined?formatNumber(fall.payout):'—';$('#rfRiseBreakEven').textContent=rfReturn(rise.break_even_pct);$('#rfFallBreakEven').textContent=rfReturn(fall.break_even_pct);$('#rfRiseScore').textContent=rise.combined_score!==undefined?Number(rise.combined_score).toFixed(3):'—';$('#rfFallScore').textContent=fall.combined_score!==undefined?Number(fall.combined_score).toFixed(3):'—';
  $('#rfRsi').textContent=m.rsi!==undefined?Number(m.rsi).toFixed(1):'—';$('#rfMomentumUp').textContent=rfPercent(m.momentum_up);$('#rfMomentumDown').textContent=rfPercent(m.momentum_down);$('#rfRangePosition').textContent=rfPercent(m.range_position);$('#rfModelEdge').textContent=rfPercent(m.edge);$('#rfVolatility').textContent=m.volatility_step!==undefined?Number(m.volatility_step).toFixed(4):'—';
  let direction=String(dec.direction||m.decision||'WAIT').toUpperCase(), confidence=direction==='RISE'?Number(m.up_confidence||0):direction==='FALL'?Number(m.down_confidence||0):0; const action=String(dec.action||'WAIT').toUpperCase();
  $('#rfBestDirection').textContent=direction;$('#rfBestDirection').className=direction==='RISE'?'positive':direction==='FALL'?'negative':'';$('#rfDecisionBadge').textContent=`${action} · ${direction}`;$('#rfDecisionBadge').className=`micro-badge ${action==='TRADE'?'running':action==='SIGNAL'?'running':'neutral'}`;
  $('#rfBestReason').textContent=dec.reason||((action==='TRADE'||action==='SIGNAL')?`${action}: ${direction} · return ${rfReturn(dec.return_pct)} · edge ${rfPercent(dec.edge)}`:'Waiting for confidence, edge and live return gates.');$('#rfConfidenceBar').style.width=`${Math.max(0,Math.min(100,confidence*100))}%`;$('#rfConfidenceText').textContent=`${(confidence*100).toFixed(1)}% confidence`;
  const positions=t.positions||[];$('#rfPositionCount').textContent=`${positions.length} active`;$('#rfPositionsBody').innerHTML=positions.length?positions.map(p=>`<tr><td>${escapeHtml(p.contract_id)}</td><td><strong class="${p.direction==='RISE'?'positive':'negative'}">${escapeHtml(p.direction)}</strong></td><td>${formatNumber(p.stake)}</td><td>${formatNumber(p.payout)}</td><td>${p.entry_spot??'—'}</td><td>${p.duration}${escapeHtml(p.duration_unit||'')}</td><td class="${Number(p.current_profit)>0?'positive':Number(p.current_profit)<0?'negative':''}">${formatNumber(p.current_profit)}</td><td>${escapeHtml(p.status||'open')}</td></tr>`).join(''):'<tr><td colspan="8" class="empty">No Rise/Fall positions</td></tr>';
  const results=(history.rows||[]).length?[...(history.rows||[])]:[...(t.recent_results||[])].reverse();$('#rfResultsBody').innerHTML=results.length?results.map(r=>`<tr><td>${escapeHtml(r.closed_utc||'')}</td><td class="${r.direction==='RISE'?'positive':'negative'}">${escapeHtml(r.direction||'')}</td><td class="${r.result==='WIN'?'positive':'negative'}">${escapeHtml(r.result||'')}</td><td>${escapeHtml(r.stake||'')}</td><td class="${Number(r.profit)>0?'positive':Number(r.profit)<0?'negative':''}">${escapeHtml(r.profit||'')}</td><td>${escapeHtml(String(r.duration||''))}${escapeHtml(r.duration_unit||'')}</td><td>${escapeHtml(r.mode||'')}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">No completed Rise/Fall trades</td></tr>';
  const hs=history.summary||{};$('#rfHistoryTrades').textContent=`${hs.trades||0} trades`;$('#rfHistoryWinRate').textContent=`${hs.win_rate||'0.0'}% wins`;const hp=Number(hs.profit||0);$('#rfHistoryProfit').textContent=formatNumber(hp);$('#rfHistoryProfit').className=hp>0?'positive':hp<0?'negative':'';
  drawRiseFallChart(t.recent_prices||[]);
}
async function pollRiseFall(){
  try{const [s,l]=await Promise.all([api('/api/rise-fall/status'),api(`/api/rise-fall/logs?after=${riseFallLogId}`)]);renderRiseFall(s.status||{});if(l.lines?.length){const term=$('#rfConsole'),atBottom=term.scrollTop+term.clientHeight>=term.scrollHeight-40;term.textContent+=(term.textContent?'\n':'')+l.lines.map(x=>x.text).join('\n');if(term.textContent.length>120000)term.textContent=term.textContent.slice(-100000);if(atBottom)term.scrollTop=term.scrollHeight;}riseFallLogId=l.last_id||riseFallLogId;}catch(_){ }
}
function renderRiseFallPresets(presets=[], selectedName=""){
  savedRiseFallPresets=Array.isArray(presets)?presets:[];const select=$('#rfPresetSelect');if(!select)return;const previous=selectedName||select.value;
  select.innerHTML='<option value="">Choose a saved setup…</option>'+savedRiseFallPresets.map((p)=>{const detail=[p.duration?`${p.duration}${p.duration_unit||''}`:'',p.decision_mode].filter(Boolean).join(' · ');return `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}${detail?` — ${escapeHtml(detail)}`:''}</option>`;}).join('');
  if(savedRiseFallPresets.some((p)=>p.name===previous))select.value=previous;$('#rfPresetCount').textContent=`${savedRiseFallPresets.length} saved`;$('#rfPresetDownload').disabled=!select.value;$('#rfPresetDelete').disabled=!select.value;const labSelect=$('#rfLabPresetSelect');if(labSelect){const lp=labSelect.value;labSelect.innerHTML='<option value="">Saved Rise/Fall preset…</option>'+savedRiseFallPresets.map((p)=>`<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`).join('');if(savedRiseFallPresets.some((p)=>p.name===lp))labSelect.value=lp;}
}
function applyRiseFallPresetSession(session={}){setFormValues($('#riseFallForm'),session||{});const live=$('#riseFallForm')?.elements?.namedItem('live_confirmation');if(live)live.value='';}
async function saveRiseFallPreset(){const selected=$('#rfPresetSelect')?.value||'';const name=String($('#rfPresetName')?.value||selected).trim();if(!name){toast('Enter a Rise/Fall preset name','error');$('#rfPresetName')?.focus();return;}if(savedRiseFallPresets.some((p)=>p.name===name)&&!confirm(`Overwrite Rise/Fall preset “${name}”?`))return;try{const settings=formToObject($('#riseFallForm'));settings.live_confirmation='';const r=await api('/api/rise-fall/presets/save',{method:'POST',body:JSON.stringify({name,settings})});renderRiseFallPresets(r.presets||[],name);$('#rfPresetName').value=name;toast(`Rise/Fall preset “${name}” saved`,'success');}catch(e){toast(e.message,'error');}}
async function loadRiseFallPreset(name=''){name=String(name||$('#rfPresetSelect')?.value||'').trim();if(!name)return;try{const r=await api('/api/rise-fall/presets/apply',{method:'POST',body:JSON.stringify({name})});applyRiseFallPresetSession(r.session||{});renderRiseFallPresets(r.presets||[],name);$('#rfPresetName').value=name;toast(`Rise/Fall preset “${name}” loaded into the form — use Apply configuration live if the engine is running`,'success');}catch(e){toast(e.message,'error');}}
async function deleteRiseFallPreset(){const name=String($('#rfPresetSelect')?.value||'').trim();if(!name)return;if(!confirm(`Delete Rise/Fall preset “${name}”?`))return;try{const r=await api('/api/rise-fall/presets/delete',{method:'POST',body:JSON.stringify({name})});renderRiseFallPresets(r.presets||[]);if($('#rfPresetName').value===name)$('#rfPresetName').value='';toast(`Rise/Fall preset “${name}” deleted`,'success');}catch(e){toast(e.message,'error');}}
function safeRiseFallPresetFilename(name){const clean=String(name||'Rise Fall Step100').replace(/[<>:"/\\|?*\x00-\x1F]/g,'-').replace(/\s+/g,' ').trim().slice(0,80);return `${clean||'Rise Fall Step100'}.json`;}
async function downloadRiseFallPreset(){const name=String($('#rfPresetSelect')?.value||'').trim();if(!name){toast('Choose a saved Rise/Fall setup first','error');return;}try{const r=await api(`/api/rise-fall/presets/export?name=${encodeURIComponent(name)}`);downloadTextFile(safeRiseFallPresetFilename(name),JSON.stringify(r.export,null,2)+'\n','application/json');toast(`Rise/Fall preset “${name}” downloaded`,'success');}catch(e){toast(e.message,'error');}}
async function importRiseFallPresetFile(file){if(!file)return;try{let payload;try{payload=JSON.parse(await file.text());}catch{throw new Error('That Rise/Fall preset file is not valid JSON');}const name=String(payload?.name||'').trim();if(!name)throw new Error('Preset file does not contain a name');const exists=savedRiseFallPresets.some((p)=>p.name===name);let overwrite=false;if(exists){overwrite=confirm(`Rise/Fall preset “${name}” already exists. Overwrite it?`);if(!overwrite)return;}const r=await api('/api/rise-fall/presets/import',{method:'POST',body:JSON.stringify({preset_file:payload,overwrite})});applyRiseFallPresetSession(r.session||{});renderRiseFallPresets(r.presets||[],r.name||name);$('#rfPresetName').value=r.name||name;toast(`Rise/Fall preset “${r.name||name}” imported and loaded`,'success');}catch(e){toast(e.message,'error');}}
async function downloadRiseFallOptimization(format='md'){try{const r=await api(`/api/rise-fall/optimization?format=${encodeURIComponent(format)}`);const report=r.report||{};if(!report.content)throw new Error('Rise/Fall optimization report is empty');downloadTextFile(report.filename||`RISE_FALL_STEP100_OPTIMIZATION.${format}`,report.content,format==='json'?'application/json':'text/markdown');toast(`Rise/Fall optimization ${format.toUpperCase()} downloaded`,'success');}catch(e){toast(e.message,'error');}}


function rfLabNum(selector, fallback){const el=$(selector);const n=Number(el?.value);return Number.isFinite(n)?n:fallback;}
function rfLabSeedDefaults(seed={}){return {label:seed.label||`RF Test ${rfLabCounter+1}`,stake:seed.stake??"1.00",max_open:seed.max_open??1,trades_per_signal:seed.trades_per_signal??1,split_delay:seed.split_delay??0,duration:seed.duration??2,duration_unit:seed.duration_unit||"t",decision_mode:seed.decision_mode||"best_odds",direction_policy:seed.direction_policy||"auto",min_confidence:seed.min_confidence??0.66,min_edge:seed.min_edge??0.30,min_return_pct:seed.min_return_pct??0,cooldown_ticks:seed.cooldown_ticks??4,quote_refresh_ticks:seed.quote_refresh_ticks??2,lookback:seed.lookback??20,trend_weight:seed.trend_weight??0.45,momentum_weight:seed.momentum_weight??2.0,reversal_weight:seed.reversal_weight??0.05,breakout_weight:seed.breakout_weight??0.30};}
function addRfLabTester(seed={}){
  if($$('.rf-lab-tester').length>=4){toast('Rise/Fall Lab supports up to 4 simultaneous testers','error');return;}
  rfLabCounter+=1;const v=rfLabSeedDefaults(seed);const id=seed.id||`rf-test-${rfLabCounter}`;const card=document.createElement('article');card.className='card lab-bot-card rf-lab-tester';card.dataset.rfLabId=id;
  const field=(name,label,type='number',extra='')=>`<label class="field"><span>${label}</span><input data-rf-lab-field="${name}" type="${type}" ${extra} value="${escapeHtml(String(v[name]??''))}"></label>`;
  card.innerHTML=`<div class="lab-bot-head"><div><div class="eyebrow">RISE/FALL TESTER</div><input class="lab-label-input" data-rf-lab-field="label" value="${escapeHtml(v.label)}" maxlength="60"></div><button class="button danger small rf-lab-remove" type="button">Remove</button></div>
  <div class="form-grid compact">${field('stake','Stake / trade','number','min="0.35" step="0.01"')}${field('duration','Duration','number','min="1" step="1"')}<label class="field"><span>Unit</span><select data-rf-lab-field="duration_unit"><option value="t">Ticks</option><option value="s">Seconds</option><option value="m">Minutes</option></select></label><label class="field"><span>Decision</span><select data-rf-lab-field="decision_mode"><option value="momentum">Momentum</option><option value="consensus">Consensus</option><option value="trend">Trend</option><option value="reversal">Reversal</option><option value="best_odds">Best Odds</option></select></label><label class="field"><span>Direction</span><select data-rf-lab-field="direction_policy"><option value="auto">Auto</option><option value="rise_only">RISE only</option><option value="fall_only">FALL only</option></select></label>${field('min_confidence','Min confidence','number','min="0.5" max="0.99" step="0.01"')}${field('min_edge','Min edge','number','min="0" max="0.95" step="0.01"')}${field('lookback','Lookback','number','min="6" max="500" step="1"')}${field('cooldown_ticks','Cooldown ticks','number','min="0" step="1"')}${field('quote_refresh_ticks','Quote refresh','number','min="1" step="1"')}${field('trend_weight','Trend weight','number','min="0" step="0.05"')}${field('momentum_weight','Momentum weight','number','min="0" step="0.05"')}${field('reversal_weight','Reversal weight','number','min="0" step="0.05"')}${field('breakout_weight','Breakout weight','number','min="0" step="0.05"')}</div><div class="lab-bot-foot"><span>Demo · fixed stake · no compounding</span><span>${escapeHtml(String(v.duration))}${escapeHtml(v.duration_unit)} · ${escapeHtml(v.decision_mode)} · ${escapeHtml(v.direction_policy)}</span></div>`;
  $('#rfLabBuilder').appendChild(card);['duration_unit','decision_mode','direction_policy'].forEach((k)=>{const e=card.querySelector(`[data-rf-lab-field="${k}"]`);if(e)e.value=String(v[k]);});
}
function gatherRfLabBots(){return $$('.rf-lab-tester').map((card)=>{const val=(n)=>card.querySelector(`[data-rf-lab-field="${n}"]`)?.value;return {id:card.dataset.rfLabId,label:val('label')||card.dataset.rfLabId,stake:val('stake'),max_open:1,trades_per_signal:1,split_delay:0,duration:val('duration'),duration_unit:val('duration_unit'),decision_mode:val('decision_mode'),direction_policy:val('direction_policy'),min_confidence:val('min_confidence'),min_edge:val('min_edge'),min_return_pct:0,cooldown_ticks:val('cooldown_ticks'),quote_refresh_ticks:val('quote_refresh_ticks'),lookback:val('lookback'),trend_weight:val('trend_weight'),momentum_weight:val('momentum_weight'),reversal_weight:val('reversal_weight'),breakout_weight:val('breakout_weight')};});}
function resetRfLabBuilder(){if(rfLabLastStatus?.running){toast('Stop the current Rise/Fall Lab run first','error');return false;}$('#rfLabBuilder').innerHTML='';rfLabCounter=0;return true;}
function buildRfLabMatrix(kind='core'){
  if(!resetRfLabBuilder())return;
  if(kind==='modes'){
    [{mode:'best_odds',label:'2T Best Odds'},{mode:'consensus',label:'2T Consensus'},{mode:'momentum',label:'2T Momentum'},{mode:'reversal',label:'2T Reversal'}].forEach((x)=>addRfLabTester({label:x.label,duration:2,decision_mode:x.mode,lookback:20,cooldown_ticks:4,min_confidence:.66,min_edge:.30,quote_refresh_ticks:2,trend_weight:.45,momentum_weight:2.0,reversal_weight:.05,breakout_weight:.30}));
  }else{
    addRfLabTester({label:'2T BO Base',duration:2,decision_mode:'best_odds',lookback:20,cooldown_ticks:3,min_confidence:.64,min_edge:.28});
    addRfLabTester({label:'2T BO Selective',duration:2,decision_mode:'best_odds',lookback:20,cooldown_ticks:5,min_confidence:.68,min_edge:.32});
    addRfLabTester({label:'2T BO Fast',duration:2,decision_mode:'best_odds',lookback:14,cooldown_ticks:2,min_confidence:.66,min_edge:.30});
    addRfLabTester({label:'2T BO Stable',duration:2,decision_mode:'best_odds',lookback:26,cooldown_ticks:6,min_confidence:.69,min_edge:.34});
  }
}
async function addSavedPresetToRfLab(){const name=$('#rfLabPresetSelect')?.value||'';if(!name){toast('Choose a saved Rise/Fall preset','error');return;}try{const r=await api(`/api/rise-fall/presets/export?name=${encodeURIComponent(name)}`);const st=r.export?.preset?.settings||{};addRfLabTester({...st,label:name,stake:st.stake_per_trade||((Number(st.capital||1)/Math.max(1,Number(st.split||1))).toFixed(2)),max_open:1,trades_per_signal:1});}catch(e){toast(e.message,'error');}}
async function startRfLab(){try{const bots=gatherRfLabBots();if(!bots.length)throw new Error('Add at least one Rise/Fall Lab tester');const r=await api('/api/rise-fall/lab/start',{method:'POST',body:JSON.stringify({bots})});renderRfLab(r.lab||{});toast(`Rise/Fall Lab started ${bots.length} isolated demo tester${bots.length===1?'':'s'}`,'success');}catch(e){toast(e.message,'error');}}
async function startRfAutoLab(){try{const payload={stake:$('#rfAutoStake')?.value||'1.00',max_rounds:Number($('#rfAutoRounds')?.value||4),screen_trades:Number($('#rfAutoScreenTrades')?.value||30),refine_trades:Number($('#rfAutoRefineTrades')?.value||50),validation_trades:Number($('#rfAutoValidationTrades')?.value||100),max_minutes_per_round:Number($('#rfAutoMaxMinutes')?.value||30)};const r=await api('/api/rise-fall/lab/auto-start',{method:'POST',body:JSON.stringify(payload)});renderRfLab(r.lab||{});toast('2T Auto Lab started · Explore → Refine → Validate','success');}catch(e){toast(e.message,'error');}}
async function stopRfAutoLab(){try{const r=await api('/api/rise-fall/lab/auto-stop',{method:'POST',body:'{}'});renderRfLab(r.lab||{});toast('Auto Lab stopping; open 2T contracts will drain','success');}catch(e){toast(e.message,'error');}}
async function downloadRfAutoBestPreset(){try{const r=await api('/api/rise-fall/lab/auto-best-preset');const x=r.preset||{};if(!x.content)throw new Error('No Auto Lab preset is ready yet');downloadTextFile(x.filename||'RF_AUTO_LAB_BEST_2T.json',x.content,'application/json');toast('Best Auto Lab 2T preset downloaded','success');}catch(e){toast(e.message,'error');}}
async function stopRfLab(botId=''){try{const r=await api('/api/rise-fall/lab/stop',{method:'POST',body:JSON.stringify({bot_id:botId})});renderRfLab(r.lab||{});toast(botId?'Tester draining; no new Rise/Fall entries':'All Rise/Fall Lab testers are draining','success');}catch(e){toast(e.message,'error');}}
async function downloadRfLabReport(botId,format='md'){try{const r=await api(`/api/rise-fall/lab/report?bot_id=${encodeURIComponent(botId)}&format=${encodeURIComponent(format)}`);const x=r.report||{};downloadTextFile(x.filename||`RF_LAB_${botId}.${format}`,x.content||'',format==='json'?'application/json':'text/markdown');}catch(e){toast(e.message,'error');}}
async function downloadRfLabSummary(format='md'){try{const r=await api(`/api/rise-fall/lab/run-summary?format=${encodeURIComponent(format)}`);const x=r.report||{};downloadTextFile(x.filename||`RISE_FALL_LAB_SUMMARY.${format}`,x.content||'',format==='json'?'application/json':'text/markdown');}catch(e){toast(e.message,'error');}}
function renderRfLab(lab={}){
  rfLabLastStatus=lab||{};const bots=[...(lab.bots||[])],auto=lab.auto||{},autoRunning=!!auto.running;const active=bots.filter(b=>b.running).length;if(document.body.dataset.engineUi==='rise-fall')syncActiveEngineTopbar();const badge=$('#rfLabRunBadge');if(badge){badge.textContent=autoRunning?`AUTO ${auto.stage||'RUNNING'} · R${auto.round||0}/${auto.max_rounds||0}`:active?`${active} running`:(bots.length?'Run complete':'Lab idle');badge.className=`badge ${(autoRunning||active)?'running':'neutral'}`;}
  $('#rfLabActive').textContent=`${active} active · ${bots.length} tested`;const combined=bots.reduce((a,b)=>a+toNumber(b.profit,0),0);$('#rfLabCombined').textContent=formatNumber(combined);$('#rfLabCombined').className=combined>0?'positive':combined<0?'negative':'';const locked=active>0||autoRunning;$('#rfLabStartAll').disabled=locked;$('#rfLabStopAll').disabled=!bots.some(b=>b.running&&!b.stop_requested)&&!autoRunning;$('#rfLabAddTester').disabled=locked;$('#rfLabMatrixCore').disabled=locked;$('#rfLabMatrixMode').disabled=locked;$('#rfAutoStart').disabled=locked;$('#rfAutoStop').disabled=!autoRunning;$('#rfAutoBestPreset').disabled=!(auto.best_candidate||lab.auto_leaderboard?.length);$$('.rf-auto-input').forEach(x=>x.disabled=locked);$$('.rf-lab-remove').forEach(x=>x.disabled=locked);$$('.rf-lab-tester input,.rf-lab-tester select').forEach(x=>x.disabled=locked);
  if($('#rfAutoStage'))$('#rfAutoStage').textContent=auto.enabled?(auto.stage||'IDLE'):'IDLE';if($('#rfAutoRound'))$('#rfAutoRound').textContent=auto.enabled?`${auto.round||0} / ${auto.max_rounds||0}`:'0 / 0';if($('#rfAutoTarget'))$('#rfAutoTarget').textContent=auto.enabled?`${auto.target_trades||0} trades`:'—';if($('#rfAutoMessage'))$('#rfAutoMessage').textContent=auto.enabled?(auto.message||'Auto Lab ready.'):'Auto Lab locks generated research to 2 ticks.';
  const top=bots.find(b=>b.id===lab.top_candidate);$('#rfLabTopCandidate').textContent=top?(top.label||top.id):(auto.best_candidate||'Need data');$('#rfLabTopMessage').textContent=auto.enabled?(auto.message||'Auto Lab collecting 2T data'):top?`${top.grade} · score ${top.screen_score} · ${top.trades} trades`:(lab.data_message||'30+ trades per tester for screening');const ready=bots.some(b=>b.report_ready);$('#rfLabSummaryMd').disabled=!bots.length;$('#rfLabSummaryJson').disabled=!bots.length;
  const sorted=[...bots].sort((a,b)=>{const ae=a.trades>=30?1:0,be=b.trades>=30?1:0;return be-ae||toNumber(b.screen_score,-999)-toNumber(a.screen_score,-999);});const body=$('#rfLabResults');body.innerHTML=sorted.length?sorted.map((b,i)=>{const st=b.settings||{};const pf=b.profit_factor===null||b.profit_factor===undefined?'—':b.profit_factor;const state=b.running?(b.stop_requested?'DRAINING':'RUNNING'):(b.exit_code===null||b.exit_code===undefined?'READY':'STOPPED');return `<tr><td><strong>#${i+1} ${escapeHtml(b.label||b.id)}</strong><small class="table-sub">${escapeHtml(String(st.duration||''))}${escapeHtml(st.duration_unit||'')} · ${escapeHtml(st.decision_mode||'')} · ${escapeHtml(st.direction_policy||'')}<br>conf ${escapeHtml(String(st.min_confidence||''))} · edge ${escapeHtml(String(st.min_edge||''))} · LB ${escapeHtml(String(st.lookback||''))}${Number(b.invalid_duration_trades||0)>0?`<br><span class="negative">INVALID DURATION ×${Number(b.invalid_duration_trades||0)}</span>`:''}</small></td><td><span class="micro-badge ${state==='RUNNING'?'up':state==='DRAINING'?'warn':'neutral'}">${state}</span></td><td>${b.trades}</td><td>${b.wins} / ${b.losses}</td><td>${b.win_rate}%</td><td>${b.average_break_even_pct??'—'}%</td><td class="${toNumber(b.edge_vs_break_even_pct,0)>0?'positive':toNumber(b.edge_vs_break_even_pct,0)<0?'negative':''}">${b.edge_vs_break_even_pct??'—'} pp</td><td>${b.conservative_edge_95_pct??'—'} pp</td><td>${pf}</td><td class="${toNumber(b.profit,0)>0?'positive':toNumber(b.profit,0)<0?'negative':''}">${b.profit}</td><td>${b.max_drawdown}</td><td><span class="micro-badge ${b.grade==='PROMISING'?'up':b.grade==='WEAK'?'down':b.grade==='WATCH'?'warn':'neutral'}">${escapeHtml(b.grade||'')}</span></td><td><div class="lab-row-actions"><button class="button danger small rf-lab-stop-one" data-bot-id="${escapeHtml(b.id)}" ${b.running&&!b.stop_requested?'':'disabled'}>Stop</button>${b.report_ready?`<button class="button secondary small rf-lab-report" data-bot-id="${escapeHtml(b.id)}" data-format="md">MD</button><button class="button secondary small rf-lab-report" data-bot-id="${escapeHtml(b.id)}" data-format="json">JSON</button>`:''}</div></td></tr>`;}).join(''):'<tr><td colspan="13" class="empty">Build a Rise/Fall experiment to begin.</td></tr>';
  const sel=$('#rfLabConsoleSelect'),prev=sel.value||rfLabSelectedConsole;sel.innerHTML='<option value="">Select tester…</option>'+bots.map(b=>`<option value="${escapeHtml(b.id)}">${escapeHtml(b.label||b.id)}</option>`).join('');if(bots.some(b=>b.id===prev))sel.value=prev;else if(bots.length)sel.value=bots[0].id;rfLabSelectedConsole=sel.value;
}
async function pollRfLabConsole(){const id=$('#rfLabConsoleSelect')?.value||'';if(!id)return;if(rfLabSelectedConsole!==id){rfLabSelectedConsole=id;$('#rfLabConsole').textContent='';}const after=rfLabLogIds[id]||0;try{const r=await api(`/api/rise-fall/lab/logs?bot_id=${encodeURIComponent(id)}&after=${after}`);if(r.lines?.length){const t=$('#rfLabConsole');const bottom=t.scrollTop+t.clientHeight>=t.scrollHeight-40;t.textContent+=(t.textContent?'\n':'')+r.lines.map(x=>x.text).join('\n');if(t.textContent.length>180000)t.textContent=t.textContent.slice(-150000);if(bottom)t.scrollTop=t.scrollHeight;}rfLabLogIds[id]=r.last_id||after;}catch(_){}}
function initRfLab(){
  $('#rfLabAddTester')?.addEventListener('click',()=>addRfLabTester());$('#rfLabMatrixCore')?.addEventListener('click',()=>buildRfLabMatrix('core'));$('#rfLabMatrixMode')?.addEventListener('click',()=>buildRfLabMatrix('modes'));$('#rfLabAddPreset')?.addEventListener('click',addSavedPresetToRfLab);$('#rfLabStartAll')?.addEventListener('click',startRfLab);$('#rfLabStopAll')?.addEventListener('click',()=>{if(rfLabLastStatus?.auto?.running)stopRfAutoLab();else stopRfLab();});$('#rfLabSummaryMd')?.addEventListener('click',()=>downloadRfLabSummary('md'));$('#rfLabSummaryJson')?.addEventListener('click',()=>downloadRfLabSummary('json'));$('#rfAutoStart')?.addEventListener('click',startRfAutoLab);$('#rfAutoStop')?.addEventListener('click',stopRfAutoLab);$('#rfAutoBestPreset')?.addEventListener('click',downloadRfAutoBestPreset);
  $('#rfLabBuilder')?.addEventListener('click',(e)=>{const b=e.target.closest('.rf-lab-remove');if(b)b.closest('.rf-lab-tester')?.remove();});$('#rfLabResults')?.addEventListener('click',(e)=>{const stop=e.target.closest('.rf-lab-stop-one');if(stop){stopRfLab(stop.dataset.botId||'');return;}const rep=e.target.closest('.rf-lab-report');if(rep)downloadRfLabReport(rep.dataset.botId||'',rep.dataset.format||'md');});$('#rfLabConsoleSelect')?.addEventListener('change',()=>{rfLabSelectedConsole=$('#rfLabConsoleSelect').value;$('#rfLabConsole').textContent='';if(rfLabSelectedConsole)rfLabLogIds[rfLabSelectedConsole]=0;pollRfLabConsole();});
}

async function startRiseFall(){
  if(localStorage.getItem(RISK_COMMISSION_ACCEPT_KEY)!=='accepted'){openRiskNotice();toast('Review and accept the risk and commission notice before Rise/Fall deployment.','info');return;}
  try{const payload=formToObject($('#riseFallForm'));const r=await api('/api/rise-fall/start',{method:'POST',body:JSON.stringify(payload)});renderRiseFall(r.status||{});toast('Rise/Fall Step 100 engine started','success');}catch(e){toast(e.message,'error');}
}
async function stopRiseFall(){try{await api('/api/rise-fall/stop',{method:'POST',body:'{}'});toast('Rise/Fall new entries stopped; open contracts will settle normally.','success');await pollRiseFall();}catch(e){toast(e.message,'error');}}
async function saveRiseFall(){try{const r=await api('/api/rise-fall/save',{method:'POST',body:JSON.stringify(formToObject($('#riseFallForm')))});setFormValues($('#riseFallForm'),r.session||{});toast('Rise/Fall configuration saved','success');}catch(e){toast(e.message,'error');}}
async function liveRiseFall(action='apply'){try{const settings=action==='apply'?formToObject($('#riseFallForm')):{};await api('/api/rise-fall/live-controls',{method:'POST',body:JSON.stringify({action,settings})});toast(action==='pause'?'Rise/Fall entries paused':action==='resume'?'Rise/Fall entries resumed':'Rise/Fall configuration applied live','success');await pollRiseFall();}catch(e){toast(e.message,'error');}}
function initRiseFall(){
  $$('[data-rf-config-tab]').forEach(b=>b.addEventListener('click',()=>rfSetConfigTab(b.dataset.rfConfigTab)));$('#rfStartButton')?.addEventListener('click',startRiseFall);$('#rfDeployInsideButton')?.addEventListener('click',startRiseFall);$('#rfStopButton')?.addEventListener('click',stopRiseFall);$('#rfPauseButton')?.addEventListener('click',()=>liveRiseFall('pause'));$('#rfResumeButton')?.addEventListener('click',()=>liveRiseFall('resume'));$('#rfApplyLiveButton')?.addEventListener('click',()=>liveRiseFall('apply'));$('#rfSaveButton')?.addEventListener('click',saveRiseFall);$('#rfClearConsole')?.addEventListener('click',()=>{$('#rfConsole').textContent='';});
  $('#rfPresetSave')?.addEventListener('click',saveRiseFallPreset);$('#rfPresetDownload')?.addEventListener('click',downloadRiseFallPreset);$('#rfPresetUpload')?.addEventListener('click',()=>$('#rfPresetFile')?.click());$('#rfPresetDelete')?.addEventListener('click',deleteRiseFallPreset);
  $('#rfPresetSelect')?.addEventListener('change',(e)=>{const name=e.currentTarget.value;$('#rfPresetDownload').disabled=!name;$('#rfPresetDelete').disabled=!name;if(name){$('#rfPresetName').value=name;loadRiseFallPreset(name);}});
  $('#rfPresetFile')?.addEventListener('change',async(e)=>{const file=e.currentTarget.files?.[0];e.currentTarget.value='';if(file)await importRiseFallPresetFile(file);});
  $('#rfOptimizationMd')?.addEventListener('click',()=>downloadRiseFallOptimization('md'));$('#rfOptimizationJson')?.addEventListener('click',()=>downloadRiseFallOptimization('json'));
}



// ===== V2.27 shared Ollama AI Optimizer / AI Trade =====
let aiOptimizerLastStatus = { settings: {}, provider: {}, engines: {}, validated_counts: {}, live_eligible_counts: {}, last_plans: {}, history: {} };

function aiOptimizerCard(engine) {
  return document.querySelector(`.ai-optimizer-card[data-ai-engine="${engine}"]`);
}

function aiOptimizerSetField(card, name, value) {
  const input = card?.querySelector(`[data-ai-field="${name}"]`);
  if (!input || document.activeElement === input || value === undefined || value === null) return;
  input.value = String(value);
}

function aiOptimizerBestText(engine, state, history = []) {
  if (state?.current_best_label) {
    const score = Number(state.current_score);
    return Number.isFinite(score) ? `${state.current_best_label} · score ${score.toFixed(2)}` : state.current_best_label;
  }
  const best = [...(Array.isArray(history) ? history : [])]
    .filter((row) => row && Number(row.trades || row.performance?.trades || 0) > 0)
    .sort((a,b) => Number(b.score ?? -99999) - Number(a.score ?? -99999))[0];
  return best ? String(best.label || best.id || 'Candidate available') : 'Need data';
}

function renderAIOptimizer(status = {}) {
  aiOptimizerLastStatus = status || aiOptimizerLastStatus;
  const settings = status.settings || {};
  const provider = status.provider || {};
  ['rise_fall','multiplier'].forEach((engine) => {
    const card = aiOptimizerCard(engine);
    if (!card) return;
    const engineSettings = settings[engine] || {};
    const state = (status.engines || {})[engine] || {};
    const plan = (status.last_plans || {})[engine] || {};
    const history = (status.history || {})[engine] || [];

    ['ollama_url','model','thinking','keep_alive','analyze_every','explore_trades','refine_trades','validate_trades','final_trades','max_concurrent','max_minutes_per_stage','min_profit_factor','min_edge_pp','allow_live_trading','live_max_loss','live_profit_target','live_max_trades','live_min_validated_trades','live_min_profit_factor','live_min_edge_pp','live_fail_closed'].forEach((name) => {
      aiOptimizerSetField(card, name, settings[name]);
    });
    ['mode','stake','max_open','trades_per_signal','split_delay'].forEach((name) => {
      aiOptimizerSetField(card, name, engineSettings[name]);
    });

    const providerStatus = card.querySelector('[data-ai-role="provider-status"]');
    if (providerStatus) {
      const models = Array.isArray(provider.models) ? provider.models : [];
      providerStatus.textContent = models.length ? `Ollama connected · ${models.length} model(s)` : `${provider.url || settings.ollama_url || 'Ollama'} · not tested`;
    }
    const running = Boolean(state.running);
    const badge = card.querySelector('[data-ai-role="badge"]');
    if (badge) {
      const modeLabel = String(state.mode || 'AI').replaceAll('_',' ');
      badge.textContent = running ? `${modeLabel} running` : 'AI idle';
      badge.className = `micro-badge ${running ? 'positive' : 'neutral'}`;
    }
    const stateEl = card.querySelector('[data-ai-role="state"]');
    if (stateEl) stateEl.textContent = running ? 'RUNNING' : String(state.stage || 'IDLE');
    const stageEl = card.querySelector('[data-ai-role="stage"]');
    if (stageEl) {
      const target = Number(state.target_trades || 0);
      const live = String(state.mode || '') === 'live_trade';
      stageEl.textContent = live
        ? `AI TRADE · ${Number(state.live_session_trades || 0)} trades · P/L ${Number(state.live_session_profit || 0).toFixed(2)}`
        : `${String(state.stage || 'IDLE')}${target ? ` · ${target} trades` : ''}`;
    }
    const bestEl = card.querySelector('[data-ai-role="best"]');
    if (bestEl) {
      const eligible = Number((status.live_eligible_counts || {})[engine] || 0);
      const base = aiOptimizerBestText(engine, state, history);
      bestEl.textContent = eligible ? `${base} · ${eligible} live-eligible` : base;
    }
    const summaryEl = card.querySelector('[data-ai-role="summary"]');
    if (summaryEl) {
      summaryEl.textContent = String(state.last_error || state.last_summary || plan.summary || state.message || 'No analysis yet.');
      summaryEl.classList.toggle('negative', Boolean(state.last_error));
    }
    const startButton = card.querySelector('[data-ai-action="start"]');
    const stopButton = card.querySelector('[data-ai-action="stop"]');
    if (startButton) startButton.disabled = running;
    if (stopButton) stopButton.disabled = !running;
    const validated = Number((status.validated_counts || {})[engine] || 0);
    const download = card.querySelector('[data-ai-action="download"]');
    if (download) download.title = validated ? `${validated} validated candidate(s)` : 'A best preset is generated after AI Lab stages';
  });
}

function collectAIOptimizerSettings(engine) {
  const card = aiOptimizerCard(engine);
  if (!card) throw new Error('AI Optimizer card not found');
  const val = (name) => card.querySelector(`[data-ai-field="${name}"]`)?.value;
  const num = (name, fallback = 0) => {
    const n = Number(val(name));
    return Number.isFinite(n) ? n : fallback;
  };
  const boolVal = (name, fallback = false) => {
    const raw = val(name);
    if (raw === undefined || raw === null || raw === '') return fallback;
    return String(raw).toLowerCase() === 'true';
  };
  const settings = {
    provider: 'ollama',
    ollama_url: String(val('ollama_url') || 'http://localhost:11434').trim(),
    model: String(val('model') || 'qwen3:8b').trim(),
    thinking: String(val('thinking') || 'low'),
    keep_alive: String(val('keep_alive') || '10m').trim(),
    analyze_every: Math.round(num('analyze_every', 25)),
    explore_trades: Math.round(num('explore_trades', 30)),
    refine_trades: Math.round(num('refine_trades', 75)),
    validate_trades: Math.round(num('validate_trades', 150)),
    final_trades: Math.round(num('final_trades', 250)),
    max_concurrent: Math.round(num('max_concurrent', 4)),
    max_minutes_per_stage: Math.round(num('max_minutes_per_stage', 60)),
    min_profit_factor: num('min_profit_factor', 1.2),
    min_edge_pp: num('min_edge_pp', 3),
    allow_live_trading: boolVal('allow_live_trading', false),
    live_max_loss: num('live_max_loss', 10),
    live_profit_target: num('live_profit_target', 20),
    live_max_trades: Math.round(num('live_max_trades', 100)),
    live_min_validated_trades: Math.round(num('live_min_validated_trades', 150)),
    live_min_profit_factor: num('live_min_profit_factor', 1.25),
    live_min_edge_pp: num('live_min_edge_pp', 2),
    live_fail_closed: boolVal('live_fail_closed', true),
  };
  const engineSettings = {
    mode: String(val('mode') || 'advisor'),
    stake: num('stake', 1.0),
  };
  if (engine === 'multiplier') {
    engineSettings.max_open = Math.round(num('max_open', 1));
    engineSettings.trades_per_signal = Math.round(num('trades_per_signal', 1));
    engineSettings.split_delay = num('split_delay', 0.5);
  }
  settings[engine] = engineSettings;
  return { settings };
}

async function saveAIOptimizerSettings(engine, quiet = false) {
  const payload = collectAIOptimizerSettings(engine);
  const result = await api('/api/ai-optimizer/config', { method: 'POST', body: JSON.stringify(payload) });
  renderAIOptimizer(result.optimizer || {});
  if (!quiet) toast(`${engine === 'rise_fall' ? 'Rise/Fall' : 'Multiplier'} Ollama settings saved locally`, 'success');
  return result.optimizer || {};
}

async function testAIOptimizer(engine) {
  const button = aiOptimizerCard(engine)?.querySelector('[data-ai-action="test"]');
  if (button) { button.disabled = true; button.textContent = 'Testing…'; }
  try {
    await saveAIOptimizerSettings(engine, true);
    const result = await api('/api/ai-optimizer/test', { method: 'POST', body: '{}' });
    renderAIOptimizer(result.optimizer || {});
    const models = Array.isArray(result.result?.models) ? result.result.models : [];
    const configured = result.result?.model || '';
    const installed = result.result?.model_installed;
    toast(`Ollama connected · ${models.length} model(s)${configured ? ` · ${configured}${installed ? ' ready' : ' not installed'}` : ''}`, installed === false ? 'warning' : 'success');
  } catch (error) { toast(error.message, 'error'); }
  finally { if (button) { button.disabled = false; button.textContent = 'Test Ollama'; } }
}

async function runAIOptimizerAdvisor(engine) {
  const button = aiOptimizerCard(engine)?.querySelector('[data-ai-action="advisor"]');
  if (button) { button.disabled = true; button.textContent = 'Analyzing…'; }
  try {
    await saveAIOptimizerSettings(engine, true);
    const result = await api('/api/ai-optimizer/analyze', { method: 'POST', body: JSON.stringify({ engine }) });
    renderAIOptimizer(result.optimizer || {});
    const summary = result.plan?.summary || 'Ollama Advisor analysis complete.';
    toast(summary.length > 160 ? `${summary.slice(0,157)}…` : summary, 'success');
  } catch (error) { toast(error.message, 'error'); }
  finally { if (button) { button.disabled = false; button.textContent = 'Run Ollama Advisor'; } }
}

async function startAIOptimizer(engine) {
  const card = aiOptimizerCard(engine);
  const mode = String(card?.querySelector('[data-ai-field="mode"]')?.value || 'advisor');
  if (mode === 'advisor') { await runAIOptimizerAdvisor(engine); return; }
  const confirmation = String(card?.querySelector('[data-ai-field="live_confirmation"]')?.value || '').trim();
  try {
    await saveAIOptimizerSettings(engine, true);
    const result = await api('/api/ai-optimizer/start', { method: 'POST', body: JSON.stringify({ engine, mode, confirmation }) });
    if (card?.querySelector('[data-ai-field="live_confirmation"]')) card.querySelector('[data-ai-field="live_confirmation"]').value = '';
    renderAIOptimizer(result.optimizer || {});
    const label = mode === 'auto_lab' ? 'Auto Lab' : mode === 'live_trade' ? 'AI Trade' : 'Adaptive';
    toast(`${engine === 'rise_fall' ? 'Rise/Fall' : 'Multiplier'} ${label} started`, 'success');
  } catch (error) { toast(error.message, 'error'); }
}

async function stopAIOptimizer(engine) {
  try {
    const result = await api('/api/ai-optimizer/stop', { method: 'POST', body: JSON.stringify({ engine }) });
    renderAIOptimizer(result.optimizer || {});
    toast('Ollama optimizer stop requested', 'success');
  } catch (error) { toast(error.message, 'error'); }
}

async function downloadAIOptimizerBest(engine) {
  try {
    const result = await api(`/api/ai-optimizer/best-preset?engine=${encodeURIComponent(engine)}`);
    const preset = result.preset || {};
    if (!preset.content) throw new Error('No AI Optimizer best preset is ready yet');
    downloadTextFile(preset.filename || (engine === 'rise_fall' ? 'RF_AI_OPTIMIZER_BEST_2T.json' : 'MULTIPLIER_AI_OPTIMIZER_BEST.json'), preset.content, 'application/json');
    toast('AI best preset downloaded', 'success');
  } catch (error) { toast(error.message, 'error'); }
}

function bindAIOptimizerEvents() {
  $$('.ai-optimizer-card').forEach((card) => {
    card.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-ai-action]');
      if (!button) return;
      const engine = String(card.dataset.aiEngine || 'rise_fall');
      const action = String(button.dataset.aiAction || '');
      if (action === 'save') { try { await saveAIOptimizerSettings(engine); } catch (error) { toast(error.message, 'error'); } }
      else if (action === 'test') await testAIOptimizer(engine);
      else if (action === 'advisor') await runAIOptimizerAdvisor(engine);
      else if (action === 'start') await startAIOptimizer(engine);
      else if (action === 'stop') await stopAIOptimizer(engine);
      else if (action === 'download') await downloadAIOptimizerBest(engine);
    });
  });
}

function bindEvents() {
  $$(".tab[data-tab]").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  $$(".engine-tab[data-engine]").forEach((button) => button.addEventListener("click", () => switchEngine(button.dataset.engine)));
  $("#startButton").addEventListener("click", deployActiveEngine);
  $("#navDeployButton")?.addEventListener("click", startBot);
  $("#sessionStartButton").addEventListener("click", startBot);
  $("#riskNoticeButton")?.addEventListener("click", openRiskNotice);
  $("#commissionNoticeButton")?.addEventListener("click", openRiskNotice);
  $("#mobileRiskCommissionButton")?.addEventListener("click", () => { setMobileMore(false); openRiskNotice(); });
  $("#closeRiskNoticeButton")?.addEventListener("click", closeRiskNotice);
  $("#riskNoticeUnderstandButton")?.addEventListener("click", acceptRiskCommissionNotice);
  $("#riskNoticeDialog")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeRiskNotice();
  });
  $("#stopButton").addEventListener("click", stopActiveEngine);
  $("#credentialsForm").addEventListener("submit", saveCredentials);
  $("#testConnectionButton").addEventListener("click", testConnection);
  $("#applyStrategyProfileButton").addEventListener("click", applySelectedStrategyProfile);
  $("#configStrategySelect").addEventListener("change", (event) => showStrategyPanel(event.target.value));
  $("#sessionStrategy").addEventListener("change", (event) => {
    showStrategyPanel(event.target.value);
    $("#sessionStrategyProfile").value = "custom";
  });
  $("#sessionStrategyEngine").addEventListener("change", () => applyEnsembleMode(true));
  $("#ensembleStrategyGrid").addEventListener("change", (event) => {
    const card = event.target.closest(".ensemble-strategy-card");
    if (card) card.classList.toggle("selected", Boolean(card.querySelector(".ensemble-enabled")?.checked));
    syncEnsembleHiddenInputs();
  });
  $("#ensembleStrategyGrid").addEventListener("input", syncEnsembleHiddenInputs);
  $("#saveConfigButton").addEventListener("click", saveConfig);
  $("#clearHistoryButton").addEventListener("click", clearHistory);
  $("#clearConsoleButton").addEventListener("click", () => { $("#consoleOutput").textContent = ""; });
  $("#sessionForm").addEventListener("input", updateSessionPreview);
  $("#sessionForm").addEventListener("change", updateSessionPreview);
  $("#credentialsForm").elements.symbol.addEventListener("input", (event) => {
    $("#heroSymbol").textContent = event.currentTarget.value || "—";
  });
  $("#labAddBotButton").addEventListener("click", () => addLabBot());
  $("#labStartAllButton").addEventListener("click", startStrategyLab);
  $("#labStopAllButton").addEventListener("click", () => stopStrategyLab());
  $("#labBotBuilder").addEventListener("click", (event) => {
    const remove = event.target.closest(".lab-remove");
    if (remove) remove.closest(".lab-bot-card")?.remove();
  });
  $("#labBotBuilder").addEventListener("input", (event) => {
    const card = event.target.closest(".lab-bot-card");
    if (card) updateLabBotFoot(card);
  });
  $("#labBotBuilder").addEventListener("change", (event) => {
    const card = event.target.closest(".lab-bot-card");
    if (card) updateLabBotFoot(card);
  });
  initRfLab();
  bindAIOptimizerEvents();
  initCandlestickSubtypeIcons();
  $$(".pattern-set-btn").forEach((button)=>button.addEventListener("click",()=>applyPatternSet(button.dataset.patternSet)));
  $$(".candle-shape-preset").forEach((button)=>button.addEventListener("click",()=>applyCandleShapePreset(button.dataset.shapePreset)));
  $$(".hammer-pattern-toggle").forEach((input)=>input.addEventListener("change",updatePatternSelectionUi));
  $("#patternSearchInput")?.addEventListener("input",(event)=>{const q=String(event.target.value||"").trim().toLowerCase();$$(`.pattern-picker label`).forEach((label)=>{label.classList.toggle("pattern-filtered",Boolean(q)&&!String(label.dataset.patternSearch||label.textContent||"").toLowerCase().includes(q));});});
  $("#tpUnit")?.addEventListener("change", updateSessionPreview);
  $("#labResultsBody").addEventListener("click", (event) => {
    const stop = event.target.closest(".lab-stop-one");
    if (stop) { stopStrategyLab(stop.dataset.botId || ""); return; }
    const report = event.target.closest(".lab-report-one");
    if (report) downloadLabReport(report.dataset.botId || "", report.dataset.format || "md");
  });
  $("#labDownloadSummaryMd").addEventListener("click", () => downloadLabRunSummary("md"));
  $("#labDownloadSummaryJson").addEventListener("click", () => downloadLabRunSummary("json"));
  $("#labConsoleSelect").addEventListener("change", () => {
    labSelectedConsoleBot = $("#labConsoleSelect").value;
    $("#labConsoleOutput").textContent = "";
    if (labSelectedConsoleBot) labLogIds[labSelectedConsoleBot] = 0;
    pollLabConsole();
  });
  $("#saveFullPresetButton")?.addEventListener("click", saveFullPreset);
  $("#downloadFullPresetButton")?.addEventListener("click", downloadFullPreset);
  $("#importFullPresetButton")?.addEventListener("click", () => $("#importFullPresetFile")?.click());
  $("#importFullPresetFile")?.addEventListener("change", async (event) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (file) await importFullPresetFile(file);
  });
  $("#deleteFullPresetButton")?.addEventListener("click", deleteFullPreset);
  $("#fullPresetSelect")?.addEventListener("change", (event) => {
    const name = event.currentTarget.value;
    $("#deleteFullPresetButton").disabled = !name;
    $("#downloadFullPresetButton").disabled = !name;
    if (name) { $("#fullPresetName").value = name; loadFullPreset(name); }
  });
  $("#uiSettingsButton")?.addEventListener("click", openUiSettings);
  $("#mobileUiSettingsButton")?.addEventListener("click", () => { setMobileMore(false); openUiSettings(); });
  $("#closeUiSettingsButton")?.addEventListener("click", () => $("#uiSettingsDialog")?.close());
  $("#doneUiSettingsButton")?.addEventListener("click", () => $("#uiSettingsDialog")?.close());
  $("#uiThemeSwatches")?.addEventListener("click", (event) => {
    const btn = event.target.closest(".theme-swatch");
    if (btn?.dataset.themeChoice) setThemePreset(btn.dataset.themeChoice);
  });
  ["#uiAccentColor","#uiAccent2Color","#uiHighlightColor","#uiBackgroundColor"].forEach((selector) => $(selector)?.addEventListener("input", markCustomThemeAndApply));
  $("#uiRefreshSpeed")?.addEventListener("change", (event) => { uiPrefs.refreshMs = Number(event.currentTarget.value || 1500); persistUiPrefs(); applyUiPrefs(); });
  $("#uiBackgroundImageOpacity")?.addEventListener("input", (event) => { uiPrefs.backgroundImageOpacity = Number(event.currentTarget.value || 0); persistUiPrefs(); applyUiPrefs({ restart: false }); });
  $("#uiHeroImageInput")?.addEventListener("change", async (event) => {
    try { uiPrefs.heroImage = await fileToResizedDataUrl(event.currentTarget.files?.[0], 640, 640, .86); persistUiPrefs(); applyUiPrefs({restart:false}); toast("Hero image updated", "success"); }
    catch (error) { toast(error.message, "error"); }
    event.currentTarget.value = "";
  });
  $("#uiBackgroundImageInput")?.addEventListener("change", async (event) => {
    try { uiPrefs.backgroundImage = await fileToResizedDataUrl(event.currentTarget.files?.[0], 1600, 1200, .74); persistUiPrefs(); applyUiPrefs({restart:false}); toast("Background image updated", "success"); }
    catch (error) { toast(error.message, "error"); }
    event.currentTarget.value = "";
  });
  $("#removeHeroImageButton")?.addEventListener("click", () => { uiPrefs.heroImage = ""; persistUiPrefs(); applyUiPrefs({restart:false}); });
  $("#removeBackgroundImageButton")?.addEventListener("click", () => { uiPrefs.backgroundImage = ""; persistUiPrefs(); applyUiPrefs({restart:false}); });
  $("#resetUiSettingsButton")?.addEventListener("click", () => { uiPrefs = { ...DEFAULT_UI_PREFS }; persistUiPrefs(); applyUiPrefs(); toast("Interface reset", "success"); });
  window.addEventListener("resize", () => { requestAnimationFrame(drawCurrentCharts); requestAnimationFrame(()=>drawRiseFallChart(riseFallLastStatus?.telemetry?.recent_prices||[])); });
}

async function init() {
  loadUiPrefs();
  applyUiPrefs({ restart: false });
  bindEvents();
  initAdvancedOps();
  initRiseFall();
  updateClock();
  clockTimer = setInterval(updateClock, 1000);
  drawCurrentCharts();
  try {
    bootstrapData = await api("/api/bootstrap");
    strategyCatalog = bootstrapData.strategy_catalog || [];
    strategyProfiles = bootstrapData.strategy_profiles || {};
    renderFullPresets(bootstrapData.presets || []);
    populateStrategySelectors();
    populateAdvancedRuntimeStrategies();
    setFormValues($("#sessionForm"), bootstrapData.session);
    renderEnsembleStrategyGrid(bootstrapData.session || {});
    $("#configStrategySelect").value = (bootstrapData.session?.strategy && bootstrapData.session.strategy !== "ensemble") ? bootstrapData.session.strategy : (strategyCatalog[0]?.id || "ema_rsi");
    populateConfigForm(bootstrapData.config);
    renderCredentials(bootstrapData.credentials);
    renderStatus(bootstrapData.status);
    renderTrades(bootstrapData.trades);
    renderAdvancedOps(bootstrapData.status, bootstrapData.trades);
    renderStrategyLab(bootstrapData.lab || {});
    renderRfLab(bootstrapData.rise_fall_lab || {});
    renderAIOptimizer(bootstrapData.ai_optimizer || {});
    if (bootstrapData.rise_fall?.session) setFormValues($("#riseFallForm"), bootstrapData.rise_fall.session);
    renderRiseFallPresets(bootstrapData.rise_fall?.presets || []);
    renderRiseFall(bootstrapData.rise_fall?.status || {});
    if (!bootstrapData.rise_fall_lab?.bots?.length && !$("#rfLabBuilder")?.children?.length) buildRfLabMatrix("core");
    switchEngine(localStorage.getItem(ENGINE_UI_KEY) || "multiplier");
    if (!bootstrapData.lab?.bots?.length) {
      addLabBot({ label: "EMA Strict", strategy: "ema_rsi", profile: "strict" });
      addLabBot({ label: "Candles Balanced", strategy: "hammer", profile: "balanced" });
      addLabBot({ label: "Donchian Strict", strategy: "donchian", profile: "strict" });
    }
    updateSessionPreview();
    poll();
    pollTimer = setInterval(poll, refreshIntervalMs);
  } catch (error) {
    $("#heroStatusText").textContent = `Dashboard startup failed: ${error.message}`;
    setApiState("error", "Offline");
    toast(error.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", init);


// ===== Android / PWA shell =====
let deferredInstallPrompt = null;

const MOBILE_MORE_TABS = new Set(['rise-fall', 'rf-lab', 'credentials', 'history', 'lab']);

function syncMobileNav(name) {
  document.querySelectorAll('[data-mobile-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.mobileTab === name);
  });
  document.querySelector('#mobileMoreButton')?.classList.toggle('active', MOBILE_MORE_TABS.has(name));
}

function setMobileMore(open) {
  const sheet = document.querySelector('#mobileMoreSheet');
  const backdrop = document.querySelector('#mobileMoreBackdrop');
  const button = document.querySelector('#mobileMoreButton');
  if (!sheet || !backdrop || !button) return;
  sheet.classList.toggle('open', open);
  backdrop.classList.toggle('open', open);
  document.body.classList.toggle('mobile-more-open', open);
  sheet.setAttribute('aria-hidden', open ? 'false' : 'true');
  backdrop.setAttribute('aria-hidden', open ? 'false' : 'true');
  button.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function switchMobileTab(name) {
  setMobileMore(false);
  switchTab(name);
  syncMobileNav(name);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function syncMobileDeploy(status = {}) {
  const button = document.querySelector('#mobileDeployButton');
  if (!button) return;
  const running = Boolean(status.running);
  const draining = Boolean(status.stop_requested);
  button.classList.toggle('running', running);
  button.classList.toggle('draining', draining);
  button.disabled = draining;
  button.innerHTML = running ? '<span>■</span>' : '<span>▶</span>';
  button.setAttribute('aria-label', running ? 'Stop new bot entries' : 'Start bot');
}

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  document.querySelector('#installAppButton')?.classList.remove('hidden');
});

window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  document.querySelector('#installAppButton')?.classList.add('hidden');
  toast('Control Nexus installed on your phone.', 'success');
});

async function installControlNexus() {
  if (!deferredInstallPrompt) {
    toast('Use your browser menu → Add to Home screen / Install app.', 'success');
    return;
  }
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  document.querySelector('#installAppButton')?.classList.add('hidden');
}

function initMobileShell() {
  document.querySelectorAll('[data-mobile-tab]').forEach((button) => {
    button.addEventListener('click', () => switchMobileTab(button.dataset.mobileTab));
  });
  document.querySelector('#mobileMoreButton')?.addEventListener('click', () => {
    const isOpen = document.querySelector('#mobileMoreSheet')?.classList.contains('open');
    setMobileMore(!isOpen);
  });
  document.querySelector('#mobileMoreClose')?.addEventListener('click', () => setMobileMore(false));
  document.querySelector('#mobileMoreBackdrop')?.addEventListener('click', () => setMobileMore(false));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setMobileMore(false); });
  document.querySelector('#mobileSaveConfigButton')?.addEventListener('click', saveConfig);
  document.querySelector('#mobileDeployButton')?.addEventListener('click', async () => {
    const startEnabled = !document.querySelector('#startButton')?.disabled;
    if (startEnabled) await deployActiveEngine(); else await stopActiveEngine();
  });
  document.querySelector('#installAppButton')?.addEventListener('click', installControlNexus);
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js').catch((error) => console.warn('PWA service worker:', error));
  }
}

document.addEventListener('DOMContentLoaded', initMobileShell);