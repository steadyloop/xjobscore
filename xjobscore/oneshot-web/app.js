(function (root) {
  "use strict";

  const FILTERS = [
    { key: "summer_internships", label: "Summer Internships", types: ["summer_internship"] },
    { key: "spring_coop", label: "Spring Co-op", types: ["spring_internship", "spring_coop"] },
    { key: "research", label: "Research", types: ["student_researcher"] },
    { key: "early_career", label: "Early-Career", types: ["student_role"] },
  ];
  const opportunityTypes = filters => filters.flatMap(key => FILTERS.find(filter => filter.key === key)?.types || []);
  const relativeTime = (value, now = new Date()) => {
    if (!value) return "Never polled";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Unknown";
    const seconds = Math.max(0, Math.floor((now - date) / 1000));
    if (seconds < 60) return "Just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };
  const postedPresentation = (value, now = new Date()) => {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "Posted date unavailable";
    const days = Math.max(0, Math.floor((now - date) / 86400000));
    if (days === 0) return "Posted today";
    if (days === 1) return "Posted yesterday";
    return `Posted ${days}d ago`;
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { FILTERS, opportunityTypes, relativeTime, postedPresentation };
  }
  if (!root.document) return;

  const $ = selector => document.querySelector(selector);
  const view = $("#view"), flash = $("#flash");
  const state = { jobs: null, selected: null, filters: [], sort: "fresh", query: "" };
  const esc = value => String(value == null ? "" : value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  const safeHref = value => /^https?:\/\//i.test(String(value || "")) ? String(value) : "";
  const attributes = job => job.attributes || {};
  const skills = job => attributes(job).skills || [];
  const initials = job => job.company.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?";
  const workplace = job => ({ remote: "Remote", hybrid: "Hybrid", on_site: "On-site" })[attributes(job).workplace] || "";

  function announce(message, action) {
    flash.replaceChildren();
    const copy = document.createElement("span");
    copy.textContent = message;
    flash.append(copy);
    if (action) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      button.dataset.action = action.name;
      button.dataset.id = action.id;
      flash.append(button);
    }
    flash.classList.toggle("has-action", Boolean(action));
    flash.classList.add("show");
    clearTimeout(announce.timer);
    announce.timer = setTimeout(() => flash.classList.remove("show", "has-action"), action ? 6000 : 3200);
  }

  const errorState = (title, error) => `<div class="mobile-error-nav"><button class="menu-button" data-action="menu" aria-label="Open navigation">☰</button></div><div class="state"><h1>${esc(title)}</h1><p>${esc(error.message || error)}</p><button class="button primary" data-action="retry">Try again</button></div>`;
  const loading = () => { view.innerHTML = '<div class="state loading"><i></i><h1>Loading opportunities</h1><p>Reading the latest API data.</p></div>'; };
  const tags = job => skills(job).slice(0, 3).map(skill => `<span class="tag">${esc(skill)}</span>`).join("") + (skills(job).length > 3 ? `<span class="tag">+${skills(job).length - 3}</span>` : "");
  const facts = job => [job.location || "Location not listed", attributes(job).term, workplace(job)].filter(Boolean);

  function filtersMarkup() {
    return FILTERS.map(filter => {
      const active = state.filters.includes(filter.key);
      return `<button class="filter ${active ? "active" : ""}" type="button" data-filter="${filter.key}" aria-pressed="${active}">${filter.label}</button>`;
    }).join("");
  }

  function opportunity(job) {
    const recent = new Date(job.posted_at).getTime() > Date.now() - 3 * 86400000;
    return `<article class="opportunity ${String(job.id) === String(state.selected) ? "selected" : ""}" data-job="${esc(job.id)}" tabindex="0" role="button" aria-selected="${String(job.id) === String(state.selected)}"><div class="job-logo">${esc(initials(job))}</div><div class="opportunity-main"><div class="job-title-line"><h3>${esc(job.title)}</h3>${recent ? '<span class="new-badge">New</span>' : ""}</div><div class="job-company">${esc(job.company)}</div><div class="meta">${esc(facts(job).join(" · "))}</div><div class="tags">${tags(job)}</div><div class="job-footline"><span>${esc(postedPresentation(job.posted_at))}</span></div></div></article>`;
  }

  function detail(job) {
    if (!job) return '<aside class="detail"><div class="section-kicker">Selection</div><h2>Choose a job</h2><p>Select a role to review its details and add it to Applications.</p></aside>';
    const applyUrl = safeHref(job.apply_url);
    return `<aside class="detail"><div class="detail-head-row"><div class="job-logo large">${esc(initials(job))}</div><div><div class="detail-company">${esc(job.company)}</div><div class="meta">${esc(facts(job).join(" · "))}</div></div><span class="job-state">Active</span></div><h2>${esc(job.title)}</h2><div class="detail-actions"><button class="button primary" data-action="add-to-applications" data-id="${esc(job.id)}">Add to Applications</button>${applyUrl ? `<a class="button" href="${esc(applyUrl)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a>` : ""}</div><section><div class="section-kicker">Job details</div><div class="tags">${tags(job) || '<span class="meta">No structured details available</span>'}</div></section><section><div class="section-kicker">Role overview</div><p class="detail-description">${esc(job.description || "No description is available yet.")}</p></section><div class="detail-freshness">${esc(postedPresentation(job.posted_at))}${job.last_seen_at ? ` · Last confirmed active ${esc(relativeTime(job.last_seen_at))}` : ""}</div></aside>`;
  }

  function render() {
    const search = $("#discover-search"), restoreFocus = search && document.activeElement === search, selectionStart = restoreFocus ? search.selectionStart : null;
    const jobs = state.jobs || [], selected = jobs.find(job => String(job.id) === String(state.selected));
    view.innerHTML = `<section class="discover-page"><header class="discover-page-head"><button class="menu-button" data-action="menu" aria-label="Open navigation">☰</button><div><div class="section-kicker">Multi-source intelligence</div><h1>Opportunities</h1></div></header><div class="discover-grid"><div class="discover-search-surface"><label class="discover-search"><svg class="discover-search-icon" aria-hidden="true"><use href="#i-search"/></svg><span class="sr-only">Search opportunities</span><input id="discover-search" data-search placeholder="Search roles, companies, or skills" autocomplete="off" value="${esc(state.query)}"><kbd>⌘K</kbd></label><label class="discover-sort"><select data-sort aria-label="Sort opportunities"><option value="fresh" ${state.sort === "fresh" ? "selected" : ""}>Newest first</option><option value="company" ${state.sort === "company" ? "selected" : ""}>Company A–Z</option><option value="title" ${state.sort === "title" ? "selected" : ""}>Role title A–Z</option></select></label></div><section class="discover-controls-pane" aria-label="Opportunity controls"><div class="discover-filterbar" aria-label="Opportunity types">${filtersMarkup()}</div></section><div class="opportunity-list" aria-label="Opportunity results">${jobs.length ? jobs.map(opportunity).join("") : '<div class="empty"><h2>No matching jobs yet</h2><p>Try another search or clear a filter.</p></div>'}</div>${detail(selected)}</div></section>`;
    if (restoreFocus) {
      const next = $("#discover-search");
      next.focus();
      if (selectionStart != null) next.setSelectionRange(selectionStart, selectionStart);
    }
  }

  async function loadJobs() {
    if (state.jobs === null) loading();
    try {
      const payload = await root.OneShotAPI.jobs.list({ q: state.query, sort: state.sort, opportunity_type: opportunityTypes(state.filters), limit: 100, offset: 0 });
      state.jobs = payload.jobs;
      if (!state.jobs.some(job => String(job.id) === String(state.selected))) state.selected = state.jobs[0]?.id || null;
      render();
    } catch (error) {
      view.innerHTML = errorState("Opportunities couldn’t load", error);
    }
  }

  async function addToApplications(id) {
    const application = await root.OneShotAPI.jobs.addToApplications(id);
    state.jobs = null;
    state.selected = null;
    await loadJobs();
    await root.OneShotOpportunityCount.refresh();
    announce(application.already_exists ? "This job is already in Applications." : "Added to Applications.", application.already_exists ? null : { label: "Undo", name: "undo-add", id: application.id });
  }

  document.addEventListener("click", async event => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    try {
      if (action === "menu") return $(".sidebar").classList.toggle("open");
      if (action === "retry") return start();
      if (action === "add-to-applications") return addToApplications(event.target.closest("[data-id]").dataset.id);
      if (action === "undo-add") {
        await root.OneShotAPI.applications.remove(event.target.closest("[data-id]").dataset.id);
        state.jobs = null;
        await loadJobs();
        await root.OneShotOpportunityCount.refresh();
        return announce("Addition undone.");
      }
      const job = event.target.closest("[data-job]");
      if (job) {
        state.selected = job.dataset.job;
        render();
      }
    } catch (error) {
      announce(error.message || "That action could not be completed.");
    }
  });
  document.addEventListener("click", event => {
    const filter = event.target.closest("[data-filter]");
    if (!filter) return;
    state.filters = state.filters.includes(filter.dataset.filter) ? state.filters.filter(key => key !== filter.dataset.filter) : [...state.filters, filter.dataset.filter];
    loadJobs();
  });
  document.addEventListener("change", event => {
    if (!event.target.matches("[data-sort]")) return;
    state.sort = event.target.value;
    loadJobs();
  });
  document.addEventListener("input", event => {
    if (!event.target.matches("[data-search]")) return;
    state.query = event.target.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(loadJobs, 250);
  });
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      return $("#discover-search")?.focus();
    }
    const focused = event.target.closest?.("[data-job]");
    if (focused && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      state.selected = focused.dataset.job;
      return render();
    }
    if (/input|textarea|select/i.test(document.activeElement.tagName) || !["j", "k"].includes(event.key.toLowerCase())) return;
    const rows = [...document.querySelectorAll("[data-job]")], current = Math.max(0, rows.findIndex(row => row.dataset.job === String(state.selected))), offset = event.key.toLowerCase() === "j" ? 1 : -1, next = rows[current + offset];
    if (next) {
      state.selected = next.dataset.job;
      render();
      document.querySelector(`[data-job="${CSS.escape(String(state.selected))}"]`)?.focus();
    }
  });

  async function start() {
    try {
      await root.OneShotAPI.health();
      await loadJobs();
    } catch (error) {
      view.innerHTML = errorState("OneShot is unavailable", error);
    }
  }
  start();
})(typeof window !== "undefined" ? window : globalThis);
