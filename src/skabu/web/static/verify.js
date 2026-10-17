(() => {
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const errorEl = document.getElementById("error");
  const statusEl = document.getElementById("status");
  const debugEl = document.getElementById("debug");
  const cardsEl = document.getElementById("cards");
  const rowsEl = document.getElementById("rows");
  const detailEl = document.getElementById("detail");
  const detailTitle = document.getElementById("detail-title");
  const detailRows = document.getElementById("detail-rows");
  const topEl = document.getElementById("top");
  const strideEl = document.getElementById("stride");
  const experimentEl = document.getElementById("experiment");
  const noteEl = document.getElementById("experiment-note");
  const runEl = document.getElementById("run");
  const saveEl = document.getElementById("save-verify");
  const stopMultEl = document.getElementById("stop-mult");
  const targetMultEl = document.getElementById("target-mult");
  const hzButtons = document.querySelectorAll("[data-hz]");

  const state = { data: null, hz: "20", versions: [] };

  function showError(message) {
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  async function api(path, options) {
    const res = await fetch(path, options || { headers: { Accept: "application/json" } });
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

  function post(path, payload) {
    return api(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function selectedVersion() {
    return state.versions.find((item) => item.id === experimentEl.value) || null;
  }

  function renderNote() {
    if (!noteEl) return;
    const item = selectedVersion();
    if (!item) {
      noteEl.textContent = "保存した版がありません。戦略画面で版を保存してください。";
      return;
    }
    const gates = Array.isArray(item.filters) ? item.filters.length : 0;
    noteEl.textContent = `${item.title || item.id}  門 ${gates}件`;
  }

  function syncSaveButton() {
    if (!saveEl) return;
    const shown = state.data && state.data.experiment && state.data.experiment.id;
    saveEl.disabled = !shown || shown !== experimentEl.value;
  }

  function pct(value) {
    if (value == null || Number.isNaN(value)) return "-";
    return `${(value * 100).toFixed(1)}%`;
  }

  function cls(value) {
    if (value == null) return "";
    if (value > 0) return "score-pos";
    if (value < 0) return "score-neg";
    return "";
  }

  function currentHorizon() {
    return (state.data && state.data.horizons && state.data.horizons[state.hz]) || null;
  }

  function renderCards() {
    const hz = currentHorizon();
    const s = hz && hz.summary;
    if (!s) {
      cardsEl.innerHTML = "";
      return;
    }
    const items = [
      ["勝率（プラス）", pct(s.win_rate)],
      ["勝率（TOPIX超）", pct(s.win_rate_vs_bench)],
      ["平均リターン", pct(s.avg_return)],
      ["TOPIX超過", pct(s.avg_excess)],
      ["最大DD", pct(s.max_drawdown)],
      ["トレード数", String(s.trades || 0)],
    ];
    cardsEl.innerHTML = items.map(([label, value]) => (
      `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
    )).join("");
  }

  function renderPeriods() {
    const hz = currentHorizon();
    rowsEl.innerHTML = "";
    if (!hz || !hz.periods || !hz.periods.length) {
      rowsEl.innerHTML = `<tr><td colspan="7">確定した予測がありません</td></tr>`;
      return;
    }
    hz.periods.forEach((period, index) => {
      const excess = period.period_return == null || period.bench_return == null
        ? null
        : period.period_return - period.bench_return;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${period.as_of}</td>
        <td>${period.entry_date}</td>
        <td>${period.exit_date}</td>
        <td>${period.complete}/${period.n}</td>
        <td class="${cls(period.period_return)}">${pct(period.period_return)}</td>
        <td class="${cls(period.bench_return)}">${pct(period.bench_return)}</td>
        <td class="${cls(excess)}">${pct(excess)}</td>
      `;
      tr.addEventListener("click", () => showDetail(period));
      if (index === hz.periods.length - 1) showDetail(period);
      rowsEl.appendChild(tr);
    });
  }

  function showDetail(period) {
    detailEl.hidden = false;
    detailTitle.textContent = `${period.as_of} の上位内訳（${period.horizon}日）`;
    detailRows.innerHTML = "";
    (period.trades || []).forEach((trade) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${trade.rank}</td>
        <td>${trade.code}</td>
        <td>${trade.co_name || ""}</td>
        <td>${trade.entry == null ? "-" : Number(trade.entry).toFixed(1)}</td>
        <td>${trade.exit == null ? "-" : Number(trade.exit).toFixed(1)}</td>
        <td>${trade.exit_reason === "stop" ? "損切" : trade.exit_reason === "target" ? "目標" : trade.exit_reason === "time" ? "時間" : "-"}</td>
        <td class="${cls(trade.return)}">${pct(trade.return)}</td>
        <td class="${cls(trade.excess)}">${pct(trade.excess)}</td>
      `;
      tr.addEventListener("click", () => {
        const url = `/?code=${encodeURIComponent(trade.code)}&tf=daily&ind=ema,rsi`;
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
      });
      detailRows.appendChild(tr);
    });
  }

  function renderAll() {
    const rules = (state.data && state.data.rules) || {};
    const cacheNote = state.data.cached ? "キャッシュ" : `${state.data.elapsed_ms}ms`;
    const picked = (state.data && state.data.experiment) || {};
    const title = picked.title || (selectedVersion() && selectedVersion().title) || "版なし";
    const gates = picked.filter_count != null
      ? picked.filter_count
      : ((selectedVersion() && selectedVersion().filters) || []).length;
    summaryEl.textContent = `${title}  門 ${gates}件  /  ロック ${state.data.locked_runs || 0}回  /  ${rules.entry || ""} → ${rules.exit || `${state.hz}日先着`}  /  ${cacheNote}`;
    renderNote();
    syncSaveButton();
    rangeEl.textContent = (state.data.as_of_dates || []).length
      ? `${state.data.as_of_dates[0]} ～ ${state.data.as_of_dates[state.data.as_of_dates.length - 1]}`
      : "";
    renderCards();
    renderPeriods();
  }

  function query(extra, withExperiment) {
    const q = new URLSearchParams();
    q.set("top", topEl.value || "20");
    q.set("stride", strideEl.value || "20");
    if (withExperiment && experimentEl && experimentEl.value) {
      q.set("experiment", experimentEl.value);
    }
    Object.entries(extra || {}).forEach(([key, value]) => q.set(key, value));
    return q;
  }

  async function loadVerify(refresh) {
    showError("");
    try {
      await saveExitLevels();
    } catch (err) {
      console.warn("exit levels save failed", err);
      showError(`倍率の保存失敗: ${err.message}`);
      return;
    }
    if (!experimentEl || !experimentEl.value) {
      showError("保存した版がありません。戦略画面で版を保存してください。");
      console.warn("verify skipped reason=no_experiment");
      syncSaveButton();
      return;
    }
    summaryEl.textContent = "検証中（ロック済み予測は書き換えません）…";
    runEl.disabled = true;
    if (saveEl) saveEl.disabled = true;
    try {
      const q = query(refresh ? { refresh: "true" } : {}, true);
      history.replaceState(null, "", `/verify?${q.toString()}&hz=${state.hz}`);
      state.data = await api(`/api/verify?${q.toString()}`);
      renderAll();
    } catch (err) {
      console.warn("verify failed", err);
      showError(`検証失敗: ${err.message}`);
      syncSaveButton();
    } finally {
      runEl.disabled = false;
    }
  }

  async function saveVerify() {
    const shown = state.data && state.data.experiment;
    const horizon = state.data && state.data.horizons && state.data.horizons["20"];
    const summary = horizon && horizon.summary;
    if (!shown || !summary || shown.id !== experimentEl.value) {
      showError("先に、選んだ版の検証を実行してください。");
      console.warn("verify save skipped", shown && shown.id, experimentEl && experimentEl.value);
      return;
    }
    const dates = state.data.as_of_dates || [];
    saveEl.disabled = true;
    try {
      await post("/api/verify/save", {
        experiment_id: shown.id,
        top: Number(topEl.value || 20),
        stride: Number(strideEl.value || 20),
        as_of_from: dates[0] || null,
        as_of_to: dates.length ? dates[dates.length - 1] : null,
        summary,
      });
      summaryEl.textContent = `${shown.title || shown.id} の20日の成績を保存しました`;
    } catch (err) {
      console.warn("verify save failed", err);
      showError(`保存失敗: ${err.message}`);
    } finally {
      syncSaveButton();
    }
  }

  hzButtons.forEach((button) => {
    button.addEventListener("click", () => {
      hzButtons.forEach((item) => item.classList.toggle("active", item === button));
      state.hz = button.dataset.hz;
      if (state.data) renderAll();
    });
  });
  runEl.addEventListener("click", () => loadVerify(true));
  [stopMultEl, targetMultEl].forEach((input) => {
    if (!input) return;
    input.addEventListener("change", () => {
      saveExitLevels().catch((err) => {
        console.warn("exit levels save failed", err);
        showError(`倍率の保存失敗: ${err.message}`);
      });
    });
  });
  if (saveEl) saveEl.addEventListener("click", () => saveVerify());
  if (experimentEl) {
    experimentEl.addEventListener("change", () => {
      renderNote();
      syncSaveButton();
    });
  }
  async function boot() {
    const params = new URLSearchParams(location.search);
    if (params.get("top")) topEl.value = params.get("top");
    if (params.get("stride")) strideEl.value = params.get("stride");
    if (params.get("hz") === "5" || params.get("hz") === "20") {
      state.hz = params.get("hz");
      hzButtons.forEach((button) => button.classList.toggle("active", button.dataset.hz === state.hz));
    }
    try {
      try {
        await loadExitLevels();
      } catch (err) {
        console.warn("exit levels load failed", err);
      }
      const status = await api("/api/status");
      statusEl.textContent = `ロック済み予測 ${Number(status.counts.prediction_runs || 0).toLocaleString()}件  /  日足 ${status.counts.daily_bars.toLocaleString()}本`;
      const strategy = await api("/api/strategy");
      const experiments = (strategy.experiments && strategy.experiments.items) || [];
      state.versions = experiments;
      const preferred = params.get("experiment")
        || (strategy.experiments && strategy.experiments.active_id)
        || (experiments[0] && experiments[0].id)
        || "";
      experimentEl.innerHTML = "";
      experiments.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.id;
        const mark = item.active ? "（採用中）" : "";
        option.textContent = `${item.title || item.id}${mark}`;
        experimentEl.appendChild(option);
      });
      if (preferred && [...experimentEl.options].some((option) => option.value === preferred)) {
        experimentEl.value = preferred;
      }
      renderNote();
      if (summaryEl) {
        summaryEl.textContent = "検証を押すと始まります。予測は日付ごとにロックされ、あとから指標を変えても書き換えません。";
      }
      if (rowsEl) {
        rowsEl.innerHTML = '<tr><td colspan="7">検証を押すと、選んだ版の成績が出ます。</td></tr>';
      }
      if (!experimentEl.value) {
        showError("保存した版がありません。戦略画面で版を保存してください。");
        console.warn("verify boot has no experiments");
      }
    } catch (err) {
      console.warn("verify boot failed", err);
      showError(`起動失敗: ${err.message}`);
    }
  }

  boot();
})();
