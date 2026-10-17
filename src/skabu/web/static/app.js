(() => {
  const searchEl = document.getElementById("search");
  const resultsEl = document.getElementById("results");
  const issueEl = document.getElementById("issue");
  const extEl = document.getElementById("ext-charts");
  const rangeEl = document.getElementById("range");
  const errorEl = document.getElementById("error");
  const statusEl = document.getElementById("status");
  const debugEl = document.getElementById("debug");
  const featuresEl = document.getElementById("features");
  const refPopEl = document.getElementById("ref-pop");
  const chartEl = document.getElementById("chart");
  const oscEl = document.getElementById("osc");
  const indBarEl = document.getElementById("ind-bar");
  const tfButtons = document.querySelectorAll(".tf button");

  const state = {
    code: new URLSearchParams(location.search).get("code") || "",
    tf: "daily",
    timer: null,
    paramTimer: null,
    catalog: [],
    groups: [],
    selected: {},
    prefs: { saved: false, items: null, defaults: {} },
    chart: null,
    candle: null,
    volume: null,
    overlays: [],
    osc: null,
    oscSeries: [],
    levelLines: [],
    refLines: [],
    levelTimes: [],
    simSpan: null,
  };

  const SIM_SPAN_KEYS = ["sim_from", "sim_to", "sim_exit"];
  const SIM_PAD_BARS = 20;
  const RIGHT_GAP_RATIO = 0.08;
  const RIGHT_GAP_MIN_PX = 40;
  const RIGHT_GAP_MAX_PX = 96;

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
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  function yen(value) {
    if (value == null || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
  }

  function formatArrivals(arrivals) {
    if (!arrivals || arrivals.target == null) return "";
    const pct = (value) => `${Math.round(Number(value) * 100)}%`;
    if (arrivals.stop == null || arrivals.time == null) {
      return `  /  目標が先 ${pct(arrivals.target)}（損切が先と、20日たってもどちらでもない、は次の学習のあと）`;
    }
    return `  /  目標が先 ${pct(arrivals.target)}  損切が先 ${pct(arrivals.stop)}  20日たってもどちらでもない ${pct(arrivals.time)}`;
  }

  function axisPrice(value) {
    if (value == null || Number.isNaN(Number(value))) return "";
    return Number(value).toFixed(1);
  }

  function pinnedLevels() {
    const url = new URLSearchParams(location.search);
    const out = {};
    ["close", "stop", "target"].forEach((key) => {
      if (!url.has(key) || url.get(key) === "") return;
      const num = Number(url.get(key));
      if (!Number.isNaN(num)) out[key] = num;
    });
    return out;
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
    debugEl.textContent = `request_id ${body.request_id || requestId}`;
    return body;
  }

  function enabledIds() {
    return state.catalog.filter((item) => state.selected[item.id] && state.selected[item.id].enabled).map((item) => item.id);
  }

  function indicatorQuery() {
    const params = new URLSearchParams();
    const ids = enabledIds();
    if (ids.length) params.set("ind", ids.join(","));
    ids.forEach((id) => {
      const values = (state.selected[id] && state.selected[id].params) || {};
      Object.entries(values).forEach(([name, value]) => {
        params.set(`${id}.${name}`, String(value));
      });
    });
    return params;
  }

  function initSelection(catalog) {
    const url = new URLSearchParams(location.search);
    const prefs = window.skabuIndPrefs;
    const prefsItems = (state.prefs && state.prefs.items) || {};
    const usePrefs = prefs && !prefs.urlHasIndicatorOverride(url) && Object.keys(prefsItems).length;
    catalog.forEach((item) => {
      prefs.applyItem(item, state.selected, prefsItems[item.id], url, usePrefs, (row) => row.enabled_by_default);
    });
  }

  function updatePrefNote() {
    const note = document.getElementById("ind-pref-note");
    if (!note || !window.skabuIndPrefs) return;
    note.textContent = window.skabuIndPrefs.noteText(state.prefs);
  }

  async function savePrefs() {
    try {
      const items = window.skabuIndPrefs.snapshot(state.catalog, state.selected);
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ items }) });
      updatePrefNote();
      if (state.indPanel) state.indPanel.refresh();
    } catch (err) {
      showError(`保存失敗: ${err.message}`);
    }
  }

  async function resetPrefs() {
    try {
      state.prefs = await api("/api/ui-prefs", { method: "DELETE" });
      state.catalog.forEach((item) => {
        const current = state.selected[item.id] || { enabled: item.enabled_by_default, params: {} };
        const params = {};
        item.params.forEach((spec) => {
          params[spec.name] = spec.default;
        });
        state.selected[item.id] = { enabled: Boolean(current.enabled), params };
      });
      renderIndicatorBar();
      updatePrefNote();
      reloadChart({ keepView: true });
    } catch (err) {
      showError(`初期化失敗: ${err.message}`);
    }
  }

  function renderIndicatorBar() {
    if (!window.skabuIndPanel) {
      console.error("indpanel missing");
      return;
    }
    state.indPanel = window.skabuIndPanel.mount({
      host: indBarEl,
      label: "インジケーター",
      noteId: "ind-pref-note",
      items: () => state.catalog,
      groups: () => state.groups,
      summary: () => window.skabuIndPanel.summarizeIndicators(state.catalog, state.selected),
      renderRow: (row, item) => {
        window.skabuIndPanel.fillIndicatorRow(row, item, state.selected[item.id], {
          onChange: () => {
            if (state.indPanel) state.indPanel.refresh();
            clearTimeout(state.paramTimer);
            state.paramTimer = setTimeout(() => reloadChart({ keepView: true }), 280);
          },
        });
      },
      onSave: savePrefs,
      onReset: resetPrefs,
      onNote: updatePrefNote,
    });
    updatePrefNote();
  }

  function renderResults(items) {
    resultsEl.innerHTML = "";
    if (!items.length) {
      resultsEl.hidden = true;
      return;
    }
    items.forEach((item, index) => {
      const li = document.createElement("li");
      li.dataset.code = item.code;
      if (index === 0) li.classList.add("active");
      li.innerHTML = `<span><span class="code">${item.code}</span> ${item.co_name || ""}</span><span class="mkt">${item.mkt_nm || ""}</span>`;
      li.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        selectIssue(item.code);
      });
      resultsEl.appendChild(li);
    });
    resultsEl.hidden = false;
  }

  async function search(q) {
    if (!q.trim()) {
      renderResults([]);
      return;
    }
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
      renderResults(data.items);
    } catch (err) {
      showError(`検索失敗: ${err.message}`);
    }
  }

  function ensureChart() {
    if (state.chart) return;
    if (typeof LightweightCharts === "undefined") {
      throw new Error("チャートライブラリの読み込みに失敗しました");
    }
    state.chart = LightweightCharts.createChart(chartEl, {
      layout: { background: { color: "#0e1117" }, textColor: "#d1d5db" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
      rightPriceScale: { borderColor: "#374151" },
      timeScale: { borderColor: "#374151", timeVisible: false },
      localization: { priceFormatter: axisPrice },
      autoSize: true,
    });
    state.candle = state.chart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
      priceFormat: { type: "price", precision: 1, minMove: 0.1 },
    });
    state.volume = state.chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    state.chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });
    state.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && state.osc) {
        state.osc.timeScale().setVisibleLogicalRange(range);
      }
      positionSimSpan();
      updateLevelLabels();
      if (window.skabuDraw) window.skabuDraw.redraw();
    });
    if (window.skabuDraw) {
      window.skabuDraw.bind(state.chart, state.candle, (message) => showError(message));
    }
    window.addEventListener("resize", () => {
      requestAnimationFrame(positionSimSpan);
    });
  }

  function ensureOsc() {
    if (state.osc) return;
    state.osc = LightweightCharts.createChart(oscEl, {
      layout: { background: { color: "#0e1117" }, textColor: "#d1d5db" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
      rightPriceScale: { borderColor: "#374151" },
      timeScale: { borderColor: "#374151", visible: false },
      autoSize: true,
    });
  }

  function captureView() {
    if (!state.chart) return null;
    try {
      const timeScale = state.chart.timeScale();
      const priceScale = state.chart.priceScale("right");
      const priceOpts = typeof priceScale.options === "function" ? priceScale.options() : {};
      let priceRange = null;
      if (typeof priceScale.getVisibleRange === "function") {
        priceRange = priceScale.getVisibleRange();
      }
      return {
        logical: timeScale.getVisibleLogicalRange(),
        time: timeScale.getVisibleRange(),
        autoScale: priceOpts.autoScale !== false,
        priceRange,
      };
    } catch (err) {
      console.warn("chart view capture failed", err);
      return null;
    }
  }

  function lockPriceScale() {
    if (!state.chart) return;
    try {
      state.chart.priceScale("right").applyOptions({ autoScale: false });
    } catch (err) {
      console.warn("chart price lock failed", err);
    }
  }

  function restoreView(view) {
    if (!state.chart || !view) return;
    try {
      const timeScale = state.chart.timeScale();
      if (view.time) timeScale.setVisibleRange(view.time);
      else if (view.logical) timeScale.setVisibleLogicalRange(view.logical);
      const priceScale = state.chart.priceScale("right");
      if (view.priceRange && typeof priceScale.setVisibleRange === "function") {
        priceScale.setVisibleRange(view.priceRange);
      }
      priceScale.applyOptions({ autoScale: false });
      if (state.osc) {
        const range = timeScale.getVisibleLogicalRange();
        if (range) state.osc.timeScale().setVisibleLogicalRange(range);
      }
    } catch (err) {
      console.warn("chart view restore failed", err);
    }
  }

  function applySavedView(view) {
    restoreView(view);
    requestAnimationFrame(() => restoreView(view));
  }

  function resetPriceLock() {
    if (!state.chart) return;
    try {
      state.chart.priceScale("right").applyOptions({ autoScale: true });
    } catch (err) {
      console.warn("chart price lock reset failed", err);
    }
  }

  function clearOverlays() {
    state.overlays.forEach((series) => {
      try { state.chart.removeSeries(series); } catch (err) { /* ignore */ }
    });
    state.overlays = [];
    state.oscSeries.forEach((series) => {
      try { state.osc.removeSeries(series); } catch (err) { /* ignore */ }
    });
    state.oscSeries = [];
    if (state.candle) state.candle.setMarkers([]);
  }

  function clearLevelLines() {
    (state.levelLines || []).forEach((item) => {
      const series = item && item.series ? item.series : item;
      try { state.chart.removeSeries(series); } catch (err) { /* ignore */ }
    });
    state.levelLines = [];
    state.levelTimes = [];
  }

  function timeKey(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    if (typeof value === "object" && value.year) {
      const m = String(value.month).padStart(2, "0");
      const d = String(value.day).padStart(2, "0");
      return `${value.year}-${m}-${d}`;
    }
    return String(value);
  }

  function visibleMidTime() {
    const times = state.levelTimes || [];
    if (!times.length) return null;
    let fromKey = null;
    let toKey = null;
    try {
      const range = state.chart && state.chart.timeScale().getVisibleRange();
      if (range) {
        fromKey = timeKey(range.from);
        toKey = timeKey(range.to);
      }
    } catch (err) {
      console.warn("level label range failed", err);
    }
    if (!fromKey || !toKey) return times[Math.floor(times.length / 2)];
    let start = 0;
    let end = times.length - 1;
    for (let i = 0; i < times.length; i += 1) {
      if (timeKey(times[i]) >= fromKey) {
        start = i;
        break;
      }
    }
    for (let i = times.length - 1; i >= 0; i -= 1) {
      if (timeKey(times[i]) <= toKey) {
        end = i;
        break;
      }
    }
    if (end < start) return times[Math.floor(times.length / 2)];
    return times[Math.floor((start + end) / 2)];
  }

  function updateLevelLabels() {
    if (!state.levelLines || !state.levelLines.length) return;
    const midTime = visibleMidTime();
    if (!midTime) {
      console.warn("level label mid missing");
      return;
    }
    state.levelLines.forEach((item) => {
      if (!item || !item.series) return;
      try {
        item.series.setMarkers([
          {
            time: midTime,
            position: "inBar",
            color: item.color,
            shape: "circle",
            size: 0.5,
            text: item.title,
          },
        ]);
      } catch (err) {
        console.warn("level label update failed", item.title, err);
      }
    });
  }

  function applyLevels(levels, candles) {
    clearLevelLines();
    if (!state.chart || !candles || !candles.length || !levels) return;
    const times = candles.map((item) => item.time);
    state.levelTimes = times;
    const addLine = (value, color, title) => {
      if (value == null || Number.isNaN(Number(value))) return;
      const series = state.chart.addLineSeries({
        color,
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "",
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
      });
      series.setData(times.map((time) => ({ time, value: Number(value) })));
      state.levelLines.push({ series, color, title });
    };
    addLine(levels.stop, "#ef5350", "損切");
    addLine(levels.target, "#26a69a", "目標");
    updateLevelLabels();
  }

  const REF_DRAW_PCT = 0.08;
  const REF_PREF_KEY = "skabu.refLines";
  const REF_COLORS = {
    pullback_low: "#38bdf8",
    rebound_high: "#c084fc",
    high_20: "#f97316",
    low_20: "#fdba74",
    high_60: "#65a30d",
    low_60: "#86efac",
    high_52: "#facc15",
    low_52: "#bef264",
    sma_5: "#ffffff",
    sma_25: "#e2e8f0",
    sma_75: "#cbd5e1",
    sma_200: "#94a3b8",
    kijun: "#fb923c",
    cloud_top: "#22d3ee",
    cloud_bottom: "#2dd4bf",
    prev_high: "#f9a8d4",
    prev_low: "#f472b6",
    prev_close: "#fecdd3",
    round_below: "#c4b5fd",
    round_at: "#a78bfa",
    round_above: "#ddd6fe",
  };

  function clearRefLines() {
    (state.refLines || []).forEach((item) => {
      const series = item && item.series ? item.series : item;
      try { state.chart.removeSeries(series); } catch (err) { /* ignore */ }
    });
    state.refLines = [];
  }

  function formatRefDiff(item) {
    const yenDiff = Number(item.diff);
    const pct = Number(item.diff_pct) * 100;
    if (Number.isNaN(yenDiff) || Number.isNaN(pct)) return "";
    const sign = yenDiff > 0 ? "+" : "";
    return `${sign}${yen(yenDiff)}円 (${sign}${pct.toFixed(1)}%)`;
  }

  function refPref() {
    try {
      const raw = localStorage.getItem(REF_PREF_KEY);
      if (raw == null || raw === "1") return null;
      if (raw === "0") return { on: [] };
      const parsed = JSON.parse(raw);
      if (parsed && Array.isArray(parsed.on)) return parsed;
      console.warn("reference line preference ignored", raw);
    } catch (err) {
      console.warn("reference line preference unreadable", err);
    }
    return null;
  }

  function refItemOn(item) {
    const pref = refPref();
    if (pref) return pref.on.includes(item.id);
    const pct = Number(item.diff_pct);
    if (Number.isNaN(pct)) return false;
    return Math.abs(pct) <= REF_DRAW_PCT;
  }

  function saveRefOn(ids) {
    try {
      localStorage.setItem(REF_PREF_KEY, JSON.stringify({ on: ids }));
    } catch (err) {
      console.warn("reference line preference unsaved", err);
    }
    console.info("reference lines on=%s", ids.join(",") || "-");
  }

  function shownRefIds(rows) {
    return rows.filter((item) => refItemOn(item)).map((item) => item.id);
  }

  function paintCandleMarkers() {
    if (!state.candle) return;
    const refMarkers = referenceMarkers(state.refItems || [], state.refCandles || [], state.tf);
    state.candle.setMarkers([...(state.baseMarkers || []), ...refMarkers]);
  }

  function applyRefVisibility() {
    applyReferenceLines(state.refItems || [], state.refCandles || []);
    paintCandleMarkers();
    syncRefButtons();
  }

  function closeOtherPops(except) {
    document.querySelectorAll(".ind-pop-wrap").forEach((wrap) => {
      if (wrap === except) return;
      wrap.querySelectorAll(".ind-pop").forEach((pop) => {
        pop.hidden = true;
      });
      wrap.querySelectorAll("[aria-expanded]").forEach((btn) => {
        btn.setAttribute("aria-expanded", "false");
      });
    });
  }

  function ensureRefPop() {
    if (!refPopEl) {
      console.warn("reference pop missing");
      return null;
    }
    if (refPopEl.dataset.ready === "1") return refPopEl;
    refPopEl.className = "ind-pop-wrap";
    refPopEl.dataset.ready = "1";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ind-pop-btn";
    btn.id = "ref-pop-btn";
    btn.textContent = "意識ポイント";
    btn.setAttribute("aria-expanded", "false");
    const summary = document.createElement("span");
    summary.className = "ind-pop-summary";
    summary.id = "ref-summary";
    const pop = document.createElement("div");
    pop.className = "ind-pop";
    pop.hidden = true;
    pop.addEventListener("click", (ev) => ev.stopPropagation());
    const head = document.createElement("div");
    head.className = "ind-pop-head";
    const title = document.createElement("span");
    title.className = "ind-pop-title";
    title.textContent = "意識ポイント";
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "ind-pop-close";
    closeBtn.setAttribute("aria-label", "閉じる");
    closeBtn.textContent = "×";
    const body = document.createElement("div");
    body.className = "ind-pop-body";
    body.id = "ref-pop-body";
    head.append(title, closeBtn);
    pop.append(head, body);
    refPopEl.append(btn, summary, pop);
    const setOpen = (open) => {
      pop.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    };
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = pop.hidden;
      closeOtherPops(refPopEl);
      setOpen(willOpen);
    });
    closeBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      setOpen(false);
    });
    return refPopEl;
  }

  function syncRefButtons() {
    const rows = state.refItems || [];
    const shown = rows.filter((item) => refItemOn(item)).length;
    const summary = document.getElementById("ref-summary");
    if (summary) summary.textContent = rows.length ? `${shown}件` : "なし";
    const all = document.getElementById("ref-all");
    if (all) {
      const allOn = rows.length > 0 && shown === rows.length;
      all.checked = allOn;
      all.indeterminate = shown > 0 && !allOn;
    }
    document.querySelectorAll("#ref-pop-body input[data-ref-id]").forEach((input) => {
      const item = rows.find((row) => row.id === input.dataset.refId);
      if (!item) return;
      input.checked = refItemOn(item);
    });
  }

  function renderReferences(items) {
    const host = ensureRefPop();
    const body = document.getElementById("ref-pop-body");
    if (!host || !body) return;
    const rows = Array.isArray(items) ? items.filter((item) => item && item.price != null && item.label) : [];
    body.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "ref-empty";
      empty.textContent = "銘柄を開くと値段が出ます";
      body.appendChild(empty);
      console.warn("reference levels empty", items);
      syncRefButtons();
      return;
    }
    const allLabel = document.createElement("label");
    allLabel.className = "ref-check";
    const all = document.createElement("input");
    all.type = "checkbox";
    all.id = "ref-all";
    const allText = document.createElement("span");
    allText.textContent = "すべて";
    allLabel.append(all, allText);
    all.addEventListener("change", () => {
      saveRefOn(all.checked ? rows.map((item) => item.id) : []);
      applyRefVisibility();
    });
    body.appendChild(allLabel);
    rows.forEach((item) => {
      const label = document.createElement("label");
      label.className = "ref-check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.refId = item.id;
      const swatch = document.createElement("span");
      swatch.className = "ref-swatch";
      swatch.style.background = REF_COLORS[item.id] || "#94a3b8";
      const when = item.date ? ` ${item.date}` : "";
      const text = document.createElement("span");
      text.textContent = `${item.label}${when}  ${yen(item.price)}  ${formatRefDiff(item)}`;
      label.append(input, swatch, text);
      input.addEventListener("change", () => {
        const ids = new Set(shownRefIds(rows));
        if (input.checked) ids.add(item.id);
        else ids.delete(item.id);
        saveRefOn([...ids]);
        applyRefVisibility();
      });
      body.appendChild(label);
    });
    syncRefButtons();
  }

  function applyReferenceLines(items, candles) {
    clearRefLines();
    if (!state.chart || !candles || !candles.length || !Array.isArray(items)) return;
    const times = candles.map((item) => item.time);
    items.forEach((item) => {
      const pct = Number(item.diff_pct);
      if (item.price == null || Number.isNaN(pct)) {
        console.warn("reference line skipped", item);
        return;
      }
      if (!refItemOn(item)) return;
      const color = REF_COLORS[item.id] || "#94a3b8";
      const series = state.chart.addLineSeries({
        color,
        lineWidth: 1,
        lineStyle: 0,
        priceLineVisible: false,
        lastValueVisible: true,
        title: item.label || "",
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
      });
      series.setData(times.map((time) => ({ time, value: Number(item.price) })));
      state.refLines.push({ series, color, title: item.label || "" });
    });
  }

  function referenceMarkers(items, candles, tf) {
    if (tf !== "daily" || !Array.isArray(items) || !candles) return [];
    const times = new Set(candles.map((item) => item.time));
    const markers = [];
    items.forEach((item) => {
      if (!item.marker || !item.date || !refItemOn(item)) return;
      if (!times.has(item.date)) {
        console.warn("reference marker date missing", item.id, item.date);
        return;
      }
      const down = item.marker === "押";
      markers.push({
        time: item.date,
        position: down ? "belowBar" : "aboveBar",
        color: down ? REF_COLORS.pullback_low : REF_COLORS.rebound_high,
        shape: down ? "arrowUp" : "arrowDown",
        text: item.marker,
      });
    });
    return markers;
  }

  function applyIndicators(indicators) {
    clearOverlays();
    const failed = indicators.filter((item) => item.error);
    if (failed.length) {
      showError(failed.map((item) => `${item.title}: ${item.error}`).join(" / "));
    }
    const oscNeeded = indicators.some((item) => item.pane === "oscillator" && !item.error);
    oscEl.hidden = !oscNeeded;
    if (oscNeeded) ensureOsc();
    const markers = [];
    const bits = [];
    indicators.forEach((item) => {
      if (item.error) return;
      if (item.features && item.features.score != null) {
        const sign = item.features.score > 0 ? "+" : "";
        bits.push(`${item.title} ${sign}${item.features.score}`);
      }
      (item.overlays || []).forEach((overlay) => {
        const target = overlay.pane === "oscillator" ? state.osc : state.chart;
        if (!target) return;
        const series = target.addLineSeries({
          color: overlay.color,
          lineWidth: overlay.width || 2,
          lineStyle: overlay.style === "dashed" ? 2 : 0,
          priceLineVisible: false,
          lastValueVisible: !String(overlay.id).includes("_over"),
        });
        series.setData(overlay.points);
        if (overlay.pane === "oscillator") state.oscSeries.push(series);
        else state.overlays.push(series);
      });
      (item.markers || []).forEach((marker) => markers.push(marker));
    });
    featuresEl.textContent = bits.join("   /   ");
    if (state.osc && oscNeeded) {
      const range = state.chart.timeScale().getVisibleLogicalRange();
      if (range) state.osc.timeScale().setVisibleLogicalRange(range);
    }
    return markers;
  }

  function readSimSpan() {
    const url = new URLSearchParams(location.search);
    const from = url.get("sim_from");
    const to = url.get("sim_to");
    if (!from || !to) return null;
    return { from, to, exit: url.get("sim_exit") || to };
  }

  function keepSimSpanParams(params) {
    const current = new URLSearchParams(location.search);
    SIM_SPAN_KEYS.forEach((key) => {
      const value = current.get(key);
      if (value) params.set(key, value);
      else params.delete(key);
    });
  }

  function nearestTime(times, target) {
    if (!times.length || !target) return null;
    if (target <= times[0]) return times[0];
    if (target >= times[times.length - 1]) return times[times.length - 1];
    let best = times[0];
    for (let i = 0; i < times.length; i += 1) {
      if (times[i] === target) return times[i];
      if (times[i] < target) best = times[i];
      else return best;
    }
    return best;
  }

  function fitContentWithRightGap() {
    const timeScale = state.chart.timeScale();
    try {
      timeScale.fitContent();
      const range = timeScale.getVisibleLogicalRange();
      const plotWidth = typeof timeScale.width === "function" ? timeScale.width() : chartEl.clientWidth;
      const mid = range ? Math.floor((range.from + range.to) / 2) : 0;
      const left = range && typeof timeScale.logicalToCoordinate === "function"
        ? timeScale.logicalToCoordinate(mid)
        : null;
      const right = range && typeof timeScale.logicalToCoordinate === "function"
        ? timeScale.logicalToCoordinate(mid + 1)
        : null;
      const spacing = left == null || right == null ? 0 : right - left;
      if (!range || !(spacing > 0) || !(plotWidth > 0)) {
        console.warn("chart right gap skipped", { range, spacing, plotWidth });
        return;
      }
      const gapPx = Math.min(RIGHT_GAP_MAX_PX, Math.max(RIGHT_GAP_MIN_PX, plotWidth * RIGHT_GAP_RATIO));
      timeScale.setVisibleLogicalRange({ from: range.from, to: range.to + gapPx / spacing });
    } catch (err) {
      console.warn("chart right gap failed", err);
    }
  }

  function zoomToSimSpan(candles) {
    const span = readSimSpan();
    if (!span || !state.chart || !candles || !candles.length) return false;
    const times = candles.map((item) => item.time);
    const fromT = nearestTime(times, span.from);
    const toT = nearestTime(times, span.to);
    if (!fromT || !toT) {
      console.warn("sim span times missing", span);
      return false;
    }
    const i0 = times.indexOf(fromT);
    const i1 = times.indexOf(toT);
    const start = Math.max(0, Math.min(i0, i1) - SIM_PAD_BARS);
    const end = Math.min(times.length - 1, Math.max(i0, i1) + SIM_PAD_BARS);
    try {
      state.chart.timeScale().setVisibleRange({ from: times[start], to: times[end] });
      return true;
    } catch (err) {
      console.warn("sim span zoom failed", err);
      return false;
    }
  }

  function positionSimSpan() {
    const el = document.getElementById("sim-span");
    if (!el) return;
    const span = state.simSpan;
    if (!span || !state.chart) {
      el.hidden = true;
      return;
    }
    try {
      const ts = state.chart.timeScale();
      const left = ts.timeToCoordinate(span.spanFrom);
      const right = ts.timeToCoordinate(span.spanTo);
      if (left == null || right == null) {
        console.warn("sim span coords missing", span);
        el.hidden = true;
        return;
      }
      const opts = typeof ts.options === "function" ? ts.options() : {};
      const spacing = opts.barSpacing || 6;
      const x1 = Math.min(left, right) - spacing / 2;
      const x2 = Math.max(left, right) + spacing / 2;
      el.hidden = false;
      el.style.left = `${x1}px`;
      el.style.width = `${Math.max(4, x2 - x1)}px`;
    } catch (err) {
      console.warn("sim span position failed", err);
      el.hidden = true;
    }
  }

  function applySimSpan(candles) {
    const raw = readSimSpan();
    const times = (candles || []).map((item) => item.time);
    if (!raw || !times.length) {
      state.simSpan = null;
      positionSimSpan();
      return [];
    }
    const spanFrom = nearestTime(times, raw.from);
    const spanTo = nearestTime(times, raw.to);
    const exitTime = nearestTime(times, raw.exit || raw.to);
    if (!spanFrom || !spanTo) {
      console.warn("sim span nearest missing", raw);
      state.simSpan = null;
      positionSimSpan();
      return [];
    }
    state.simSpan = { ...raw, spanFrom, spanTo, exitTime };
    requestAnimationFrame(positionSimSpan);
    const markers = [
      { time: spanFrom, position: "belowBar", color: "#5eead4", shape: "arrowUp", text: "買" },
    ];
    if (exitTime && exitTime !== spanFrom) {
      markers.push({ time: exitTime, position: "aboveBar", color: "#fbbf24", shape: "arrowDown", text: "決" });
    }
    return markers;
  }

  function renderExternalCharts(code, links) {
    extEl.replaceChildren();
    const items = Array.isArray(links) ? links.filter((item) => item && item.url && item.label) : [];
    if (!items.length) {
      extEl.hidden = true;
      console.warn("public chart links unavailable", code, links);
      return;
    }
    for (const item of items) {
      const anchor = document.createElement("a");
      anchor.className = "ghost-link";
      anchor.href = item.url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = item.label;
      extEl.appendChild(anchor);
    }
    extEl.hidden = false;
  }

  async function loadChart(code, tf, opts) {
    showError("");
    const extra = indicatorQuery();
    extra.set("tf", tf);
    const data = await api(`/api/chart/${encodeURIComponent(code)}?${extra.toString()}`);
    ensureChart();
    const keepView = Boolean(opts && opts.keepView) && state.code === data.issue.code && state.tf === tf;
    const savedView = keepView ? captureView() : null;
    if (savedView) lockPriceScale();
    state.code = data.issue.code;
    state.tf = tf;
    const params = new URLSearchParams(location.search);
    params.set("code", state.code);
    params.set("tf", tf);
    const ind = indicatorQuery();
    if (ind.get("ind")) params.set("ind", ind.get("ind"));
    else params.delete("ind");
    ["ind", ...[...ind.keys()].filter((key) => key.includes("."))].forEach((key) => {
      if (key === "ind") return;
      if (ind.has(key)) params.set(key, ind.get(key));
    });
    [...params.keys()].forEach((key) => {
      if (key.includes(".") && !ind.has(key)) params.delete(key);
    });
    const pinned = params.get("code") === data.issue.code ? pinnedLevels() : {};
    ["close", "stop", "target"].forEach((key) => {
      if (pinned[key] != null) params.set(key, String(pinned[key]));
      else params.delete(key);
    });
    keepSimSpanParams(params);
    history.replaceState(null, "", `?${params.toString()}`);
    issueEl.textContent = `${data.issue.code}  ${data.issue.co_name || ""}  ${data.issue.mkt_nm || ""}`;
    renderExternalCharts(data.issue.code, data.links);
    const lastClose = data.candles.length ? data.candles[data.candles.length - 1].close : null;
    const lv = { ...(data.levels || {}), ...pinned };
    const levelNote = lv.note ? `  /  ${lv.note}` : "";
    const span = readSimSpan();
    const spanNote = span ? `  /  シミュ ${span.from} ～ ${span.to}` : "";
    const arrivalNote = formatArrivals(data.arrivals);
    rangeEl.textContent = data.count
      ? `${tf === "weekly" ? "週足" : "日足"} ${data.count}本  ${data.from} ～ ${data.to}  /  終値 ${yen(lastClose)}  損切 ${yen(lv.stop)}  目標 ${yen(lv.target)}${arrivalNote}${levelNote}${spanNote}`
      : "表示できる足がありません";
    state.candle.setData(data.candles);
    state.volume.setData(data.volumes);
    const indMarkers = applyIndicators(data.indicators || []);
    applyLevels(lv, data.candles);
    const simMarkers = applySimSpan(data.candles);
    state.refItems = data.references || [];
    state.refCandles = data.candles;
    state.baseMarkers = [...(indMarkers || []), ...simMarkers];
    renderReferences(state.refItems);
    applyRefVisibility();
    if (savedView) {
      applySavedView(savedView);
    } else if (zoomToSimSpan(data.candles)) {
      resetPriceLock();
    } else {
      resetPriceLock();
      fitContentWithRightGap();
    }
    requestAnimationFrame(() => {
      positionSimSpan();
      updateLevelLabels();
    });
    if (window.skabuDraw) window.skabuDraw.show(state.code, state.tf, data.candles);
    if (!data.count) {
      showError("この銘柄の足データがありません。同期漏れの日付がないか status を確認してください。");
    }
  }

  function reloadChart(opts) {
    if (!state.code) return;
    loadChart(state.code, state.tf, opts).catch((err) => showError(`チャート取得失敗: ${err.message}`));
  }

  function selectIssue(code) {
    searchEl.value = code;
    resultsEl.hidden = true;
    loadChart(code, state.tf).catch((err) => showError(`チャート取得失敗: ${err.message}`));
  }

  searchEl.addEventListener("input", () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => search(searchEl.value), 220);
  });

  searchEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      const first = resultsEl.querySelector("li");
      if (first) selectIssue(first.dataset.code);
    }
    if (ev.key === "Escape") resultsEl.hidden = true;
  });

  document.addEventListener("click", (ev) => {
    if (!ev.target.closest(".search-wrap")) resultsEl.hidden = true;
  });

  tfButtons.forEach((button) => {
    button.addEventListener("click", () => {
      tfButtons.forEach((item) => item.classList.toggle("active", item === button));
      resultsEl.hidden = true;
      state.tf = button.dataset.tf;
      reloadChart();
    });
  });

  ensureRefPop();

  async function boot() {
    try {
      const status = await api("/api/status");
      const from = status.daily_bar_from || "-";
      const to = status.daily_bar_to || "-";
      statusEl.textContent = `日足 ${status.counts.daily_bars.toLocaleString()}本  ${from} ～ ${to}  /  銘柄 ${status.counts.listed_master.toLocaleString()}${autoSyncSuffix(status.auto_sync)}`;
      const all = status.indicators || (await api("/api/indicators")).items;
      state.groups = status.groups || [];
      state.catalog = window.skabuIndPanel.bySurface(all, "chart");
      try {
        state.prefs = await api("/api/ui-prefs");
      } catch (err) {
        console.warn("ui prefs load failed", err);
      }
      initSelection(state.catalog);
      renderIndicatorBar();
      const params = new URLSearchParams(location.search);
      state.tf = params.get("tf") === "weekly" ? "weekly" : "daily";
      tfButtons.forEach((button) => button.classList.toggle("active", button.dataset.tf === state.tf));
      const start = state.code || status.default_code;
      searchEl.value = start;
      await loadChart(start, state.tf);
    } catch (err) {
      showError(`起動失敗: ${err.message}`);
    }
  }

  window.__skabuDebug = {
    getView: captureView,
    setLogicalRange(from, to) {
      if (!state.chart) return;
      state.chart.timeScale().setVisibleLogicalRange({ from, to });
    },
    levelMid: visibleMidTime,
  };

  boot();
})();
