(function (root) {
  "use strict";

  const safeDate = value => {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const sourceLabel = value => String(value || "Source").split(/[_-]/).map(part => part ? part[0].toUpperCase() + part.slice(1) : part).join(" ");
  const sourceMark = value => String(value || "?").replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "?";
  const number = value => new Intl.NumberFormat().format(Number(value) || 0);
  const boardLabel = (type, count) => String(type || "").toLowerCase() === "ats" && Number(count) > 0 ? `${number(count)} ${Number(count) === 1 ? "board" : "boards"}` : "";
  const healthKey = value => {
    const key = String(value || "unknown").trim().toLowerCase().replace(/\s+/g, "-");
    return ["healthy", "partial", "needs-attention", "not-polled", "disabled"].includes(key) ? key : "unknown";
  };
  const needsAttention = source => ["partial", "needs-attention", "not-polled"].includes(healthKey(source.health));
  const summarize = payload => {
    const sources = Array.isArray(payload?.sources) ? payload.sources : [];
    return {
      sources,
      sourceCount: Number(payload?.source_count) || sources.length,
      totalCoverage: Number(payload?.total_coverage) || 0,
      totalAddedToday: Number(payload?.total_added_today) || 0,
      attentionCount: sources.filter(needsAttention).length,
    };
  };
  const relativeTime = (value, now = new Date()) => {
    const date = safeDate(value);
    if (!date) return "Never polled";
    const seconds = Math.max(0, Math.floor((now - date) / 1000));
    if (seconds < 60) return "Just now";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    const months = Math.floor(days / 30);
    return `${months}mo ago`;
  };
  const withTimeout = (promise, milliseconds = 8000) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("The source coverage request timed out.")), milliseconds);
    Promise.resolve(promise).then(
      value => { clearTimeout(timer); resolve(value); },
      error => { clearTimeout(timer); reject(error); },
    );
  });

  const exported = { sourceLabel, sourceMark, boardLabel, healthKey, needsAttention, summarize, relativeTime, withTimeout };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (!root.document) return;

  const $ = selector => document.querySelector(selector);
  const content = $("#coverage-content");
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const exactTime = value => {
    const date = safeDate(value);
    return date ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date) : "No successful poll recorded";
  };

  function renderSummary(summary) {
    $("#header-total").textContent = number(summary.totalCoverage);
    $("#source-count").textContent = number(summary.sourceCount);
    $("#role-count").textContent = number(summary.totalCoverage);
    $("#added-count").textContent = summary.totalAddedToday ? `+${number(summary.totalAddedToday)}` : "—";
    const attention = $("#attention-summary");
    attention.hidden = !summary.attentionCount;
    attention.querySelector("span").textContent = `${number(summary.attentionCount)} ${summary.attentionCount === 1 ? "source needs" : "sources need"} attention`;
  }

  function sourceRow(source) {
    const type = String(source.type || "Other"), boards = boardLabel(type, source.board_count), metadata = [type, boards].filter(Boolean).join(" · ");
    const status = String(source.health || "Unknown"), statusKey = healthKey(status), attention = needsAttention(source);
    const polledAt = safeDate(source.last_poll_at), additions = Number(source.added_today) || 0;
    return `<div class="source-row ${attention ? "attention-row" : ""}" role="row">
      <div class="source-cell" data-label="Source" role="cell"><span class="source-mark">${esc(sourceMark(source.source))}</span><div><strong>${esc(sourceLabel(source.source))}</strong><small>${esc(metadata)}</small></div></div>
      <div class="coverage-cell" data-label="Coverage" role="cell"><strong>${esc(number(source.coverage))}</strong><span>roles</span></div>
      <div class="poll-cell" data-label="Last poll" role="cell">${polledAt ? `<time datetime="${esc(polledAt.toISOString())}" title="${esc(exactTime(polledAt))}">${esc(relativeTime(polledAt))}</time>` : `<span>Never polled</span>`}<small>${esc(exactTime(polledAt))}</small></div>
      <div data-label="Health" role="cell"><span class="health-pill ${esc(statusKey)}"><i aria-hidden="true"></i>${esc(status)}</span></div>
      <div class="change-cell ${additions ? "positive" : "zero"}" data-label="Today" role="cell">${additions ? `+${esc(number(additions))}` : "—"}</div>
    </div>`;
  }

  function render(payload) {
    const summary = summarize(payload);
    renderSummary(summary);
    if (!summary.sources.length) {
      content.innerHTML = `<div class="state"><span class="state-icon"><svg aria-hidden="true"><use href="#icon-sources"/></svg></span><h2>No source coverage yet</h2><p>Configured sources will appear here after coverage data becomes available.</p></div>`;
      return;
    }
    content.innerHTML = `<div class="source-table" role="table" aria-label="Source coverage">
      <div class="table-head" role="row"><span role="columnheader">Source</span><span role="columnheader">Coverage</span><span role="columnheader">Last poll</span><span role="columnheader">Health</span><span role="columnheader">Today</span></div>
      <div class="table-body" role="rowgroup">${summary.sources.map(sourceRow).join("")}</div>
    </div>`;
  }

  function renderError(error) {
    content.innerHTML = `<div class="state"><span class="state-icon error"><svg aria-hidden="true"><use href="#icon-alert"/></svg></span><h2>Source coverage could not load</h2><p>${esc(error?.message || error || "The coverage service is unavailable.")}</p><button type="button" data-action="retry">Try again</button></div>`;
  }

  async function loadCoverage() {
    content.innerHTML = `<div class="state loading-state"><span class="spinner"></span><p>Loading source coverage…</p></div>`;
    try {
      render(await withTimeout(root.OneShotAPI.sources.coverage()));
    } catch (error) {
      renderError(error);
    }
  }

  document.addEventListener("click", event => {
    if (event.target.closest('[data-action="retry"]')) loadCoverage();
  });
  loadCoverage();
})(typeof window !== "undefined" ? window : globalThis);
