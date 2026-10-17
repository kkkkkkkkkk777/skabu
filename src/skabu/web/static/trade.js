(() => {
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const warnEl = document.getElementById("warn");
  const errorEl = document.getElementById("error");
  const statusEl = document.getElementById("status");
  const debugEl = document.getElementById("debug");
  const cardsEl = document.getElementById("cards");
  const rowsEl = document.getElementById("rows");
  const positionsEl = document.getElementById("positions");
  const envBadge = document.getElementById("env-badge");
  const modeApprove = document.getElementById("mode-approve");
  const modeAuto = document.getElementById("mode-auto");
  const checkAll = document.getElementById("check-all");
  const holdEl = document.getElementById("hold-days");
  const labels = {};
  const exitLabels = { time: "期間", stop: "損切", target: "利確" };

  function showError(message) {
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
    if (message) console.error("trade", message);
  }

  async function api(path, options) {
    const res = await fetch(path, {
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      ...(options || {}),
    });
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

  function yen(value) {
    if (value == null || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString("ja-JP", { maximumFractionDigits: 0 });
  }

  function skipText(reason) {
    if (!reason) return "-";
    return labels[reason] || reason;
  }

  function selectedCodes(kind) {
    const selector = kind ? `input[data-kind="${kind}"]:checked` : "input[data-code]:checked";
    return Array.from(rowsEl.querySelectorAll(selector)).map((el) => el.dataset.code);
  }

  function reasonText(row) {
    if (row.exit && exitLabels[row.exit]) return exitLabels[row.exit];
    if (row.exit_note) return row.exit_note;
    return skipText(row.skip_reason);
  }

  function exitCell(row) {
    if (row.exit_order_id) return row.exit_date || "引け売り";
    if (row.status === "filled" && !row.exit) {
      const value = row.exit_date || "";
      return `<input class="exit-date" type="date" data-code="${row.code || ""}" value="${value}">`;
    }
    return row.exit_date || "-";
  }

  function render(data) {
    const prefs = data.prefs || {};
    const snap = data.snapshot || {};
    const book = data.book || {};
    const tickets = book.tickets || [];
    const positions = snap.positions || [];
    Object.assign(labels, data.skip_labels || {});
    const live = Boolean(data.live);
    envBadge.textContent = live ? "実発注" : "ペーパー";
    envBadge.className = live ? "badge badge-live" : "badge badge-paper";
    modeApprove.classList.toggle("active", prefs.mode === "approve");
    modeAuto.classList.toggle("active", prefs.mode === "auto");
    const online = Boolean(snap.online);
    const halted = Boolean(prefs.halted);
    warnEl.hidden = !(halted || (live && !online));
    warnEl.textContent = halted
      ? "緊急停止中です。再開するまで発注しません。"
      : (live && !online ? (snap.error || "kabuステーションに接続できません") : "");
    summaryEl.textContent = live
      ? "実発注です。目標の指値は出さず、売る日の引けで売ります。切替は .env の KABU_LIVE だけです。"
      : "ペーパーです。目標の指値は出さず、売る日の引けで売ります。実口座には出しません。";
    if (document.activeElement !== holdEl && prefs.hold_days != null) {
      holdEl.value = String(prefs.hold_days);
    }
    rangeEl.textContent = book.as_of ? `予測日 ${book.as_of}` : "";
    const items = [
      ["接続", online ? "オンライン" : "オフライン"],
      ["モード", halted ? "停止" : (prefs.mode === "auto" ? "自動" : "承認")],
      ["余力", yen(snap.cash)],
      ["保有", String(positions.length)],
      ["待ち", String(tickets.filter((row) => row.status === "pending").length)],
      ["送信", String(tickets.filter((row) => row.status === "sent" || row.status === "filled").length)],
      ["上限", yen(data.max_order_yen)],
    ];
    cardsEl.innerHTML = items.map(([label, value]) => (
      `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
    )).join("");
    rowsEl.innerHTML = "";
    if (!tickets.length) {
      rowsEl.innerHTML = `<tr><td colspan="12">台帳は空です。日次のあと「候補を台帳へ」を押してください。</td></tr>`;
    } else {
      for (const row of tickets) {
        const tr = document.createElement("tr");
        const due = row.status === "filled" && !row.exit && !row.exit_order_id && row.exit_date && row.exit_date === data.today;
        if (row.status === "skipped" || row.status === "rejected") tr.classList.add("skip");
        if (due) tr.classList.add("due");
        const canBuy = row.status === "pending";
        const canClose = Boolean(due);
        const kind = canClose ? "close" : "buy";
        const enabled = canBuy || canClose;
        tr.innerHTML = `
          <td><input type="checkbox" data-code="${row.code || ""}" data-kind="${kind}" ${enabled ? "" : "disabled"}></td>
          <td><a class="chart-link" target="skabu-chart" href="/?code=${encodeURIComponent(row.code || "")}">${row.kabu_code || row.code || "-"}</a></td>
          <td>${row.co_name || "-"}</td>
          <td>${row.s33_nm || "-"}</td>
          <td>${row.qty ?? "-"}</td>
          <td>${yen(row.alloc)}</td>
          <td><span class="status-pill status-${row.status || "pending"}">${row.status || "-"}</span></td>
          <td class="reason">${reasonText(row)}</td>
          <td>${row.buy_order_id || "-"}</td>
          <td>${exitCell(row)}</td>
          <td>${row.stop != null ? yen(row.stop) : "-"}</td>
          <td>${row.fill_price != null ? yen(row.fill_price) : "-"}</td>
        `;
        const chartLink = tr.querySelector("a.chart-link");
        if (chartLink) {
          chartLink.addEventListener("click", (event) => {
            event.preventDefault();
            let opened = null;
            try {
              opened = window.open(chartLink.href, "skabu-chart");
            } catch (err) {
              console.error("chart window failed", err);
            }
            if (!opened) {
              console.error("chart window blocked", chartLink.href);
              showError("チャート窓を開けませんでした。ポップアップを許可してください。");
            }
          });
        }
        const dateInput = tr.querySelector("input.exit-date");
        if (dateInput) {
          dateInput.addEventListener("change", () => saveExitDate(row.code, dateInput.value));
        }
        rowsEl.appendChild(tr);
      }
    }
    positionsEl.innerHTML = "";
    if (!positions.length) {
      positionsEl.innerHTML = `<tr><td colspan="6">保有はありません。</td></tr>`;
    } else {
      for (const row of positions) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${row.kabu_code || row.code || "-"}</td>
          <td>${row.name || "-"}</td>
          <td>${row.qty ?? "-"}</td>
          <td>${yen(row.price)}</td>
          <td>${yen(row.value)}</td>
          <td>${yen(row.pnl)}</td>
        `;
        positionsEl.appendChild(tr);
      }
    }
    statusEl.textContent = online
      ? (live ? "カブコム接続中" : "ペーパーで待機中")
      : (snap.error || "未接続");
    checkAll.checked = false;
  }

  async function refresh() {
    showError("");
    try {
      render(await api("/api/trade"));
    } catch (err) {
      showError(err.message);
      statusEl.textContent = "読み込み失敗";
    }
  }

  async function run(path, options, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return;
    showError("");
    try {
      await api(path, options);
      render(await api("/api/trade"));
    } catch (err) {
      showError(err.message);
    }
  }

  modeApprove.addEventListener("click", () => run("/api/trade/mode", { method: "PUT", body: JSON.stringify({ mode: "approve" }) }));
  modeAuto.addEventListener("click", () => run("/api/trade/mode", { method: "PUT", body: JSON.stringify({ mode: "auto" }) }));
  document.getElementById("buy").addEventListener("click", () => run("/api/trade/buy", { method: "POST" }));
  document.getElementById("watch").addEventListener("click", () => run("/api/trade/watch", { method: "POST" }));
  document.getElementById("halt").addEventListener("click", () => run("/api/trade/halt", { method: "POST" }, "未約定の注文を取消し、承認モードで停止します。続行しますか？"));
  document.getElementById("resume").addEventListener("click", () => run("/api/trade/resume", { method: "POST" }));
  document.getElementById("approve").addEventListener("click", () => {
    const codes = selectedCodes("buy");
    if (!codes.length) {
      showError("承認する待ち行を選んでください");
      return;
    }
    run("/api/trade/approve", { method: "POST", body: JSON.stringify({ codes }) });
  });
  document.getElementById("close-sell").addEventListener("click", () => {
    const codes = selectedCodes("close");
    if (!codes.length) {
      showError("今日が売る日の行を選んでください");
      return;
    }
    run("/api/trade/close", { method: "POST", body: JSON.stringify({ codes }) }, "選んだ銘柄を今日の引けで売ります。続行しますか？");
  });
  holdEl.addEventListener("change", () => {
    const value = Number(holdEl.value);
    if (!Number.isInteger(value) || value < 1 || value > 250) {
      showError("保有日数は1から250の営業日にしてください。");
      refresh();
      return;
    }
    run("/api/trade/hold", { method: "PUT", body: JSON.stringify({ hold_days: value }) });
  });

  async function saveExitDate(code, exitDate) {
    showError("");
    try {
      await api("/api/trade/exit-date", { method: "PUT", body: JSON.stringify({ code, exit_date: exitDate }) });
      render(await api("/api/trade"));
    } catch (err) {
      showError(err.message);
      refresh();
    }
  }
  checkAll.addEventListener("change", () => {
    rowsEl.querySelectorAll("input[data-code]:not(:disabled)").forEach((el) => {
      el.checked = checkAll.checked;
    });
  });

  refresh();
})();
