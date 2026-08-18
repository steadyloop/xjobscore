(function (root) {
  "use strict";

  const FILTERS = [
    { key: "internships", label: "Internships", types: ["internship"] },
    { key: "coops", label: "Co-ops", types: ["coop"] },
    { key: "student_programs", label: "Student Programs", types: ["student_researcher", "fellowship", "apprenticeship", "new_grad_program", "student_role"] },
  ];
  const DEGREE_FILTERS = [
    { key: "computer_science", label: "CS" },
    { key: "mathematics", label: "Math" },
    { key: "bioengineering", label: "Bioengineering" },
  ];
  const SOURCE_LABELS = {
    adp: "ADP",
    ashby: "Ashby",
    greenhouse: "Greenhouse",
    icims: "iCIMS",
    lever: "Lever",
    smartrecruiters: "SmartRecruiters",
    workday: "Workday",
  };
  const PAGE_SIZE = 100;
  const opportunityTypes = filters => filters.flatMap(key => FILTERS.find(filter => filter.key === key)?.types || []);
  const degreeFields = filters => filters.filter(key => DEGREE_FILTERS.some(filter => filter.key === key));
  const esc = value => String(value == null ? "" : value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  const safeHref = value => /^https?:\/\//i.test(String(value || "")) ? String(value) : "";
  const attributes = job => job?.attributes || {};
  function skillDetails(job) {
    const values = Array.isArray(job?.skills) ? job.skills : attributes(job).skills;
    if (!Array.isArray(values)) return [];
    const seen = new Set();
    return values.flatMap(value => {
      const structured = value && typeof value === "object" && !Array.isArray(value);
      const name = String(structured ? value.name || "" : typeof value === "string" ? value : "").trim();
      if (!name) return [];
      const id = String(structured ? value.id || name : name).trim();
      if (!id || seen.has(id)) return [];
      seen.add(id);
      const category = structured && typeof value.category === "string" && value.category.trim() ? value.category.trim() : null;
      return [{ id, name, category }];
    });
  }

  function detailSkillsMarkup(job) {
    const values = skillDetails(job);
    if (!values.length) return "";
    const chips = values.map(skill => `<span class="skill" data-skill-id="${esc(skill.id)}"${skill.category ? ` data-skill-category="${esc(skill.category)}"` : ""}>${esc(skill.name)}</span>`).join("");
    return `<section><div class="section-kicker">Skills</div><div class="skill-list">${chips}</div></section>`;
  }
  const initials = job => String(job?.company || "").split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?";
  const workplaceLabel = value => ({ remote: "Remote", hybrid: "Hybrid", on_site: "On-site" })[value] || "";

  function sourceLabel(value) {
    const provider = String(value || "").split(":", 1)[0].trim().toLowerCase();
    if (!provider) return "Source unavailable";
    if (SOURCE_LABELS[provider]) return SOURCE_LABELS[provider];
    return provider.replace(/[-_]+/g, " ").replace(/\b\w/g, character => character.toUpperCase());
  }

  function relativeTime(value, now = new Date()) {
    if (!value) return "Verification unavailable";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Verification unavailable";
    const seconds = Math.max(0, Math.floor((now - date) / 1000));
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  }

  function postedPresentation(value, now = new Date(), firstSeen = null) {
    const days = input => {
      const date = new Date(input);
      return !input || Number.isNaN(date.getTime()) ? null : Math.max(0, Math.floor((now - date) / 86400000));
    };
    const phrase = (verb, count) => count === 0 ? `${verb} today` : count === 1 ? `${verb} yesterday` : `${verb} ${count}d ago`;
    const posted = days(value);
    if (posted !== null) return phrase("Posted", posted);
    const added = days(firstSeen);
    return added === null ? "Date unavailable" : phrase("Added", added);
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { FILTERS, DEGREE_FILTERS, opportunityTypes, degreeFields, safeHref, sourceLabel, relativeTime, postedPresentation, detailSkillsMarkup, skillDetails, workplaceLabel };
  }
  if (!root.document) return;

  const $ = selector => root.document.querySelector(selector);
  const view = $("#view"), flash = $("#flash");
  const state = { jobs: null, total: 0, loadingMore: false, selected: null, filters: ["internships"], degreeFilters: [], sort: "fresh", query: "", expandedDescriptions: new Set() };
  const sampleToggle = () => '<button class="sample-toggle" type="button" data-action="sample-toggle" role="switch" aria-checked="false" aria-label="Turn on sample data"><span class="sample-toggle-copy">Sample data<small>Off</small></span><span class="sample-switch" aria-hidden="true"><i></i></span></button>';

  function syncDescriptionOverflow() {
    root.document.querySelectorAll("[data-description-shell]").forEach(shell => {
      const body = shell.querySelector("[data-description-body]");
      const button = shell.querySelector("[data-action='toggle-description']");
      if (!body || !button) return;
      const expanded = button.getAttribute("aria-expanded") === "true";
      const overflow = expanded || body.scrollHeight > body.clientHeight + 1;
      shell.classList.toggle("has-overflow", overflow && !expanded);
      button.hidden = !overflow;
    });
  }

  const paint = html => {
    view.innerHTML = html;
    root.OneShotSampleData?.sync?.();
    root.requestAnimationFrame?.(syncDescriptionOverflow);
  };

  function announce(message, action) {
    flash.replaceChildren();
    const copy = root.document.createElement("span");
    copy.textContent = message;
    flash.append(copy);
    if (action) {
      const button = root.document.createElement("button");
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

  const errorState = (title, error) => `<header class="discover-page-head">${sampleToggle()}</header><div class="state"><h1>${esc(title)}</h1><p>${esc(error.message || error)}</p><button class="button primary" data-action="retry">Try again</button></div>`;
  const loading = () => paint('<div class="state loading"><i></i><h1>Loading Opportunities</h1><p>Reading the latest API data.</p></div>');
  const freshness = job => postedPresentation(job.posted_at, new Date(), job.first_seen_at);
  const listFacts = job => [job.location || "Location not listed", attributes(job).term, workplaceLabel(attributes(job).workplace)].filter(Boolean);
  const listTags = job => {
    const values = skillDetails(job);
    return values.slice(0, 3).map(skill => `<span class="tag">${esc(skill.name)}</span>`).join("") + (values.length > 3 ? `<span class="tag">+${values.length - 3}</span>` : "");
  };

  function filtersMarkup() {
    return FILTERS.map(filter => {
      const active = state.filters.includes(filter.key);
      return `<button class="filter ${active ? "active" : ""}" type="button" role="tab" data-filter="${filter.key}" aria-selected="${active}" tabindex="${active ? "0" : "-1"}">${filter.label}</button>`;
    }).join("");
  }

  function degreeFiltersMarkup() {
    return DEGREE_FILTERS.map(filter => {
      const active = state.degreeFilters.includes(filter.key);
      return `<button class="degree-filter ${active ? "active" : ""}" type="button" data-degree-filter="${filter.key}" aria-pressed="${active}">${filter.label}</button>`;
    }).join("");
  }

  function opportunity(job) {
    const recent = new Date(job.posted_at || job.first_seen_at).getTime() > Date.now() - 3 * 86400000;
    const selected = String(job.id) === String(state.selected);
    return `<article class="opportunity ${selected ? "selected" : ""}" data-job="${esc(job.id)}" tabindex="0" role="button" aria-selected="${selected}"><div class="job-logo">${esc(initials(job))}</div><div class="opportunity-main"><div class="job-title-line"><h3>${esc(job.title)}</h3>${recent ? '<span class="new-badge">New</span>' : ""}</div><div class="job-company">${esc(job.company)}</div><div class="meta">${esc(listFacts(job).join(" · "))}</div>${listTags(job) ? `<div class="tags">${listTags(job)}</div>` : ""}<div class="job-footline">${esc(freshness(job))}</div></div></article>`;
  }

  const BULLETS = /\s*[•●▪‣]\s*/;
  function descriptionContent(text) {
    const lines = text.split(/\n+/).map(line => line.trim()).filter(Boolean);
    if (lines.length > 1) return lines.map(line => `<p>${esc(line)}</p>`).join("");
    const [lead, ...rest] = text.split(BULLETS);
    const items = rest.map(item => item.trim()).filter(Boolean);
    if (!items.length) return `<p>${esc(text)}</p>`;
    const intro = lead.trim() ? `<p>${esc(lead.trim())}</p>` : "";
    return `${intro}<ul>${items.map(item => `<li>${esc(item)}</li>`).join("")}</ul>`;
  }

  function descriptionMarkup(job) {
    const text = String(job.description || "").trim();
    if (!text) return '<p class="description-missing">Description not available. View the original posting for complete details.</p>';
    const expanded = state.expandedDescriptions.has(String(job.id));
    const bodyId = `job-description-${String(job.id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    return `<div class="description-shell ${expanded ? "is-expanded" : ""}" data-description-shell><div class="description-body" id="${esc(bodyId)}" data-description-body>${descriptionContent(text)}</div><button class="description-toggle" type="button" data-action="toggle-description" data-id="${esc(job.id)}" aria-controls="${esc(bodyId)}" aria-expanded="${expanded}" hidden>${expanded ? "Show less" : "Show full description"}</button></div>`;
  }

  function detail(job) {
    if (!job) return '<aside class="detail"><div class="section-kicker">Selection</div><h2>Choose a job</h2><p>Select a role to review its details and add it to Applications.</p></aside>';
    const applyUrl = safeHref(job.apply_url);
    return `<aside class="detail"><div class="detail-topline"><div class="detail-head-row"><div class="job-logo large">${esc(initials(job))}</div><div class="detail-company">${esc(job.company)}</div></div></div><h2>${esc(job.title)}</h2><div class="detail-actions"><button class="button primary" data-action="add-to-applications" data-id="${esc(job.id)}">Add to Applications</button>${applyUrl ? `<a class="button" href="${esc(applyUrl)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a>` : ""}</div>${detailSkillsMarkup(job)}<section><div class="section-kicker">Job description</div>${descriptionMarkup(job)}</section><div class="detail-freshness">${esc(freshness(job))}${job.last_seen_at ? ` · Last confirmed active ${esc(relativeTime(job.last_seen_at))}` : ""}</div></aside>`;
  }

  function pagerMarkup(jobs) {
    if (!jobs.length) return "";
    const hasMore = jobs.length < state.total;
    return `<div class="list-pager"><span>Showing ${jobs.length.toLocaleString()} of ${state.total.toLocaleString()}</span>${hasMore ? `<button class="button" type="button" data-action="load-more" ${state.loadingMore ? "disabled" : ""}>${state.loadingMore ? "Loading…" : "Load more"}</button>` : ""}</div>`;
  }

  function render() {
    const search = $("#opportunities-v2-search"), restoreFocus = search && root.document.activeElement === search, selectionStart = restoreFocus ? search.selectionStart : null;
    const jobs = state.jobs || [], selected = jobs.find(job => String(job.id) === String(state.selected));
    paint(`<section class="discover-page"><header class="discover-page-head"><div><div class="section-kicker">Opportunity discovery</div><h1>Opportunities</h1></div>${sampleToggle()}</header><div class="discover-grid"><section class="discover-controls-pane" aria-label="Opportunity controls"><div class="discover-filterbar" role="tablist" aria-label="Opportunity types">${filtersMarkup()}</div><div class="degree-filterbar" role="group" aria-label="Degree field mentioned"><span class="degree-filter-label">Degree field mentioned</span>${degreeFiltersMarkup()}</div></section><div class="discover-search-surface"><label class="discover-search"><svg aria-hidden="true"><use href="#icon-search"/></svg><span class="sr-only">Search opportunities</span><input id="opportunities-v2-search" data-search placeholder="Search roles, companies, or skills" autocomplete="off" value="${esc(state.query)}"><kbd>⌘ K</kbd></label><label class="discover-sort"><select data-sort aria-label="Sort opportunities"><option value="fresh" ${state.sort === "fresh" ? "selected" : ""}>Newest first</option><option value="company" ${state.sort === "company" ? "selected" : ""}>Company A–Z</option><option value="title" ${state.sort === "title" ? "selected" : ""}>Role title A–Z</option></select></label></div><div class="opportunity-list" role="tabpanel" aria-label="Opportunity results">${jobs.length ? jobs.map(opportunity).join("") : '<div class="empty"><h2>No matching jobs yet</h2><p>Try another search.</p></div>'}${pagerMarkup(jobs)}</div>${detail(selected)}</div></section>`);
    if (restoreFocus) {
      const next = $("#opportunities-v2-search");
      next?.focus();
      if (next && selectionStart != null) next.setSelectionRange(selectionStart, selectionStart);
    }
  }

  async function loadJobs({ append = false } = {}) {
    if (append) { state.loadingMore = true; render(); }
    else if (state.jobs === null) loading();
    try {
      const offset = append ? (state.jobs || []).length : 0;
      const payload = await root.OneShotAPI.jobs.list({ q: state.query, sort: state.sort, opportunity_type: opportunityTypes(state.filters), degree_field: degreeFields(state.degreeFilters), limit: PAGE_SIZE, offset });
      state.total = payload.total;
      state.jobs = append ? [...(state.jobs || []), ...payload.jobs] : payload.jobs;
      state.loadingMore = false;
      if (!append && !state.jobs.some(job => String(job.id) === String(state.selected))) state.selected = state.jobs[0]?.id || null;
      render();
    } catch (error) {
      state.loadingMore = false;
      if (append) { render(); announce(error.message || "Couldn’t load more opportunities."); }
      else paint(errorState("Opportunities couldn’t load", error));
    }
  }

  async function addToApplications(id) {
    const listed = state.jobs || [];
    const position = listed.findIndex(job => String(job.id) === String(id));
    const selectedJob = position < 0 ? null : listed[position];
    const successor = position < 0 ? null : listed[position + 1];
    const application = await root.OneShotAPI.jobs.addToApplications(id, selectedJob);
    state.jobs = null;
    state.selected = successor?.id || null;
    await loadJobs();
    await root.OneShotOpportunityCount.refresh();
    announce(application.already_exists ? "This job is already in Applications." : "Added to Applications.", application.already_exists ? null : { label: "Undo", name: "undo-add", id: application.id });
  }

  root.document.addEventListener("click", async event => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    try {
      if (action === "retry") return start();
      if (action === "load-more") return state.loadingMore ? undefined : loadJobs({ append: true });
      if (action === "add-to-applications") return addToApplications(event.target.closest("[data-id]").dataset.id);
      if (action === "undo-add") {
        await root.OneShotAPI.applications.remove(event.target.closest("[data-id]").dataset.id);
        state.jobs = null;
        await loadJobs();
        await root.OneShotOpportunityCount.refresh();
        return announce("Addition undone.");
      }
      if (action === "toggle-description") {
        const button = event.target.closest("[data-id]");
        const id = String(button.dataset.id);
        const expanded = button.getAttribute("aria-expanded") !== "true";
        if (expanded) state.expandedDescriptions.add(id); else state.expandedDescriptions.delete(id);
        const shell = button.closest("[data-description-shell]");
        shell.classList.toggle("is-expanded", expanded);
        shell.classList.toggle("has-overflow", !expanded);
        button.setAttribute("aria-expanded", String(expanded));
        button.textContent = expanded ? "Show less" : "Show full description";
        return;
      }
      const job = event.target.closest("[data-job]");
      if (job) { state.selected = job.dataset.job; render(); }
    } catch (error) {
      announce(error.message || "That action could not be completed.");
    }
  });

  root.document.addEventListener("click", event => {
    const filter = event.target.closest("[data-filter]");
    if (!filter) return;
    state.filters = [filter.dataset.filter];
    loadJobs();
  });
  root.document.addEventListener("click", event => {
    const filter = event.target.closest("[data-degree-filter]");
    if (!filter) return;
    const key = filter.dataset.degreeFilter;
    state.degreeFilters = state.degreeFilters.includes(key)
      ? state.degreeFilters.filter(value => value !== key)
      : [...state.degreeFilters, key];
    loadJobs();
  });
  root.document.addEventListener("keydown", event => {
    const tab = event.target.closest('[role="tab"][data-filter]');
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...tab.parentElement.querySelectorAll('[role="tab"][data-filter]')];
    const at = tabs.indexOf(tab);
    const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs[(at + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    event.preventDefault();
    next.click();
  });
  root.document.addEventListener("change", event => {
    if (!event.target.matches("[data-sort]")) return;
    state.sort = event.target.value;
    loadJobs();
  });
  root.document.addEventListener("input", event => {
    if (!event.target.matches("[data-search]")) return;
    state.query = event.target.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(loadJobs, 250);
  });
  root.document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      return $("#opportunities-v2-search")?.focus();
    }
    const focused = event.target.closest?.("[data-job]");
    if (focused && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      state.selected = focused.dataset.job;
      return render();
    }
    if (/input|textarea|select/i.test(root.document.activeElement.tagName) || !["j", "k"].includes(event.key.toLowerCase())) return;
    const rows = [...root.document.querySelectorAll("[data-job]")];
    const current = Math.max(0, rows.findIndex(row => row.dataset.job === String(state.selected)));
    const next = rows[current + (event.key.toLowerCase() === "j" ? 1 : -1)];
    if (next) {
      state.selected = next.dataset.job;
      render();
      root.document.querySelector(`[data-job="${CSS.escape(String(state.selected))}"]`)?.focus();
    }
  });

  async function start() {
    try { await root.OneShotAPI.health(); await loadJobs(); }
    catch (error) { paint(errorState("OneShot is unavailable", error)); }
  }
  start();
})(typeof window !== "undefined" ? window : globalThis);
