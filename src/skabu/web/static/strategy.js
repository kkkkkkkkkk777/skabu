(() => {
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const warnEl = document.getElementById("warn");
  const errorEl = document.getElementById("error");
  const cardsEl = document.getElementById("cards");
  const rowsEl = document.getElementById("rows");
  const versionsEl = document.getElementById("versions");
  const searchRowsEl = document.getElementById("search-rows");
  const searchEl = document.getElementById("search");
  const debugEl = document.getElementById("debug");

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

  function confirmAvgReturn(item) {
    const holdout = item.holdout || ((item.verify || {}).holdout) || {};
    const raw = holdout.avg_return;
    if (raw == null || Number.isNaN(Number(raw))) return null;
    return Number(raw);
  }

  function confirmUnused(item) {
    const raw = confirmAvgReturn(item);
    if (raw != null) return raw <= 0;
    return item.holdout_ok !== true;
  }

  function confirmNote(item) {
    if (!confirmUnused(item)) return "";
    if (confirmAvgReturn(item) == null) {
      return "確認リターンは未計測です。買う判定には使いません";
    }
    return "確認リターンはプラスではありません。買う判定には使いません";
  }

  function render(data) {
    const active = data.active || {};
    const last = data.last || {};
    const report = last.report || {};
    const candidates = report.candidates || [];
    const skipNote = active.id ? confirmNote(active) : "";
    if (!active.id) {
      warnEl.hidden = false;
      warnEl.textContent = "まだ本採用していません。`python -m skabu adopt` で前半期間の超過が一番良い組み合わせを選びます。";
    } else if (active.warning || skipNote) {
      warnEl.hidden = false;
      warnEl.textContent = [active.warning, skipNote].filter(Boolean).join(" ");
    } else {
      warnEl.hidden = true;
      warnEl.textContent = "";
    }
    const defaultTitle = "EMA + RSI";
    summaryEl.textContent = active.id
      ? `採用中 ${active.title || active.id}`
      : `未採用。既定は ${defaultTitle} です。`;
    if (skipNote) summaryEl.textContent = `${summaryEl.textContent}  /  ${skipNote}`;
    rangeEl.textContent = active.adopted_at ? `採用 ${active.adopted_at}` : "";
    const items = [
      ["採用", active.title || "-"],
      ["選定超過", pct((active.train || {}).avg_excess)],
      ["確認リターン", pct((active.holdout || {}).avg_return)],
      ["確認超過", pct((active.holdout || {}).avg_excess)],
      ["指数勝ち", active.holdout_ok == null ? "-" : (active.holdout_ok ? "はい" : "いいえ")],
      ["状態", last.status || "未実行"],
    ];
    cardsEl.innerHTML = items.map(([label, value]) => (
      `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
    )).join("");
    const versions = (data.experiments && data.experiments.items) || [];
    versionsEl.innerHTML = "";
    if (!versions.length) {
      versionsEl.innerHTML = `<tr><td colspan="5">版がありません</td></tr>`;
    } else {
      versions.forEach((item) => {
        const holdout = ((item.verify || {}).holdout) || {};
        const tr = document.createElement("tr");
        if (item.active) tr.style.outline = "1px solid #5eead4";
        const action = item.active
          ? "採用中"
          : `<button type="button" data-id="${item.id}">採用する</button>`;
        const adoptWarn = confirmUnused(item)
          ? `<div class="reason">確認は買う判定に使いません</div>`
          : "";
        tr.innerHTML = `
          <td>${item.title || item.id}${adoptWarn}</td>
          <td class="reason">${item.filter_summary || item.note || ""}</td>
          <td class="${cls(holdout.avg_return)}">${pct(holdout.avg_return)}</td>
          <td class="${cls(holdout.avg_excess)}">${pct(holdout.avg_excess)}</td>
          <td>${action}</td>
        `;
        const button = tr.querySelector("button");
        if (button) {
          button.addEventListener("click", () => useVersion(item.id));
        }
        versionsEl.appendChild(tr);
      });
    }
    const search = data.search || {};
    const searchRows = search.candidates || [];
    if (searchRowsEl) {
      searchRowsEl.innerHTML = "";
      if (!searchRows.length) {
        searchRowsEl.innerHTML = `<tr><td colspan="5">自動探索を実行すると出ます</td></tr>`;
      } else {
        const gate = search.gate || {};
        const pickedId = search.candidate_id || gate.candidate_id || search.winner_id;
        searchRows.sort((left, right) => {
          const a = (left.select || left.train || {}).avg_return;
          const b = (right.select || right.train || {}).avg_return;
          return (b == null ? -Infinity : b) - (a == null ? -Infinity : a);
        });
        searchRows.forEach((item) => {
          const select = item.select || item.train || {};
          const confirm = item.confirm || {};
          const tr = document.createElement("tr");
          if (item.id === pickedId) tr.style.outline = "1px solid #5eead4";
          const n = item.id === pickedId ? (gate.n ?? confirm.periods) : (confirm.periods ?? "-");
          const need = item.id === pickedId ? (gate.need_n ?? item.need_n) : (item.need_n ?? "-");
          const p = item.id === pickedId && gate.p_gt_zero != null ? Number(gate.p_gt_zero).toFixed(3) : "-";
          tr.innerHTML = `
            <td>${item.title || item.id}</td>
            <td class="${cls(select.avg_return)}">${pct(select.avg_return)}</td>
            <td class="${cls(confirm.avg_return)}">${pct(confirm.avg_return)}</td>
            <td>${n} / ${need}</td>
            <td>${p}</td>
          `;
          searchRowsEl.appendChild(tr);
        });
      }
    }
    if (search.gate) {
      const reason = search.adopted ? "日次の採用を更新した" : "日次は維持した";
      rangeEl.textContent = [rangeEl.textContent, reason, search.note || ""].filter(Boolean).join("  /  ");
    }
    rowsEl.innerHTML = "";
    if (!candidates.length) {
      rowsEl.innerHTML = `<tr><td colspan="5">adopt 後に比較表が出ます</td></tr>`;
      return;
    }
    candidates.forEach((item) => {
      const train = item.train || {};
      const holdout = item.holdout || {};
      const tr = document.createElement("tr");
      if (item.id === active.id) tr.style.outline = "1px solid #5eead4";
      tr.innerHTML = `
        <td>${item.title || item.id}</td>
        <td class="${cls(train.avg_excess)}">${pct(train.avg_excess)}</td>
        <td>${pct(train.win_rate_vs_bench)}</td>
        <td class="${cls(holdout.avg_excess)}">${pct(holdout.avg_excess)}</td>
        <td>${pct(holdout.win_rate_vs_bench)}</td>
      `;
      rowsEl.appendChild(tr);
    });
  }

  async function useVersion(id) {
    showError("");
    try {
      const data = await api(`/api/strategy/use?id=${encodeURIComponent(id)}`, { method: "POST" });
      const full = await api("/api/strategy");
      render({ ...full, experiments: data.experiments || full.experiments });
    } catch (err) {
      showError(`採用失敗: ${err.message}`);
    }
  }

  async function runSearch() {
    showError("");
    if (searchEl) searchEl.disabled = true;
    if (summaryEl) summaryEl.textContent = "自動探索中… 単体のあと上位を組み合わせます。初回は十数分かかることがあります";
    try {
      const data = await api("/api/strategy/search", { method: "POST" });
      const full = await api("/api/strategy");
      render({ ...full, search: data.search || full.search, experiments: data.experiments || full.experiments });
    } catch (err) {
      showError(`自動探索失敗: ${err.message}`);
    } finally {
      if (searchEl) searchEl.disabled = false;
    }
  }

  if (searchEl) searchEl.addEventListener("click", runSearch);

  async function boot() {
    try {
      render(await api("/api/strategy"));
    } catch (err) {
      showError(`起動失敗: ${err.message}`);
    }
  }

  boot();
})();
