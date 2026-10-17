(() => {
  function urlHasIndicatorOverride(url) {
    if ((url.get("ind") || "").trim()) return true;
    return [...url.keys()].some((key) => key.includes("."));
  }

  function filterIndicatorUrl(url, catalog) {
    const known = new Set((catalog || []).map((item) => item.id));
    const next = new URLSearchParams(url);
    const requested = (next.get("ind") || "").split(",").map((id) => id.trim()).filter(Boolean);
    const kept = requested.filter((id) => known.has(id));
    if (requested.length !== kept.length) {
      if (kept.length) next.set("ind", kept.join(","));
      else next.delete("ind");
    }
    [...next.keys()].forEach((key) => {
      if (!key.includes(".")) return;
      const pluginId = key.split(".")[0];
      if (!known.has(pluginId)) next.delete(key);
    });
    return next;
  }

  function snapshot(catalog, selected) {
    const items = {};
    catalog.forEach((item) => {
      const current = selected[item.id] || {};
      items[item.id] = {
        enabled: Boolean(current.enabled),
        params: { ...(current.params || {}) },
      };
    });
    return items;
  }

  function applyItem(item, selected, prefsItem, url, usePrefs, enabledFallback) {
    const params = {};
    const saved = prefsItem || {};
    const savedParams = saved.params || {};
    item.params.forEach((spec) => {
      const key = `${item.id}.${spec.name}`;
      if (url.has(key)) params[spec.name] = url.get(key);
      else if (usePrefs && savedParams[spec.name] != null && savedParams[spec.name] !== "") {
        params[spec.name] = savedParams[spec.name];
      } else params[spec.name] = spec.default;
    });
    const fromUrl = (url.get("ind") || "").split(",").filter(Boolean);
    let enabled;
    if (fromUrl.length) enabled = fromUrl.includes(item.id);
    else if (usePrefs && Object.prototype.hasOwnProperty.call(saved, "enabled")) enabled = Boolean(saved.enabled);
    else enabled = enabledFallback(item);
    selected[item.id] = { enabled, params };
  }

  function applyDefaults(catalog, selected) {
    catalog.forEach((item) => {
      const params = {};
      item.params.forEach((spec) => {
        params[spec.name] = spec.default;
      });
      selected[item.id] = { enabled: Boolean(item.enabled_by_default), params };
    });
  }

  function noteText(prefs) {
    if (prefs && prefs.saved && prefs.saved_at) return `保存済 ${prefs.saved_at}`;
    return "未保存";
  }

  window.skabuIndPrefs = {
    urlHasIndicatorOverride,
    filterIndicatorUrl,
    snapshot,
    applyItem,
    applyDefaults,
    noteText,
  };
})();
