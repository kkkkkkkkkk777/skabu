(() => {
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const warnEl = document.getElementById("warn");
  const errorEl = document.getElementById("error");
  const statusEl = document.getElementById("status");
  const debugEl = document.getElementById("debug");
  const cardsEl = document.getElementById("cards");
  const rowsEl = document.getElementById("rows");
  const rerunEl = document.getElementById("rerun");
  const csvEl = document.getElementById("csv");
  const cashEl = document.getElementById("cash");
  const riskEl = document.getElementById("risk-pct");
  let activeIds = [];

  function showError(message) {
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  async function api(path, options) {
    const res = await fetch(path, { headers: { Accept: "application/json" }, ...(options || {}) });
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

  function render(data) {
    const last = data.last || {};
    const lastOk = data.last_ok || {};
    const freshness = data.freshness || {};
    const rows = data.rows || [];
    warnEl.hidden = !freshness.message;
    warnEl.textContent = freshness.message || "";
    const lastStatus = last.status || "未実行";
    summaryEl.textContent = last.id
      ? `最終バッチ #${last.id}  ${lastStatus}  /  成功時 as_of ${lastOk.as_of || "-"}`
      : "まだ日次バッチはありません。`python -m skabu daily --skip-sync` か、下の再出力を使います。";
    if (data.cash_note) {
      summaryEl.textContent += `  /  ${data.cash_note}`;
    }
    rangeEl.textContent = last.finished_at ? `更新 ${last.finished_at}` : "";
    csvEl.hidden = !data.csv_ready;
    const yen = (value) => {
      if (value == null || Number.isNaN(value)) return "-";
      return Number(value).toLocaleString("ja-JP", { maximumFractionDigits: 0 });
    };
    const fmt = (value, digits = 1) => {
      if (value == null || Number.isNaN(value)) return "-";
      return Number(value).toLocaleString("ja-JP", { maximumFractionDigits: digits });
    };
    const learn = data.learn || {};
    const items = [
      ["状態", lastStatus],
      ["予測日", lastOk.as_of || "-"],
      ["プラン", data.plan || "-"],
      ["ロック", lastOk.locked ? "済み" : "-"],
      ["買う", String(data.bought ?? rows.length)],
      ["見送り", String(data.skipped ?? 0)],
      ["資金", yen(data.start_cash)],
      ["リスク%", data.risk_points != null ? String(data.risk_points) : "-"],
      ["現金", data.cash ? "はい" : "いいえ"],
      ["同期日数", String(last.synced_days ?? lastOk.synced_days ?? "-")],
      ["未採点", String(learn.pending_runs ?? "-")],
      ["採点済み", String(learn.graded ?? "-")],
      ["直近超過", learn.top20_excess_recent == null ? "-" : `${(learn.top20_excess_recent * 100).toFixed(2)}%`],
    ];
    cardsEl.innerHTML = items.map(([label, value]) => (
      `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
    )).join("");
    rowsEl.innerHTML = "";
    if (!rows.length) {
      rowsEl.innerHTML = `<tr><td colspan="12">${data.cash_note || "今日は買わない（現金）。"}</td></tr>`;
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${row.rank ?? ""}</td>
        <td>${row.code || ""}</td>
        <td>${row.co_name || ""}</td>
        <td>${row.s33_nm || ""}</td>
        <td>${fmt(row.close)}</td>
        <td>${yen(row.alloc)}</td>
        <td>${row.qty == null ? "-" : Number(row.qty).toLocaleString("ja-JP")}</td>
        <td>${row.total ?? "-"}</td>
        <td>${fmt(row.stop)}</td>
        <td>${fmt(row.target)}</td>
        <td>${fmt(row.rr, 2)}</td>
        <td>${row.reason || ""}</td>
      `;
      tr.addEventListener("click", () => {
        const q = new URLSearchParams();
        q.set("code", row.code);
        q.set("tf", "daily");
        if (activeIds.length) q.set("ind", activeIds.join(","));
        openChart(`/?${q.toString()}`);
      });
      rowsEl.appendChild(tr);
    });
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

  function query() {
    const q = new URLSearchParams();
    q.set("cash", String(cashEl.value || "1000000"));
    q.set("risk_pct", String(riskEl.value || "1"));
    return q;
  }

  function syncCsv() {
    csvEl.href = `/api/daily/csv?${query().toString()}`;
  }

  async function load() {
    showError("");
    syncCsv();
    const data = await api(`/api/daily?${query().toString()}`);
    render(data);
    return data;
  }

  async function rerun() {
    showError("");
    summaryEl.textContent = "ローカル足から再出力中…";
    rerunEl.disabled = true;
    try {
      const data = await api(`/api/daily?skip_sync=true&${query().toString()}`, { method: "POST" });
      render(data);
      return data;
    } catch (err) {
      showError(`再出力失敗: ${err.message}`);
      return null;
    } finally {
      rerunEl.disabled = false;
    }
  }

  rerunEl.addEventListener("click", () => {
    rerun().catch((err) => {
      console.error("daily rerun failed", err);
      showError(`再出力失敗: ${err.message}`);
    });
  });

  async function boot() {
    try {
      const status = await api("/api/status");
      activeIds = ((status.active_strategy && status.active_strategy.selections) || []).map((item) => item.id);
      statusEl.textContent = `日足 ${Number(status.counts.daily_bars || 0).toLocaleString()}本  /  予測ロック ${Number(status.counts.prediction_runs || 0).toLocaleString()}件`;
      const data = await load();
      if (data && data.rerun) await rerun();
    } catch (err) {
      showError(`起動失敗: ${err.message}`);
    }
  }

  cashEl.addEventListener("change", () => { load().catch((err) => showError(err.message)); });
  riskEl.addEventListener("change", () => { load().catch((err) => showError(err.message)); });

  boot();
})();
