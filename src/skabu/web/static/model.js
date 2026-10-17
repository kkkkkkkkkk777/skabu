(() => {
  const summaryEl = document.getElementById("summary");
  const rangeEl = document.getElementById("range");
  const warnEl = document.getElementById("warn");
  const errorEl = document.getElementById("error");
  const cardsEl = document.getElementById("cards");
  const rowsEl = document.getElementById("rows");
  const learnRowsEl = document.getElementById("learn-rows");
  const calRowsEl = document.getElementById("cal-rows");
  const rollRowsEl = document.getElementById("roll-rows");
  const histRowsEl = document.getElementById("hist-rows");
  const labelRowsEl = document.getElementById("label-rows");
  const labelNoteEl = document.getElementById("label-note");
  const debugEl = document.getElementById("debug");

  function showError(message) {
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  async function api(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
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

  function render(data) {
    const last = data.last || {};
    const meta = data.meta || last.meta || {};
    const metrics = meta.metrics || {};
    if (!data.ready) {
      warnEl.hidden = false;
      warnEl.textContent = "モデル未学習です。プロジェクトフォルダで `python -m skabu train` を実行してください。kabu接続はまだありません。";
    } else {
      warnEl.hidden = true;
      warnEl.textContent = "";
    }
    summaryEl.textContent = data.ready
      ? `学習済み  /  検証精度 ${pct(metrics.valid_accuracy)}  /  AUC ${metrics.valid_auc == null ? "-" : metrics.valid_auc}`
      : "未学習。学習後にランキングの Boost が点数を出します。";
    rangeEl.textContent = (metrics.train_as_of || []).length
      ? `学習 ${metrics.train_as_of[0]} ～ ${metrics.train_as_of[1]}`
      : "";
    const learn = data.learn || {};
    const trigger = learn.trigger || {};
    const items = [
      ["状態", last.status || "未実行"],
      ["学習件数", String(last.n_train ?? metrics.n_train ?? "-")],
      ["検証件数", String(last.n_valid ?? metrics.n_valid ?? "-")],
      ["検証精度", pct(metrics.valid_accuracy)],
      ["AUC", metrics.valid_auc == null ? "-" : String(metrics.valid_auc)],
      ["未採点", String(learn.pending_runs ?? "-")],
      ["採点済み", String(learn.graded ?? "-")],
      ["直近超過", learn.top20_excess_recent == null ? "-" : pct(learn.top20_excess_recent)],
    ];
    cardsEl.innerHTML = items.map(([label, value]) => (
      `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
    )).join("");
    const labels = data.labels || null;
    if (labelNoteEl) {
      labelNoteEl.textContent = labels
        ? (labels.note || "比較済み。自動では入れ替えない。")
        : "未比較。`python -m skabu labels` でリターン>0 と指数勝ちを測る。本番 Boost は入れ替えない。";
    }
    if (labelRowsEl) {
      const rows = (labels && labels.items) || [];
      labelRowsEl.innerHTML = rows.length
        ? rows.map((item) => (
          `<tr>
            <td>${item.name || "-"}</td>
            <td>${item.label || "-"}</td>
            <td>${item.pos_rate == null ? "-" : pct(item.pos_rate)}</td>
            <td>${item.n_train ?? "-"}</td>
            <td>${item.n_valid ?? "-"}</td>
            <td>${item.valid_auc == null ? "-" : item.valid_auc}</td>
            <td>${item.valid_accuracy == null ? "-" : pct(item.valid_accuracy)}</td>
            <td>${item.cut || "-"}</td>
            <td>${item.note || "-"}</td>
          </tr>`
        )).join("")
        : `<tr><td colspan="9">未比較</td></tr>`;
    }
    const gap = metrics.feature_gap || {};
    const names = Object.keys(gap);
    rowsEl.innerHTML = "";
    if (!names.length) {
      rowsEl.innerHTML = `<tr><td colspan="2">学習後に表示します</td></tr>`;
    } else {
      names.sort((a, b) => Math.abs(gap[b]) - Math.abs(gap[a]));
      names.forEach((name) => {
        const tr = document.createElement("tr");
        const value = gap[name];
        tr.innerHTML = `<td>${name}</td><td class="${value > 0 ? "score-pos" : value < 0 ? "score-neg" : ""}">${value}</td>`;
        rowsEl.appendChild(tr);
      });
    }
    const lastLearn = learn.last_learn || {};
    const learnMeta = (lastLearn.meta && lastLearn.meta.learn) || {};
    learnRowsEl.innerHTML = [
      ["最終採点日", learn.last_graded_as_of || "-"],
      ["次の再学習", trigger.ready ? `条件達成（${trigger.reason || "-"}）` : `待機（新規${trigger.new_as_of_count || 0}/${trigger.need_as_ofs || 3}日）`],
      ["前回判定", lastLearn.status || "未実行"],
      ["前回理由", (learnMeta.verdict && learnMeta.verdict.reason) || "-"],
    ].map(([label, value]) => `<tr><td>${label}</td><td>${value}</td></tr>`).join("");
    const cals = learn.calibration || [];
    calRowsEl.innerHTML = cals.length
      ? cals.map((item) => (
        `<tr><td>${item.label}</td><td>${item.n}</td><td>${item.avg_proba == null ? "-" : pct(item.avg_proba)}</td><td>${item.realized_hit == null ? "-" : pct(item.realized_hit)}</td></tr>`
      )).join("")
      : `<tr><td colspan="4">採点後に表示します</td></tr>`;
    const rolls = (learn.rolling || []).slice(-12).reverse();
    rollRowsEl.innerHTML = rolls.length
      ? rolls.map((item) => (
        `<tr><td>${item.as_of}</td><td>${item.n}</td><td>${item.avg_excess == null ? "-" : pct(item.avg_excess)}</td><td>${item.hit_rate == null ? "-" : pct(item.hit_rate)}</td></tr>`
      )).join("")
      : `<tr><td colspan="4">採点後に表示します</td></tr>`;
    const hist = learn.history || [];
    histRowsEl.innerHTML = hist.length
      ? hist.map((item) => {
        const reason = ((item.meta && item.meta.learn && item.meta.learn.verdict) || {}).reason || item.status || "";
        return `<tr><td>${item.id ?? ""}</td><td>${item.status || ""}</td><td>${item.auc == null ? "-" : item.auc}</td><td>${reason}</td></tr>`;
      }).join("")
      : `<tr><td colspan="4">学習後に表示します</td></tr>`;
  }

  async function boot() {
    try {
      const data = await api("/api/model");
      render(data);
    } catch (err) {
      showError(`起動失敗: ${err.message}`);
    }
  }

  boot();
})();
