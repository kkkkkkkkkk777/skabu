(() => {
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const warnEl = document.getElementById("warn");
  const errorEl = document.getElementById("error");
  const cardsEl = document.getElementById("cards");
  const rowsEl = document.getElementById("rows");
  const curveRowsEl = document.getElementById("curve-rows");
  const chartEl = document.getElementById("sim-chart");
  const splitBarEl = document.getElementById("sim-split-bar");
  const splitEl = document.querySelector(".sim-split");
  const CHART_H_KEY = "skabu.simChartH";
  const CHART_H_MIN = 80;
  const asOfEl = document.getElementById("as-of");
  const asOfWrapEl = document.getElementById("as-of-wrap");
  const startEl = document.getElementById("sim-start");
  const endEl = document.getElementById("sim-end");
  const startWrapEl = document.getElementById("start-wrap");
  const endWrapEl = document.getElementById("end-wrap");
  const simModeMonthEl = document.getElementById("sim-mode-month");
  const simModeLongEl = document.getElementById("sim-mode-long");
  const cashEl = document.getElementById("cash");
  const topEl = document.getElementById("top");
  const riskEl = document.getElementById("risk-pct");
  const noTargetEl = document.getElementById("no-target");
  const holdWrapEl = document.getElementById("hold-wrap");
  const holdEl = document.getElementById("hold-days");
  const runEl = document.getElementById("run");
  const statusEl = document.getElementById("status");
  const debugEl = document.getElementById("debug");
  const indBarEl = document.getElementById("ind-bar");
  const gateBarEl = document.getElementById("gate-bar");
  const modeNoteEl = document.getElementById("ind-mode-note");
  const OP_SYMBOL = { eq: "=", lt: "<", lte: "<=", gt: ">", gte: ">=" };
  const sourceActiveEl = document.getElementById("source-active");
  const sourceChecksEl = document.getElementById("source-checks");
  const experimentEl = document.getElementById("experiment");
  const saveTitleEl = document.getElementById("save-title");
  const saveResultEl = document.getElementById("save-result");
  const saveListEl = document.getElementById("save-list");
  const saveOpenEl = document.getElementById("save-open");
  const saveDeleteEl = document.getElementById("save-delete");
  const saveCompareEl = document.getElementById("save-compare");
  const saveCompareBtnEl = document.getElementById("save-compare-btn");
  const saveCompareBoxEl = document.getElementById("save-compare-box");
  const saveCompareTableEl = document.getElementById("save-compare-table");
  let chart = null;
  let equitySeries = null;
  let benchSeries = null;
  let lastSim = null;
  const state = {
    source: "active",
    experimentId: "",
    catalog: [],
    gateCatalog: [],
    groups: [],
    selected: {},
    gates: {},
    prefs: { saved: false, items: null, gates: null, defaults: {} },
    activeIds: [],
    activeParams: {},
    activeTitle: "",
    adoptedSelections: [],
    adoptedFilters: [],
    presetById: {},
    simMode: "month",
    longStart: "",
    longEnd: "",
    saves: [],
    openedSave: null,
    gatePassedNote: "",
  };

  function autoSyncSuffix(auto) {
    if (!auto || auto.enabled === false) return "";
    if (auto.running) return "  /  同期中";
    if (auto.last_error) return "  /  同期失敗";
    if (auto.last_action === "empty") return `  /  株価未配信${auto.last_bars_to ? " " + auto.last_bars_to : ""}`;
    if (auto.last_bars_to) return `  /  最終 ${auto.last_bars_to}`;
    if (auto.last_action === "synced" || auto.last_at) return "  /  自動同期済";
    return "";
  }

  function showError(message) {
    if (!errorEl) {
      console.error("sim error element missing", message);
      return;
    }
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
    if (message) console.error("sim", message);
  }

  async function api(path, options) {
    const extra = options || {};
    const headers = { Accept: "application/json", ...(extra.headers || {}) };
    if (extra.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const res = await fetch(path, { ...extra, headers });
    const requestId = res.headers.get("X-Request-Id") || "-";
    let body = {};
    try {
      body = await res.json();
    } catch (err) {
      throw new Error(`JSON解析に失敗しました request_id=${requestId}`);
    }
    if (!res.ok || body.ok === false) {
      throw new Error(`${body.message || body.error || res.statusText} request_id=${body.request_id || requestId}`);
    }
    if (debugEl) debugEl.textContent = `request_id ${body.request_id || requestId}`;
    return body;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function yen(value) {
    if (value == null || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString("ja-JP", { maximumFractionDigits: 0 });
  }

  function pct(value) {
    if (value == null || Number.isNaN(value)) return "-";
    return `${(value * 100).toFixed(2)}%`;
  }

  function reasonLabel(reason) {
    if (reason === "stop") return "損切";
    if (reason === "target") return "目標";
    if (reason === "time") return "時間";
    if (reason === "end") return "終了";
    return reason || "-";
  }

  function syncHold() {
    const on = Boolean(noTargetEl && noTargetEl.checked);
    if (holdWrapEl) holdWrapEl.hidden = !on;
  }

  function setSimMode(next) {
    state.simMode = next === "long" ? "long" : "month";
    if (simModeMonthEl) simModeMonthEl.classList.toggle("active", state.simMode === "month");
    if (simModeLongEl) simModeLongEl.classList.toggle("active", state.simMode === "long");
    if (asOfWrapEl) asOfWrapEl.hidden = state.simMode === "long";
    if (startWrapEl) startWrapEl.hidden = state.simMode !== "long";
    if (endWrapEl) endWrapEl.hidden = state.simMode !== "long";
    if (state.simMode === "long") {
      if (startEl && !startEl.value && state.longStart) startEl.value = state.longStart;
      if (endEl && !endEl.value && state.longEnd) endEl.value = state.longEnd;
    }
  }

  function enabledIds() {
    return state.catalog.filter((item) => state.selected[item.id] && state.selected[item.id].enabled).map((item) => item.id);
  }

  function gateKey(item) {
    return `${item.id}.${item.feature}`;
  }

  function selectionPayload() {
    return enabledIds().map((id) => ({
      id,
      params: { ...((state.selected[id] && state.selected[id].params) || {}) },
    }));
  }

  async function saveVersion() {
    if (state.source !== "checks") return;
    const titleEl = document.getElementById("version-title");
    const noteEl = document.getElementById("version-save-note");
    const title = titleEl ? titleEl.value.trim() : "";
    if (!title) {
      showError("版の名前を入れてください。");
      return;
    }
    showError("");
    if (noteEl) noteEl.textContent = "保存中…";
    try {
      const data = await api("/api/strategy/save", {
        method: "POST",
        body: JSON.stringify({
          title,
          selections: selectionPayload(),
          filters: enabledGates(),
        }),
      });
      const saved = data.experiment || {};
      if (noteEl) {
        noteEl.textContent = `「${saved.title || title}」を版として保存しました。戦略画面の採用するで日次に回します。`;
      }
    } catch (err) {
      if (noteEl) noteEl.textContent = "";
      showError(`版の保存失敗: ${err.message}`);
    }
  }

  function enabledGates() {
    return state.gateCatalog.filter((item) => {
      const current = state.gates[gateKey(item)];
      return current && current.enabled;
    }).map((item) => {
      const current = state.gates[gateKey(item)];
      return {
        id: item.id,
        feature: item.feature,
        op: current.op || item.default_op,
        value: current.value,
      };
    });
  }

  function encodeFlt(filters) {
    return filters.map((item) => `${item.id}.${item.feature}${OP_SYMBOL[item.op] || "="}${item.value}`).join(",");
  }

  function applyGateList(filters) {
    const byKey = {};
    (filters || []).forEach((item) => {
      if (!item || !item.id || !item.feature) return;
      byKey[`${item.id}.${item.feature}`] = item;
    });
    state.gateCatalog.forEach((item) => {
      const key = gateKey(item);
      const saved = byKey[key];
      state.gates[key] = {
        enabled: Boolean(saved),
        op: saved && saved.op ? saved.op : item.default_op,
        value: saved && saved.value != null ? saved.value : (saved && saved.equals != null ? saved.equals : item.default_value),
      };
    });
  }

  function selectedFromSelections(selections) {
    const map = {};
    state.catalog.forEach((item) => {
      const params = {};
      item.params.forEach((spec) => {
        params[spec.name] = spec.default;
      });
      map[item.id] = { enabled: false, params };
    });
    (selections || []).forEach((item) => {
      if (!map[item.id]) map[item.id] = { enabled: true, params: {} };
      map[item.id].enabled = true;
      map[item.id].params = { ...map[item.id].params, ...(item.params || {}) };
    });
    return map;
  }

  function gatesFromFilters(filters) {
    const out = {};
    const byKey = {};
    (filters || []).forEach((item) => {
      if (item && item.id && item.feature) byKey[`${item.id}.${item.feature}`] = item;
    });
    state.gateCatalog.forEach((item) => {
      const key = gateKey(item);
      const saved = byKey[key];
      out[key] = {
        enabled: Boolean(saved),
        op: saved && saved.op ? saved.op : item.default_op,
        value: saved && saved.value != null ? saved.value : (saved && saved.equals != null ? saved.equals : item.default_value),
      };
    });
    return out;
  }

  function displaySelections() {
    if (state.source === "experiment") {
      const spec = state.presetById[state.experimentId] || {};
      return spec.selections || [];
    }
    if (state.source === "active") return state.adoptedSelections;
    return null;
  }

  function displayFilters() {
    if (state.source === "experiment") {
      const spec = state.presetById[state.experimentId] || {};
      return spec.filters || [];
    }
    if (state.source === "active") return state.adoptedFilters;
    return null;
  }

  function indicatorQuery() {
    const params = new URLSearchParams();
    const ids = enabledIds();
    if (ids.length) params.set("ind", ids.join(","));
    ids.forEach((id) => {
      Object.entries((state.selected[id] && state.selected[id].params) || {}).forEach(([name, value]) => {
        params.set(`${id}.${name}`, String(value));
      });
    });
    params.set("flt", encodeFlt(enabledGates()));
    return params;
  }

  function strategyEnabled(item) {
    if (state.activeIds.length) return state.activeIds.includes(item.id);
    return item.id === "ema" || item.id === "rsi";
  }

  function initSelection(catalog) {
    const url = window.skabuIndPrefs.filterIndicatorUrl
      ? window.skabuIndPrefs.filterIndicatorUrl(new URLSearchParams(), catalog)
      : new URLSearchParams();
    const prefs = window.skabuIndPrefs;
    const prefsItems = (state.prefs && state.prefs.items) || {};
    const usePrefs = prefs && Object.keys(prefsItems).length;
    catalog.forEach((item) => {
      if (prefs) prefs.applyItem(item, state.selected, prefsItems[item.id], url, usePrefs, strategyEnabled);
      else {
        const params = {};
        item.params.forEach((spec) => {
          params[spec.name] = spec.default;
        });
        state.selected[item.id] = { enabled: strategyEnabled(item), params };
      }
    });
  }

  function updatePrefNote() {
    const note = document.getElementById("ind-pref-note");
    if (!note || !window.skabuIndPrefs) return;
    note.textContent = window.skabuIndPrefs.noteText(state.prefs);
  }

  function setSource(next, experimentId) {
    if (next === "experiment") {
      state.source = "experiment";
      state.experimentId = experimentId || (experimentEl && experimentEl.value) || "";
    } else if (next === "checks") {
      state.source = "checks";
      state.experimentId = "";
      if (experimentEl) experimentEl.value = "";
    } else {
      state.source = "active";
      state.experimentId = "";
      if (experimentEl) experimentEl.value = "";
    }
    if (sourceActiveEl) sourceActiveEl.classList.toggle("active", state.source === "active");
    if (sourceChecksEl) sourceChecksEl.classList.toggle("active", state.source === "checks");
    const versionSaveEl = document.getElementById("version-save");
    if (versionSaveEl) versionSaveEl.hidden = state.source !== "checks";
    if (modeNoteEl) {
      if (state.source === "experiment") {
        modeNoteEl.textContent = `版 ${state.experimentId} の点と門です。日次と売買は変わりません。`;
      } else if (state.source === "active") {
        modeNoteEl.textContent = "採用戦略を使っています。点と門は実験に切り替えると使います。";
      } else {
        modeNoteEl.textContent = "この画面の点と門だけで実験します。日次と売買は変わりません。";
      }
    }
    if (state.scorePanel) state.scorePanel.refresh();
    if (state.gatePanel) state.gatePanel.refresh();
  }

  async function savePrefs() {
    if (state.source !== "checks") return;
    try {
      const items = window.skabuIndPrefs.snapshot(state.catalog, state.selected);
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ items }) });
      updatePrefNote();
    } catch (err) {
      showError(`保存失敗: ${err.message}`);
    }
  }

  async function saveGatePrefs() {
    if (state.source !== "checks") return;
    try {
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ gates: enabledGates() }) });
      updateGateNote();
    } catch (err) {
      showError(`門の保存失敗: ${err.message}`);
    }
  }

  function updateGateNote() {
    const note = document.getElementById("gate-pref-note");
    if (!note) return;
    if (state.prefs && state.prefs.gates != null && state.prefs.saved_at) {
      note.textContent = `保存済 ${state.prefs.saved_at}`;
    } else {
      note.textContent = "未保存";
    }
  }

  function catalogDefaults() {
    state.catalog.forEach((item) => {
      const params = {};
      item.params.forEach((spec) => {
        params[spec.name] = spec.default;
      });
      state.selected[item.id] = { enabled: strategyEnabled(item), params };
    });
  }

  async function resetPrefs() {
    if (state.source !== "checks") return;
    try {
      catalogDefaults();
      const items = window.skabuIndPrefs.snapshot(state.catalog, state.selected);
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ items }) });
      if (state.scorePanel) state.scorePanel.refresh();
      updatePrefNote();
    } catch (err) {
      showError(`初期化失敗: ${err.message}`);
    }
  }

  async function resetGatePrefs() {
    if (state.source !== "checks") return;
    try {
      applyGateList(state.adoptedFilters);
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ gates: enabledGates() }) });
      if (state.gatePanel) state.gatePanel.refresh();
      updateGateNote();
    } catch (err) {
      showError(`門の初期化失敗: ${err.message}`);
    }
  }

  function renderIndicatorBar() {
    if (!window.skabuIndPanel || !indBarEl) {
      console.error("sim score panel missing");
      return;
    }
    state.scorePanel = window.skabuIndPanel.mount({
      host: indBarEl,
      label: "点",
      noteId: "ind-pref-note",
      locked: () => state.source !== "checks",
      items: () => state.catalog,
      groups: () => state.groups,
      summary: () => {
        const live = displaySelections();
        const selected = live ? selectedFromSelections(live) : state.selected;
        return window.skabuIndPanel.summarizeIndicators(state.catalog, selected);
      },
      renderRow: (row, item, ctx) => {
        const live = displaySelections();
        const selected = live ? selectedFromSelections(live)[item.id] : state.selected[item.id];
        window.skabuIndPanel.fillIndicatorRow(row, item, selected, {
          locked: ctx.locked,
          onChange: () => {
            if (state.scorePanel) state.scorePanel.refresh();
          },
        });
      },
      onSave: savePrefs,
      onReset: resetPrefs,
      onNote: updatePrefNote,
    });
    updatePrefNote();
  }

  function renderGateBar() {
    if (!window.skabuIndPanel || !gateBarEl) {
      console.error("sim gate panel missing");
      return;
    }
    state.gatePanel = window.skabuIndPanel.mount({
      host: gateBarEl,
      label: "門",
      noteId: "gate-pref-note",
      locked: () => state.source !== "checks",
      items: () => state.gateCatalog,
      groups: () => state.groups,
      summary: () => {
        const live = displayFilters();
        const gates = live ? gatesFromFilters(live) : state.gates;
        const base = window.skabuIndPanel.summarizeGates(state.gateCatalog, gates);
        return state.gatePassedNote ? `${base} ${state.gatePassedNote}` : base;
      },
      renderRow: (row, item, ctx) => {
        const live = displayFilters();
        const selected = live ? gatesFromFilters(live)[gateKey(item)] : state.gates[gateKey(item)];
        window.skabuIndPanel.fillGateRow(row, item, selected, {
          locked: ctx.locked,
          onChange: () => {
            state.gatePassedNote = "";
            if (state.gatePanel) state.gatePanel.refresh();
          },
        });
      },
      onSave: saveGatePrefs,
      onReset: resetGatePrefs,
      onNote: updateGateNote,
    });
    updateGateNote();
  }

  function strategyLabel(data) {
    const ids = (data.strategy || []).map((item) => {
      const found = state.catalog.find((plugin) => plugin.id === item.id);
      return found ? found.title : item.id;
    });
    const names = ids.length ? ids.join("+") : "-";
    const source = data.source || state.source;
    if (source === "checks") {
      const gates = (data.filters || []).map((item) => item.id).join("+") || "門なし";
      return `実験 ${names} / ${gates}`;
    }
    if (source === "experiment") {
      const expId = data.experiment_id || state.experimentId || "-";
      const extra = names !== "-" ? ` / ${names}` : "";
      return `版 ${expId}${extra}`;
    }
    const title = state.activeTitle || "採用戦略";
    const extra = title.includes("v1") || title.includes("過熱") ? title : `採用 ${title}`;
    return `本番 ${extra} / ${names}`;
  }

  function preferredChartHeight() {
    try {
      const raw = localStorage.getItem(CHART_H_KEY);
      const value = raw == null ? 160 : Number(raw);
      if (Number.isFinite(value)) return value;
    } catch (err) {
      console.warn("sim chart height load failed", err);
    }
    return 160;
  }

  function chartHeightLimit() {
    if (!splitEl) return 480;
    const tableMin = 120;
    const bar = splitBarEl ? splitBarEl.offsetHeight : 10;
    return Math.max(CHART_H_MIN, splitEl.clientHeight - tableMin - bar);
  }

  function applyChartHeight(px) {
    const maxH = chartHeightLimit();
    const height = Math.min(maxH, Math.max(CHART_H_MIN, Math.round(px)));
    document.documentElement.style.setProperty("--sim-chart-h", `${height}px`);
    if (chart && chartEl) {
      chart.applyOptions({ width: chartEl.clientWidth, height });
    }
    return height;
  }

  function loadChartHeight() {
    applyChartHeight(preferredChartHeight());
  }

  function bindSplit() {
    if (!splitBarEl || !chartEl || !splitEl) {
      console.error("sim split missing");
      return;
    }
    let drag = null;
    function onMove(ev) {
      if (!drag) return;
      applyChartHeight(drag.startH + (ev.clientY - drag.startY));
    }
    function endDrag(ev) {
      if (!drag) return;
      const next = applyChartHeight(drag.startH + (ev.clientY - drag.startY));
      drag = null;
      splitBarEl.classList.remove("sim-split-dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      try {
        localStorage.setItem(CHART_H_KEY, String(next));
      } catch (err) {
        console.warn("sim chart height save failed", err);
      }
    }
    splitBarEl.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      drag = { startY: ev.clientY, startH: chartEl.getBoundingClientRect().height };
      splitBarEl.classList.add("sim-split-dragging");
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", endDrag);
      window.addEventListener("pointercancel", endDrag);
      try {
        splitBarEl.setPointerCapture(ev.pointerId);
      } catch (err) {
        console.warn("sim split capture failed", err);
      }
    });
    splitBarEl.addEventListener("pointermove", onMove);
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => {
        if (drag) return;
        loadChartHeight();
      });
      observer.observe(splitEl);
    }
  }

  function ensureChart() {
    if (chart || typeof LightweightCharts === "undefined" || !chartEl) return;
    const height = chartEl.clientHeight || 200;
    chart = LightweightCharts.createChart(chartEl, {
      layout: { background: { color: "#151a23" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#2a3344" }, horzLines: { color: "#2a3344" } },
      rightPriceScale: { borderColor: "#2a3344" },
      timeScale: { borderColor: "#2a3344" },
      width: chartEl.clientWidth,
      height,
    });
    equitySeries = chart.addLineSeries({ color: "#5eead4", lineWidth: 2, title: "残高" });
    benchSeries = chart.addLineSeries({ color: "#94a3b8", lineWidth: 2, title: "TOPIX同額" });
    window.addEventListener("resize", () => {
      if (!chart || !chartEl) return;
      loadChartHeight();
    });
  }

  function chartHref(row) {
    const q = new URLSearchParams();
    q.set("code", row.code || "");
    q.set("tf", "daily");
    const from = row.entry_date || (lastSim && lastSim.entry_date);
    const to = row.exit_date || (lastSim && lastSim.end_date);
    if (from) q.set("sim_from", from);
    if (to) q.set("sim_to", to);
    const exit = row.exit_date || to;
    if (exit) q.set("sim_exit", exit);
    if (row.stop != null) q.set("stop", String(row.stop));
    if (row.target != null) q.set("target", String(row.target));
    return `/?${q.toString()}`;
  }

  function openChart(row) {
    if (!row || !row.code) {
      console.error("sim chart row missing code", row);
      showError("銘柄コードが無いのでチャートを開けません");
      return;
    }
    const url = chartHref(row);
    let opened = null;
    try {
      opened = window.open(url, "skabu-chart");
    } catch (err) {
      console.error("sim chart window failed", err);
    }
    if (!opened) {
      console.error("sim chart window blocked", url);
      showError("チャート窓を開けませんでした。ポップアップを許可してください。");
    }
  }

  function sourceLabel(source, experimentId) {
    if (source === "checks") return "実験";
    if (source === "experiment") return `版${experimentId || ""}`;
    return "本番";
  }

  function defaultSaveTitle(data) {
    const mode = data.mode === "long" ? "長期" : "1か月";
    const src = sourceLabel(data.source || state.source, data.experiment_id || state.experimentId);
    let period = data.as_of || "";
    if (data.mode === "long" && data.as_of && data.end_date) {
      period = `${data.as_of}～${data.end_date}`;
    }
    const hold = data.take_profit === false && data.horizon != null ? `利確なし${data.horizon}日` : "";
    let ret = "";
    if (data.return != null && !Number.isNaN(Number(data.return))) {
      const pctVal = Number(data.return) * 100;
      ret = `${pctVal >= 0 ? "+" : ""}${pctVal.toFixed(1)}%`;
    }
    return `${mode} ${src} ${period} ${hold} ${ret}`.replace(/\s+/g, " ").trim();
  }

  function saveOptionLabel(item) {
    const summary = item.summary || {};
    const ret = summary.return == null || Number.isNaN(Number(summary.return))
      ? "-"
      : `${Number(summary.return) >= 0 ? "+" : ""}${(Number(summary.return) * 100).toFixed(1)}%`;
    return `${item.title}  ${ret}`;
  }

  function fillSaveSelect(selectEl, selectedId) {
    if (!selectEl) return;
    const current = selectedId != null ? selectedId : selectEl.value;
    selectEl.innerHTML = "";
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "—";
    selectEl.appendChild(blank);
    state.saves.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = saveOptionLabel(item);
      selectEl.appendChild(opt);
    });
    if (current && state.saves.some((item) => item.id === current)) selectEl.value = current;
  }

  function refreshSaveSelects(selectedId) {
    fillSaveSelect(saveListEl, selectedId);
    fillSaveSelect(saveCompareEl, saveCompareEl ? saveCompareEl.value : "");
  }

  async function loadSaves(selectedId) {
    try {
      const data = await api("/api/sim-saves");
      state.saves = data.items || [];
      refreshSaveSelects(selectedId);
    } catch (err) {
      console.error("sim saves load failed", err);
      showError(`保存一覧の取得失敗: ${err.message}`);
    }
  }

  function currentInputs(data) {
    const src = (data && data.source) || state.source;
    return {
      source: src,
      experiment_id: (data && data.experiment_id) || state.experimentId || null,
      mode: (data && data.mode) || state.simMode,
      as_of: (asOfEl && asOfEl.value) || (data && data.as_of) || null,
      start: (startEl && startEl.value) || (data && data.as_of) || null,
      end: (endEl && endEl.value) || (data && data.end_date) || null,
      cash: cashEl ? Number(cashEl.value) : (data && data.cash_start),
      top: topEl ? Number(topEl.value) : (data && data.top),
      risk_pct: riskEl ? Number(riskEl.value) : (data && data.risk_points),
      take_profit: noTargetEl ? !noTargetEl.checked : data.take_profit !== false,
      horizon: holdEl ? Number(holdEl.value) : (data && data.horizon),
      strategy: (data && data.strategy) || [],
      filters: (data && data.filters) || [],
    };
  }

  function applySavedInputs(inputs) {
    const src = (inputs && inputs.source) || "active";
    const mode = inputs && inputs.mode === "long" ? "long" : "month";
    setSimMode(mode);
    if (mode === "long") {
      if (startEl) startEl.value = inputs.start || inputs.as_of || "";
      if (endEl) endEl.value = inputs.end || "";
    } else if (asOfEl) {
      asOfEl.value = inputs.as_of || "";
    }
    if (cashEl && inputs.cash != null) cashEl.value = String(inputs.cash);
    if (topEl && inputs.top != null) topEl.value = String(inputs.top);
    if (riskEl && inputs.risk_pct != null) riskEl.value = String(inputs.risk_pct);
    if (noTargetEl) noTargetEl.checked = inputs.take_profit === false;
    if (holdEl && inputs.horizon != null) holdEl.value = String(inputs.horizon);
    syncHold();
    if (src === "experiment" && experimentEl) {
      experimentEl.value = inputs.experiment_id || "";
    }
    if (src === "checks") {
      state.selected = selectedFromSelections(inputs.strategy || []);
      applyGateList(inputs.filters || []);
    }
    setSource(src, inputs.experiment_id || "");
  }

  async function saveCurrent() {
    if (!lastSim) {
      showError("先に実行してから保存してください");
      return;
    }
    const title = ((saveTitleEl && saveTitleEl.value) || defaultSaveTitle(lastSim)).trim();
    if (!title) {
      showError("保存名を入れてください");
      return;
    }
    if (saveResultEl) saveResultEl.disabled = true;
    try {
      const data = await api("/api/sim-saves", {
        method: "POST",
        body: JSON.stringify({ title, result: lastSim, inputs: currentInputs(lastSim) }),
      });
      const item = data.item || {};
      if (saveTitleEl) saveTitleEl.value = item.title || title;
      await loadSaves(item.id);
      showError("");
    } catch (err) {
      showError(`結果の保存失敗: ${err.message}`);
    } finally {
      if (saveResultEl) saveResultEl.disabled = false;
    }
  }

  async function openSaved() {
    const saveId = saveListEl && saveListEl.value;
    if (!saveId) {
      showError("開く保存を選んでください");
      return;
    }
    try {
      const data = await api(`/api/sim-saves/${encodeURIComponent(saveId)}`);
      const item = data.item || {};
      if (!item.result) {
        showError("保存結果が空です");
        return;
      }
      applySavedInputs(item.inputs || {});
      state.openedSave = { id: item.id, title: item.title };
      render(item.result);
      if (saveTitleEl) saveTitleEl.value = item.title || "";
      showError("");
    } catch (err) {
      showError(`保存の表示失敗: ${err.message}`);
    }
  }

  async function deleteSaved() {
    const saveId = saveListEl && saveListEl.value;
    if (!saveId) {
      showError("削除する保存を選んでください");
      return;
    }
    try {
      await api(`/api/sim-saves/${encodeURIComponent(saveId)}`, { method: "DELETE" });
      if (state.openedSave && state.openedSave.id === saveId) state.openedSave = null;
      await loadSaves("");
      if (saveCompareBoxEl) saveCompareBoxEl.hidden = true;
      showError("");
    } catch (err) {
      showError(`保存の削除失敗: ${err.message}`);
    }
  }

  function findSave(saveId) {
    return state.saves.find((item) => item.id === saveId) || null;
  }

  function compareSaved() {
    const leftId = saveListEl && saveListEl.value;
    const rightId = saveCompareEl && saveCompareEl.value;
    if (!leftId || !rightId) {
      showError("比べる保存を2つ選んでください");
      return;
    }
    if (leftId === rightId) {
      showError("違う保存を2つ選んでください");
      return;
    }
    const left = findSave(leftId);
    const right = findSave(rightId);
    if (!left || !right) {
      showError("保存一覧を読み直せませんでした");
      return;
    }
    if (!saveCompareBoxEl || !saveCompareTableEl) {
      console.error("sim compare box missing");
      return;
    }
    const rows = [
      ["名前", left.title, right.title],
      ["ソース", sourceLabel((left.summary || {}).source || (left.inputs || {}).source, (left.inputs || {}).experiment_id), sourceLabel((right.summary || {}).source || (right.inputs || {}).source, (right.inputs || {}).experiment_id)],
      ["期間", (left.summary || {}).period || "-", (right.summary || {}).period || "-"],
      ["リターン", pct((left.summary || {}).return), pct((right.summary || {}).return)],
      ["最大下落", pct((left.summary || {}).max_drawdown), pct((right.summary || {}).max_drawdown)],
      ["勝ち", `${(left.summary || {}).wins ?? 0} / ${(left.summary || {}).n ?? 0}`, `${(right.summary || {}).wins ?? 0} / ${(right.summary || {}).n ?? 0}`],
      ["増減", yen((left.summary || {}).pnl), yen((right.summary || {}).pnl)],
    ];
    saveCompareTableEl.innerHTML = `
      <thead><tr><th></th><th>${escapeHtml(left.title)}</th><th>${escapeHtml(right.title)}</th></tr></thead>
      <tbody>
        ${rows.map(([label, a, b]) => `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(a)}</td><td>${escapeHtml(b)}</td></tr>`).join("")}
      </tbody>
    `;
    saveCompareBoxEl.hidden = false;
    showError("");
  }

  function gateNarrowNote(data) {
    if (!data || data.mode === "long") return "";
    if (!(data.filters || []).length) return "";
    if (data.gate_passed == null) return "";
    const passed = Number(data.gate_passed).toLocaleString("ja-JP");
    const bits = data.universe != null
      ? [`ユニバース ${Number(data.universe).toLocaleString("ja-JP")} → 門通過 ${passed}件`]
      : [`門通過 ${passed}件`];
    if (data.scored != null && Number(data.scored) < Number(data.gate_passed)) {
      bits.push(`点数あり ${Number(data.scored).toLocaleString("ja-JP")}件`);
    }
    return bits.join(" / ");
  }

  function render(data) {
    lastSim = data || null;
    const freshness = data.freshness || {};
    if (warnEl) {
      warnEl.hidden = !(freshness.message || data.lookback_note);
      warnEl.textContent = [data.lookback_note, freshness.message].filter(Boolean).join(" ");
    }
    if (data.as_of && asOfEl && !asOfEl.value) asOfEl.value = data.as_of;
    const modeLine = strategyLabel(data);
    const savedPrefix = state.openedSave && state.openedSave.title ? `保存: ${state.openedSave.title}  /  ` : "";
    const narrow = gateNarrowNote(data);
    state.gatePassedNote = narrow ? `→ ${Number(data.gate_passed).toLocaleString("ja-JP")}件` : "";
    if (state.gatePanel) state.gatePanel.refresh();
    if (summaryEl) {
      const narrowBit = narrow ? `  /  ${narrow}` : "";
      if (data.mode === "long") {
        summaryEl.textContent = data.cash
          ? `${savedPrefix}${modeLine}  /  ${data.cash_note || `${data.as_of} ～ ${data.end_date} は条件を満たす銘柄がなく、現金のままです。`}`
          : data.take_profit === false
            ? `${savedPrefix}${modeLine}  /  ${data.as_of} ～ ${data.end_date}、目標では売らず、${data.horizon}営業日で売る。売れた枠に別銘柄を入れます。`
            : `${savedPrefix}${modeLine}  /  ${data.as_of} ～ ${data.end_date}、目標か損切まで保有。売れた枠に別銘柄を入れます。`;
      } else {
        const holdNote = data.take_profit === false ? "目標では売らず、損切か保有日数で売ります。" : "";
        summaryEl.textContent = data.cash
          ? `${savedPrefix}${modeLine}${narrowBit}  /  ${data.cash_note || `予測日 ${data.as_of} は条件を満たす銘柄がなく、現金のままです。`}`
          : `${savedPrefix}${modeLine}${narrowBit}  /  予測日 ${data.as_of} の数字で翌営業日 ${data.entry_date} に買い、${data.end_date} まで見ます。${holdNote}`;
      }
    }
    if (rangeEl) rangeEl.textContent = `${data.entry_date || "-"} ～ ${data.end_date || "-"}`;
    const items = [
      ["最終残高", yen(data.equity_end)],
      ["増減", yen(data.pnl)],
      ["リターン", pct(data.return)],
      ["最大下落", pct(data.max_drawdown)],
      ["勝ち銘柄", `${data.wins ?? 0} / ${data.n ?? 0}`],
      ["開始資金", yen(data.cash_start)],
      ["リスク%", data.risk_points != null ? String(data.risk_points) : pct(data.risk_pct)],
      ["見送り", String(data.skipped ?? 0)],
      ["現金", data.cash ? "はい" : "いいえ"],
    ];
    if (cardsEl) {
      cardsEl.innerHTML = items.map(([label, value]) => (
        `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
      )).join("");
    }
    ensureChart();
    if (equitySeries) {
      equitySeries.setData((data.curve || []).map((row) => ({ time: row.date, value: row.total })));
      benchSeries.setData((data.curve || []).filter((row) => row.bench != null).map((row) => ({ time: row.date, value: row.bench })));
      chart.timeScale().fitContent();
    }
    if (!rowsEl) {
      console.error("sim rows missing");
      return;
    }
    const trades = data.trades || [];
    rowsEl.innerHTML = "";
    if (!trades.length) {
      rowsEl.innerHTML = `<tr><td colspan="12">${data.cash_note || "買わない（現金）。"}</td></tr>`;
    } else {
      trades.forEach((row) => {
        const tr = document.createElement("tr");
        const pnlClass = row.pnl > 0 ? "score-pos" : row.pnl < 0 ? "score-neg" : "";
        tr.innerHTML = `
          <td>${row.rank ?? ""}</td>
          <td>${row.code || ""}</td>
          <td>${row.co_name || ""}</td>
          <td>${row.s33_nm || ""}</td>
          <td>${row.entry_date || ""}</td>
          <td>${row.exit_date || ""}</td>
          <td>${yen(row.alloc)}</td>
          <td>${row.qty == null ? "-" : Number(row.qty).toLocaleString("ja-JP")}</td>
          <td>${yen(row.entry)}</td>
          <td>${yen(row.exit)}</td>
          <td>${reasonLabel(row.exit_reason)}</td>
          <td class="${pnlClass}">${yen(row.pnl)}</td>
        `;
        tr.addEventListener("click", () => {
          openChart(row);
        });
        rowsEl.appendChild(tr);
      });
    }
    if (!curveRowsEl) return;
    const days = data.curve || [];
    curveRowsEl.innerHTML = "";
    if (!days.length) {
      curveRowsEl.innerHTML = `<tr><td colspan="5">日次がありません</td></tr>`;
      return;
    }
    days.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${row.date}</td>
        <td>${yen(row.total)}</td>
        <td>${yen(row.cash)}</td>
        <td>${yen(row.positions)}</td>
        <td>${yen(row.bench)}</td>
      `;
      curveRowsEl.appendChild(tr);
    });
    loadChartHeight();
  }

  async function run(options) {
    const stampTitle = Boolean(options && options.stampTitle);
    showError("");
    state.openedSave = null;
    state.gatePassedNote = "";
    if (state.gatePanel) state.gatePanel.refresh();
    if (summaryEl) summaryEl.textContent = "計算中…";
    if (runEl) runEl.disabled = true;
    const q = state.source === "checks" ? indicatorQuery() : new URLSearchParams();
    q.set("source", state.source);
    if (state.source === "experiment" && state.experimentId) {
      q.set("experiment_id", state.experimentId);
    }
    q.set("mode", state.simMode);
    if (state.simMode === "long") {
      if (!startEl || !startEl.value || !endEl || !endEl.value) {
        showError("開始日と終了日を入れてください");
        if (runEl) runEl.disabled = false;
        return;
      }
      q.set("start", startEl.value);
      q.set("end", endEl.value);
    } else if (asOfEl && asOfEl.value) {
      q.set("as_of", asOfEl.value);
    }
    if (noTargetEl && noTargetEl.checked) {
      const days = Number(holdEl && holdEl.value);
      if (!Number.isFinite(days) || days < 1 || days > 250) {
        showError("保有日数は1から250の営業日にしてください");
        if (summaryEl) summaryEl.textContent = "";
        if (runEl) runEl.disabled = false;
        return;
      }
      q.set("take_profit", "0");
      q.set("horizon", String(days));
    }
    q.set("cash", String((cashEl && cashEl.value) || "1000000"));
    q.set("top", String((topEl && topEl.value) || "5"));
    q.set("risk_pct", String((riskEl && riskEl.value) || "1"));
    try {
      const data = await api(`/api/sim?${q.toString()}`);
      render(data);
      if (stampTitle && saveTitleEl) saveTitleEl.value = defaultSaveTitle(data);
    } catch (err) {
      showError(`実行失敗: ${err.message}`);
    } finally {
      if (runEl) runEl.disabled = false;
    }
  }

  if (sourceActiveEl) {
    sourceActiveEl.addEventListener("click", () => {
      setSource("active");
      run();
    });
  }
  if (sourceChecksEl) {
    sourceChecksEl.addEventListener("click", () => {
      setSource("checks");
      run();
    });
  }
  if (experimentEl) {
    experimentEl.addEventListener("change", () => {
      const expId = experimentEl.value;
      if (!expId) {
        setSource("active");
        run();
        return;
      }
      setSource("experiment", expId);
      run();
    });
  }
  if (simModeMonthEl) {
    simModeMonthEl.addEventListener("click", () => {
      setSimMode("month");
      run();
    });
  }
  if (simModeLongEl) {
    simModeLongEl.addEventListener("click", () => {
      setSimMode("long");
    });
  }
  if (noTargetEl) {
    noTargetEl.addEventListener("change", () => {
      syncHold();
    });
  }
  syncHold();
  if (runEl) runEl.addEventListener("click", () => run({ stampTitle: true }));
  const saveVersionEl = document.getElementById("save-version");
  if (saveVersionEl) {
    saveVersionEl.addEventListener("click", () => {
      saveVersion().catch((err) => {
        console.error("sim version save failed", err);
        showError(`版の保存失敗: ${err.message}`);
      });
    });
  }
  if (saveResultEl) saveResultEl.addEventListener("click", saveCurrent);
  if (saveOpenEl) saveOpenEl.addEventListener("click", openSaved);
  if (saveDeleteEl) saveDeleteEl.addEventListener("click", deleteSaved);
  if (saveCompareBtnEl) saveCompareBtnEl.addEventListener("click", compareSaved);

  async function boot() {
    try {
      loadChartHeight();
      bindSplit();
      const status = await api("/api/status");
      const strategy = await api("/api/strategy");
      const active = (strategy && strategy.active) || (status.active_strategy) || {};
      const activeSels = strategy.selections || active.selections || [];
      state.activeIds = activeSels.map((item) => item.id);
      state.activeParams = Object.fromEntries(activeSels.map((item) => [item.id, item.params || {}]));
      state.activeTitle = active.title || "";
      state.longStart = status.long_sim_start || "";
      state.longEnd = status.long_sim_end || "";
      if (startEl && state.longStart) startEl.value = state.longStart;
      if (endEl && state.longEnd) endEl.value = state.longEnd;
      setSimMode("month");
      if (statusEl) {
        statusEl.textContent = `日足 ${Number(status.counts.daily_bars || 0).toLocaleString()}本  /  最新 ${status.daily_bar_to || "-"}  /  採用 ${state.activeTitle || "-"}`;
        statusEl.textContent += autoSyncSuffix(status.auto_sync);
      }
      if (status.freshness && status.freshness.message && warnEl) {
        warnEl.hidden = false;
        warnEl.textContent = `${status.freshness.message} シミュの1か月は最新足から20営業日前です。`;
      }
      state.catalog = window.skabuIndPanel.bySurface(status.indicators || [], "score");
      state.gateCatalog = status.gates || [];
      state.groups = status.groups || [];
      state.adoptedSelections = activeSels;
      state.adoptedFilters = strategy.filters || active.filters || [];
      (strategy.presets || []).forEach((item) => {
        if (item && item.id) state.presetById[item.id] = item;
      });
      ((strategy.experiments && strategy.experiments.items) || []).forEach((item) => {
        if (item && item.id) state.presetById[item.id] = item;
      });
      try {
        state.prefs = await api("/api/ui-prefs");
      } catch (err) {
        console.warn("ui prefs load failed", err);
      }
      initSelection(state.catalog);
      if (state.prefs && state.prefs.gates != null) applyGateList(state.prefs.gates);
      else applyGateList(state.adoptedFilters);
      renderIndicatorBar();
      renderGateBar();
      setSource("active");
      await loadSaves();
      await run();
    } catch (err) {
      showError(`起動失敗: ${err.message}`);
    }
  }

  boot();
})();
