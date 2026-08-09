/* OneShot is intentionally dependency-free.  The small utility surface is exported for node:test. */
(function (root) {
  "use strict";
  const slug = value => String(value || "").toLowerCase().replace(/\bco[\s-]?op\b/g, "coop").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  const TYPES = [
    "Spring Internship",
    "Spring Co-op",
    "Student Researcher",
    "Summer Internship",
    "Fall Co-op",
    "Fall Internship",
    "Student Role",
    "Fellowship",
  ];
  const FILTERS = [
    { key: "summer_internships", label: "Summer Internships", types: ["summer_internship"] },
    { key: "spring_coop", label: "Spring Co-op", types: ["spring_internship", "spring_coop"] },
    { key: "research", label: "Research", types: ["student_researcher"] },
    { key: "early_career", label: "Early-Career", types: ["student_role"] },
  ];
  const filterTypes = value => FILTERS.find(filter => filter.key === value)?.types || [];
  const states = ["all", ...FILTERS.map(filter => filter.key)];
  const unwrap = value => Array.isArray(value) ? value : (value && (value.items || value.results || value.data || value.jobs || value.sources || value.applications)) || [];
  const getField = (item, names, fallback = "") => { for (const n of names) if (item && item[n] != null && item[n] !== "") return item[n]; return fallback; };
  const jobId = j => getField(j, ["id", "job_id", "ref"]);
  const opportunityType = j => getField(j, ["opportunity_type", "type", "employment_type"], "");
  const normalizeFilters = filters => [...new Set((filters || []).filter(f => states.includes(f)))];
  const matchesFilters = (job, filters) => {
    const active = normalizeFilters(filters);
    if (!active.length || active.includes("all")) return true;
    return active.some(f => filterTypes(f).includes(slug(opportunityType(job))));
  };
  const queryForFilters = filters => {
    const active = normalizeFilters(filters);
    const parts = active.flatMap(f => filterTypes(f).map(type => `opportunity_type=${encodeURIComponent(type)}`));
    return parts.join("&");
  };
  const sortJobs = (jobs, sort) => [...(jobs || [])].sort((a, b) => {
    if (sort === "company") return String(getField(a,["company","company_name"],"")).localeCompare(String(getField(b,["company","company_name"],"")));
    if (sort === "title") return String(getField(a,["title","job_title"],"")).localeCompare(String(getField(b,["title","job_title"],"")));
    return String(getField(b,["posted_at","published_at","created_at"],"")) .localeCompare(String(getField(a,["posted_at","published_at","created_at"],"")));
  });
  const duePresentation = (value, now = new Date()) => {
    const raw = String(value || "").trim();
    if (!raw) return null;
    let due;
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
      const [year, month, day] = raw.split("-").map(Number);
      due = new Date(year, month - 1, day, 23, 59, 59, 999);
    } else {
      const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
      due = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(normalized) ? normalized : `${normalized}Z`);
    }
    if (Number.isNaN(due.getTime())) return null;
    const difference = due.getTime() - now.getTime();
    const days = Math.max(1, Math.ceil(Math.abs(difference) / 86400000));
    if (difference < 0) return { label: `${days}d overdue`, tone: "overdue" };
    if (difference <= 86400000) return { label: "Due today", tone: "soon" };
    if (difference <= 172800000) return { label: "Due tomorrow", tone: "soon" };
    return { label: `Due in ${Math.ceil(difference / 86400000)}d`, tone: difference <= 604800000 ? "upcoming" : "" };
  };
  const relativeTime = (value, now = new Date()) => {
    if (!value) return "Never polled";
    const then = new Date(value);
    if (Number.isNaN(then.getTime())) return "Unknown";
    const seconds = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 1000));
    if (seconds < 60) return "Just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };
  const sourceBoardLabel = (type, count) => type === "ATS" ? `${Number(count) || 0} board${Number(count) === 1 ? "" : "s"}` : "";
  const postedPresentation = (value, now = new Date()) => {
    if (!value) return "Posted date unavailable";
    const posted = new Date(value);
    if (Number.isNaN(posted.getTime())) return "Posted date unavailable";
    const days = Math.max(0, Math.floor((now.getTime() - posted.getTime()) / 86400000));
    if (days === 0) return "Posted today";
    if (days === 1) return "Posted yesterday";
    return `Posted ${days}d ago`;
  };
  const dateOnlyPresentation = value => {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00Z` : raw);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
  };
  const dateInputValue = value => {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const match = raw.match(/^\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : "";
  };
  const utils = { TYPES, FILTERS, slug, unwrap, normalizeFilters, matchesFilters, queryForFilters, sortJobs, duePresentation, relativeTime, sourceBoardLabel, postedPresentation, dateOnlyPresentation, dateInputValue };
  if (typeof module !== "undefined" && module.exports) module.exports = utils;
  root.OneShotUtils = utils;
  if (!root.document) return;

  const $ = s => document.querySelector(s);
  const view = $("#view"), modalRoot = $("#modal-root"), flash = $("#flash");
  const store = { current: "discover", data: {}, paths: {}, selected: null, filters: [], sort: "fresh", compact: false, q: "", loaded: new Set(), profile: null };
  const esc = value => String(value == null ? "" : value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[c]);
  const api = (path, options = {}) => root.OneShotAPI.request(path, options);
  const announce = (text, action = null) => {
    flash.replaceChildren();
    const message=document.createElement("span");message.textContent=text;flash.appendChild(message);
    if(action){const button=document.createElement("button");button.type="button";button.textContent=action.label;button.dataset.action=action.action;if(action.id)button.dataset.id=action.id;flash.appendChild(button);}
    flash.classList.toggle("has-action",Boolean(action));flash.classList.add("show");clearTimeout(announce.timer);announce.timer=setTimeout(()=>{flash.classList.remove("show","has-action");},action?6000:3200);
  };
  const errorState = (title, error, retry) => `<div class="state"><h1>${esc(title)}</h1><p>${esc(error.message || error)}</p>${retry ? '<button class="button primary" data-action="retry">Try again</button>' : ""}</div>`;
  const loading = label => view.innerHTML = `<div class="state loading"><i></i><h1>${esc(label || "Loading")}</h1><p>Reading the latest API data.</p></div>`;
  const asList = key => unwrap(store.data[key]);
  const title = j => getField(j,["title","role","job_title"],"Untitled opportunity");
  const company = j => getField(j,["company","company_name","employer"],"Unknown company");
  const locationText = j => getField(j,["location_text","location"],"Location not listed");
  const postedAt = j => getField(j,["posted_at","published_at"],"");
  const lastSeenAt = j => getField(j,["last_seen_at"],"");
  const description = j => getField(j,["description","description_text","description_html"],"No description is available yet.");
  const attributes = j => getField(j,["attributes"],{}) || {};
  const tags = j => getField(j,["tags","skills"],attributes(j).skills || []) || [];
  const initials = j => company(j).split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0]).join("").toUpperCase() || "?";
  const isActive = j => ["active","open"].includes(String(getField(j,["status"],"active")).toLowerCase());
  const safeHref = value => /^https?:\/\//i.test(String(value || "")) ? String(value) : "";
  const tagsHtml = j => { const values=tags(j); return values.slice(0,3).map(x => {const name=typeof x==="string"?x:(x.name||x.label||x.value);return `<span class="tag">${esc(name)}</span>`;}).join("")+(values.length>3?`<span class="tag">+${values.length-3}</span>`:""); };
  function optionFilters() {
    return FILTERS.map(filter => `<button class="filter ${store.filters.includes(filter.key) ? "active" : ""}" data-filter="${filter.key}">${filter.label}</button>`).join("");
  }
  function sortOptions() {
    return `<option value="fresh" ${store.sort === "fresh" ? "selected" : ""}>Newest first</option><option value="company" ${store.sort === "company" ? "selected" : ""}>Company A–Z</option><option value="title" ${store.sort === "title" ? "selected" : ""}>Role title A–Z</option>`;
  }
  function controls() {
    return `<div class="filterbar">${optionFilters()}<div class="filter-controls"><select data-sort>${sortOptions()}</select><button class="button" data-action="compact">${store.compact ? "Full view" : "Compact"}</button></div></div>`;
  }
  const mobileMenu = () => '<button class="menu-button" data-action="menu" aria-label="Open navigation">☰</button>';
  function opportunity(j, selected = false, small = false, cue = "") {
    const id = jobId(j), term=attributes(j).term, workplace=attributes(j).workplace;
    const facts=[locationText(j),term,workplace].filter(Boolean).join(" · ");
    const recent=new Date(postedAt(j)).getTime()>Date.now()-3*86400000;
    return `<article class="opportunity ${selected ? "selected" : ""}" data-job="${esc(id)}" tabindex="0"><div class="job-logo">${esc(initials(j))}</div><div class="opportunity-main"><div class="job-title-line"><h3>${esc(title(j))}</h3>${recent?'<span class="new-badge">New</span>':""}</div><div class="job-company">${esc(company(j))}</div><div class="meta">${esc(facts)}</div>${store.compact || small ? "" : `<div class="tags">${tagsHtml(j)}</div>`}<div class="job-footline"><span>${esc(postedPresentation(postedAt(j)))}</span><span class="job-state ${isActive(j)?"active":"closed"}">${isActive(j)?"Active":"Closed"}</span></div></div></article>`;
  }
  function empty(noun, copy) { return `<div class="empty"><h2>No ${esc(noun)} yet</h2><p>${esc(copy)}</p></div>`; }
  function renderDetail(j) {
    if (!j) return `<aside class="detail"><div class="section-kicker">Selection</div><h2>Choose a job</h2><p>Select a role to review its details and add it to Applications.</p></aside>`;
    const id=jobId(j),apply=safeHref(getField(j,["apply_url","source_url","posting_url"],"")),facts=[locationText(j),attributes(j).term,attributes(j).workplace].filter(Boolean);
    return `<aside class="detail"><div class="detail-head-row"><div class="job-logo large">${esc(initials(j))}</div><div><div class="detail-company">${esc(company(j))}</div><div class="meta">${esc(facts.join(" · "))}</div></div><span class="job-state ${isActive(j)?"active":"closed"}">${isActive(j)?"Active":"Closed"}</span></div><h2>${esc(title(j))}</h2><div class="detail-actions"><button class="button primary" data-action="add-to-applications" data-id="${esc(id)}" ${isActive(j)?"":'disabled title="This posting is closed"'}>Add to Applications</button>${apply?`<a class="button" href="${esc(apply)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a>`:""}</div><section><div class="section-kicker">Job details</div><div class="tags detail-tags">${tagsHtml(j)||'<span class="meta">No structured details available</span>'}</div></section><section><div class="section-kicker">Role overview</div><p class="detail-description">${esc(description(j))}</p></section><div class="detail-freshness">${esc(postedPresentation(postedAt(j)))}${lastSeenAt(j)?` · Last confirmed active ${esc(relativeTime(lastSeenAt(j)))}`:""}</div></aside>`;
  }
  /* A cache entry is keyed by the request it came from, so a changed filter refetches
     while a local-only action (expanding a group, toggling compact) reuses what we have. */
  async function load(key, path, identity = path) {
    if (store.loaded.has(key) && store.paths[key] === identity) return store.data[key];
    const data = await api(path);
    store.data[key] = data; store.paths[key] = identity; store.loaded.add(key);
    return data;
  }
  const invalidate = (...keys) => keys.forEach(key => store.loaded.delete(key));
  /* Blank the view only when there is nothing worth keeping on screen. Painting a
     placeholder in front of a 2ms refetch is a guaranteed flash, not feedback. */
  const loadingIfEmpty = (key, label) => { if (store.data[key] === undefined) loading(label); };
  function updateCounts() { const map = { discover: asList("jobs").length, coverage: asList("coverage").length }; Object.entries(map).forEach(([k,v]) => document.querySelectorAll(`[data-count="${k}"]`).forEach(el => el.textContent = v || "")); }
  async function discoverResults() {
    loadingIfEmpty("jobs", "Loading opportunities");
    const params = new URLSearchParams({ q: store.q, sort: store.sort, limit:"100", offset:"0" });
    new URLSearchParams(queryForFilters(store.filters)).forEach((value, key) => params.append(key, value));
    /* compact is presentation-only and the API discards it, so it stays out of the
       cache identity: toggling the view must not cost a round trip. */
    const identity = `/jobs?${params}`;
    await load("jobs", `${identity}&compact=${store.compact}`, identity);
    const jobs = sortJobs(asList("jobs").filter(j => matchesFilters(j,store.filters)), store.sort);
    if (!store.selected || !jobs.some(j => String(jobId(j)) === String(store.selected))) store.selected = jobs[0] && jobId(jobs[0]);
    let selected = jobs.find(j => String(jobId(j)) === String(store.selected));
    if (selected) { const id = jobId(selected); selected = await load(`job:${id}`, `/jobs/${encodeURIComponent(id)}`); }
    updateCounts();
    return { jobs, selected };
  }
  async function renderDiscover() {
    try {
      const { jobs, selected } = await discoverResults();
      view.innerHTML = `<header class="page-head">${mobileMenu()}<div><div class="section-kicker">Opportunity search</div><h1>Discover</h1><p>Browse active roles and decide what to apply to next.</p></div></header><div class="global-search discover-search"><span>⌕</span><input id="discover-search" data-search placeholder="Search roles, companies, or skills" autocomplete="off" value="${esc(store.q)}"><kbd>⌘K</kbd></div>${controls()}<div class="discover-grid"><div class="opportunity-list">${jobs.length ? jobs.map(j => opportunity(j,String(jobId(j)) === String(store.selected))).join("") : empty("matching jobs","Try another search or clear a filter.")}</div>${renderDetail(selected)}</div>`;
    } catch(e) { view.innerHTML = errorState("Discover couldn’t load",e,true); }
  }
  async function renderPipeline() {
    loadingIfEmpty("applications", "Loading applications");
    try {
      await load("applications",`/applications?${queryForFilters(store.filters)}`);
      const apps=asList("applications").filter(a=>matchesFilters(a,store.filters));
      const columns=[{key:"considering",label:"Considering"},{key:"preparing",label:"Preparing"},{key:"submitted",label:"Submitted"},{key:"assessment",label:"Assessment"},{key:"screen",label:"Screen"},{key:"interview",label:"Interview"},{key:"offer",label:"Offer"},{key:"rejected",label:"Rejected"},{key:"withdrawn",label:"Withdrawn"},{key:"no_reply",label:"No reply"}];
      view.innerHTML=`<header class="page-head">${mobileMenu()}<div><h1>Applications</h1><p>Keep track of every application and what comes next.</p></div><div class="right"><button class="button primary" data-action="add-external">Add application</button></div></header>${controls()}<div class="pipeline">${columns.map(column=>{const group=apps.filter(a=>String(getField(a,["stage","state"],"considering")).toLowerCase()===column.key);return `<section class="pipeline-column"><header><span>${column.label}</span><span class="meta">${group.length}</span></header>${group.length?group.map(a=>{const due=duePresentation(a.next_due_at);return `<article class="pipeline-card" data-app="${esc(getField(a,["id","application_id"]))}"><b>${esc(getField(a,["company","company_name"],"Unknown company"))}</b><div>${esc(getField(a,["title","role","job_title"],"Untitled role"))}</div><div class="meta">${esc(getField(a,["next_action"],"No next action"))}</div>${due?`<span class="due-chip ${esc(due.tone)}">${esc(due.label)}</span>`:""}</article>`;}).join(""):""}</section>`}).join("")}</div>`;
    } catch(e) { view.innerHTML=errorState("Applications couldn’t load",e,true); }
  }
  const sourceLabel = value => String(value || "source").split(/[_-]/).map(part=>part?part[0].toUpperCase()+part.slice(1):part).join(" ");
  const sourceMark = value => String(value || "?").replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "?";
  const countLabel = value => new Intl.NumberFormat().format(Number(value) || 0);
  function coverageRow(source) {
    const type=getField(source,["type"],"Other"),boards=sourceBoardLabel(type,source.board_count),health=getField(source,["health"],"Unknown"),healthy=health==="Healthy";
    return `<div class="coverage-row"><div class="coverage-source"><span class="coverage-icon">${esc(sourceMark(source.source))}</span><div><strong>${esc(sourceLabel(source.source))}</strong><small>${esc([type,boards].filter(Boolean).join(" · "))}</small></div></div><span>${esc(countLabel(source.coverage))} roles</span><span>${esc(relativeTime(source.last_poll_at))}</span><span class="coverage-health ${healthy?"healthy":"attention"}">${esc(health)}</span><span class="coverage-change">+${esc(countLabel(source.added_today))}</span></div>`;
  }
  async function renderCoverage() {
    loadingIfEmpty("coverage", "Loading source coverage");
    try {
      const data=await load("coverage","/source-coverage"),sources=getField(data,["sources"],[]);
      updateCounts();
      view.innerHTML=`<header class="page-head coverage-page-head">${mobileMenu()}<div><h1>Source coverage</h1><p>See where your opportunities come from and when each source was last updated.</p></div><div class="right coverage-total"><strong>${esc(countLabel(data.total_coverage))}</strong><span>active source postings</span></div></header>${sources.length?`<section class="coverage-table"><div class="coverage-head"><span>Source</span><span>Coverage</span><span>Last poll</span><span>Health</span><span>Changes today</span></div>${sources.map(coverageRow).join("")}</section>`:empty("source coverage","No configured sources are available yet.")}`;
    } catch(e) { view.innerHTML=errorState("Source coverage couldn’t load",e,true); }
  }
  const renders={discover:renderDiscover,pipeline:renderPipeline,coverage:renderCoverage};
  async function navigate(name) { store.current=renders[name]?name:"discover"; document.querySelectorAll("[data-view-link]").forEach(a=>a.classList.toggle("active",a.dataset.viewLink===store.current)); history.replaceState(null,"",`#${store.current}`); await renders[store.current](); }
  function modal(html, className = "") { modalRoot.innerHTML=`<div class="modal-shade" data-modal-shade><section class="modal ${esc(className)}" role="dialog" aria-modal="true">${html}</section></div>`; }
  function externalApplicationModal() { modal(`<h2>Add external opportunity</h2><p>This creates an application tracker entry only. It will not appear in Discover.</p><div class="form-row"><label class="field">Company<input id="external-company" required></label><label class="field">Role title<input id="external-title" required></label><label class="field full">Application URL<input id="external-url" type="url" placeholder="https://…" required></label><label class="field full">Location<input id="external-location" placeholder="Optional"></label><label class="field full">Note<textarea id="external-note" placeholder="Optional"></textarea></label></div><div class="modal-actions"><button class="button" data-action="close-modal">Cancel</button><button class="button primary" data-action="external-submit">Add to Applications</button></div>`); }
  async function eventModal(id) {
    try {
      const application=await api(`/applications/${encodeURIComponent(id)}`),events=getField(application,["events"],[]),source=safeHref(application.apply_url_snapshot),editable=Boolean(application.external);
      const stageLabels={considering:"Considering",submitted:"Applied",assessment:"Assessment",interview:"Interview",offer:"Offer",rejected:"Closed / Rejected"};
      const timeline=[...events].reverse().map((item,index)=>{const stage=getField(item,["to_stage"],"")||(["considering","submitted","assessment","interview","offer","rejected"].includes(item.event_type)?item.event_type:"");const description=stage?`${index===events.length-1?"Added to":"Moved to"} ${stageLabels[stage]||stage}`:(item.note||String(item.event_type||"Event").replaceAll("_"," "));return `<div class="application-history-row"><span>${esc(dateOnlyPresentation(item.occurred_at)||"Date unavailable")}</span><span>${esc(description)}</span></div>`;}).join("");
      const locked=editable?"":' readonly aria-readonly="true" title="This value comes from the discovered job"';
      modal(`<div class="application-modal-head"><div><h2>Application</h2><p>Edit details, notes, and your next step.</p></div><button class="modal-close" type="button" data-action="close-modal" aria-label="Close">×</button></div><div class="application-modal-body"><div class="form-row"><label class="field">Company<input id="application-company" value="${esc(application.company)}"${locked}></label><label class="field">Role<input id="application-title" value="${esc(application.title)}"${locked}></label><label class="field">Stage<select id="application-stage"><option value="considering">Considering</option><option value="submitted">Applied</option><option value="assessment">Assessment</option><option value="interview">Interview</option><option value="offer">Offer</option><option value="rejected">Closed / Rejected</option></select></label><label class="field">Date applied<input id="application-submitted" type="date" value="${esc(dateInputValue(application.submitted_at))}"></label><label class="field full">Posting URL<input id="application-url" type="url" value="${esc(application.apply_url_snapshot||"")}" placeholder="https://…"${locked}></label><label class="field">Next action<input id="application-next-action" value="${esc(application.next_action||"")}" placeholder="e.g. Follow up with recruiter"></label><label class="field">Next action date<input id="application-next-date" type="date" value="${esc(dateInputValue(application.next_due_at))}"></label><label class="field full">Notes<textarea id="application-note" rows="4" placeholder="Recruiter contacts, referrals, interview notes…">${esc(application.note||"")}</textarea></label></div><h3 class="application-history-title">History</h3><div class="application-history">${timeline||'<span class="meta">No history yet.</span>'}</div><div class="application-modal-actions"><button class="button danger" data-action="application-delete" data-id="${esc(id)}">Delete</button><div>${source?`<a class="button" href="${esc(source)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a>`:""}<button class="button" data-action="close-modal">Cancel</button><button class="button primary" data-action="application-save" data-id="${esc(id)}" data-editable="${editable}">Save</button></div></div></div>`,"application-editor");
      const stage=$("#application-stage");if(stage&&[...stage.options].some(option=>option.value===application.stage))stage.value=application.stage;
    } catch(err) { announce(err.message||"Application history could not be loaded."); }
  }
  async function addToApplications(id) {
    const result=await root.OneShotAPI.jobs.addToApplications(id);
    invalidate("applications","jobs",`job:${id}`);store.selected=null;
    await navigate("discover");
    announce(result?.already_exists?"This job is already in Applications.":"Added to Applications.",result?.already_exists?null:{label:"Undo",action:"undo-add",id:result.id});
  }
  document.addEventListener("click", async e => {
    const a=e.target.closest("[data-action]");
    if(!a)return;
    const action=a.dataset.action;
    try {
      if(action==="menu")return $(".rail").classList.toggle("open");
      if(action==="add-external")return externalApplicationModal();
      if(action==="close-modal")return modalRoot.innerHTML="";
      if(action==="retry")return navigate(store.current);
      if(action==="compact"){store.compact=!store.compact;return navigate(store.current);}
      if(action==="add-to-applications")return addToApplications(a.dataset.id);
      if(action==="external-submit"){const company=$("#external-company").value.trim(),title=$("#external-title").value.trim(),url=$("#external-url").value.trim();if(!company||!title||!url)return announce("Company, role title, and application URL are required.");await api("/applications/external",{method:"POST",body:JSON.stringify({company,title,url,location:$("#external-location").value.trim(),note:$("#external-note").value.trim()})});modalRoot.innerHTML="";invalidate("applications");announce("External opportunity added to Applications.");return navigate("pipeline");}
      if(action==="application-save"){const stage=$("#application-stage").value;let submitted=$("#application-submitted").value||null;if(stage==="submitted"&&!submitted)submitted=new Date().toISOString().slice(0,10);const body={stage,submitted_at:submitted,next_action:$("#application-next-action").value.trim(),next_due_at:$("#application-next-date").value||null,note:$("#application-note").value.trim()};if(a.dataset.editable==="true"){body.company=$("#application-company").value.trim();body.title=$("#application-title").value.trim();body.apply_url_snapshot=$("#application-url").value.trim();if(!body.company||!body.title)return announce("Company and role are required.");}await root.OneShotAPI.applications.update(a.dataset.id,body);modalRoot.innerHTML="";invalidate("applications");announce("Application updated.");return navigate("pipeline");}
      if(action==="application-delete"){if(!root.confirm("Delete this application? This can't be undone."))return;await root.OneShotAPI.applications.remove(a.dataset.id);modalRoot.innerHTML="";invalidate("applications","jobs");store.selected=null;await navigate("pipeline");return announce("Application deleted.");}
      if(action==="undo-add"){await root.OneShotAPI.applications.remove(a.dataset.id);invalidate("applications","jobs");store.selected=null;await navigate("discover");return announce("Addition undone.");}
    } catch(err){announce(err.message||"That action could not be completed.");}
  });
  document.addEventListener("click",e=>{const shade=e.target.closest("[data-modal-shade]");if(shade&&e.target===shade)modalRoot.innerHTML="";const link=e.target.closest("[data-view-link]");if(link){e.preventDefault();navigate(link.dataset.viewLink);}const row=e.target.closest("[data-job]");if(row&&!e.target.closest("button")){store.selected=row.dataset.job;if(store.current==="discover")navigate(store.current);}const app=e.target.closest("[data-app]");if(app)eventModal(app.dataset.app);});
  document.addEventListener("click",e=>{const f=e.target.closest("[data-filter]");if(!f)return;const value=f.dataset.filter;store.filters=store.filters.includes(value)?store.filters.filter(x=>x!==value):[...store.filters,value];navigate(store.current);});
  document.addEventListener("change",e=>{if(e.target.matches("[data-sort]")){store.sort=e.target.value;navigate(store.current);}});document.addEventListener("input",e=>{if(e.target.matches("[data-search]")){store.q=e.target.value;clearTimeout(store.searchTimer);store.searchTimer=setTimeout(()=>navigate("discover"),250);}});
  document.addEventListener("keydown",e=>{const discovering=store.current==="discover";if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"){const search=$("#discover-search");if(discovering&&search){e.preventDefault();search.focus();}return;}if(/input|textarea|select/i.test(document.activeElement.tagName))return;if(e.key==="Escape")return modalRoot.innerHTML="";if(!discovering)return;const rows=[...document.querySelectorAll("[data-job]")];const at=Math.max(0,rows.findIndex(x=>x.dataset.job===String(store.selected)));if(e.key.toLowerCase()==="j"&&rows[at+1]){store.selected=rows[at+1].dataset.job;return navigate(store.current);}if(e.key.toLowerCase()==="k"&&rows[at-1]){store.selected=rows[at-1].dataset.job;return navigate(store.current);}if(e.key==="Enter")return navigate(store.current);});
  (async()=>{try{await root.OneShotAPI.health();const requested=location.hash.slice(1);navigate(renders[requested]?requested:"discover");}catch(e){view.innerHTML=errorState("OneShot is unavailable",e,true);}})();
})(typeof window !== "undefined" ? window : globalThis);
