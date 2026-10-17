/* チャート上の線と箱。価格と日付で覚え、銘柄ごとに保存する。日足と週足は同じ図を描く。 */
(() => {
  const COLORS = ["#fbbf24", "#5eead4", "#60a5fa", "#f472b6", "#fb923c", "#e5e7eb"];
  const HIT = 6;
  const HANDLE = 8;
  const MAX = 200;

  const toolsEl = document.getElementById("draw-tools");
  const canvas = document.getElementById("draw-layer");
  const extendBtn = document.getElementById("draw-extend");
  const snapEl = document.getElementById("draw-snap");
  const colorsEl = document.getElementById("draw-colors");
  if (!toolsEl || !canvas || !extendBtn || !snapEl || !colorsEl) return;

  const ctx = canvas.getContext("2d");
  const toolButtons = [...toolsEl.querySelectorAll("button[data-tool]")];

  let chart = null;
  let series = null;
  let onError = () => {};
  let code = "";
  let tf = "daily";
  let candles = [];
  let shapes = [];
  let selectedId = null;
  let draft = null;
  let tool = "hand";
  let color = COLORS[0];
  let undo = [];
  let drag = null;
  let dirty = false;
  let saveTimer = 0;
  let saveSeq = 0;
  let loadToken = 0;
  let frame = 0;

  function report(message) {
    try {
      onError(message);
    } catch (err) {
      console.error("draw error report failed", err);
    }
  }

  function snapshot(list) {
    return JSON.parse(JSON.stringify(list || shapes));
  }

  function find(id) {
    return shapes.find((item) => item.id === id) || null;
  }

  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function formatTime(value) {
    if (!value) return null;
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    if (typeof value === "object" && value.year && value.month && value.day) {
      return `${value.year}-${pad(value.month)}-${pad(value.day)}`;
    }
    return null;
  }

  function timeToX(time) {
    if (!chart || !time) return null;
    const direct = chart.timeScale().timeToCoordinate(time);
    if (direct != null) return direct;
    const snapped = nearestCandleTime(time);
    if (!snapped || snapped === time) return null;
    const x = chart.timeScale().timeToCoordinate(snapped);
    return x == null ? null : x;
  }

  function nearestCandleTime(time) {
    if (!candles.length) return null;
    let lo = 0;
    let hi = candles.length - 1;
    let best = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (candles[mid].time <= time) {
        best = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    const next = best + 1;
    if (next < candles.length && dateDistance(candles[next].time, time) < dateDistance(candles[best].time, time)) {
      return candles[next].time;
    }
    return candles[best].time;
  }

  function dateDistance(left, right) {
    const a = Date.parse(left);
    const b = Date.parse(right);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return Number.POSITIVE_INFINITY;
    return Math.abs(a - b);
  }

  function priceToY(price) {
    if (!series || !Number.isFinite(price)) return null;
    const y = series.priceToCoordinate(price);
    return y == null ? null : y;
  }

  function nearestTime(x) {
    if (!chart || !candles.length) return null;
    const scale = chart.timeScale();
    const logical = typeof scale.coordinateToLogical === "function" ? scale.coordinateToLogical(x) : null;
    if (logical != null && Number.isFinite(logical)) {
      const index = Math.max(0, Math.min(candles.length - 1, Math.round(logical)));
      const time = candles[index] && candles[index].time;
      if (time) return time;
    }
    return formatTime(scale.coordinateToTime(x));
  }

  function place(x, y) {
    if (!chart || !series) return null;
    const time = nearestTime(x);
    const price = series.coordinateToPrice(y);
    if (!time || price == null || !Number.isFinite(price)) return null;
    if (!snapEl.checked) return { time, price: roundPrice(price) };
    const bar = candles.find((item) => item.time === time);
    if (!bar) return { time, price: roundPrice(price) };
    let best = price;
    let bestD = Infinity;
    for (const value of [bar.open, bar.high, bar.low, bar.close]) {
      if (!Number.isFinite(value)) continue;
      const py = priceToY(value);
      if (py == null) continue;
      const dist = Math.abs(py - y);
      if (dist < bestD) {
        bestD = dist;
        best = value;
      }
    }
    return { time, price: roundPrice(best) };
  }

  function roundPrice(value) {
    return Math.round(value * 10000) / 10000;
  }

  function movedPoint(point, dx, dy) {
    const x = timeToX(point.time);
    const y = priceToY(point.price);
    if (x == null || y == null) return null;
    return place(x + dx, y + dy);
  }

  function geometry(shape) {
    if (!shape) return null;
    if (shape.kind === "hline") {
      const y = priceToY(shape.price);
      if (y == null) return null;
      return { x1: 0, y1: y, x2: canvas.clientWidth, y2: y, handles: [] };
    }
    if (shape.kind === "vline") {
      const x = timeToX(shape.time);
      if (x == null) return null;
      return { x1: x, y1: 0, x2: x, y2: canvas.clientHeight, handles: [] };
    }
    if (!shape.a || !shape.b) return null;
    const a = { which: "a", x: timeToX(shape.a.time), y: priceToY(shape.a.price) };
    const b = { which: "b", x: timeToX(shape.b.time), y: priceToY(shape.b.price) };
    if (a.x == null || a.y == null || b.x == null || b.y == null) return null;
    if (shape.kind === "box") {
      return {
        left: Math.min(a.x, b.x),
        top: Math.min(a.y, b.y),
        right: Math.max(a.x, b.x),
        bottom: Math.max(a.y, b.y),
        handles: [a, b],
      };
    }
    if (shape.kind !== "trend") return null;
    let x1 = a.x;
    let y1 = a.y;
    let x2 = b.x;
    let y2 = b.y;
    if (shape.extend && a.x !== b.x) {
      const left = a.x <= b.x ? a : b;
      const right = a.x <= b.x ? b : a;
      const t = (canvas.clientWidth - left.x) / (right.x - left.x);
      x1 = left.x;
      y1 = left.y;
      x2 = canvas.clientWidth;
      y2 = left.y + (right.y - left.y) * t;
    }
    return { x1, y1, x2, y2, handles: [a, b] };
  }

  function distToSegment(px, py, geom) {
    const dx = geom.x2 - geom.x1;
    const dy = geom.y2 - geom.y1;
    const len2 = dx * dx + dy * dy;
    if (len2 === 0) return Math.hypot(px - geom.x1, py - geom.y1);
    let t = ((px - geom.x1) * dx + (py - geom.y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (geom.x1 + t * dx), py - (geom.y1 + t * dy));
  }

  function hitHandle(id, x, y) {
    const shape = find(id);
    const geom = geometry(shape);
    if (!geom) return null;
    for (const handle of geom.handles) {
      if (Math.hypot(handle.x - x, handle.y - y) <= HANDLE) return handle.which;
    }
    return null;
  }

  function hitShape(x, y) {
    for (let i = shapes.length - 1; i >= 0; i -= 1) {
      const shape = shapes[i];
      const geom = geometry(shape);
      if (!geom) continue;
      if (shape.kind === "box") {
        const inside = x >= geom.left - HIT && x <= geom.right + HIT && y >= geom.top - HIT && y <= geom.bottom + HIT;
        if (inside) return shape;
      } else if (distToSegment(x, y, geom) <= HIT) {
        return shape;
      }
    }
    return null;
  }

  function paintShape(shape, preview) {
    const geom = geometry(shape);
    if (!geom) return;
    const selected = shape.id === selectedId;
    ctx.save();
    ctx.strokeStyle = shape.color || COLORS[0];
    ctx.fillStyle = shape.color || COLORS[0];
    ctx.lineWidth = selected ? 2.5 : 1.5;
    ctx.setLineDash(preview ? [5, 4] : []);
    if (shape.kind === "box") {
      ctx.globalAlpha = 0.16;
      ctx.fillRect(geom.left, geom.top, geom.right - geom.left, geom.bottom - geom.top);
      ctx.globalAlpha = 1;
      ctx.strokeRect(geom.left, geom.top, geom.right - geom.left, geom.bottom - geom.top);
    } else {
      ctx.beginPath();
      ctx.moveTo(geom.x1, geom.y1);
      ctx.lineTo(geom.x2, geom.y2);
      ctx.stroke();
    }
    if (selected && !preview) {
      for (const handle of geom.handles) {
        ctx.beginPath();
        ctx.arc(handle.x, handle.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = "#0e1117";
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = "#e5e7eb";
        ctx.setLineDash([]);
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  let sizeWarned = false;

  function paint() {
    const chartEl = document.getElementById("chart");
    if (!ctx || !chartEl) return;
    const width = chartEl.clientWidth;
    const height = chartEl.clientHeight;
    if (width < 2 || height < 2) return;
    const ratio = window.devicePixelRatio || 1;
    const bw = Math.max(1, Math.floor(width * ratio));
    const bh = Math.max(1, Math.floor(height * ratio));
    if (canvas.width !== bw || canvas.height !== bh) {
      canvas.width = bw;
      canvas.height = bh;
    }
    if (!sizeWarned && (Math.abs(canvas.clientWidth - width) > 2 || Math.abs(canvas.clientHeight - height) > 2)) {
      sizeWarned = true;
      console.error("draw canvas left the chart", canvas.clientWidth, canvas.clientHeight, width, height);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    try {
      for (const shape of shapes) paintShape(shape, false);
      if (draft) paintShape(draft, true);
    } catch (err) {
      console.error("draw paint failed", err);
    }
  }

  function redraw() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(paint);
  }

  function updateExtend() {
    const shape = find(selectedId);
    const show = Boolean(shape && shape.kind === "trend");
    extendBtn.hidden = !show;
    extendBtn.classList.toggle("active", Boolean(show && shape.extend));
  }

  function markColors() {
    for (const button of colorsEl.querySelectorAll(".draw-color")) {
      button.classList.toggle("active", button.dataset.color === color);
    }
  }

  function setTool(next) {
    tool = next;
    if (next !== "trend" && next !== "box") draft = null;
    if (next !== "select") selectedId = null;
    for (const button of toolButtons) {
      button.classList.toggle("active", button.dataset.tool === next);
    }
    canvas.classList.toggle("is-armed", next !== "hand");
    canvas.classList.toggle("is-select", next === "select");
    updateExtend();
    redraw();
  }

  function pushUndo() {
    undo.push(snapshot());
    if (undo.length > 40) undo.shift();
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    const saveCode = code;
    const saveTf = tf;
    const payload = snapshot();
    saveTimer = setTimeout(() => flushSave(saveCode, saveTf, payload), 300);
  }

  async function flushSave(saveCode, saveTf, payload) {
    if (!saveCode) return;
    const seq = ++saveSeq;
    try {
      const res = await fetch(`/api/drawings/${encodeURIComponent(saveCode)}?tf=${encodeURIComponent(saveTf)}`, {
        method: "PUT",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ shapes: payload }),
      });
      let body = {};
      try {
        body = await res.json();
      } catch (err) {
        console.error("drawings save json failed", err);
        throw new Error("描画の保存結果を読めませんでした");
      }
      if (!res.ok || body.ok === false) {
        console.error("drawings save failed", saveCode, saveTf, body);
        throw new Error(body.message || "描画を保存できませんでした");
      }
      if (seq === saveSeq && saveCode === code && saveTf === tf) dirty = false;
    } catch (err) {
      console.error("drawings save failed", err);
      report(err.message || "描画を保存できませんでした。");
    }
  }

  async function loadDrawings() {
    const loadCode = code;
    const loadTf = tf;
    const token = ++loadToken;
    if (!loadCode) return;
    try {
      const res = await fetch(`/api/drawings/${encodeURIComponent(loadCode)}?tf=${encodeURIComponent(loadTf)}`, {
        headers: { Accept: "application/json" },
      });
      let body = {};
      try {
        body = await res.json();
      } catch (err) {
        console.error("drawings load json failed", err);
        throw new Error("描画を読めませんでした");
      }
      if (!res.ok || body.ok === false) {
        console.error("drawings load failed", loadCode, loadTf, body);
        throw new Error(body.message || "描画を読めませんでした");
      }
      if (token !== loadToken || loadCode !== code) return;
      shapes = Array.isArray(body.shapes) ? body.shapes : [];
      selectedId = null;
      draft = null;
      undo = [];
      dirty = false;
      updateExtend();
      redraw();
    } catch (err) {
      console.error("drawings load failed", err);
      report(err.message || "描画を読めませんでした。");
    }
  }

  function commitDraft() {
    if (!draft) return;
    if (shapes.length >= MAX) {
      draft = null;
      report("図形は200個までです。");
      redraw();
      return;
    }
    pushUndo();
    const shape = snapshot([draft])[0];
    shapes.push(shape);
    selectedId = shape.id;
    draft = null;
    dirty = true;
    updateExtend();
    scheduleSave();
    redraw();
  }

  function localPoint(ev) {
    const rect = canvas.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  }

  function onDown(ev) {
    if (ev.button !== 0 || tool === "hand") return;
    const { x, y } = localPoint(ev);
    canvas.setPointerCapture(ev.pointerId);
    if (tool === "select") {
      const which = selectedId && hitHandle(selectedId, x, y);
      if (which) {
        drag = { mode: "handle", which, id: selectedId, before: snapshot(), origin: snapshot([find(selectedId)])[0] };
        return;
      }
      const hit = hitShape(x, y);
      selectedId = hit ? hit.id : null;
      if (hit) {
        drag = { mode: "body", id: hit.id, x, y, before: snapshot(), origin: snapshot([hit])[0] };
      }
      updateExtend();
      redraw();
      return;
    }
    const point = place(x, y);
    if (!point) {
      console.warn("draw point unavailable", x, y);
      report("この位置には点を置けません。");
      return;
    }
    if (tool === "trend" || tool === "box") {
      if (!draft || draft.kind !== tool) {
        draft = { id: newId(), kind: tool, a: point, b: { ...point }, color, extend: false };
        redraw();
        return;
      }
      draft.b = point;
      draft.color = color;
      commitDraft();
      return;
    }
    if (shapes.length >= MAX) {
      report("図形は200個までです。");
      return;
    }
    pushUndo();
    const shape = tool === "hline"
      ? { id: newId(), kind: "hline", price: point.price, color }
      : { id: newId(), kind: "vline", time: point.time, color };
    shapes.push(shape);
    selectedId = shape.id;
    dirty = true;
    updateExtend();
    scheduleSave();
    redraw();
  }

  function onMove(ev) {
    const { x, y } = localPoint(ev);
    if (draft && (tool === "trend" || tool === "box")) {
      const point = place(x, y);
      if (point) draft.b = point;
      redraw();
      return;
    }
    if (drag) {
      const shape = find(drag.id);
      if (!shape) return;
      if (drag.mode === "handle") {
        const point = place(x, y);
        if (point) shape[drag.which] = point;
      } else if (shape.kind === "hline") {
        const y0 = priceToY(drag.origin.price);
        const next = y0 == null ? null : place(x, y0 + (y - drag.y));
        if (next) shape.price = next.price;
      } else if (shape.kind === "vline") {
        const x0 = timeToX(drag.origin.time);
        const next = x0 == null ? null : place(x0 + (x - drag.x), y);
        if (next) shape.time = next.time;
      } else {
        const a = movedPoint(drag.origin.a, x - drag.x, y - drag.y);
        const b = movedPoint(drag.origin.b, x - drag.x, y - drag.y);
        if (a) shape.a = a;
        if (b) shape.b = b;
      }
      redraw();
      return;
    }
    if (tool !== "select") return;
    const which = selectedId && hitHandle(selectedId, x, y);
    if (which) canvas.style.cursor = "grab";
    else if (hitShape(x, y)) canvas.style.cursor = "move";
    else canvas.style.cursor = "default";
  }

  function onUp() {
    if (!drag) return;
    const changed = JSON.stringify(shapes) !== JSON.stringify(drag.before);
    if (changed) {
      undo.push(drag.before);
      if (undo.length > 40) undo.shift();
      dirty = true;
      scheduleSave();
    }
    drag = null;
    redraw();
  }

  function removeSelected() {
    if (!selectedId) return;
    pushUndo();
    shapes = shapes.filter((item) => item.id !== selectedId);
    selectedId = null;
    dirty = true;
    updateExtend();
    scheduleSave();
    redraw();
  }

  function setColor(next) {
    color = next;
    markColors();
    const shape = find(selectedId);
    if (!shape || shape.color === next) return;
    pushUndo();
    shape.color = next;
    dirty = true;
    scheduleSave();
    redraw();
  }

  for (const button of toolButtons) {
    button.addEventListener("click", () => setTool(button.dataset.tool));
  }

  extendBtn.addEventListener("click", () => {
    const shape = find(selectedId);
    if (!shape || shape.kind !== "trend") return;
    pushUndo();
    shape.extend = !shape.extend;
    dirty = true;
    updateExtend();
    scheduleSave();
    redraw();
  });

  for (const item of COLORS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "draw-color";
    button.dataset.color = item;
    button.style.background = item;
    button.title = "色";
    button.addEventListener("click", () => setColor(item));
    colorsEl.appendChild(button);
  }
  markColors();

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("wheel", (ev) => {
    if (!canvas.classList.contains("is-armed")) return;
    const chartEl = document.getElementById("chart");
    if (!chartEl) return;
    chartEl.dispatchEvent(new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaX: ev.deltaX,
      deltaY: ev.deltaY,
      deltaMode: ev.deltaMode,
      clientX: ev.clientX,
      clientY: ev.clientY,
      ctrlKey: ev.ctrlKey,
    }));
    ev.preventDefault();
  }, { passive: false });

  document.addEventListener("keydown", (ev) => {
    const tag = ev.target && ev.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (ev.key === "Escape") {
      draft = null;
      setToolsOpen(false);
      return;
    }
    if ((ev.key === "Delete" || ev.key === "Backspace") && selectedId) {
      ev.preventDefault();
      removeSelected();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z" && !ev.shiftKey) {
      const prev = undo.pop();
      if (!prev) return;
      ev.preventDefault();
      shapes = prev;
      selectedId = null;
      draft = null;
      dirty = true;
      updateExtend();
      scheduleSave();
      redraw();
    }
  });

  const toggleBtn = document.getElementById("draw-toggle");

  function setToolsOpen(open) {
    toolsEl.hidden = !open;
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open) setTool("hand");
  }

  if (toggleBtn) {
    toggleBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const open = toolsEl.hidden;
      setToolsOpen(open);
      console.info("draw tools", open ? "open" : "close");
    });
  }

  document.addEventListener("click", (ev) => {
    if (toolsEl.hidden) return;
    const target = ev.target;
    if (!(target instanceof Element)) return;
    if (target.closest("#draw-tools") || target.closest("#draw-toggle") || target.closest("#chart") || target.closest("#draw-layer")) return;
    setToolsOpen(false);
  });

  window.skabuDraw = {
    bind(nextChart, nextSeries, reportError) {
      chart = nextChart;
      series = nextSeries;
      if (typeof reportError === "function") onError = reportError;
      const host = document.getElementById("chart");
      if (host && window.ResizeObserver) {
        const observer = new ResizeObserver(() => redraw());
        observer.observe(host);
      }
      chart.timeScale().subscribeVisibleLogicalRangeChange(() => redraw());
      redraw();
    },
    show(nextCode, nextTf, nextCandles) {
      const next = nextTf === "weekly" ? "weekly" : "daily";
      candles = Array.isArray(nextCandles) ? nextCandles : [];
      if (nextCode === code) {
        tf = next;
        redraw();
        return;
      }
      clearTimeout(saveTimer);
      if (dirty && code) flushSave(code, tf, snapshot());
      code = nextCode || "";
      tf = next;
      shapes = [];
      selectedId = null;
      draft = null;
      undo = [];
      dirty = false;
      updateExtend();
      redraw();
      loadDrawings();
    },
    redraw,
  };
})();
