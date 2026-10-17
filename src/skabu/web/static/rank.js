(() => {
  const indBarEl = document.getElementById("ind-bar");
  const gateBarEl = document.getElementById("gate-bar");
  const rowsEl = document.getElementById("rows");
  const headsRowEl = document.getElementById("rank-heads");
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const errorEl = document.getElementById("error");
  const statusEl = document.getElementById("status");
  const debugEl = document.getElementById("debug");
  const marketEl = document.getElementById("market");
  const minvolEl = document.getElementById("minvol");
  const topEl = document.getElementById("top");
  const stopMultEl = document.getElementById("stop-mult");
  const targetMultEl = document.getElementById("target-mult");
  const runEl = document.getElementById("run");
  const adoptedEl = document.getElementById("adopted");
  const OP_SYMBOL = { eq: "=", lt: "<", lte: "<=", gt: ">", gte: ">=" };
  const OP_FROM_SYMBOL = { "<": "lt", "<=": "lte", ">": "gt", ">=": "gte", "=": "eq", "==": "eq" };
  const FLT_RE = /^([a-z0-9_]+)\.([a-z0-9_]+)(<=|>=|==|=|<|>)(.+)$/i;

  const state = {
    catalog: [],
    gateCatalog: [],
    groups: [],
    selected: {},
    gates: {},
    prefs: { saved: false, items: null, gates: null, defaults: {} },
    activeIds: [],
    activeParams: {},
    adoptedFilters: [],
    gatesReady: false,
    useAdopted: false,
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
      console.error("rank error element missing", message);
      return;
    }
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
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

  async function loadExitLevels() {
    const body = await api("/api/exit-levels");
    if (stopMultEl && body.stop_mult != null) stopMultEl.value = body.stop_mult;
    if (targetMultEl && body.target_mult != null) targetMultEl.value = body.target_mult;
  }

  async function saveExitLevels() {
    const body = await api("/api/exit-levels", {
      method: "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        stop_mult: Number(stopMultEl && stopMultEl.value),
        target_mult: Number(targetMultEl && targetMultEl.value),
      }),
    });
    if (stopMultEl && body.stop_mult != null) stopMultEl.value = body.stop_mult;
    if (targetMultEl && body.target_mult != null) targetMultEl.value = body.target_mult;
    return body;
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

  function parseFlt(raw) {
    const out = [];
    String(raw || "").split(",").forEach((chunk) => {
      const text = chunk.trim();
      if (!text) return;
      const match = text.match(FLT_RE);
      if (!match) return;
      const op = OP_FROM_SYMBOL[match[3]];
      if (!op) return;
      const value = Number(match[4]);
      if (Number.isNaN(value)) return;
      out.push({ id: match[1], feature: match[2], op, value });
    });
    return out;
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
    if (state.gatesReady && !state.useAdopted) {
      params.set("flt", encodeFlt(enabledGates()));
    }
    return params;
  }

  function strategyEnabled(item) {
    if (state.activeIds.length) return state.activeIds.includes(item.id);
    return item.id === "ema" || item.id === "rsi";
  }

  function initSelection(catalog) {
    const rawUrl = new URLSearchParams(location.search);
    const url = window.skabuIndPrefs.filterIndicatorUrl
      ? window.skabuIndPrefs.filterIndicatorUrl(rawUrl, catalog)
      : rawUrl;
    const prefs = window.skabuIndPrefs;
    const prefsItems = (state.prefs && state.prefs.items) || {};
    const usePrefs = prefs && !prefs.urlHasIndicatorOverride(url) && Object.keys(prefsItems).length;
    catalog.forEach((item) => {
      prefs.applyItem(item, state.selected, prefsItems[item.id], url, usePrefs, strategyEnabled);
      if (usePrefs) return;
      item.params.forEach((spec) => {
        const key = `${item.id}.${spec.name}`;
        if (url.has(key)) return;
        if (state.activeParams[item.id] && state.activeParams[item.id][spec.name] != null) {
          state.selected[item.id].params[spec.name] = state.activeParams[item.id][spec.name];
        }
      });
    });
    if (url.get("market")) marketEl.value = url.get("market");
    if (url.get("min_avg_volume")) minvolEl.value = url.get("min_avg_volume");
    if (url.get("top")) topEl.value = url.get("top");
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

  function initGates() {
    const url = new URLSearchParams(location.search);
    if (url.has("flt")) {
      applyGateList(parseFlt(url.get("flt")));
      state.useAdopted = false;
    } else if (state.prefs && state.prefs.gates != null) {
      applyGateList(state.prefs.gates);
      state.useAdopted = false;
    } else {
      applyGateList(state.adoptedFilters);
      state.useAdopted = !enabledIds().length;
    }
    state.gatesReady = true;
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
    } catch (err) {
      showError(`保存失敗: ${err.message}`);
    }
  }

  async function saveGatePrefs() {
    try {
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ gates: enabledGates() }) });
      updatePrefNote();
      updateGateNote();
    } catch (err) {
      showError(`門の保存失敗: ${err.message}`);
    }
  }

  async function resetPrefs() {
    try {
      catalogDefaultsForRank();
      const items = window.skabuIndPrefs.snapshot(state.catalog, state.selected);
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ items }) });
      renderIndicatorBar();
      updatePrefNote();
    } catch (err) {
      showError(`初期化失敗: ${err.message}`);
    }
  }

  async function resetGatePrefs() {
    try {
      applyGateList(state.adoptedFilters);
      state.useAdopted = !enabledIds().length;
      state.prefs = await api("/api/ui-prefs", { method: "PUT", body: JSON.stringify({ gates: enabledGates() }) });
      renderGateBar();
      updateGateNote();
    } catch (err) {
      showError(`門の初期化失敗: ${err.message}`);
    }
  }

  function catalogDefaultsForRank() {
    state.catalog.forEach((item) => {
      const params = {};
      item.params.forEach((spec) => {
        params[spec.name] = spec.default;
      });
      state.selected[item.id] = { enabled: strategyEnabled(item), params };
    });
  }

  function renderIndicatorBar() {
    if (!window.skabuIndPanel || !indBarEl) {
      console.error("rank score panel missing");
      return;
    }
    state.scorePanel = window.skabuIndPanel.mount({
      host: indBarEl,
      label: "点",
      noteId: "ind-pref-note",
      items: () => state.catalog,
      groups: () => state.groups,
      summary: () => window.skabuIndPanel.summarizeIndicators(state.catalog, state.selected),
      renderRow: (row, item) => {
        window.skabuIndPanel.fillIndicatorRow(row, item, state.selected[item.id], {
          onChange: () => {
            state.useAdopted = false;
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

  function updateGateNote() {
    const note = document.getElementById("gate-pref-note");
    if (!note) return;
    if (state.prefs && state.prefs.gates != null && state.prefs.saved_at) {
      note.textContent = `保存済 ${state.prefs.saved_at}`;
    } else {
      note.textContent = "未保存（採用の門）";
    }
  }

  function renderGateBar() {
    if (!window.skabuIndPanel || !gateBarEl) {
      console.error("rank gate panel missing");
      return;
    }
    state.gatePanel = window.skabuIndPanel.mount({
      host: gateBarEl,
      label: "門",
      noteId: "gate-pref-note",
      items: () => state.gateCatalog,
      groups: () => state.groups,
      summary: () => {
        const base = window.skabuIndPanel.summarizeGates(state.gateCatalog, state.gates);
        return state.gatePassedNote ? `${base} ${state.gatePassedNote}` : base;
      },
      renderRow: (row, item) => {
        const key = gateKey(item);
        if (!state.gates[key]) {
          state.gates[key] = { enabled: false, op: item.default_op, value: item.default_value };
        }
        window.skabuIndPanel.fillGateRow(row, item, state.gates[key], {
          onChange: () => {
            state.useAdopted = false;
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

  function scoreClass(value) {
    if (value == null) return "";
    if (value > 0) return "score-pos";
    if (value < 0) return "score-neg";
    return "";
  }

  function fmt(value, digits = 1) {
    if (value == null || Number.isNaN(value)) return "-";
    return Number(value).toLocaleString("ja-JP", { maximumFractionDigits: digits });
  }

  function openChart(url) {
    let opened = null;
    try {
      opened = window.open(url, "skabu-chart");
    } catch (err) {
      console.error("chart window failed", err);
    }
    if (!opened) {
      console.error("chart window blocked", url);
      showError("チャート窓を開けませんでした。ポップアップを許可してください。");
    }
  }

  function chartHref(row) {
    const q = indicatorQuery();
    q.set("code", row.code);
    q.set("tf", "daily");
    if (row.close != null) q.set("close", String(row.close));
    if (row.stop != null) q.set("stop", String(row.stop));
    if (row.target != null) q.set("target", String(row.target));
    return `/?${q.toString()}`;
  }

  function scoreHead(id) {
    const found = state.catalog.find((item) => item.id === id);
    return found ? found.title : id;
  }

  function renderTable(data) {
    const ids = (data.selections || []).map((item) => item.id);
    const headerRow = headsRowEl || document.getElementById("rank-heads");
    if (!headerRow || !rowsEl) {
      console.error("rank table missing", { header: Boolean(headerRow), rows: Boolean(rowsEl), ids });
      throw new Error("ランキング表の見出しがありません");
    }
    headerRow.innerHTML = `
      <th>順位</th><th>コード</th><th>銘柄</th><th>市場</th><th>業種</th><th>終値</th><th>総合</th>
      ${ids.map((id) => `<th>${scoreHead(id)}</th>`).join("")}
      <th>損切</th><th>目標</th><th>RR</th><th>購入</th><th>根拠</th>
    `;
    rowsEl.innerHTML = "";
    if (!data.rows || !data.rows.length) {
      rowsEl.innerHTML = `<tr><td colspan="${11 + ids.length}">該当なし</td></tr>`;
      return;
    }
    data.rows.forEach((row) => {
      const tr = document.createElement("tr");
      const scoreCells = ids.map((id) => {
        const value = row.scores ? row.scores[id] : null;
        const text = value == null ? "-" : (value > 0 ? `+${value}` : String(value));
        return `<td class="${scoreClass(value)}">${text}</td>`;
      }).join("");
      tr.innerHTML = `
        <td>${row.rank}</td>
        <td>${row.code}</td>
        <td>${row.co_name || ""}</td>
        <td>${row.mkt_nm || ""}</td>
        <td>${row.s33_nm || ""}</td>
        <td>${fmt(row.close)}</td>
        <td class="${scoreClass(row.total)}">${row.total > 0 ? "+" : ""}${row.total}</td>
        ${scoreCells}
        <td>${fmt(row.stop)}</td>
        <td>${fmt(row.target)}</td>
        <td>${fmt(row.rr, 2)}</td>
        <td>${row.buyable ? "買う" : (row.skip_label || "見送り")}</td>
        <td class="reason">${row.reason || ""}</td>
      `;
      tr.addEventListener("click", () => {
        openChart(chartHref(row));
      });
      rowsEl.appendChild(tr);
    });
  }

  async function loadRank(refresh) {
    showError("");
    if (refresh) {
      try {
        await saveExitLevels();
      } catch (err) {
        console.warn("exit levels save failed", err);
        showError(`倍率の保存失敗: ${err.message}`);
        return;
      }
    }
    state.gatePassedNote = "";
    if (state.gatePanel) state.gatePanel.refresh();
    summaryEl.textContent = refresh ? "再計算中…" : "集計中…";
    runEl.disabled = true;
    const q = indicatorQuery();
    q.set("market", marketEl.value);
    q.set("min_avg_volume", minvolEl.value || "0");
    q.set("top", topEl.value || "50");
    if (refresh) q.set("refresh", "true");
    history.replaceState(null, "", `/rank?${q.toString()}`);
    try {
      const data = await api(`/api/rank?${q.toString()}`);
      const cacheNote = data.cached ? "（キャッシュ）" : `（${data.elapsed_ms}ms）`;
      const levelsNote = data.levels_note || "損切・目標は予測日終値の仮数字。執行は翌始値で確定します。";
      const bought = data.bought_n != null ? data.bought_n : (data.rows || []).filter((row) => row.buyable).length;
      const regimeNote = data.cash
        ? ` / 地合いオフ ${((data.regime || {}).reason) || ""}`
        : "";
      const filters = data.filters || [];
      const shown = (data.rows || []).length;
      const scored = data.scored;
      const passed = data.gate_passed != null
        ? data.gate_passed
        : (data.all_rows != null ? data.all_rows : scored);
      let narrow;
      if (filters.length && passed != null) {
        const bits = [`ユニバース ${Number(data.universe || 0).toLocaleString("ja-JP")} → 門通過 ${Number(passed).toLocaleString("ja-JP")}件`];
        if (scored != null && Number(scored) < Number(passed)) {
          bits.push(`点数あり ${Number(scored).toLocaleString("ja-JP")}件`);
        }
        bits.push(`表示 ${shown.toLocaleString("ja-JP")}件`);
        narrow = bits.join(" / ");
        state.gatePassedNote = `→ ${Number(passed).toLocaleString("ja-JP")}件`;
      } else {
        narrow = `対象 ${scored}銘柄 / ユニバース ${data.universe}`;
        state.gatePassedNote = "";
      }
      if (state.gatePanel) state.gatePanel.refresh();
      summaryEl.textContent = `予測日 ${data.as_of || "-"}  /  ${narrow} / 買う ${bought}  ${cacheNote}${regimeNote}  /  ${levelsNote}`;
      rangeEl.textContent = data.from_date ? `${data.from_date} ～ ${data.as_of}` : "";
      renderTable(data);
    } catch (err) {
      console.error("rank load failed", err);
      state.gatePassedNote = "";
      if (state.gatePanel) state.gatePanel.refresh();
      showError(`ランキング失敗: ${err.message}`);
      if (rowsEl) rowsEl.innerHTML = `<tr><td colspan="8">失敗</td></tr>`;
    } finally {
      runEl.disabled = false;
    }
  }

  function viewAdopted() {
    state.catalog.forEach((item) => {
      if (!state.selected[item.id]) return;
      state.selected[item.id].enabled = false;
    });
    applyGateList(state.adoptedFilters);
    state.useAdopted = true;
    if (typeof renderIndicatorBar === "function") {
      renderIndicatorBar();
    }
    if (typeof renderGateBar === "function") {
      renderGateBar();
    }
    loadRank(true);
  }

  runEl.addEventListener("click", () => loadRank(true));
  [stopMultEl, targetMultEl].forEach((input) => {
    if (!input) return;
    input.addEventListener("change", () => {
      saveExitLevels().catch((err) => {
        console.warn("exit levels save failed", err);
        showError(`倍率の保存失敗: ${err.message}`);
      });
    });
  });
  if (adoptedEl) {
    adoptedEl.addEventListener("click", viewAdopted);
  }
  const saveVersionEl = document.getElementById("save-version");
  if (saveVersionEl) {
    saveVersionEl.addEventListener("click", () => {
      saveVersion().catch((err) => {
        console.error("rank version save failed", err);
        showError(`版の保存失敗: ${err.message}`);
      });
    });
  }

  async function boot() {
    try {
      try {
        await loadExitLevels();
      } catch (err) {
        console.warn("exit levels load failed", err);
      }
      const status = await api("/api/status");
      const strategy = await api("/api/strategy");
      const activeSels = strategy.selections || (status.active_strategy && status.active_strategy.selections) || [];
      state.activeIds = activeSels.map((item) => item.id);
      state.activeParams = Object.fromEntries(activeSels.map((item) => [item.id, item.params || {}]));
      const fallbackTitle = "EMA + RSI";
      const title = (status.active_strategy && status.active_strategy.title) || fallbackTitle;
      statusEl.textContent = `日足 ${status.counts.daily_bars.toLocaleString()}本  ${status.daily_bar_from} ～ ${status.daily_bar_to}  /  採用 ${title}${autoSyncSuffix(status.auto_sync)}`;
      state.catalog = window.skabuIndPanel.bySurface(status.indicators || [], "score");
      state.gateCatalog = status.gates || [];
      state.groups = status.groups || [];
      state.adoptedFilters = (status.active_strategy && status.active_strategy.filters) || [];
      try {
        state.prefs = await api("/api/ui-prefs");
      } catch (err) {
        console.warn("ui prefs load failed", err);
      }
      initSelection(state.catalog);
      initGates();
      renderIndicatorBar();
      renderGateBar();
      await loadRank(false);
    } catch (err) {
      showError(`起動失敗: ${err.message}`);
    }
  }

  boot();
})();
