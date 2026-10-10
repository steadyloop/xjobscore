/* Self-contained visual prototype. All provider health data is illustrative.
   No network requests or changes to live feeds are made by this page. */
(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const format = value => value.toLocaleString("en-US");
  const statuses = ["healthy", "retrying", "failing", "overdue"];
  const statusLabels = { healthy: "Healthy", retrying: "Retrying", failing: "Failing", overdue: "Overdue" };
  const PAGE_SIZE = 6;
  const mainProviders = [
    { id: "workday", name: "Workday", initials: "w", boards: 6589, jobs: "1.1M", healthy: 6408, retrying: 42, failing: 6, overdue: 133 },
    { id: "oracle", name: "Oracle", initials: "or", boards: 1813, jobs: "588.3K", healthy: 1798, retrying: 5, failing: 0, overdue: 10 },
    { id: "greenhouse", name: "Greenhouse", initials: "gh", boards: 6917, jobs: "198.2K", healthy: 6814, retrying: 19, failing: 3, overdue: 81 },
    { id: "ukg", name: "UKG", initials: "ukg", boards: 3915, jobs: "153.1K", healthy: 3746, retrying: 42, failing: 3, overdue: 124 },
    { id: "smartrecruiters", name: "SmartRecruiters", initials: "sr", boards: 3042, jobs: "296.4K", healthy: 2970, retrying: 28, failing: 2, overdue: 42 },
    { id: "eightfold", name: "Eightfold", initials: "8f", boards: 70, jobs: "52.8K", healthy: 21, retrying: 17, failing: 9, overdue: 23 },
  ];
  const otherProviders = [
    { id: "bamboohr", name: "BambooHR", initials: "b", boards: 7867, healthy: 7864, retrying: 3, failing: 0, overdue: 0 },
    { id: "lever", name: "Lever", initials: "l", boards: 1893, healthy: 1490, retrying: 55, failing: 0, overdue: 348 },
    { id: "ashby", name: "Ashby", initials: "a", boards: 2029, healthy: 897, retrying: 3, failing: 0, overdue: 1129 },
    { id: "workable", name: "Workable", initials: "w", boards: 13718, healthy: 6767, retrying: 4096, failing: 19, overdue: 2836 },
    { id: "rippling", name: "Rippling", initials: "r", boards: 1615, healthy: 1612, retrying: 3, failing: 0, overdue: 0 },
    { id: "gem", name: "Gem", initials: "g", boards: 925, healthy: 925, retrying: 0, failing: 0, overdue: 0 },
  ];
  const remaining = [
    ["icims", "iCIMS", "ic", 12540, 73, 12, 412],
    ["taleo", "Taleo", "t", 8220, 84, 7, 270],
    ["successfactors", "SuccessFactors", "sf", 6740, 32, 2, 114],
    ["adp", "ADP", "adp", 5960, 11, 0, 28],
    ["dayforce", "Dayforce", "d", 4100, 8, 0, 12],
    ["teamtailor", "Teamtailor", "tt", 3890, 6, 0, 0],
    ["personio", "Personio", "p", 3540, 0, 0, 0],
    ["jazzhr", "JazzHR", "j", 3100, 7, 0, 22],
    ["recruitee", "Recruitee", "re", 2940, 4, 0, 3],
    ["jobvite", "Jobvite", "jv", 2320, 12, 1, 19],
    ["comeet", "Comeet", "c", 1730, 0, 0, 0],
    ["breezy", "Breezy", "br", 1620, 3, 0, 0],
    ["pinpoint", "Pinpoint", "pi", 1340, 0, 0, 0],
    ["fountain", "Fountain", "f", 1120, 5, 0, 9],
    ["avature", "Avature", "av", 860, 7, 1, 8],
    ["manatal", "Manatal", "m", 620, 0, 0, 0],
    ["recruitcrm", "Recruit CRM", "rc", 420, 0, 0, 0],
    ["bullhorn", "Bullhorn", "bu", 210, 1, 0, 0],
    ["vincere", "Vincere", "v", 120, 0, 0, 0],
    ["talentreef", "TalentReef", "tr", 90, 0, 0, 0],
    ["zohorecruit", "Zoho Recruit", "z", 70, 0, 0, 0],
    ["trakstar", "Trakstar", "ts", 35, 0, 0, 0],
    ["oleeo", "Oleeo", "o", 5, 0, 0, 0],
    ["usajobs", "USAJOBS", "us", 1, 0, 0, 0],
  ];
  // Keep the sample board inventory consistent with the supplied overview.
  remaining[0][3] += 89638 - otherProviders.reduce((sum, p) => sum + p.boards, 0) - remaining.reduce((sum, p) => sum + p[3], 0);
  for (const [id, name, initials, boards, retrying, failing, overdue] of remaining) {
    otherProviders.push({ id, name, initials, boards, healthy: boards - retrying - failing - overdue, retrying, failing, overdue });
  }
  const providers = [...mainProviders, ...otherProviders];
  const byId = new Map(providers.map(provider => [provider.id, provider]));
  const state = { filter: "all", search: "", sort: "issues", page: 1 };
  const mapLayout = new WeakMap();
  const mapStates = new Map();
  let lastDialogTrigger = null;

  const attention = provider => provider.boards - provider.healthy;
  const percent = provider => provider.healthy / provider.boards * 100;
  const percentText = provider => percent(provider) >= 99.95 ? "100" : percent(provider).toFixed(1);
  const tone = provider => percent(provider) < 60 ? "error" : percent(provider) < 90 ? "warn" : "normal";
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const avatar = provider => `<span class="provider-avatar" data-vendor="${provider.id}" aria-hidden="true">${provider.initials}</span>`;
  const healthTrack = provider => `<div class="health-track" aria-hidden="true">${statuses.map(status => `<span class="${status}" style="width:${provider[status] / provider.boards * 100}%"></span>`).join("")}</div>`;

  function matches(provider) {
    return (state.filter !== "issues" || attention(provider) > 0) && provider.name.toLowerCase().includes(state.search);
  }

  function providerCard(provider) {
    return `<article class="provider-card ${tone(provider) === "error" ? "has-failures" : ""}" aria-labelledby="name-${provider.id}">
      <div class="provider-card-header">
        <div class="provider-identity">${avatar(provider)}<div><h3 class="provider-name" id="name-${provider.id}">${provider.name}</h3><p class="provider-meta"><b>${format(provider.boards)}</b> boards<span class="note-divider">·</span><b>${provider.jobs}</b> indexed jobs</p></div></div>
        <div class="health-headline" data-tone="${tone(provider)}"><div class="health-value">${percentText(provider)}<span>%</span></div><span class="health-label">healthy boards</span></div>
      </div>
      <div class="board-map ${provider.boards < 100 ? "compact" : ""}"><canvas data-provider="${provider.id}" role="img" aria-label="${provider.name} board map: ${format(provider.healthy)} healthy, ${format(provider.retrying)} retrying, ${format(provider.failing)} failing, ${format(provider.overdue)} overdue. Each dot represents one board."></canvas>${provider.boards < 100 ? `<span class="compact-map-note">${icon("alert")}${provider.failing} boards failing</span>` : ""}</div>
      <div class="card-footer"><div class="status-summary">${["retrying", "failing", "overdue"].map(status => `<span class="${status === "failing" && provider[status] ? "has-error" : ""}"><i class="status-dot ${status}" aria-hidden="true"></i><b>${format(provider[status])}</b> ${status}</span>`).join("")}</div><button class="details-button" type="button" data-detail="${provider.id}" aria-label="View ${provider.name} details">Details${icon("arrow")}</button></div>
    </article>`;
  }

  function providerRow(provider) {
    return `<tr>
      <td><button class="table-provider" type="button" data-detail="${provider.id}" aria-label="View ${provider.name} details">${avatar(provider)}${provider.name}</button></td>
      <td class="numeric boards-column">${format(provider.boards)}</td>
      <td class="health-column"><div class="table-health">${healthTrack(provider)}<span class="table-health-value" data-tone="${tone(provider)}">${percentText(provider)}<span>%</span><span class="sr-only"> healthy</span></span></div></td>
      ${["retrying", "failing", "overdue"].map(status => `<td class="numeric ${status === "retrying" ? "retry" : status === "failing" ? "fail" : "overdue"}-column"><span class="count-chip ${provider[status] ? "" : "zero"}" data-tone="${provider[status] ? status : "zero"}">${provider[status] ? format(provider[status]) : "—"}</span></td>`).join("")}
      <td class="row-action"><button class="icon-button" type="button" data-detail="${provider.id}" aria-label="Open ${provider.name} details">${icon("chevron")}</button></td>
    </tr>`;
  }

  function render() {
    $("map-tooltip").hidden = true;
    for (const tab of document.querySelectorAll("[data-filter]")) {
      const active = tab.dataset.filter === state.filter;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-pressed", String(active));
    }
    const main = mainProviders.filter(matches);
    $("provider-grid").innerHTML = main.map(providerCard).join("");
    $("main-count").textContent = main.length;
    $("main-empty").hidden = main.length !== 0;
    document.querySelector(".map-caption").hidden = main.length === 0;
    renderDirectory();
    drawMaps();
  }

  function renderDirectory() {
    let list = otherProviders.filter(matches);
    const compare = {
      issues: (a, b) => attention(b) - attention(a) || a.name.localeCompare(b.name),
      name: (a, b) => a.name.localeCompare(b.name),
      boards: (a, b) => b.boards - a.boards,
      health: (a, b) => percent(b) - percent(a) || a.name.localeCompare(b.name),
    };
    list.sort(compare[state.sort]);
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * PAGE_SIZE;
    $("provider-rows").innerHTML = list.slice(start, start + PAGE_SIZE).map(providerRow).join("");
    $("other-count").textContent = list.length;
    $("directory-description").textContent = `${format(list.reduce((sum, p) => sum + p.boards, 0))} boards ${state.search || state.filter !== "all" ? "matching your filters" : "across the rest of your network"}`;
    $("directory-empty").hidden = list.length !== 0;
    $("page-description").textContent = list.length ? `Showing ${start + 1}–${Math.min(start + PAGE_SIZE, list.length)} of ${list.length} providers` : "0 providers";
    $("pagination").innerHTML = `<button type="button" data-page="${state.page - 1}" ${state.page === 1 ? "disabled" : ""} aria-label="Previous provider page">${icon("chevron").replace('class="icon"', 'class="icon" style="transform:rotate(180deg)"')}</button>${Array.from({ length: pages }, (_, index) => `<button type="button" data-page="${index + 1}" aria-label="Provider page ${index + 1}" ${state.page === index + 1 ? 'aria-current="page"' : ""}>${index + 1}</button>`).join("")}<button type="button" data-page="${state.page + 1}" ${state.page === pages ? "disabled" : ""} aria-label="Next provider page">${icon("chevron")}</button>`;
    $("pagination").hidden = list.length === 0;
  }

  function getBoardStates(provider) {
    if (mapStates.has(provider.id)) return mapStates.get(provider.id);
    const values = [];
    for (const status of statuses) {
      for (let i = 0; i < provider[status]; i++) values.push(status);
    }
    // Seeded shuffle keeps the visual stable across resizing and filtering.
    let seed = provider.boards;
    for (let i = values.length - 1; i > 0; i--) {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      const j = seed % (i + 1);
      [values[i], values[j]] = [values[j], values[i]];
    }
    mapStates.set(provider.id, values);
    return values;
  }

  function drawMaps() {
    const css = getComputedStyle(document.documentElement);
    const colors = { healthy: css.getPropertyValue("--dot-green").trim(), retrying: css.getPropertyValue("--dot-amber").trim(), failing: css.getPropertyValue("--dot-red").trim(), overdue: css.getPropertyValue("--slate").trim() };
    for (const canvas of document.querySelectorAll("canvas[data-provider]")) {
      const provider = byId.get(canvas.dataset.provider);
      const { width, height } = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const context = canvas.getContext("2d");
      context.scale(dpr, dpr);
      let step = Math.min(6, Math.sqrt(width * height / provider.boards));
      let columns = Math.max(1, Math.floor(width / step));
      while (Math.ceil(provider.boards / columns) * step > height) {
        step -= .01;
        columns = Math.max(1, Math.floor(width / step));
      }
      const size = Math.max(.5, step * .62);
      const rows = Math.ceil(provider.boards / columns);
      const offsetY = provider.boards < 100 ? 9 : Math.max(0, (height - rows * step) / 2);
      const values = getBoardStates(provider);
      values.forEach((status, index) => {
        context.fillStyle = colors[status];
        context.fillRect((index % columns) * step, Math.floor(index / columns) * step + offsetY, size, size);
      });
      mapLayout.set(canvas, { step, columns, offsetY, values });
    }
  }

  function boardAt(canvas, event) {
    const layout = mapLayout.get(canvas);
    if (!layout) return null;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top - layout.offsetY;
    const column = Math.floor(x / layout.step);
    const row = Math.floor(y / layout.step);
    if (row < 0 || column < 0 || column >= layout.columns) return null;
    const index = row * layout.columns + column;
    return index < layout.values.length ? { index, status: layout.values[index] } : null;
  }

  function showDetails(id, trigger, board = null) {
    const provider = byId.get(id);
    if (!provider) return;
    lastDialogTrigger = trigger?.matches("canvas") ? document.querySelector(`[data-detail="${id}"]`) : trigger;
    const notice = provider.failing ? `${format(provider.failing)} boards are failing. Review these boards before retrying.` : attention(provider) ? `${format(attention(provider))} boards need attention across retries and overdue checks.` : "All boards are healthy. No issues need attention.";
    $("provider-detail").innerHTML = `<div class="dialog-identity">${avatar(provider)}<div><h2 id="detail-name">${provider.name}</h2><p>${format(provider.boards)} monitored boards${provider.jobs ? ` · ${provider.jobs} indexed jobs` : ""}</p></div></div>
      <div class="dialog-health"><div class="dialog-health-top"><p>Healthy boards</p><strong>${percentText(provider)}%</strong></div>${healthTrack(provider)}</div>
      <dl class="detail-counts">${statuses.map(status => `<div class="detail-count"><dt><i class="status-dot ${status}" aria-hidden="true"></i>${statusLabels[status]}</dt><dd>${format(provider[status])}</dd></div>`).join("")}</dl>
      ${board ? `<p class="selected-board">Selected sample board #${format(board.index + 1)} · ${statusLabels[board.status]}</p>` : ""}
      <div class="detail-notice ${provider.failing ? "error" : attention(provider) ? "" : "positive"}">${icon(attention(provider) ? "alert" : "check")}<span>${notice}</span></div>
      <p class="dialog-note">Illustrative snapshot for design review. Board health is sample data.</p>`;
    $("map-tooltip").hidden = true;
    $("provider-dialog").showModal();
  }

  function setFilter(value) {
    state.filter = value;
    state.page = 1;
    render();
  }

  $("boards-total").textContent = format(providers.reduce((sum, p) => sum + p.boards, 0));
  $("attention-total").textContent = format(providers.reduce((sum, p) => sum + attention(p), 0));
  $("attention-note").textContent = `${providers.filter(p => attention(p)).length} providers with boards to review`;
  $("issues-provider-count").textContent = providers.filter(p => attention(p)).length;

  document.addEventListener("click", event => {
    const detail = event.target.closest("[data-detail]");
    if (detail) showDetails(detail.dataset.detail, detail);
    const filter = event.target.closest("[data-filter]");
    if (filter) setFilter(filter.dataset.filter);
    const pagination = event.target.closest("[data-page]");
    if (pagination && !pagination.disabled) {
      state.page = Number(pagination.dataset.page);
      renderDirectory();
      document.querySelector(`[data-page="${state.page}"][aria-current]`)?.focus({ preventScroll: true });
    }
    const canvas = event.target.closest("canvas[data-provider]");
    if (canvas) showDetails(canvas.dataset.provider, canvas, boardAt(canvas, event));
  });
  $("provider-search").addEventListener("input", event => {
    state.search = event.target.value.trim().toLowerCase();
    state.page = 1;
    render();
  });
  $("provider-sort").addEventListener("change", event => {
    state.sort = event.target.value;
    state.page = 1;
    renderDirectory();
  });
  $("attention-shortcut").addEventListener("click", () => {
    state.search = "";
    $("provider-search").value = "";
    setFilter("issues");
    $("directory").scrollIntoView({ block: "start" });
    $("issues-filter").focus({ preventScroll: true });
  });
  $("theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    const label = `Switch to ${dark ? "light" : "dark"} mode`;
    $("theme-toggle").setAttribute("aria-label", label);
    $("theme-toggle").title = label;
    $("theme-toggle").innerHTML = icon(dark ? "sun" : "moon");
    drawMaps();
  });
  $("refresh").addEventListener("click", () => {
    $("updated").textContent = "Preview refreshed just now";
    drawMaps();
  });
  $("close-dialog").addEventListener("click", () => $("provider-dialog").close());
  $("provider-dialog").addEventListener("click", event => {
    const rect = $("provider-dialog").getBoundingClientRect();
    if (event.target === $("provider-dialog") && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) $("provider-dialog").close();
  });
  $("provider-dialog").addEventListener("close", () => lastDialogTrigger?.focus({ preventScroll: true }));
  document.addEventListener("keydown", event => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !$("provider-dialog").open && !event.target.closest("input, textarea, select, [contenteditable]")) {
      event.preventDefault();
      $("provider-search").focus();
    }
  });
  document.addEventListener("pointermove", event => {
    const canvas = event.target.closest("canvas[data-provider]");
    const tooltip = $("map-tooltip");
    if (!canvas || event.pointerType === "touch") { tooltip.hidden = true; return; }
    const board = boardAt(canvas, event);
    if (!board) { tooltip.hidden = true; return; }
    const provider = byId.get(canvas.dataset.provider);
    tooltip.innerHTML = `${provider.name} · Board #${format(board.index + 1)}<span>${statusLabels[board.status]} · Sample data</span>`;
    tooltip.hidden = false;
    tooltip.style.left = `${Math.max(8, Math.min(event.clientX + 12, innerWidth - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(event.clientY + 14, innerHeight - tooltip.offsetHeight - 8))}px`;
  });
  document.addEventListener("pointerleave", () => { $("map-tooltip").hidden = true; });
  window.addEventListener("scroll", () => { $("map-tooltip").hidden = true; }, { passive: true });
  const observer = new ResizeObserver(drawMaps);
  observer.observe($("provider-grid"));
  render();
})();
