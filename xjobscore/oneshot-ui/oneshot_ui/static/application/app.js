(function (root) {
  "use strict";

  const ACTIVE_GROUPS = Object.freeze([
    { key: "saved", label: "Saved", stages: ["considering"], color: "#6f7971" },
    { key: "preparing", label: "Preparing", stages: ["preparing"], color: "#9a641d" },
    { key: "applied", label: "Applied", stages: ["submitted", "assessment"], color: "#386f9f" },
    { key: "interviewing", label: "Interviewing", stages: ["screen", "interview"], color: "#75529a" },
    { key: "offer", label: "Offer", stages: ["offer"], color: "#347552" },
  ]);
  const ARCHIVE_STAGES = Object.freeze(["rejected", "withdrawn", "no_reply"]);
  const STAGE_LABELS = Object.freeze({
    considering: "Saved", preparing: "Preparing", submitted: "Applied",
    assessment: "Assessment", screen: "Recruiter screen", interview: "Interview",
    offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn", no_reply: "No reply",
  });
  const COMPANION_KEY = "oneshot.application.workspace.v1";

  const safeDate = value => {
    if (!value) return null;
    const normalized = /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? `${value}T23:59:59` : String(value);
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const groupForStage = stage => ACTIVE_GROUPS.find(group => group.stages.includes(String(stage || "").toLowerCase()))?.key || "archive";
  const dueState = (value, now = new Date()) => {
    const date = safeDate(value);
    if (!date) return { key: "none", label: "No due date", rank: Number.MAX_SAFE_INTEGER };
    const difference = date.getTime() - now.getTime();
    const absoluteDays = Math.max(1, Math.ceil(Math.abs(difference) / 86400000));
    if (difference < 0) return { key: "overdue", label: `${absoluteDays}d overdue`, rank: date.getTime() };
    if (difference <= 86400000) return { key: "soon", label: "Due today", rank: date.getTime() };
    if (difference <= 172800000) return { key: "soon", label: "Due tomorrow", rank: date.getTime() };
    return { key: difference <= 604800000 ? "upcoming" : "none", label: `Due in ${Math.ceil(difference / 86400000)}d`, rank: date.getTime() };
  };
  const daysSince = (value, now = new Date()) => {
    const date = safeDate(value);
    if (!date) return null;
    return Math.max(0, Math.floor((now.getTime() - date.getTime()) / 86400000));
  };
  const buildMetrics = (applications, now = new Date()) => {
    const active = applications.filter(item => !ARCHIVE_STAGES.includes(String(item.stage).toLowerCase()));
    return {
      active: active.length,
      overdue: active.filter(item => dueState(item.next_due_at, now).key === "overdue").length,
      interviews: active.filter(item => ["screen", "interview"].includes(String(item.stage).toLowerCase())).length,
      offers: active.filter(item => String(item.stage).toLowerCase() === "offer").length,
      archive: applications.length - active.length,
    };
  };
  const filterApplications = (applications, options = {}, now = new Date()) => {
    const query = String(options.query || "").trim().toLowerCase();
    const archive = options.statusView === "archive";
    return applications.filter(item => {
      const isArchive = ARCHIVE_STAGES.includes(String(item.stage).toLowerCase());
      if (archive !== isArchive) return false;
      const haystack = `${item.company || ""} ${item.title || ""} ${item.location || item.location_text || ""} ${item.next_action || ""}`.toLowerCase();
      if (query && !haystack.includes(query)) return false;
      if (options.quickFilter === "attention" && !["overdue", "soon"].includes(dueState(item.next_due_at, now).key)) return false;
      if (options.quickFilter === "interviews" && !["screen", "interview"].includes(String(item.stage).toLowerCase())) return false;
      if (options.quickFilter === "offers" && String(item.stage).toLowerCase() !== "offer") return false;
      return true;
    });
  };
  const sortApplications = (applications, sort = "next_due") => [...applications].sort((a, b) => {
    if (sort === "company") return String(a.company || "").localeCompare(String(b.company || ""));
    if (sort === "applied") return String(b.submitted_at || "").localeCompare(String(a.submitted_at || ""));
    if (sort === "updated") return String(b.updated_at || b.events?.at?.(-1)?.occurred_at || "").localeCompare(String(a.updated_at || a.events?.at?.(-1)?.occurred_at || ""));
    return dueState(a.next_due_at).rank - dueState(b.next_due_at).rank;
  });

  const exported = { ACTIVE_GROUPS, ARCHIVE_STAGES, STAGE_LABELS, groupForStage, dueState, daysSince, buildMetrics, filterApplications, sortApplications };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  root.OneShotApplicationUtils = exported;
  if (!root.document) return;

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const content = $("#application-content");
  const drawer = $("#application-drawer");
  const drawerContent = $("#drawer-content");
  const addDialog = $("#add-dialog");
  const addForm = $("#add-form");
  const submissionDialog = $("#submission-dialog");
  const submissionForm = $("#submission-form");
  const toast = $("#toast");
  const state = {
    applications: [],
    query: "",
    quickFilter: "",
    statusView: "active",
    display: root.localStorage.getItem("oneshot.application.display") || "board",
    sort: "next_due",
    selectedId: null,
    drawerTab: "overview",
    dragId: null,
    pendingSubmissionId: null,
    loading: true,
  };

  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const safeHref = value => /^https?:\/\//i.test(String(value || "")) ? String(value) : "";
  const initials = value => String(value || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase();
  const markColor = value => {
    const palette = ["#244a3a", "#3e5e82", "#74533e", "#5f4a78", "#47636a", "#7a4c55"];
    const sum = [...String(value || "")].reduce((total, character) => total + character.charCodeAt(0), 0);
    return palette[sum % palette.length];
  };
  const formatDate = value => {
    const date = safeDate(value);
    return date ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date) : "Not set";
  };
  const dateInputValue = value => String(value || "").match(/^\d{4}-\d{2}-\d{2}/)?.[0] || "";
  const isoFromDate = value => value ? `${value}T12:00:00` : null;
  const companionState = () => {
    try { return JSON.parse(root.localStorage.getItem(COMPANION_KEY)) || {}; }
    catch (_) { return {}; }
  };
  const saveCompanionState = value => root.localStorage.setItem(COMPANION_KEY, JSON.stringify(value));
  const companionFor = id => {
    const all = companionState();
    return all[id] || { tasks: [], contacts: [], materials: [], events: [] };
  };
  const updateCompanion = (id, updater) => {
    const all = companionState();
    const current = all[id] || { tasks: [], contacts: [], materials: [], events: [] };
    all[id] = updater(current) || current;
    saveCompanionState(all);
    return all[id];
  };
  const presentApplication = application => {
    const companion = companionFor(application.id);
    const openTasks = (companion.tasks || []).filter(task => !task.completed).sort((a, b) => dueState(a.due_at).rank - dueState(b.due_at).rank);
    if (openTasks.length) return { ...application, next_action: openTasks[0].title, next_due_at: openTasks[0].due_at || null };
    const mirroredTask = (companion.tasks || []).find(task => task.title === application.next_action);
    if (companion.serverTaskCompleted || mirroredTask?.completed) return { ...application, next_action: "", next_due_at: null };
    return application;
  };
  const showToast = (message, action) => {
    toast.replaceChildren();
    const copy = document.createElement("span");
    copy.textContent = message;
    toast.append(copy);
    if (action) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      button.addEventListener("click", async () => { toast.classList.remove("show"); await action.run(); }, { once: true });
      toast.append(button);
    }
    toast.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("show"), action ? 6500 : 3500);
  };

  function metricsMarkup(metrics) {
    return [
      ["Active applications", metrics.active, "Across every open stage", "icon-briefcase", ""],
      ["Needs attention", metrics.overdue, metrics.overdue ? "Overdue next steps" : "Nothing overdue", "icon-calendar", "attention"],
      ["Interviews", metrics.interviews, "Screens and interviews", "icon-calendar", "interviews"],
      ["Offers", metrics.offers, metrics.offers ? "Ready for a decision" : "Keep the momentum", "icon-check", "offers"],
    ].map(([label, value, note, icon, className]) => `<article class="metric ${className}"><header><span>${label}</span><i class="metric-icon"><svg aria-hidden="true"><use href="#${icon}"/></svg></i></header><strong>${value}</strong><small>${note}</small></article>`).join("");
  }

  function dueBadge(application) {
    if (!application.next_due_at) return "";
    const due = dueState(application.next_due_at);
    return `<span class="due-badge ${due.key}"><svg aria-hidden="true"><use href="#icon-calendar"/></svg>${esc(due.label)}</span>`;
  }

  function applicationCard(application) {
    const age = daysSince(application.submitted_at);
    const stage = String(application.stage || "considering").toLowerCase();
    const substage = ["assessment", "screen"].includes(stage) ? `<span class="substage">${esc(STAGE_LABELS[stage])}</span>` : "";
    return `<button class="application-card" type="button" draggable="true" data-application-id="${esc(application.id)}" aria-label="Open ${esc(application.title)} at ${esc(application.company)}">
      <span class="card-company-row"><i class="company-mark" style="--mark-color:${markColor(application.company)}">${esc(initials(application.company))}</i><strong>${esc(application.company || "Unknown company")}</strong>${application.external ? '<em class="external-tag">External</em>' : ""}</span>
      <h3>${esc(application.title || "Untitled role")}</h3>
      <p class="card-location">${esc(application.location || application.location_text || application.opportunity_type || "Location not set")}</p>
      ${substage}
      <span class="next-step"><span>Next step</span><p>${esc(application.next_action || "Add a next action")}</p></span>
      <span class="card-footer">${dueBadge(application)}${age != null ? `<span class="age-badge">${age}d since applied</span>` : ""}<i class="card-open"><svg aria-hidden="true"><use href="#icon-chevron"/></svg></i></span>
    </button>`;
  }

  function boardMarkup(applications) {
    if (state.statusView === "archive") {
      return listMarkup(applications);
    }
    return `<div class="board" aria-label="Active application pipeline">${ACTIVE_GROUPS.map(group => {
      const items = applications.filter(application => groupForStage(application.stage) === group.key);
      return `<section class="board-column" data-drop-group="${group.key}" style="--column-color:${group.color}">
        <header class="column-header"><span></span><h2>${group.label}</h2><b>${items.length}</b></header>
        <div class="column-stack">${items.length ? items.map(applicationCard).join("") : `<div class="empty-column">No applications here</div>`}</div>
      </section>`;
    }).join("")}</div>`;
  }

  function listMarkup(applications) {
    return `<div class="application-list"><div class="list-head"><span>Application</span><span>Stage</span><span>Applied</span><span>Next action</span><span>Due</span><span></span></div>${applications.map(application => {
      const due = dueState(application.next_due_at);
      return `<button class="application-row" type="button" data-application-id="${esc(application.id)}"><span class="row-role"><i class="company-mark" style="--mark-color:${markColor(application.company)}">${esc(initials(application.company))}</i><div><strong>${esc(application.title || "Untitled role")}</strong><span>${esc(application.company || "Unknown company")}${application.location ? ` · ${esc(application.location)}` : ""}</span></div></span><span class="stage-pill">${esc(STAGE_LABELS[application.stage] || application.stage)}</span><span class="row-date">${esc(application.submitted_at ? formatDate(application.submitted_at) : "Not applied")}</span><span class="row-next">${esc(application.next_action || "No next action")}</span><span class="${application.next_due_at ? `due-badge ${due.key}` : "row-date"}">${esc(application.next_due_at ? due.label : "—")}</span><svg class="row-chevron" aria-hidden="true"><use href="#icon-chevron"/></svg></button>`;
    }).join("")}</div>`;
  }

  function emptyMarkup() {
    const filtered = Boolean(state.query || state.quickFilter);
    return `<div class="empty-state"><i class="empty-icon"><svg aria-hidden="true"><use href="#${filtered ? "icon-search" : "icon-briefcase"}"/></svg></i><h2>${filtered ? "No matching applications" : state.statusView === "archive" ? "Your archive is empty" : "Build your application pipeline"}</h2><p>${filtered ? "Try a broader search or clear the quick filter." : state.statusView === "archive" ? "Closed applications will collect here without crowding active work." : "Add a role you are considering or one you have already applied to."}</p>${filtered ? '<button class="button" type="button" data-action="clear-filters">Clear filters</button>' : state.statusView === "active" ? '<button class="button primary" type="button" data-action="open-add">Add your first application</button>' : ""}</div>`;
  }

  function render() {
    const presentedApplications = state.applications.map(presentApplication);
    const metrics = buildMetrics(presentedApplications);
    $("#metrics").innerHTML = metricsMarkup(metrics);
    $("#nav-count").textContent = metrics.active;
    $("#active-count").textContent = metrics.active;
    $("#archive-count").textContent = metrics.archive;
    $("#active-tab").setAttribute("aria-selected", String(state.statusView === "active"));
    $("#archive-tab").setAttribute("aria-selected", String(state.statusView === "archive"));
    content.setAttribute("aria-labelledby", state.statusView === "active" ? "active-tab" : "archive-tab");
    $(".display-switch").hidden = state.statusView === "archive";
    $$('[data-display]').forEach(button => button.setAttribute("aria-pressed", String(button.dataset.display === state.display)));
    const visible = sortApplications(filterApplications(presentedApplications, state), state.sort);
    content.innerHTML = visible.length ? (state.display === "board" ? boardMarkup(visible) : listMarkup(visible)) : emptyMarkup();
  }

  async function loadApplications() {
    state.loading = true;
    try {
      root.OneShotAPI.useRemote();
      await root.OneShotAPI.health();
      const payload = await root.OneShotAPI.applications.list({});
      state.applications = Array.isArray(payload) ? payload : payload?.applications || payload?.items || [];
      state.loading = false;
      render();
      const requested = new URLSearchParams(location.hash.replace(/^#/, "")).get("app");
      if (requested && state.applications.some(item => String(item.id) === requested)) openApplication(requested);
    } catch (error) {
      state.loading = false;
      content.innerHTML = `<div class="error-state"><h2>Applications could not load</h2><p>${esc(error.message || error)}</p><button class="button primary" type="button" data-action="enable-sample">Open with sample data</button></div>`;
    }
  }

  const selectedApplication = () => state.applications.find(application => String(application.id) === String(state.selectedId));
  const stageOptions = application => Object.entries(STAGE_LABELS).map(([value, label]) => `<option value="${value}" ${application.stage === value ? "selected" : ""}>${label}</option>`).join("");
  const taskRows = (application, companion) => {
    const tasks = [...(companion.tasks || [])];
    if (application.next_action && !tasks.some(task => task.title === application.next_action)) tasks.unshift({ id: "server-next", title: application.next_action, due_at: application.next_due_at, completed: Boolean(companion.serverTaskCompleted), server: true });
    if (!tasks.length) return '<div class="empty-column">No tasks yet. Add the next concrete step.</div>';
    return tasks.map(task => `<div class="task-row ${task.completed ? "completed" : ""}"><button class="task-check" type="button" data-action="toggle-task" data-task-id="${esc(task.id)}" aria-label="${task.completed ? "Reopen" : "Complete"} ${esc(task.title)}"><svg aria-hidden="true"><use href="#icon-check"/></svg></button><span class="task-copy"><strong>${esc(task.title)}</strong><span>${esc(task.due_at ? formatDate(task.due_at) : "No due date")}${task.server ? " · synced next action" : ""}</span></span>${task.server ? "" : `<button class="task-delete" type="button" data-action="delete-task" data-task-id="${esc(task.id)}" aria-label="Delete ${esc(task.title)}">×</button>`}</div>`).join("");
  };

  function overviewPanel(application, companion) {
    const age = daysSince(application.submitted_at);
    const nextDue = dueState(application.next_due_at);
    return `<div class="panel-grid"><div class="info-card"><span>Stage</span><strong>${esc(STAGE_LABELS[application.stage] || application.stage)}</strong></div><div class="info-card"><span>Date applied</span><strong>${esc(application.submitted_at ? formatDate(application.submitted_at) : "Not applied yet")}</strong></div><div class="info-card"><span>Time in process</span><strong>${age == null ? "—" : `${age} days`}</strong></div><div class="info-card"><span>Next deadline</span><strong>${esc(application.next_due_at ? `${nextDue.label} · ${formatDate(application.next_due_at)}` : "Not set")}</strong></div><div class="info-card full"><span>Next action</span><strong>${esc(application.next_action || "No next action yet")}</strong></div><div class="info-card full"><span>Notes</span><strong>${esc(application.note || "No notes yet")}</strong></div></div>
      <div class="local-note">Tasks, contacts, and materials on this page are saved privately in this browser. Core stages and application events use the OneShot API.</div>`;
  }

  function tasksPanel(application, companion) {
    return `<div class="section-head"><div><h3>Next actions</h3><p>Keep every follow-up and preparation step in one place.</p></div></div><div class="task-list">${taskRows(application, companion)}</div><form class="inline-form" data-form="task"><input name="title" required maxlength="500" placeholder="Add a concrete next step" aria-label="Task title"><input name="due_at" type="date" aria-label="Task due date"><button class="button primary" type="submit">Add</button></form>`;
  }

  function timelinePanel(application, companion) {
    const events = [...(application.events || []), ...(companion.events || [])].sort((a, b) => String(b.occurred_at || "").localeCompare(String(a.occurred_at || "")));
    return `<div class="section-head"><div><h3>Application history</h3><p>Stage changes, submissions, notes, and local workspace activity.</p></div></div><div class="timeline">${events.length ? events.map(event => `<div class="timeline-item"><div class="timeline-track"><i class="timeline-dot"></i></div><div class="timeline-copy"><strong>${esc(STAGE_LABELS[event.to_stage] || String(event.event_type || "activity").replaceAll("_", " "))}</strong><p>${esc(event.note || (event.from_stage && event.to_stage ? `${STAGE_LABELS[event.from_stage] || event.from_stage} → ${STAGE_LABELS[event.to_stage] || event.to_stage}` : "Application updated"))}</p><time datetime="${esc(event.occurred_at || "")}">${esc(formatDate(event.occurred_at))}</time></div></div>`).join("") : '<div class="empty-column">No history yet</div>'}</div>`;
  }

  function peoplePanel(application, companion) {
    const contacts = companion.contacts || [];
    return `<div class="section-head"><div><h3>People</h3><p>Recruiters, interviewers, referrals, and hiring managers.</p></div></div><div class="material-list">${contacts.length ? contacts.map(contact => `<div class="contact-card"><i class="contact-avatar">${esc(initials(contact.name))}</i><div><strong>${esc(contact.name)}</strong><span>${esc([contact.role, contact.email].filter(Boolean).join(" · "))}</span></div><button class="task-delete" type="button" data-action="delete-contact" data-contact-id="${esc(contact.id)}" aria-label="Delete ${esc(contact.name)}">×</button></div>`).join("") : '<div class="empty-column">No contacts saved</div>'}</div><form class="contact-form" data-form="contact"><label>Name<input name="name" required autocomplete="name"></label><label>Role<input name="role" placeholder="Recruiter"></label><label class="full">Email<input name="email" type="email" autocomplete="email"></label><div class="full"><button class="button primary" type="submit">Add contact</button></div></form>`;
  }

  function materialsPanel(application, companion) {
    const materials = companion.materials || [];
    const resume = application.resume_variant ? [{ id: "resume", label: "Résumé", detail: application.resume_variant, url: "", server: true }] : [];
    const all = [...resume, ...materials];
    return `<div class="section-head"><div><h3>Materials</h3><p>Keep the exact résumé, cover letter, portfolio, and prep links together.</p></div></div><div class="material-list">${all.length ? all.map(material => `<div class="material-row"><i class="company-mark" style="--mark-color:#47636a">${esc(initials(material.label))}</i><div><strong>${esc(material.label)}</strong><span>${esc(material.detail || material.url || "Saved material")}</span></div>${material.url ? `<a class="icon-button" href="${esc(safeHref(material.url))}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(material.label)}"><svg aria-hidden="true"><use href="#icon-external"/></svg></a>` : ""}${material.server ? "" : `<button class="task-delete" type="button" data-action="delete-material" data-material-id="${esc(material.id)}" aria-label="Delete ${esc(material.label)}">×</button>`}</div>`).join("") : '<div class="empty-column">No materials saved</div>'}</div><form class="material-form" data-form="material"><label>Name<input name="label" required placeholder="Cover letter"></label><label>Detail<input name="detail" placeholder="Product version"></label><label class="full">Link<input name="url" type="url" placeholder="https://…"></label><div class="full"><button class="button primary" type="submit">Add material</button></div></form>`;
  }

  function renderDrawer() {
    const application = selectedApplication();
    if (!application) return;
    const companion = companionFor(application.id);
    const source = safeHref(application.apply_url_snapshot);
    const panels = {
      overview: overviewPanel(presentApplication(application), companion),
      tasks: tasksPanel(application, companion),
      timeline: timelinePanel(application, companion),
      people: peoplePanel(application, companion),
      materials: materialsPanel(application, companion),
    };
    drawerContent.innerHTML = `<div class="drawer-shell"><header class="drawer-head"><i class="company-mark" style="--mark-color:${markColor(application.company)}">${esc(initials(application.company))}</i><div class="drawer-title"><h2 id="drawer-title">${esc(application.title || "Untitled role")}</h2><p>${esc(application.company || "Unknown company")}${application.location ? ` · ${esc(application.location)}` : ""}</p></div><button class="icon-button" type="button" data-action="close-drawer" aria-label="Close application details"><svg aria-hidden="true"><use href="#icon-close"/></svg></button></header><div class="drawer-hero"><label class="sr-only" for="drawer-stage">Application stage</label><select id="drawer-stage">${stageOptions(application)}</select>${source ? `<a class="button" href="${esc(source)}" target="_blank" rel="noopener noreferrer">Open posting <svg aria-hidden="true"><use href="#icon-external"/></svg></a>` : ""}<button class="button" type="button" data-action="quick-task">Add next step</button></div><div class="drawer-tabs" role="tablist" aria-label="Application details">${[["overview", "Overview"], ["tasks", "Tasks"], ["timeline", "Timeline"], ["people", "People"], ["materials", "Materials"]].map(([key, label]) => `<button type="button" role="tab" data-drawer-tab="${key}" aria-selected="${state.drawerTab === key}">${label}</button>`).join("")}</div><div class="drawer-body">${panels[state.drawerTab]}</div></div>`;
  }

  async function openApplication(id) {
    state.selectedId = id;
    state.drawerTab = "overview";
    const existing = state.applications.find(item => String(item.id) === String(id));
    try {
      const detail = await root.OneShotAPI.applications.get(id);
      state.applications = state.applications.map(item => String(item.id) === String(id) ? { ...existing, ...detail } : item);
    } catch (_) { /* The list record remains enough for a useful drawer. */ }
    renderDrawer();
    if (!drawer.open) drawer.showModal();
    history.replaceState(null, "", `#app=${encodeURIComponent(id)}`);
  }

  function closeDrawer() {
    if (drawer.open) drawer.close();
    state.selectedId = null;
    history.replaceState(null, "", location.pathname + location.search);
  }

  async function transitionApplication(application, nextStage, submittedDate = null) {
    const previousStage = application.stage;
    if (nextStage === previousStage) return;
    const occurredAt = new Date().toISOString();
    if (nextStage === "submitted") {
      const date = submittedDate || dateInputValue(application.submitted_at) || new Date().toISOString().slice(0, 10);
      if (application.external) {
        await root.OneShotAPI.applications.addEvent(application.id, { event_type: "submitted", stage: "submitted", occurred_at: isoFromDate(date), confirmed: true, note: "Human-confirmed submission." });
      } else if (application.job_id) {
        await root.OneShotAPI.request(root.OneShotAPI.ROUTES.addToApplications(application.job_id), { method: "POST", body: JSON.stringify({ stage: "submitted", confirmation: "human_confirmed", submitted_at: date }) });
      }
      application.submitted_at = isoFromDate(date);
    } else {
      const eventType = ["assessment", "screen", "interview", "offer", "rejected", "withdrawn", "no_reply"].includes(nextStage) ? nextStage : "stage_changed";
      await root.OneShotAPI.applications.addEvent(application.id, { event_type: eventType, stage: nextStage, note: `Moved to ${STAGE_LABELS[nextStage] || nextStage}.` });
    }
    application.stage = nextStage;
    application.updated_at = occurredAt;
    updateCompanion(application.id, companion => ({ ...companion, events: [...(companion.events || []), { occurred_at: occurredAt, event_type: "stage_changed", from_stage: previousStage, to_stage: nextStage, note: "" }] }));
    render();
    renderDrawer();
    showToast(`Moved to ${STAGE_LABELS[nextStage] || nextStage}.`, { label: "Undo", run: async () => transitionApplication(application, previousStage, dateInputValue(application.submitted_at)) });
  }

  function requestSubmission(application) {
    state.pendingSubmissionId = application.id;
    submissionForm.reset();
    submissionForm.elements.submitted_at.value = dateInputValue(application.submitted_at) || new Date().toISOString().slice(0, 10);
    submissionDialog.showModal();
  }

  async function addTask(form) {
    const application = selectedApplication();
    const data = new FormData(form);
    const task = { id: root.crypto?.randomUUID?.() || `task-${Date.now()}`, title: String(data.get("title") || "").trim(), due_at: String(data.get("due_at") || ""), completed: false };
    if (!task.title) return;
    updateCompanion(application.id, companion => ({ ...companion, tasks: [...(companion.tasks || []), task], events: [...(companion.events || []), { occurred_at: new Date().toISOString(), event_type: "task_added", note: task.title }] }));
    try {
      const updated = await root.OneShotAPI.applications.addEvent(application.id, { event_type: "task_added", due_at: task.due_at ? isoFromDate(task.due_at) : null, note: task.title });
      Object.assign(application, updated || {}, { next_action: task.title, next_due_at: task.due_at || null });
    } catch (_) {
      application.next_action = task.title;
      application.next_due_at = task.due_at || null;
      showToast("Task saved locally; the application API did not accept the sync.");
    }
    render();
    renderDrawer();
  }

  function mutateLocalCollection(collection, value) {
    const application = selectedApplication();
    updateCompanion(application.id, companion => ({ ...companion, [collection]: [...(companion[collection] || []), value] }));
    renderDrawer();
  }

  document.addEventListener("click", async event => {
    const applicationTrigger = event.target.closest("[data-application-id]");
    if (applicationTrigger) return openApplication(applicationTrigger.dataset.applicationId);
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "open-add") { addForm.reset(); $("[data-submitted-field]").hidden = true; return addDialog.showModal(); }
    if (action === "close-add") return addDialog.close();
    if (action === "close-submission") { state.pendingSubmissionId = null; submissionDialog.close(); renderDrawer(); return; }
    if (action === "close-drawer") return closeDrawer();
    if (action === "clear-filters") { state.query = ""; state.quickFilter = ""; $("#application-search").value = ""; $$('[data-quick-filter]').forEach(button => button.setAttribute("aria-pressed", "false")); return render(); }
    if (action === "enable-sample") { root.OneShotSampleData?.setEnabled?.(true); return location.reload(); }
    if (action === "quick-task") { state.drawerTab = "tasks"; renderDrawer(); return drawerContent.querySelector('[name="title"]')?.focus(); }
    if (action === "toggle-task") {
      const id = event.target.closest("[data-task-id]").dataset.taskId;
      const application = selectedApplication();
      updateCompanion(application.id, companion => id === "server-next" ? { ...companion, serverTaskCompleted: !companion.serverTaskCompleted } : { ...companion, tasks: (companion.tasks || []).map(task => task.id === id ? { ...task, completed: !task.completed } : task) });
      renderDrawer();
      return showToast("Task updated.");
    }
    if (action === "delete-task" || action === "delete-contact" || action === "delete-material") {
      const application = selectedApplication();
      const map = { "delete-task": ["tasks", "taskId"], "delete-contact": ["contacts", "contactId"], "delete-material": ["materials", "materialId"] };
      const [collection, dataKey] = map[action], id = event.target.closest("[data-action]").dataset[dataKey];
      updateCompanion(application.id, companion => ({ ...companion, [collection]: (companion[collection] || []).filter(item => item.id !== id) }));
      return renderDrawer();
    }
  });

  document.addEventListener("click", event => {
    const statusTab = event.target.closest("[data-status-view]");
    if (statusTab) { state.statusView = statusTab.dataset.statusView; state.quickFilter = ""; $$('[data-quick-filter]').forEach(button => button.setAttribute("aria-pressed", "false")); render(); }
    const quickFilter = event.target.closest("[data-quick-filter]");
    if (quickFilter) { const selected = state.quickFilter === quickFilter.dataset.quickFilter; state.quickFilter = selected ? "" : quickFilter.dataset.quickFilter; $$('[data-quick-filter]').forEach(button => button.setAttribute("aria-pressed", String(!selected && button === quickFilter))); render(); }
    const display = event.target.closest("[data-display]");
    if (display) { state.display = display.dataset.display; root.localStorage.setItem("oneshot.application.display", state.display); render(); }
    const drawerTab = event.target.closest("[data-drawer-tab]");
    if (drawerTab) { state.drawerTab = drawerTab.dataset.drawerTab; renderDrawer(); }
  });

  document.addEventListener("input", event => {
    if (event.target.id === "application-search") { state.query = event.target.value; render(); }
  });
  document.addEventListener("change", async event => {
    if (event.target.id === "application-sort") { state.sort = event.target.value; render(); }
    if (event.target.name === "starting_stage") { $("[data-submitted-field]").hidden = event.target.value !== "submitted"; }
    if (event.target.id === "drawer-stage") {
      const application = selectedApplication();
      if (event.target.value === "submitted") return requestSubmission(application);
      try { await transitionApplication(application, event.target.value); }
      catch (error) { showToast(error.message || "Stage could not be updated."); renderDrawer(); }
    }
  });

  document.addEventListener("submit", async event => {
    if (event.target === addForm) {
      event.preventDefault();
      const submit = addForm.querySelector('[type="submit"]');
      submit.disabled = true;
      try {
        const data = new FormData(addForm);
        const startingStage = String(data.get("starting_stage"));
        const application = await root.OneShotAPI.applications.createExternal({ company: String(data.get("company")).trim(), title: String(data.get("title")).trim(), url: String(data.get("url")).trim(), location: String(data.get("location") || "").trim(), note: String(data.get("note") || "").trim() });
        if (startingStage === "submitted") {
          const submittedAt = String(data.get("submitted_at") || new Date().toISOString().slice(0, 10));
          const updated = await root.OneShotAPI.applications.addEvent(application.id, { event_type: "submitted", stage: "submitted", occurred_at: isoFromDate(submittedAt), confirmed: true, note: "Human-confirmed submission." });
          Object.assign(application, updated || {}, { stage: "submitted", submitted_at: isoFromDate(submittedAt) });
        }
        state.applications.unshift(application);
        addDialog.close();
        render();
        showToast("Application added.");
        openApplication(application.id);
      } catch (error) { showToast(error.message || "Application could not be added."); }
      finally { submit.disabled = false; }
      return;
    }
    if (event.target === submissionForm) {
      event.preventDefault();
      const application = state.applications.find(item => String(item.id) === String(state.pendingSubmissionId));
      if (!application) return submissionDialog.close();
      const data = new FormData(submissionForm);
      try {
        await transitionApplication(application, "submitted", String(data.get("submitted_at")));
        state.pendingSubmissionId = null;
        submissionDialog.close();
      } catch (error) { showToast(error.message || "Submission could not be recorded."); }
      return;
    }
    const formType = event.target.dataset.form;
    if (!formType) return;
    event.preventDefault();
    if (formType === "task") return addTask(event.target);
    const data = new FormData(event.target);
    if (formType === "contact") mutateLocalCollection("contacts", { id: root.crypto?.randomUUID?.() || `contact-${Date.now()}`, name: String(data.get("name") || "").trim(), role: String(data.get("role") || "").trim(), email: String(data.get("email") || "").trim() });
    if (formType === "material") mutateLocalCollection("materials", { id: root.crypto?.randomUUID?.() || `material-${Date.now()}`, label: String(data.get("label") || "").trim(), detail: String(data.get("detail") || "").trim(), url: safeHref(data.get("url")) });
  });

  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#application-search").focus(); }
    if (event.key === "Escape" && drawer.open) closeDrawer();
    if (event.target.matches('[role="tab"]') && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      const tabs = [...event.target.parentElement.querySelectorAll('[role="tab"]')], current = tabs.indexOf(event.target), direction = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(current + direction + tabs.length) % tabs.length];
      next.focus(); next.click();
    }
  });

  document.addEventListener("dragstart", event => {
    const card = event.target.closest("[data-application-id]");
    if (!card) return;
    state.dragId = card.dataset.applicationId;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
  });
  document.addEventListener("dragend", event => { event.target.closest("[data-application-id]")?.classList.remove("dragging"); $$(".drag-over").forEach(column => column.classList.remove("drag-over")); state.dragId = null; });
  document.addEventListener("dragover", event => { const column = event.target.closest("[data-drop-group]"); if (column && state.dragId) { event.preventDefault(); column.classList.add("drag-over"); } });
  document.addEventListener("dragleave", event => { const column = event.target.closest("[data-drop-group]"); if (column && !column.contains(event.relatedTarget)) column.classList.remove("drag-over"); });
  document.addEventListener("drop", async event => {
    const column = event.target.closest("[data-drop-group]");
    if (!column || !state.dragId) return;
    event.preventDefault();
    column.classList.remove("drag-over");
    const application = state.applications.find(item => String(item.id) === String(state.dragId));
    const group = ACTIVE_GROUPS.find(item => item.key === column.dataset.dropGroup);
    if (!application || !group) return;
    const nextStage = group.key === "applied" ? "submitted" : group.key === "interviewing" ? "interview" : group.stages[0];
    if (nextStage === "submitted") return requestSubmission(application);
    try { await transitionApplication(application, nextStage); }
    catch (error) { showToast(error.message || "Application could not be moved."); render(); }
  });

  drawer.addEventListener("click", event => { if (event.target === drawer) closeDrawer(); });
  addDialog.addEventListener("click", event => { if (event.target === addDialog) addDialog.close(); });
  submissionDialog.addEventListener("click", event => { if (event.target === submissionDialog) { state.pendingSubmissionId = null; submissionDialog.close(); renderDrawer(); } });
  drawer.addEventListener("close", () => { if (state.selectedId) { state.selectedId = null; history.replaceState(null, "", location.pathname + location.search); } });

  loadApplications();
})(typeof window !== "undefined" ? window : globalThis);
