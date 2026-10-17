(() => {
  const button = document.getElementById("manual-sync");
  if (!button) {
    console.error("manual sync button missing");
    return;
  }

  function showError(message) {
    const errorEl = document.getElementById("error");
    if (!errorEl) {
      console.error("manual sync", message);
      return;
    }
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  button.addEventListener("click", async () => {
    const previous = button.textContent;
    let keepDisabled = false;
    button.disabled = true;
    button.textContent = "同期中…";
    showError("");
    try {
      const res = await fetch("/api/sync", {
        method: "POST",
        headers: { Accept: "application/json" },
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
      const result = body.result || {};
      const statusEl = document.getElementById("status");
      if (body.action === "busy") {
        if (statusEl) statusEl.textContent = "別の同期が実行中です";
        return;
      }
      if (body.action === "empty" || (Number(result.synced_days || 0) === 0 && Number(result.empty_days || 0) > 0)) {
        const until = result.last_bars_to ? `最終 ${result.last_bars_to}` : "未取得";
        showError(`株価はまだ配信されていません（${until}）。引け後にもう一度押してください。`);
        if (statusEl) statusEl.textContent = `株価未配信  ${until}`;
        return;
      }
      const synced = Number(result.synced_days || 0);
      if (statusEl) {
        statusEl.textContent = synced > 0 ? `株価を ${synced} 日分同期しました` : "株価は最新です";
      }
      keepDisabled = true;
      window.location.reload();
    } catch (err) {
      console.error("manual sync failed", err);
      showError(`同期失敗: ${err.message}`);
    } finally {
      if (!keepDisabled) {
        button.disabled = false;
        button.textContent = previous;
      }
    }
  });
})();
