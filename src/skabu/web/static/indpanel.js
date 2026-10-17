(() => {
  const OP_SYMBOL = { eq: "=", lt: "<", lte: "<=", gt: ">", gte: ">=" };
  let docBound = false;
  let ignoreCloseUntil = 0;

  function bindDoc() {
    if (docBound) return;
    docBound = true;
    document.addEventListener("click", (ev) => {
      if (Date.now() < ignoreCloseUntil) return;
      if (!ev.target.closest(".ind-row-help") && !ev.target.closest(".ind-help-btn")) {
        closeRowHelps();
      }
      document.querySelectorAll(".ind-pop-wrap").forEach((wrap) => {
        if (!wrap.contains(ev.target)) closeWrap(wrap);
      });
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      closeRowHelps();
      document.querySelectorAll(".ind-pop-wrap").forEach(closeWrap);
    });
  }

  function resetPopPos(pop) {
    if (!pop) return;
    pop.classList.remove("ind-pop-moved");
    pop.style.position = "";
    pop.style.left = "";
    pop.style.top = "";
  }

  function closeWrap(wrap) {
    wrap.querySelectorAll(".ind-row-help").forEach((tip) => tip.remove());
    wrap.querySelectorAll(".ind-pop").forEach((pop) => {
      pop.hidden = true;
      resetPopPos(pop);
    });
    wrap.querySelectorAll("[aria-expanded]").forEach((btn) => {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function helpHost(el) {
    return el ? el.closest(".ind-pop-row, .ind-pop-group") : null;
  }

  function closeRowHelps(exceptHost) {
    document.querySelectorAll(".ind-row-help").forEach((tip) => {
      const host = helpHost(tip);
      if (exceptHost && host === exceptHost) return;
      tip.remove();
    });
    document.querySelectorAll(".ind-help-btn").forEach((btn) => {
      const host = helpHost(btn);
      if (exceptHost && host === exceptHost && host && host.querySelector(".ind-row-help")) return;
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function attachRowHelp(rowEl, item) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ind-help-btn";
    btn.textContent = "？";
    btn.setAttribute("aria-label", `${item.title || item.id}の説明`);
    btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const existing = rowEl.querySelector(".ind-row-help");
      if (existing) {
        existing.remove();
        btn.setAttribute("aria-expanded", "false");
        return;
      }
      closeRowHelps();
      const tip = document.createElement("div");
      tip.className = "ind-row-help";
      tip.addEventListener("click", (inner) => inner.stopPropagation());
      const head = document.createElement("div");
      head.className = "ind-row-help-head";
      const title = document.createElement("strong");
      title.textContent = item.title || item.id;
      const closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "ind-pop-close";
      closeBtn.setAttribute("aria-label", "閉じる");
      closeBtn.textContent = "×";
      closeBtn.addEventListener("click", (inner) => {
        inner.preventDefault();
        inner.stopPropagation();
        tip.remove();
        btn.setAttribute("aria-expanded", "false");
      });
      head.append(title, closeBtn);
      tip.appendChild(head);
      (itemHelp(item) || "説明なし").split(/\n+/).forEach((para) => {
        if (!para.trim()) return;
        const text = document.createElement("p");
        text.textContent = para.trim();
        tip.appendChild(text);
      });
      const rect = btn.getBoundingClientRect();
      tip.style.position = "fixed";
      tip.style.left = `${Math.max(8, rect.left)}px`;
      tip.style.top = `${rect.bottom + 4}px`;
      rowEl.appendChild(tip);
      const tipRect = tip.getBoundingClientRect();
      const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - tipRect.width - 8));
      let top = rect.bottom + 4;
      if (top + tipRect.height > window.innerHeight - 8) {
        top = Math.max(8, rect.top - tipRect.height - 4);
      }
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
      btn.setAttribute("aria-expanded", "true");
    });
    return btn;
  }

  function fallbackGroups() {
    return [
      { id: "trend", title: "向き" },
      { id: "power", title: "勢い" },
      { id: "shape", title: "形" },
      { id: "wave", title: "波" },
      { id: "market", title: "市場全体" },
      { id: "facts", title: "会社の数字" },
      { id: "model", title: "機械の点数" },
      { id: "other", title: "その他" },
    ];
  }

  function resolveGroups(opts) {
    try {
      const raw = typeof opts.groups === "function" ? opts.groups() : opts.groups;
      if (raw && raw.length) return raw;
    } catch (err) {
      console.warn("indpanel groups failed", err);
    }
    return fallbackGroups();
  }

  function groupedItems(items, groups) {
    const order = [];
    const seen = new Set();
    (groups || []).forEach((item) => {
      if (item && item.id && !seen.has(item.id)) {
        order.push(item.id);
        seen.add(item.id);
      }
    });
    if (!seen.has("other")) {
      order.push("other");
      seen.add("other");
    }
    const buckets = {};
    order.forEach((id) => {
      buckets[id] = [];
    });
    (items || []).forEach((item) => {
      const gid = item.group || "other";
      if (!buckets[gid]) {
        buckets[gid] = [];
        order.push(gid);
      }
      buckets[gid].push(item);
    });
    const metaById = {};
    fallbackGroups().forEach((item) => {
      metaById[item.id] = item;
    });
    (groups || []).forEach((item) => {
      if (item && item.id) metaById[item.id] = item;
    });
    return order
      .filter((id) => buckets[id] && buckets[id].length)
      .map((id) => ({
        meta: metaById[id] || { id, title: id },
        items: buckets[id],
      }));
  }

  function bySurface(items, name) {
    return (items || []).filter((item) => {
      const surfaces = item.surface && item.surface.length ? item.surface : ["chart", "score"];
      return surfaces.includes(name);
    });
  }

  function itemHelp(item) {
    const bits = [];
    if (item.help) bits.push(String(item.help).trim());
    else if (item.description) bits.push(item.description);
    const params = (item.params || [])
      .map((spec) => `${spec.label || spec.name}は最初 ${spec.default}`)
      .join("、");
    if (params) bits.push(`設定: ${params}。`);
    if (item.default_op) {
      const symbol = OP_SYMBOL[item.default_op] || item.default_op;
      bits.push(`この門の最初の設定は ${symbol}${item.default_value}。数字が無い株は落ちます。`);
    }
    return bits.join("\n\n");
  }

  function makePop(titleText) {
    const pop = document.createElement("div");
    pop.className = "ind-pop";
    pop.hidden = true;
    pop.addEventListener("click", (ev) => ev.stopPropagation());
    const head = document.createElement("div");
    head.className = "ind-pop-head";
    const title = document.createElement("span");
    title.className = "ind-pop-title";
    title.textContent = titleText;
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "ind-pop-close";
    closeBtn.setAttribute("aria-label", "閉じる");
    closeBtn.textContent = "×";
    const body = document.createElement("div");
    body.className = "ind-pop-body";
    head.append(title, closeBtn);
    pop.append(head, body);
    bindDrag(pop, head);
    return { pop, body, closeBtn };
  }

  function bindDrag(pop, handle) {
    let drag = null;
    handle.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      if (ev.target.closest("input, select, button, a")) return;
      ev.preventDefault();
      ev.stopPropagation();
      const rect = pop.getBoundingClientRect();
      pop.classList.add("ind-pop-moved", "ind-pop-dragging");
      pop.style.position = "fixed";
      pop.style.left = `${rect.left}px`;
      pop.style.top = `${rect.top}px`;
      drag = {
        dx: ev.clientX - rect.left,
        dy: ev.clientY - rect.top,
        moved: false,
        pointerId: ev.pointerId,
      };
      try {
        handle.setPointerCapture(ev.pointerId);
      } catch (err) {
        console.warn("indpanel drag capture failed", err);
      }
    });
    handle.addEventListener("pointermove", (ev) => {
      if (!drag) return;
      const maxX = Math.max(8, window.innerWidth - 80);
      const maxY = Math.max(8, window.innerHeight - 40);
      const x = Math.min(Math.max(8, ev.clientX - drag.dx), maxX);
      const y = Math.min(Math.max(8, ev.clientY - drag.dy), maxY);
      if (Math.abs(ev.clientX - (drag.dx + parseFloat(pop.style.left || "0"))) > 2
        || Math.abs(ev.clientY - (drag.dy + parseFloat(pop.style.top || "0"))) > 2) {
        drag.moved = true;
      }
      pop.style.left = `${x}px`;
      pop.style.top = `${y}px`;
    });
    function endDrag(ev) {
      if (!drag) return;
      if (drag.moved) ignoreCloseUntil = Date.now() + 200;
      drag = null;
      pop.classList.remove("ind-pop-dragging");
      if (ev && handle.hasPointerCapture && handle.hasPointerCapture(ev.pointerId)) {
        handle.releasePointerCapture(ev.pointerId);
      }
    }
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);
  }

  function summarizeIndicators(catalog, selected) {
    const names = (catalog || [])
      .filter((item) => selected[item.id] && selected[item.id].enabled)
      .map((item) => item.title);
    return names.length ? names.join(", ") : "なし";
  }

  function summarizeGates(catalog, gates) {
    const parts = [];
    (catalog || []).forEach((item) => {
      const key = `${item.id}.${item.feature}`;
      const current = gates[key];
      if (!current || !current.enabled) return;
      const symbol = OP_SYMBOL[current.op] || "=";
      parts.push(`${item.title}${symbol}${current.value}`);
    });
    return parts.length ? parts.join(", ") : "なし";
  }

  function fillIndicatorRow(rowEl, item, selected, opts) {
    const locked = Boolean(opts && opts.locked);
    const current = selected || { enabled: false, params: {} };
    if (!current.params) current.params = {};
    const head = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = Boolean(current.enabled);
    box.disabled = locked;
    const params = document.createElement("div");
    params.className = "ind-params";
    params.hidden = !current.enabled;
    box.addEventListener("change", () => {
      current.enabled = box.checked;
      params.hidden = !current.enabled;
      if (opts && opts.onChange) opts.onChange();
    });
    head.append(box, document.createTextNode(` ${item.title}`));
    const helpBtn = attachRowHelp(rowEl, item);
    (item.params || []).forEach((spec) => {
      const lab = document.createElement("label");
      const input = document.createElement("input");
      input.type = "number";
      if (spec.min != null) input.min = spec.min;
      if (spec.max != null) input.max = spec.max;
      input.step = spec.step || (spec.kind === "float" ? "0.001" : "1");
      input.value = current.params[spec.name] != null ? current.params[spec.name] : spec.default;
      input.disabled = locked;
      input.addEventListener("change", () => {
        current.params[spec.name] = input.value;
        if (opts && opts.onChange) opts.onChange();
      });
      lab.append(`${spec.label} `, input);
      params.appendChild(lab);
    });
    rowEl.append(head, helpBtn, params);
  }

  function fillGateRow(rowEl, item, selected, opts) {
    const locked = Boolean(opts && opts.locked);
    const current = selected || { enabled: false, op: item.default_op, value: item.default_value };
    const head = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = Boolean(current.enabled);
    box.disabled = locked;
    const params = document.createElement("div");
    params.className = "ind-params";
    params.hidden = !current.enabled;
    box.addEventListener("change", () => {
      current.enabled = box.checked;
      params.hidden = !current.enabled;
      if (opts && opts.onChange) opts.onChange();
    });
    head.append(box, document.createTextNode(` ${item.title}`));
    const helpBtn = attachRowHelp(rowEl, item);
    const opSel = document.createElement("select");
    (item.ops || ["eq"]).forEach((op) => {
      const opt = document.createElement("option");
      opt.value = op;
      opt.textContent = OP_SYMBOL[op] || op;
      if (op === current.op) opt.selected = true;
      opSel.appendChild(opt);
    });
    opSel.disabled = locked;
    opSel.addEventListener("change", () => {
      current.op = opSel.value;
      if (opts && opts.onChange) opts.onChange();
    });
    const valInput = document.createElement("input");
    valInput.type = "number";
    valInput.step = item.value_kind === "int" ? "1" : "0.01";
    valInput.value = current.value;
    valInput.disabled = locked;
    valInput.addEventListener("change", () => {
      current.value = valInput.value === "" ? item.default_value : Number(valInput.value);
      if (opts && opts.onChange) opts.onChange();
    });
    const opLab = document.createElement("label");
    opLab.append("演算 ", opSel);
    const valLab = document.createElement("label");
    valLab.append("閾値 ", valInput);
    params.append(opLab, valLab);
    rowEl.append(head, helpBtn, params);
  }

  function mount(opts) {
    bindDoc();
    const host = opts.host;
    if (!host) {
      console.error("indpanel host missing", opts && opts.label);
      return { refresh() {}, setLocked() {} };
    }
    host.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "ind-pop-wrap";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ind-pop-btn";
    btn.textContent = opts.label || "設定";
    btn.setAttribute("aria-expanded", "false");
    const summary = document.createElement("span");
    summary.className = "ind-pop-summary";
    const settings = makePop(opts.label || "設定");
    const pop = settings.pop;
    const body = settings.body;
    settings.closeBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      closeWrap(wrap);
    });

    function isLocked() {
      return Boolean(typeof opts.locked === "function" ? opts.locked() : opts.locked);
    }

    function renderBody() {
      body.innerHTML = "";
      const list = document.createElement("div");
      list.className = "ind-pop-list";
      const items = (typeof opts.items === "function" ? opts.items() : opts.items) || [];
      groupedItems(items, resolveGroups(opts)).forEach((bucket) => {
        const head = document.createElement("div");
        head.className = "ind-pop-group";
        const title = document.createElement("span");
        title.className = "ind-pop-group-title";
        title.textContent = bucket.meta.title || bucket.meta.id;
        head.append(title, attachRowHelp(head, bucket.meta));
        list.appendChild(head);
        bucket.items.forEach((item) => {
          const row = document.createElement("div");
          row.className = "ind-pop-row";
          opts.renderRow(row, item, { locked: isLocked() });
          list.appendChild(row);
        });
      });
      body.appendChild(list);
      if (!isLocked() && (opts.onSave || opts.onReset)) {
        const actions = document.createElement("div");
        actions.className = "ind-actions";
        if (opts.onSave) {
          const save = document.createElement("button");
          save.type = "button";
          save.textContent = "保存";
          save.addEventListener("click", opts.onSave);
          actions.appendChild(save);
        }
        if (opts.onReset) {
          const reset = document.createElement("button");
          reset.type = "button";
          reset.textContent = "初期化";
          reset.addEventListener("click", opts.onReset);
          actions.appendChild(reset);
        }
        const note = document.createElement("span");
        note.className = "ind-pref-note";
        note.id = opts.noteId || "";
        actions.appendChild(note);
        body.appendChild(actions);
        if (opts.onNote) opts.onNote();
      }
    }

    function refresh() {
      try {
        summary.textContent = (opts.summary && opts.summary()) || "なし";
      } catch (err) {
        console.error("indpanel summary failed", err);
        summary.textContent = "なし";
      }
      wrap.classList.toggle("ind-pop-locked", isLocked());
      if (!pop.hidden) renderBody();
    }

    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = pop.hidden;
      document.querySelectorAll(".ind-pop-wrap").forEach((other) => {
        if (other !== wrap) closeWrap(other);
      });
      if (willOpen) {
        pop.hidden = false;
        btn.setAttribute("aria-expanded", "true");
        renderBody();
      } else {
        closeWrap(wrap);
      }
    });

    wrap.append(btn, summary, pop);
    host.appendChild(wrap);
    refresh();
    return {
      refresh,
      setLocked(next) {
        opts.locked = next;
        refresh();
      },
    };
  }

  window.skabuIndPanel = {
    mount,
    fillIndicatorRow,
    fillGateRow,
    summarizeIndicators,
    summarizeGates,
    bySurface,
    itemHelp,
    OP_SYMBOL,
  };
})();
