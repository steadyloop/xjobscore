(function (root) {
  "use strict";

  const ENABLED_KEY = "oneshot.sample-data.enabled";
  const STATE_KEY = "oneshot.sample-data.state.v4";
  const clone = value => JSON.parse(JSON.stringify(value));
  const isoIn = (days = 0, hours = 0) => new Date(Date.now() + days * 86400000 + hours * 3600000).toISOString();

  function createState() {
    const jobs = [
      {
        id: "sample-research",
        company: "Northwind Robotics",
        title: "Student Researcher, Distributed Training",
        location: "Remote, United States",
        posted_at: isoIn(0, -2),
        last_seen_at: isoIn(0, -1),
        description: "Research sharded optimizer state and fault-tolerant training for multi-node machine-learning workloads. You will prototype ideas, evaluate them on production-scale clusters, and present results to the research systems team.",
        attributes: { version: 1, opportunity_type: "student_researcher", term: "Spring 2027", workplace: "remote", skills: ["JAX", "Python", "Ray"], eligibility: "eligible" },
        apply_url: "https://example.com/apply/sample-research",
      },
      {
        id: "sample-quant",
        company: "Vantage Quant",
        title: "Summer Quant Systems Intern",
        location: "New York, NY",
        posted_at: isoIn(-1),
        last_seen_at: isoIn(0, -3),
        description: "Build research infrastructure used to test and deploy systematic trading strategies. Projects include low-latency data processing, simulation tooling, and production monitoring.",
        attributes: { version: 1, opportunity_type: "summer_internship", term: "Summer 2027", workplace: "on_site", skills: ["Python", "C++", "Linux"], eligibility: "eligible" },
        apply_url: "https://example.com/apply/sample-quant",
      },
      {
        id: "sample-software",
        company: "Halcyon Systems",
        title: "Software Engineering Intern",
        location: "San Diego, CA",
        posted_at: isoIn(-2),
        last_seen_at: isoIn(0, -4),
        description: "Join the platform team building services for fleet operations. You will own a scoped backend project, write production code, and work with engineers across infrastructure and product.",
        attributes: { version: 1, opportunity_type: "summer_internship", workplace: "hybrid", skills: ["Go", "Kubernetes", "PostgreSQL"], eligibility: "review" },
        apply_url: "https://example.com/apply/sample-software",
      },
      {
        id: "sample-embedded",
        company: "Cobalt Foundry",
        title: "Fall Embedded Software Co-op",
        location: "Austin, TX",
        posted_at: isoIn(-3),
        last_seen_at: isoIn(-1),
        description: "Develop firmware and systems software for industrial control hardware. The role includes driver development, hardware bring-up, automated testing, and debugging real-time systems.",
        attributes: { version: 1, opportunity_type: "fall_coop", term: "Fall 2027", workplace: "on_site", skills: ["C++", "RTOS", "Linux"], eligibility: "eligible" },
        apply_url: "https://example.com/apply/sample-embedded",
      },
      {
        id: "sample-platform",
        company: "Arbor Signal",
        title: "Backend Platform Intern",
        location: "Boston, MA",
        posted_at: isoIn(-4),
        last_seen_at: isoIn(0, -5),
        description: "Build internal APIs and data services for a growing observability platform. You will ship a production project with the infrastructure team.",
        attributes: { version: 1, opportunity_type: "summer_internship", term: "Summer 2027", workplace: "hybrid", skills: ["TypeScript", "PostgreSQL", "AWS"], eligibility: "eligible" },
        apply_url: "https://example.com/apply/sample-platform",
      },
      {
        id: "sample-ml-research",
        company: "Kepler Bio",
        title: "Machine Learning Research Intern",
        location: "Cambridge, MA",
        posted_at: isoIn(-5),
        last_seen_at: isoIn(0, -6),
        description: "Develop evaluation methods for biological foundation models and collaborate with research scientists on reproducible experiments.",
        attributes: { version: 1, opportunity_type: "student_researcher", term: "Spring 2027", workplace: "on_site", skills: ["Python", "PyTorch", "Transformers"], eligibility: "eligible" },
        apply_url: "https://example.com/apply/sample-ml-research",
      },
    ];

    const applications = [
      {
        id: "sample-app-considering",
        job_id: "sample-quant",
        company: "Vantage Quant",
        title: "Summer Quant Systems Intern",
        stage: "considering",
        closed_reason: null,
        next_action: "Review the role and decide whether to apply",
        next_due_at: isoIn(4),
        external: false,
        submitted_at: null,
        apply_url_snapshot: "https://example.com/apply/sample-quant",
        updated_at: isoIn(-1),
        note: "",
        events: [{ occurred_at: isoIn(-1), event_type: "considering", to_stage: "considering", note: "Added from Discover." }],
      },
      {
        id: "sample-app-assessment",
        job_id: "sample-research",
        company: "Northwind Robotics",
        title: "Student Researcher, Distributed Training",
        stage: "assessment",
        closed_reason: null,
        next_action: "Complete the systems exercise",
        next_due_at: isoIn(1),
        external: false,
        submitted_at: isoIn(-6),
        apply_url_snapshot: "https://example.com/apply/sample-research",
        updated_at: isoIn(-1),
        note: "",
        events: [
          { occurred_at: isoIn(-6), event_type: "applied", to_stage: "applied", note: "Application submitted." },
          { occurred_at: isoIn(-1), event_type: "assessment", to_stage: "assessment", note: "Systems exercise received." },
        ],
      },
      {
        id: "sample-app-interview",
        company: "Meridian Labs",
        title: "Robotics Software Co-op",
        stage: "interview",
        closed_reason: null,
        next_action: "Prepare architecture examples",
        next_due_at: isoIn(7),
        external: true,
        submitted_at: isoIn(-10),
        apply_url_snapshot: "https://example.com/apply/meridian",
        updated_at: isoIn(-2),
        note: "Technical interview scheduled with the platform team.",
        events: [{ occurred_at: isoIn(-2), event_type: "interview", to_stage: "interview", note: "Technical interview scheduled." }],
      },
      {
        id: "sample-app-offer",
        company: "Atlas Grid",
        title: "Site Reliability Intern",
        stage: "offer",
        closed_reason: null,
        next_action: "Review the written offer",
        next_due_at: isoIn(10),
        external: true,
        submitted_at: isoIn(-18),
        apply_url_snapshot: "https://example.com/apply/atlas",
        updated_at: isoIn(-1),
        note: "Compare the start date and relocation package.",
        events: [{ occurred_at: isoIn(-1), event_type: "offer", to_stage: "offer", note: "Written offer received." }],
      },
    ];

    return { jobs, applications, nextApplicationId: 5 };
  }

  const jsonResponse = (payload, status = 200) => new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  const parseBody = options => {
    try { return JSON.parse(options?.body || "{}"); }
    catch (_) { return {}; }
  };
  const findJob = (state, id) => state.jobs.find(job => String(job.id) === String(id));
  const findApplication = (state, id) => state.applications.find(application => String(application.id) === String(id));

  function applicationFromJob(state, job) {
    return {
      id: `sample-app-${state.nextApplicationId++}`,
      job_id: job.id,
      company: job.company,
      title: job.title,
      location: job.location,
      stage: "considering",
      closed_reason: null,
      next_action: null,
      next_due_at: null,
      external: false,
      submitted_at: null,
      apply_url_snapshot: job.apply_url,
      updated_at: new Date().toISOString(),
      note: "",
      events: [{ occurred_at: new Date().toISOString(), event_type: "stage_changed", to_stage: "considering", note: null }],
    };
  }

  async function handle(input, options = {}, state = createState()) {
    const url = new URL(typeof input === "string" ? input : input.url, "http://oneshot.local");
    const method = String(options.method || "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api(?=\/)/, "");

    if (method === "GET" && path === "/health") {
      return jsonResponse({ ok: true });
    }
    if (method === "GET" && path === "/jobs") {
      const q = String(url.searchParams.get("q") || "").trim().toLowerCase();
      const types = url.searchParams.getAll("opportunity_type");
      const applicationJobIds = new Set(state.applications.map(application => application.job_id).filter(Boolean));
      const filtered = state.jobs.filter(job => {
        const searchable = `${job.title} ${job.company} ${job.location} ${job.attributes.skills.join(" ")}`.toLowerCase();
        return !applicationJobIds.has(job.id) && (!q || searchable.includes(q)) && (!types.length || types.includes(job.attributes.opportunity_type));
      });
      const sort = url.searchParams.get("sort") || "fresh";
      filtered.sort((a, b) => sort === "company"
        ? a.company.localeCompare(b.company)
        : sort === "title"
          ? a.title.localeCompare(b.title)
          : b.posted_at.localeCompare(a.posted_at));
      return jsonResponse({ jobs: clone(filtered), total: filtered.length, limit: 100, offset: 0 });
    }

    const addMatch = path.match(/^\/jobs\/([^/]+)\/applications$/);
    if (method === "POST" && addMatch) {
      const job = findJob(state, decodeURIComponent(addMatch[1]));
      if (!job) return jsonResponse({ detail: "Job not found" }, 404);
      const existing = state.applications.find(application => application.job_id === job.id);
      if (existing) return jsonResponse({ ...clone(existing), already_exists: true });
      const application = applicationFromJob(state, job);
      state.applications.unshift(application);
      return jsonResponse(clone(application), 201);
    }

    if (method === "POST" && path === "/applications/external") {
      const body = parseBody(options);
      const application = {
        id: `sample-app-${state.nextApplicationId++}`,
        company: body.company,
        title: body.title,
        stage: "considering",
        closed_reason: null,
        next_action: null,
        next_due_at: null,
        external: true,
        submitted_at: null,
        apply_url_snapshot: body.url,
        updated_at: new Date().toISOString(),
        location: body.location || "",
        note: body.note || "",
        events: [{ occurred_at: new Date().toISOString(), event_type: "stage_changed", to_stage: "considering", note: body.note || null }],
      };
      state.applications.unshift(application);
      return jsonResponse(clone(application), 201);
    }

    if (method === "GET" && path === "/applications") {
      const applications = state.applications.map(({ events, ...application }) => application);
      return jsonResponse({ applications: clone(applications) });
    }

    const applicationMatch = path.match(/^\/applications\/([^/]+)$/);
    if (method === "GET" && applicationMatch) {
      const application = findApplication(state, decodeURIComponent(applicationMatch[1]));
      return application ? jsonResponse(clone(application)) : jsonResponse({ detail: "Application not found" }, 404);
    }
    if (method === "PATCH" && applicationMatch) {
      const application = findApplication(state, decodeURIComponent(applicationMatch[1]));
      if (!application) return jsonResponse({ detail: "Application not found" }, 404);
      const body = parseBody(options),previousStage=application.stage;
      if (application.external) {
        if (typeof body.company === "string" && body.company.trim()) application.company = body.company.trim();
        if (typeof body.title === "string" && body.title.trim()) application.title = body.title.trim();
        if (typeof body.apply_url_snapshot === "string") application.apply_url_snapshot = body.apply_url_snapshot.trim();
      }
      if (body.stage) application.stage = body.stage;
      if (body.closed_reason !== undefined) application.closed_reason = body.closed_reason;
      if (application.stage !== "closed") application.closed_reason = null;
      if (body.submitted_at !== undefined) application.submitted_at = body.submitted_at;
      if (body.next_action !== undefined) application.next_action = body.next_action;
      if (body.next_due_at !== undefined) application.next_due_at = body.next_due_at;
      if (body.note !== undefined) application.note = body.note;
      application.updated_at = new Date().toISOString();
      if (application.stage !== previousStage) application.events.push({ occurred_at: application.updated_at, event_type: "stage_changed", to_stage: application.stage, note: "" });
      return jsonResponse(clone(application));
    }
    if (method === "DELETE" && applicationMatch) {
      const index = state.applications.findIndex(application => String(application.id) === String(decodeURIComponent(applicationMatch[1])));
      if (index < 0) return jsonResponse({ detail: "Application not found" }, 404);
      state.applications.splice(index, 1);
      return new Response(null, { status: 204 });
    }

    if (method === "GET" && path === "/source-coverage") {
      return jsonResponse({
        sources: [
          { source: "greenhouse", type: "ATS", board_count: 1, coverage: 1, last_poll_at: isoIn(0, -1), health: "Healthy", added_today: 1 },
          { source: "lever", type: "ATS", board_count: 2, coverage: 2, last_poll_at: isoIn(0, -3), health: "Healthy", added_today: 1 },
          { source: "smartrecruiters", type: "ATS", board_count: 1, coverage: 1, last_poll_at: isoIn(-1), health: "Needs attention", added_today: 0 },
        ],
        source_count: 3,
        total_coverage: 4,
        total_added_today: 2,
      });
    }

    return jsonResponse({ detail: `No sample response for ${method} ${path}` }, 404);
  }

  let state;
  const storageAvailable = () => Boolean(root.localStorage);
  const isEnabled = () => storageAvailable() && root.localStorage.getItem(ENABLED_KEY) === "1";
  const save = () => { if (storageAvailable()) root.localStorage.setItem(STATE_KEY, JSON.stringify(state)); };
  const loadState = () => {
    if (state) return state;
    try { state = JSON.parse(root.localStorage.getItem(STATE_KEY)) || createState(); }
    catch (_) { state = createState(); }
    return state;
  };
  const setEnabled = value => {
    if (!storageAvailable()) return;
    root.localStorage.setItem(ENABLED_KEY, value ? "1" : "0");
    if (value) { state = createState(); save(); }
  };
  const request = async (path, options = {}) => {
    const result = await handle(path, options, loadState());
    if (String(options.method || "GET").toUpperCase() !== "GET") save();
    return result;
  };
  const syncToggle = () => {
    const toggle = root.document?.querySelector('[data-action="sample-toggle"]');
    if (!toggle) return;
    const active = isEnabled();
    toggle.classList.toggle("active", active);
    toggle.setAttribute("aria-checked", String(active));
    toggle.setAttribute("aria-label", `Turn ${active ? "off" : "on"} sample data`);
    const status = toggle.querySelector("small");
    if (status) status.textContent = active ? "On" : "Off";
  };

  if (typeof module !== "undefined" && module.exports) module.exports = { createState, handle };
  root.OneShotSampleData = { request, isEnabled };
  if (!root.document || !storageAvailable()) return;

  root.document.addEventListener("click", event => {
    const toggle = event.target.closest('[data-action="sample-toggle"]');
    if (!toggle) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setEnabled(!isEnabled());
    root.location.reload();
  }, true);
  syncToggle();
})(typeof window !== "undefined" ? window : globalThis);
