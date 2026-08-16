/* Display formatting helpers shared by every page. */

export function formatBytes(value) {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let scaled = value / 1024;
  for (const unit of units) {
    if (scaled < 1024) return `${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1)} ${unit}`;
    scaled /= 1024;
  }
  return `${scaled.toFixed(1)} TB`;
}

export function formatMs(value) {
  if (value == null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`;
}

export function formatCount(value) {
  if (value == null) return "—";
  return new Intl.NumberFormat().format(value);
}

export function relativeTime(iso) {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  const units = [
    [60, "s"],
    [3600, "m", 60],
    [86400, "h", 3600],
    [Infinity, "d", 86400],
  ];
  for (const [limit, suffix, divisor = 1] of units) {
    if (abs < limit) {
      const amount = Math.max(1, Math.round(abs / divisor));
      return seconds <= 0 ? `${amount}${suffix} ago` : `in ${amount}${suffix}`;
    }
  }
  return iso;
}

export function formatInterval(seconds) {
  if (seconds == null) return "—";
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} m`;
  return `${seconds} s`;
}

export function statusLabel(status) {
  return (
    {
      ok: "OK",
      not_modified: "304",
      partial: "Partial",
      error: "Error",
      never: "Never run",
    }[status] ?? status ?? "—"
  );
}

/* DOM helper: el("div", {class: "x", onclick}, child, ...) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value == null) continue;
    if (name.startsWith("on") && typeof value === "function") {
      node.addEventListener(name.slice(2), value);
    } else if (name === "dataset") {
      Object.assign(node.dataset, value);
    } else {
      node.setAttribute(name, value);
    }
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function statusChip(status, label) {
  return el(
    "span",
    { class: "chip", "data-status": status ?? "never" },
    el("span", { class: "dot" }),
    label ?? statusLabel(status)
  );
}
