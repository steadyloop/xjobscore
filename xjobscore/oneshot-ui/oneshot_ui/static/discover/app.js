(function (root) {
  "use strict";

  const LOCAL_KEY = "oneshot.discover.sample.workspace.v1";
  const DAY = 86400000;
  const PROFILE = Object.freeze({
    "sample-research": { strength: "strong", salary: "$38–46/hr", deadlineDays: 5, changed: true, reasons: ["Distributed systems and ML research overlap", "Remote within the United States", "Spring 2027 term matches your timeline"], required: ["Python", "Distributed training", "Research experience"], preferred: ["JAX", "Ray", "Technical writing"], source: "Employer careers · Greenhouse", authority: "Official employer source", sponsorship: "Not specified", education: "Currently enrolled in CS, Math, or related field" },
    "sample-quant": { strength: "strong", salary: "$48–62/hr", deadlineDays: 3, changed: false, reasons: ["Python, C++, and Linux align with your systems profile", "Summer 2027 internship", "Research infrastructure rather than pure trading"], required: ["Python", "Algorithms", "Linux"], preferred: ["C++", "Low-latency systems", "Quantitative coursework"], source: "Employer careers · Lever", authority: "Official employer source", sponsorship: "Case by case", education: "Pursuing a technical degree" },
    "sample-software": { strength: "good", salary: "$32–38/hr", deadlineDays: 6, changed: true, reasons: ["Backend ownership with production scope", "San Diego hybrid location", "Go and Kubernetes map to infrastructure interests"], required: ["Programming fundamentals", "Backend development", "Git"], preferred: ["Go", "Kubernetes", "PostgreSQL"], source: "Employer careers · Ashby", authority: "Official employer source", sponsorship: "Not specified", education: "Currently enrolled in a technical program" },
    "sample-embedded": { strength: "review", salary: "$29–35/hr", deadlineDays: 13, changed: false, reasons: ["Strong C++ and Linux overlap", "Hands-on systems work", "Fall co-op may require schedule review"], required: ["C or C++", "Computer architecture", "Debugging"], preferred: ["RTOS", "Drivers", "Hardware bring-up"], source: "Employer careers · Lever", authority: "Official employer source", sponsorship: "No information", education: "Computer Engineering or CS student" },
    "sample-platform": { strength: "strong", salary: "$35–43/hr", deadlineDays: 8, changed: false, reasons: ["Backend APIs and data services", "Production ownership with infrastructure team", "PostgreSQL experience is directly relevant"], required: ["TypeScript or similar", "SQL", "API design"], preferred: ["AWS", "PostgreSQL", "Observability"], source: "Employer careers · Workday", authority: "Official employer source", sponsorship: "Not specified", education: "Rising junior or senior" },
    "sample-ml-research": { strength: "good", salary: "$37–45/hr", deadlineDays: 10, changed: true, reasons: ["ML evaluation and reproducible research", "PyTorch and Python match", "Biology domain would be new"], required: ["Python", "Machine learning", "Experimental design"], preferred: ["PyTorch", "Transformers", "Biology interest"], source: "Employer careers · Greenhouse", authority: "Official employer source", sponsorship: "Available", education: "Graduate or advanced undergraduate student" },
  });

  const safeDate = value => { const date = new Date(value || ""); return Number.isNaN(date.getTime()) ? null : date; };
  const daysAgo = (value, now = new Date()) => { const date = safeDate(value); return date ? Math.max(0, Math.floor((now - date) / DAY)) : Number.MAX_SAFE_INTEGER; };
  const deadlineFrom = (now, days) => new Date(now.getTime() + days * DAY).toISOString();
  const workplaceOf = job => job.workplace || job.workplace_type || job.attributes?.workplace || "Not specified";
  const skillsOf = job => job.skills || job.semantic_stack || job.stack || job.attributes?.skills || [];
  const termOf = job => job.term || job.attributes?.term || "Term not listed";
  const enrichJob = (job, now = new Date()) => {
    const profile = PROFILE[job.id] || {};
    return {
      ...job,
      location: job.location || job.location_text || "Location not listed",
      workplace: workplaceOf(job),
      term: termOf(job),
      skills: skillsOf(job),
      description: job.description || job.description_text || String(job.description_html || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(),
      posted_at: job.posted_at || job.published_at || job.first_seen_at,
      deadline_at: job.deadline_at || deadlineFrom(now, profile.deadlineDays || 14),
      ...profile,
    };
  };
  const visibleState = local => ({ saved: new Set(local.saved || []), hidden: new Set(local.hidden || []) });
  const filterJobs = (jobs, options = {}, local = {}, now = new Date()) => {
    const query = String(options.query || "").trim().toLowerCase(), sets = visibleState(local), facets = options.facets || {};
    return jobs.filter(job => {
      const hidden = sets.hidden.has(job.id), saved = sets.saved.has(job.id);
      if (options.scope === "hidden" ? !hidden : hidden) return false;
      if (options.scope === "saved" && !saved) return false;
      if (options.scope === "recommended" && !["strong", "good"].includes(job.strength)) return false;
      if (options.scope === "remote" && job.workplace !== "Remote") return false;
      const text = [job.title, job.company, job.location, job.workplace, job.term, job.description, ...(job.skills || []), ...(job.required || [])].join(" ").toLowerCase();
      if (query && !text.includes(query)) return false;
      if (facets.types?.length && !facets.types.includes(job.opportunity_type)) return false;
      if (facets.workplaces?.length && !facets.workplaces.includes(job.workplace)) return false;
      if (facets.freshness && facets.freshness !== "any" && daysAgo(job.posted_at, now) > Number(facets.freshness)) return false;
      if (facets.deadline && safeDate(job.deadline_at) - now > 7 * DAY) return false;
      if (facets.changed && !job.changed) return false;
      return true;
    });
  };
  const sortJobs = (jobs, sort = "recommended") => [...jobs].sort((a, b) => {
    if (sort === "company") return String(a.company).localeCompare(String(b.company));
    if (sort === "newest") return String(b.posted_at || "").localeCompare(String(a.posted_at || ""));
    if (sort === "deadline") return String(a.deadline_at || "9999").localeCompare(String(b.deadline_at || "9999"));
    const rank = { strong: 0, good: 1, review: 2 };
    return (rank[a.strength] ?? 3) - (rank[b.strength] ?? 3) || String(b.posted_at || "").localeCompare(String(a.posted_at || ""));
  });
  const summarize = (jobs, local = {}) => {
    const sets = visibleState(local), available = jobs.filter(job => !sets.hidden.has(job.id));
    return { all: available.length, recommended: available.filter(job => ["strong", "good"].includes(job.strength)).length, remote: available.filter(job => job.workplace === "Remote").length, saved: available.filter(job => sets.saved.has(job.id)).length, hidden: jobs.length - available.length };
  };

  const exported = { PROFILE, enrichJob, filterJobs, sortJobs, summarize, daysAgo };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  root.OneShotDiscoverUtils = exported;
  if (!root.document) return;

  const $ = selector => document.querySelector(selector), $$ = selector => [...document.querySelectorAll(selector)];
  const results = $("#job-results"), detail = $("#job-detail"), filterDialog = $("#filter-dialog"), compareDialog = $("#compare-dialog"), saveViewDialog = $("#save-view-dialog"), toast = $("#toast");
  const defaultLocal = { saved: [], hidden: [], compared: [], savedViews: [] };
  const readLocal = () => { try { return { ...defaultLocal, ...(JSON.parse(root.localStorage.getItem(LOCAL_KEY)) || {}) }; } catch (_) { return { ...defaultLocal }; } };
  const writeLocal = value => root.localStorage.setItem(LOCAL_KEY, JSON.stringify(value));
  const state = { jobs: [], query: "", scope: "all", sort: "recommended", facets: { types: [], workplaces: [], freshness: "any", deadline: false, changed: false }, selectedId: null, detailTab: "overview", local: readLocal(), sample: Boolean(root.OneShotSampleData?.isEnabled?.()) };
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const safeHref = value => /^https?:\/\//i.test(String(value || "")) ? String(value) : "";
  const initials = value => String(value || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase();
  const formatDate = value => { const date = safeDate(value); return date ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date) : "Not listed"; };
  const relativePosted = value => { const days = daysAgo(value); return days === 0 ? "Posted today" : days === 1 ? "Posted yesterday" : days < 1000 ? `Posted ${days}d ago` : "Posted date unavailable"; };
  const deadlineLabel = value => { const date = safeDate(value); if (!date) return "No deadline"; const days = Math.ceil((date - new Date()) / DAY); return days <= 0 ? "Closing today" : days === 1 ? "Closes tomorrow" : `Closes in ${days}d`; };
  const selectedJob = () => state.jobs.find(job => String(job.id) === String(state.selectedId));
  const saveState = () => writeLocal(state.local);
  const showToast = (message, action) => { toast.replaceChildren(); const span = document.createElement("span"); span.textContent = message; toast.append(span); if (action) { const button = document.createElement("button"); button.type = "button"; button.textContent = action.label; button.addEventListener("click", action.run, { once: true }); toast.append(button); } toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.remove("show"), action ? 6500 : 3400); };
  const facetCount = () => state.facets.types.length + state.facets.workplaces.length + Number(state.facets.freshness !== "any") + Number(state.facets.deadline) + Number(state.facets.changed);

  function jobSignals(job) {
    const signals = [];
    if (daysAgo(job.posted_at) <= 2) signals.push('<em class="signal new">New</em>');
    if (job.changed) signals.push('<em class="signal changed">Changed</em>');
    if (safeDate(job.deadline_at) - new Date() <= 7 * DAY) signals.push('<em class="signal closing">Closing soon</em>');
    if (job.strength === "strong") signals.push('<em class="signal strong">Strong alignment</em>');
    if (job.tracked) signals.push('<em class="signal">In Applications</em>');
    return signals.join("");
  }
  function jobCard(job) {
    const saved = state.local.saved.includes(job.id), compared = state.local.compared.includes(job.id), hidden = state.local.hidden.includes(job.id), selected = String(job.id) === String(state.selectedId);
    return `<article class="job-card ${selected ? "selected" : ""}" data-card-id="${esc(job.id)}"><button type="button" class="job-card-main" data-action="select-job" data-id="${esc(job.id)}"><i class="company-mark">${esc(initials(job.company))}</i><div><div class="job-heading"><div><h2>${esc(job.title)}</h2><p>${esc(job.company)}</p></div><svg aria-hidden="true"><use href="#i-chevron"/></svg></div><div class="signal-row">${jobSignals(job)}</div><div class="job-facts"><span>${esc(job.location)}</span><span>${esc(job.workplace)}</span><span>${esc(job.term)}</span></div><p class="match-reason">${esc(job.reasons?.[0] || "Review this opportunity")}</p></div></button><footer class="card-bottom"><div class="skill-list">${(job.skills || []).slice(0, 3).map(skill => `<span class="skill">${esc(skill)}</span>`).join("")}</div><div class="card-actions"><button type="button" class="mini-action ${compared ? "active" : ""}" data-action="toggle-compare" data-id="${esc(job.id)}" aria-label="${compared ? "Remove from" : "Add to"} comparison"><svg aria-hidden="true"><use href="#i-compare"/></svg></button><button type="button" class="mini-action ${saved ? "active" : ""}" data-action="toggle-save" data-id="${esc(job.id)}" aria-label="${saved ? "Unsave" : "Save"} ${esc(job.title)}"><svg aria-hidden="true"><use href="#i-bookmark"/></svg></button><button type="button" class="mini-action ${hidden ? "active" : ""}" data-action="toggle-hidden" data-id="${esc(job.id)}" aria-label="${hidden ? "Restore" : "Hide"} ${esc(job.title)}"><svg aria-hidden="true"><use href="#i-close"/></svg></button></div></footer></article>`;
  }

  function activeFilterMarkup() {
    const chips = [];
    state.facets.types.forEach(value => chips.push(["type", value]));
    state.facets.workplaces.forEach(value => chips.push(["workplace", value]));
    if (state.facets.freshness !== "any") chips.push(["freshness", `Last ${state.facets.freshness} days`]);
    if (state.facets.deadline) chips.push(["deadline", "Closing within 7 days"]);
    if (state.facets.changed) chips.push(["changed", "Changed recently"]);
    $("#active-filters").innerHTML = chips.map(([key, label]) => `<span class="filter-chip">${esc(label)}<button type="button" data-action="remove-filter" data-filter-key="${key}" data-filter-value="${esc(label)}" aria-label="Remove ${esc(label)}">×</button></span>`).join("");
    const count = facetCount(); $("#filter-count").textContent = count; $("#filter-count").hidden = !count;
  }

  function renderResults() {
    const counts = summarize(state.jobs, state.local);
    Object.entries(counts).forEach(([key, value]) => { const node = $(`[data-scope-count="${key}"]`); if (node) node.textContent = value; });
    $("#nav-count").textContent = counts.all;
    $$('[data-scope]').forEach(button => button.setAttribute("aria-selected", String(button.dataset.scope === state.scope)));
    const visible = sortJobs(filterJobs(state.jobs, state, state.local), state.sort);
    $("#result-count").textContent = `${visible.length} ${visible.length === 1 ? "role" : "roles"}`;
    $("#result-context").textContent = state.query ? `Matching “${state.query}”` : state.scope === "all" ? "All available opportunities" : `${state.scope[0].toUpperCase()}${state.scope.slice(1)} opportunities`;
    activeFilterMarkup();
    if (!visible.some(job => String(job.id) === String(state.selectedId))) state.selectedId = visible[0]?.id || null;
    if (!state.sample) {
      results.innerHTML = `<div class="empty-state"><i><svg aria-hidden="true"><use href="#i-layers"/></svg></i><h2>Sample data is off</h2><p>This concept page intentionally stays on the empty API stub. Turn on sample data to explore every interaction without contacting a live source.</p><button class="button primary" type="button" data-action="enable-sample">Turn on sample data</button></div>`;
    } else if (!visible.length) {
      results.innerHTML = `<div class="empty-state"><i><svg aria-hidden="true"><use href="#i-search"/></svg></i><h2>No matching opportunities</h2><p>Try a broader search, another view, or remove one of the active filters.</p><button class="button" type="button" data-action="clear-all">Clear search and filters</button></div>`;
    } else results.innerHTML = visible.map(jobCard).join("");
    renderDetail(); renderCompareTray();
  }

  function overviewPanel(job) {
    return `<section class="detail-section"><h3>Role overview</h3><p class="overview-copy">${esc(job.description || "No role summary is available.")}</p></section><section class="detail-section"><h3>At a glance</h3><div class="fact-grid"><div class="fact"><span>Compensation</span><strong>${esc(job.salary || "Not listed")}</strong></div><div class="fact"><span>Deadline</span><strong>${esc(formatDate(job.deadline_at))}</strong></div><div class="fact"><span>Workplace</span><strong>${esc(job.workplace)}</strong></div><div class="fact"><span>Term</span><strong>${esc(job.term)}</strong></div><div class="fact"><span>Sponsorship</span><strong>${esc(job.sponsorship)}</strong></div><div class="fact"><span>Education</span><strong>${esc(job.education)}</strong></div></div></section><section class="detail-section"><h3>Why it may be worth your time</h3><div class="reason-list">${(job.reasons || []).map(reason => `<div class="reason-item"><i><svg aria-hidden="true"><use href="#i-check"/></svg></i><span>${esc(reason)}</span></div>`).join("")}</div></section>`;
  }
  function requirementsPanel(job) {
    const rows = [...(job.required || []).map(value => [value, "required"]), ...(job.preferred || []).map(value => [value, "preferred"])];
    return `<section class="detail-section"><h3>Requirements</h3><div class="requirement-list">${rows.map(([value, level]) => `<div class="requirement-item"><span>${esc(value)}</span><em class="${level}">${level}</em></div>`).join("")}</div></section><section class="detail-section"><h3>Extracted skills</h3><div class="skill-list">${(job.skills || []).map(skill => `<span class="skill">${esc(skill)}</span>`).join("")}</div></section>`;
  }
  function evidencePanel(job) {
    return `<section class="detail-section"><h3>Source evidence</h3><div class="evidence-list"><div class="evidence-item"><i class="evidence-mark">${esc(initials(job.company))}</i><div><strong>${esc(job.source)}</strong><small>${esc(job.authority)} · Last confirmed ${esc(relativePosted(job.last_seen_at || job.posted_at).toLowerCase())}</small></div></div>${job.changed ? '<div class="evidence-item"><i class="evidence-mark">Δ</i><div><strong>Posting changed recently</strong><small>Requirements or description changed in the latest source snapshot.</small></div></div>' : ""}</div></section><section class="detail-section"><h3>Freshness</h3><div class="fact-grid"><div class="fact"><span>Published</span><strong>${esc(formatDate(job.posted_at))}</strong></div><div class="fact"><span>Last confirmed</span><strong>${esc(formatDate(job.last_seen_at || job.posted_at))}</strong></div></div></section>`;
  }

  function renderDetail() {
    const job = selectedJob();
    if (!job) { detail.innerHTML = '<div class="detail-empty"><div><i><svg aria-hidden="true"><use href="#i-compass"/></svg></i><h2>Select an opportunity</h2><p>Review requirements, recommendation signals, and source evidence here.</p></div></div>'; return; }
    const saved = state.local.saved.includes(job.id), source = safeHref(job.apply_url || job.source_url || job.posting_url), panels = { overview: overviewPanel(job), requirements: requirementsPanel(job), evidence: evidencePanel(job) };
    detail.innerHTML = `<header class="detail-head"><i class="company-mark">${esc(initials(job.company))}</i><div><h2>${esc(job.title)}</h2><p>${esc(job.company)} · ${esc(job.location)}</p><div class="signal-row">${jobSignals(job)}</div></div><button type="button" class="icon-button detail-close" data-action="close-detail" aria-label="Close job details"><svg aria-hidden="true"><use href="#i-close"/></svg></button></header><div class="detail-actions">${job.tracked ? `<a class="button primary" href="/static/application/index.html#app=${encodeURIComponent(job.application_id || "")}">Open in Applications</a>` : `<button type="button" class="button primary" data-action="add-application" data-id="${esc(job.id)}">Add to Applications</button>`}<button type="button" class="button ${saved ? "saved" : ""}" data-action="toggle-save" data-id="${esc(job.id)}"><svg aria-hidden="true"><use href="#i-bookmark"/></svg>${saved ? "Saved" : "Save"}</button>${source ? `<a class="button" href="${esc(source)}" target="_blank" rel="noopener noreferrer" aria-label="Open official posting"><svg aria-hidden="true"><use href="#i-external"/></svg></a>` : ""}</div><nav class="detail-tabs" role="tablist" aria-label="Job details"><button type="button" role="tab" data-detail-tab="overview" aria-selected="${state.detailTab === "overview"}">Overview</button><button type="button" role="tab" data-detail-tab="requirements" aria-selected="${state.detailTab === "requirements"}">Requirements</button><button type="button" role="tab" data-detail-tab="evidence" aria-selected="${state.detailTab === "evidence"}">Evidence</button></nav><div class="detail-body">${panels[state.detailTab]}</div>`;
  }

  function renderCompareTray() { const count = state.local.compared.length; $("#compare-count").textContent = count; $("#compare-tray").hidden = !count; $("#compare-tray").querySelector('[data-action="open-compare"]').disabled = count < 2; }
  function renderSavedViews() { const select = $("#saved-view"); select.innerHTML = '<option value="">Saved views</option>' + (state.local.savedViews || []).map(view => `<option value="${esc(view.id)}">${esc(view.name)}</option>`).join(""); }
  function renderComparison() { const jobs = state.local.compared.map(id => state.jobs.find(job => job.id === id)).filter(Boolean).slice(0, 2); $("#compare-content").innerHTML = jobs.map(job => `<article class="compare-column"><i class="company-mark">${esc(initials(job.company))}</i><h3>${esc(job.title)}</h3><p>${esc(job.company)}</p><div class="compare-row"><span>Recommendation</span><strong>${esc(job.reasons?.[0] || "Review")}</strong></div><div class="compare-row"><span>Location</span><strong>${esc(job.location)} · ${esc(job.workplace)}</strong></div><div class="compare-row"><span>Compensation</span><strong>${esc(job.salary)}</strong></div><div class="compare-row"><span>Deadline</span><strong>${esc(formatDate(job.deadline_at))}</strong></div><div class="compare-row"><span>Required</span><strong>${esc((job.required || []).join(", "))}</strong></div><div class="compare-row"><span>Preferred</span><strong>${esc((job.preferred || []).join(", "))}</strong></div></article>`).join(""); }

  function toggleIn(collection, id) { const values = new Set(state.local[collection] || []); values.has(id) ? values.delete(id) : values.add(id); state.local[collection] = [...values]; saveState(); }
  function clearAll() { state.query = ""; state.scope = "all"; state.facets = { types: [], workplaces: [], freshness: "any", deadline: false, changed: false }; $("#job-search").value = ""; renderResults(); }
  async function addApplication(id) {
    const job = state.jobs.find(item => item.id === id); if (!job || job.tracked) return;
    try { const application = await root.OneShotAPI.jobs.addToApplications(id); job.tracked = true; job.application_id = application.id; renderResults(); showToast("Added to Applications as Saved.", { label: "Undo", run: async () => { await root.OneShotAPI.applications.remove(application.id); job.tracked = false; job.application_id = null; renderResults(); showToast("Addition undone."); } }); }
    catch (error) { showToast(error.message || "Could not add this role."); }
  }

  function syncFilterForm() {
    const form = $("#filter-form"); $$('input[name="type"]', form).forEach(input => input.checked = state.facets.types.includes(input.value)); $$('input[name="workplace"]', form).forEach(input => input.checked = state.facets.workplaces.includes(input.value)); const freshness = form.querySelector(`[name="freshness"][value="${state.facets.freshness}"]`); if (freshness) freshness.checked = true; form.elements.deadline.checked = state.facets.deadline; form.elements.changed.checked = state.facets.changed;
  }

  document.addEventListener("click", event => {
    const actionNode = event.target.closest("[data-action]"), action = actionNode?.dataset.action;
    if (action === "select-job") { state.selectedId = actionNode.dataset.id; state.detailTab = "overview"; renderResults(); detail.classList.add("open"); $("#mobile-shade").classList.add("open"); history.replaceState(null, "", `#job=${encodeURIComponent(state.selectedId)}`); }
    if (action === "close-detail") { detail.classList.remove("open"); $("#mobile-shade").classList.remove("open"); history.replaceState(null, "", location.pathname + location.search); }
    if (action === "toggle-save") { toggleIn("saved", actionNode.dataset.id); renderResults(); showToast(state.local.saved.includes(actionNode.dataset.id) ? "Role saved." : "Role removed from saved."); }
    if (action === "toggle-hidden") { const id = actionNode.dataset.id, wasHidden = state.local.hidden.includes(id); toggleIn("hidden", id); renderResults(); showToast(wasHidden ? "Role restored." : "Role hidden.", wasHidden ? null : { label: "Undo", run: () => { toggleIn("hidden", id); renderResults(); showToast("Role restored."); } }); }
    if (action === "toggle-compare") { if (!state.local.compared.includes(actionNode.dataset.id) && state.local.compared.length >= 2) return showToast("Compare up to two roles at a time."); toggleIn("compared", actionNode.dataset.id); renderResults(); }
    if (action === "add-application") addApplication(actionNode.dataset.id);
    if (action === "open-filters") { syncFilterForm(); filterDialog.showModal(); }
    if (action === "close-filters") filterDialog.close();
    if (action === "reset-filters") { state.facets = { types: [], workplaces: [], freshness: "any", deadline: false, changed: false }; syncFilterForm(); }
    if (action === "clear-all") clearAll();
    if (action === "enable-sample") { root.OneShotSampleData?.setEnabled?.(true); location.reload(); }
    if (action === "retry") location.reload();
    if (action === "remove-filter") { const key = actionNode.dataset.filterKey, label = actionNode.dataset.filterValue; if (key === "type") state.facets.types = state.facets.types.filter(value => value !== label); if (key === "workplace") state.facets.workplaces = state.facets.workplaces.filter(value => value !== label); if (key === "freshness") state.facets.freshness = "any"; if (key === "deadline") state.facets.deadline = false; if (key === "changed") state.facets.changed = false; renderResults(); }
    if (action === "save-view") { if (!state.sample) return showToast("Turn on sample data before saving a view."); $("#save-view-form").reset(); saveViewDialog.showModal(); }
    if (action === "close-save-view") saveViewDialog.close();
    if (action === "clear-compare") { state.local.compared = []; saveState(); renderResults(); }
    if (action === "open-compare") { renderComparison(); compareDialog.showModal(); }
    if (action === "close-compare") compareDialog.close();
  });
  document.addEventListener("click", event => { const scope = event.target.closest("[data-scope]"); if (scope) { state.scope = scope.dataset.scope; renderResults(); } const tab = event.target.closest("[data-detail-tab]"); if (tab) { state.detailTab = tab.dataset.detailTab; renderDetail(); } });
  $("#job-search").addEventListener("input", event => { state.query = event.target.value; renderResults(); });
  $("#job-sort").addEventListener("change", event => { state.sort = event.target.value; renderResults(); });
  $("#saved-view").addEventListener("change", event => { const view = state.local.savedViews.find(item => item.id === event.target.value); if (!view) return; state.query = view.query; state.scope = view.scope; state.sort = view.sort; state.facets = JSON.parse(JSON.stringify(view.facets)); $("#job-search").value = state.query; renderResults(); });
  $("#filter-form").addEventListener("submit", event => { event.preventDefault(); const data = new FormData(event.target); state.facets = { types: data.getAll("type"), workplaces: data.getAll("workplace"), freshness: data.get("freshness") || "any", deadline: data.get("deadline") === "soon", changed: data.get("changed") === "yes" }; filterDialog.close(); renderResults(); });
  $("#save-view-form").addEventListener("submit", event => { event.preventDefault(); const name = String(new FormData(event.target).get("name") || "").trim(); if (!name) return; state.local.savedViews.push({ id: root.crypto?.randomUUID?.() || `view-${Date.now()}`, name, query: state.query, scope: state.scope, sort: state.sort, facets: JSON.parse(JSON.stringify(state.facets)) }); saveState(); renderSavedViews(); saveViewDialog.close(); showToast("View saved in this browser."); });
  [filterDialog, compareDialog, saveViewDialog].forEach(dialog => dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); }));
  document.addEventListener("keydown", event => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#job-search").focus(); } if (event.target.matches('[role="tab"]') && ["ArrowLeft", "ArrowRight"].includes(event.key)) { const tabs = [...event.target.parentElement.querySelectorAll('[role="tab"]')], at = tabs.indexOf(event.target), next = tabs[(at + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length]; next.focus(); next.click(); } });

  async function load() {
    renderSavedViews();
    if (!state.sample) { $("#freshness").innerHTML = "<span></span>Sample data is off"; renderResults(); return; }
    try {
      const [jobPayload, applicationPayload] = await Promise.all([root.OneShotAPI.jobs.list({ limit: 100 }), root.OneShotAPI.applications.list({})]);
      const listed = jobPayload?.jobs || [], applications = applicationPayload?.applications || [], trackedIds = applications.map(item => item.job_id).filter(Boolean);
      const trackedJobs = (await Promise.all(trackedIds.map(id => root.OneShotAPI.jobs.get(id).catch(() => null)))).filter(Boolean);
      const applicationByJob = new Map(applications.filter(item => item.job_id).map(item => [item.job_id, item.id]));
      const unique = new Map([...listed, ...trackedJobs].map(job => [job.id, job]));
      state.jobs = [...unique.values()].map(job => ({ ...enrichJob(job), tracked: applicationByJob.has(job.id), application_id: applicationByJob.get(job.id) || null }));
      const requested = new URLSearchParams(location.hash.replace(/^#/, "")).get("job"); state.selectedId = state.jobs.some(job => job.id === requested) ? requested : state.jobs[0]?.id || null;
      $("#freshness").classList.add("live"); $("#freshness").innerHTML = "<span></span>Sample data ready"; renderResults();
      if (requested && root.matchMedia?.("(max-width: 820px)").matches) { detail.classList.add("open"); $("#mobile-shade").classList.add("open"); }
    } catch (error) { results.innerHTML = `<div class="empty-state"><h2>Sample opportunities could not load</h2><p>${esc(error.message || error)}</p><button class="button" type="button" data-action="retry">Try again</button></div>`; }
  }
  load();
})(typeof window !== "undefined" ? window : globalThis);
