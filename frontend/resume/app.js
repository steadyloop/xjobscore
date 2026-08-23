/* Resume studio: content bank -> targets -> builder -> rendered PDF. */
(() => {
  "use strict";

  const state = {
    profile: null,
    entries: [],
    skills: [],
    jobs: [],
    resumes: [],
    typstAvailable: false,
    browserLocal: false,
    tab: "bank",
    resumeId: null,
    coverage: null,
    check: null,
  };

  const KIND_TITLES = {
    education: "Education",
    experience: "Experience",
    research: "Research",
    project: "Projects",
    leadership: "Leadership",
  };
  const SKILL_TITLES = {
    language: "Languages",
    framework: "Frameworks & Libraries",
    tool: "Tools",
    coursework: "Coursework",
    math: "Math & Methods",
  };

  const view = document.getElementById("view");
  const toast = document.getElementById("toast");
  let toastTimer = null;

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function say(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
  }

  // Sample mode cannot run Typst in the browser, so it links a pre-rendered
  // export of the sample content bank instead of compiling on demand.
  const SAMPLE_PDF_URL = "/static/resume/sample-resume.pdf";
  const SAMPLE_TYP_URL = "/static/resume/sample-resume.typ";

  function sampleModeOn() {
    return Boolean(window.OneShotSampleData?.isEnabled?.());
  }

  async function api(path, options = {}) {
    const init = {
      headers: options.body ? { "Content-Type": "application/json" } : {},
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    };
    let response;
    if (sampleModeOn()) {
      response = await window.OneShotSampleData.request(`/resume${path}`, init);
    } else if (window.OneShotVisitorData && await window.OneShotVisitorData.isLocal()) {
      response = await window.OneShotVisitorData.request(`/resume${path}`, init);
    } else {
      response = await fetch(`/api/resume${path}`, init);
    }
    // The studio has its own transport, so the 401 handling in api-client.js
    // does not cover it. Same contract: navigate, and never settle, so no toast
    // paints over the page on its way out.
    if (response.status === 401) {
      if (window.location.pathname !== "/login/") window.location.assign("/login/");
      return new Promise(() => {});
    }
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : "Request failed";
      throw new Error(detail);
    }
    return payload;
  }

  async function loadAll() {
    const [profile, entries, skills, jobs, resumes] = await Promise.all([
      api("/profile"), api("/entries"), api("/skills"), api("/jobs"), api("/resumes"),
    ]);
    state.profile = profile;
    state.entries = entries.entries;
    state.skills = skills.skills;
    state.jobs = jobs.jobs;
    state.resumes = resumes.resumes;
    state.typstAvailable = resumes.typst_available;
    if (state.resumeId === null && state.resumes.length) state.resumeId = state.resumes[0].id;
    // Sample mode has a pre-rendered file. Browser-local resumes deliberately
    // never upload their content, so server-side Typst export requires sign-in.
    const badge = document.getElementById("typst-badge");
    state.browserLocal = Boolean(
      !sampleModeOn() && window.OneShotVisitorData
      && await window.OneShotVisitorData.isLocal()
    );
    badge.hidden = state.typstAvailable || sampleModeOn();
    badge.textContent = state.browserLocal
      ? "Saved in this browser — sign in for PDF export"
      : "Typst not installed — PDF export disabled";
  }

  const currentResume = () => state.resumes.find((r) => r.id === state.resumeId) || null;

  /* ---------- rendering ---------- */

  function render() {
    document.querySelectorAll(".view-tabs button").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.tab === state.tab));
    });
    if (state.tab === "bank") renderBank();
    else if (state.tab === "targets") renderTargets();
    else renderBuilder();
  }

  function renderBank() {
    const profile = state.profile;
    const links = (profile.links || [])
      .map((link) => `${link.label || ""} ${link.url}`.trim())
      .join("\n");
    const groups = Object.keys(KIND_TITLES).map((kind) => {
      const cards = state.entries.filter((entry) => entry.kind === kind).map(entryCard).join("");
      return `
        <div class="kind-group">
          <header><h3>${KIND_TITLES[kind]}</h3>
            <button class="button small" data-action="add-entry" data-kind="${kind}">+ Add</button></header>
          ${cards || '<p class="empty">Nothing yet.</p>'}
        </div>`;
    }).join("");

    const skillGroups = Object.keys(SKILL_TITLES).map((category) => {
      const chips = state.skills.filter((skill) => skill.category === category).map((skill) => `
        <span class="skill">${esc(skill.name)}
          <button type="button" data-action="delete-skill" data-id="${skill.id}" aria-label="Remove ${esc(skill.name)}">×</button>
        </span>`).join("");
      return `<div class="skill-group"><h3>${SKILL_TITLES[category]}</h3>
        <div class="skill-chips">${chips || '<p class="empty">—</p>'}</div></div>`;
    }).join("");

    view.innerHTML = `
      <div class="bank-grid">
        <div>
          <section class="panel">
            <h2>Profile</h2>
            <form id="profile-form" class="profile-grid">
              <label>Full name<input name="full_name" value="${esc(profile.full_name)}"></label>
              <label>Email<input name="email" value="${esc(profile.email)}"></label>
              <label>Phone<input name="phone" value="${esc(profile.phone)}"></label>
              <label>Location<input name="location" value="${esc(profile.location)}"></label>
              <label class="full">Links — one per line: "label url"<textarea name="links" rows="3">${esc(links)}</textarea></label>
              <div class="full"><button class="button primary" type="submit">Save profile</button></div>
            </form>
          </section>
          ${groups}
        </div>
        <aside class="panel">
          <h2>Skill bank <small>only what survives an interview</small></h2>
          ${skillGroups}
          <form id="skill-form" class="skill-add">
            <div class="row">
              <select name="category">${Object.entries(SKILL_TITLES).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select>
              <select name="audience"><option value="all">All</option><option value="mlsys">ML Systems</option><option value="swe">Systems SWE</option><option value="quant">Quant</option></select>
            </div>
            <div class="row"><input name="name" required maxlength="120" placeholder="e.g. PyTorch">
              <button class="button small primary" type="submit">Add</button></div>
          </form>
        </aside>
      </div>`;
  }

  function entryCard(entry) {
    const bullets = entry.bullets.map((bullet) => `
      <li>
        <div class="text">${esc(bullet.text)}
          ${bullet.defense_notes ? `<small>defense: ${esc(bullet.defense_notes)}</small>` : ""}
        </div>
        <span class="chip ${bullet.audience}">${bullet.audience}</span>
        <span class="entry-tools">
          <button class="icon-button mini" data-action="edit-bullet" data-id="${bullet.id}" data-entry="${entry.id}" aria-label="Edit bullet"><svg aria-hidden="true"><use href="#icon-edit"/></svg></button>
          <button class="icon-button mini" data-action="delete-bullet" data-id="${bullet.id}" aria-label="Delete bullet"><svg aria-hidden="true"><use href="#icon-trash"/></svg></button>
        </span>
      </li>`).join("");
    const dates = [entry.start_date, entry.end_date].filter(Boolean).join(" – ");
    return `
      <article class="entry-card" data-entry-id="${entry.id}">
        <header>
          <b>${esc(entry.org)}</b>${entry.role ? `<i>· ${esc(entry.role)}</i>` : ""}
          <span class="entry-tools">
            <button class="icon-button mini" data-action="edit-entry" data-id="${entry.id}" aria-label="Edit entry"><svg aria-hidden="true"><use href="#icon-edit"/></svg></button>
            <button class="icon-button mini" data-action="delete-entry" data-id="${entry.id}" aria-label="Delete entry"><svg aria-hidden="true"><use href="#icon-trash"/></svg></button>
          </span>
          <span class="dates">${esc(dates)}</span>
        </header>
        ${entry.detail ? `<div class="empty">${esc(entry.detail)}</div>` : ""}
        <ul class="bullet-list">${bullets}</ul>
        <button class="button small" style="margin-top:8px" data-action="add-bullet" data-entry="${entry.id}">+ Bullet</button>
      </article>`;
  }

  function ageBadge(job) {
    if (!job.date_posted) return '<span class="age-badge unknown">posted: unknown</span>';
    const days = Math.floor((Date.now() - new Date(`${job.date_posted}T00:00:00`)) / 86400000);
    if (Number.isNaN(days)) return '<span class="age-badge unknown">posted: unknown</span>';
    const bucket = days <= 7 ? "fresh" : days <= 21 ? "warm" : "stale";
    return `<span class="age-badge ${bucket}">${days} day${days === 1 ? "" : "s"} since posted</span>`;
  }

  function renderTargets() {
    const cards = state.jobs.map((job) => `
      <article class="target-card">
        <header>
          <div><h3>${esc(job.company)}</h3><p>${esc(job.title)}</p></div>
          <span class="tools">
            <button class="icon-button mini" data-action="edit-job" data-id="${job.id}" aria-label="Edit target"><svg aria-hidden="true"><use href="#icon-edit"/></svg></button>
            <button class="icon-button mini" data-action="delete-job" data-id="${job.id}" aria-label="Delete target"><svg aria-hidden="true"><use href="#icon-trash"/></svg></button>
          </span>
        </header>
        ${ageBadge(job)}
        <p class="meta">${job.url ? `<a href="${esc(job.url)}" target="_blank" rel="noopener">posting ↗</a> · ` : ""}JD: ${job.jd_text ? `${job.jd_text.length.toLocaleString()} chars` : "not pasted"}</p>
        ${job.note ? `<p class="note">${esc(job.note)}</p>` : ""}
      </article>`).join("");
    view.innerHTML = `
      <div style="margin-bottom:14px"><button class="button primary" data-action="add-job">+ Add target posting</button>
      <span class="empty" style="margin-left:10px">Speed matters: apply within the first 2–3 weeks of a posting.</span></div>
      <div class="target-grid">${cards || '<p class="empty">No targets yet. Paste a JD to start tailoring.</p>'}</div>`;
  }

  function renderBuilder() {
    const resume = currentResume();
    const options = state.resumes.map((r) =>
      `<option value="${r.id}" ${r.id === state.resumeId ? "selected" : ""}>${esc(r.name)}</option>`).join("");
    if (!resume) {
      view.innerHTML = `
        <div class="resume-picker">
          <button class="button primary" data-action="new-resume">+ New resume</button>
          <span class="empty">Create a resume, then select bullets from the bank.</span>
        </div>`;
      return;
    }
    const selection = resume.selection || {};
    const pickedBullets = new Set(
      (selection.entries || []).flatMap((pick) => (pick.bullets || []).map((b) => b.bullet_id)));
    const pickedSkills = new Set(selection.skills || []);
    const jobOptions = ['<option value="">— no target job —</option>']
      .concat(state.jobs.map((job) =>
        `<option value="${job.id}" ${job.id === resume.target_job_id ? "selected" : ""}>${esc(job.company)} · ${esc(job.title)}</option>`))
      .join("");

    const entryPicks = state.entries.map((entry) => {
      const rows = entry.bullets.map((bullet) => {
        const off = bullet.audience !== "all" && bullet.audience !== resume.variant;
        return `<li><label class="${off ? "off-audience" : ""}">
          <input type="checkbox" data-pick-bullet="${bullet.id}" data-pick-entry="${entry.id}" ${pickedBullets.has(bullet.id) ? "checked" : ""}>
          <span>${esc(bullet.text)} <span class="chip ${bullet.audience}">${bullet.audience}</span></span>
        </label></li>`;
      }).join("");
      const dates = [entry.start_date, entry.end_date].filter(Boolean).join(" – ");
      return `
        <div class="pick-entry" data-kind="${entry.kind}">
          <label><b>${esc(entry.org)}</b> ${entry.role ? esc(entry.role) : ""}
            <span class="chip">${KIND_TITLES[entry.kind]}</span><span class="dates">${esc(dates)}</span></label>
          <ul>${rows || '<li class="empty">No bullets in the bank for this entry.</li>'}</ul>
        </div>`;
    }).join("");

    const skillPicks = Object.keys(SKILL_TITLES).map((category) => {
      const rows = state.skills.filter((skill) => skill.category === category).map((skill) => `
        <label><input type="checkbox" data-pick-skill="${skill.id}" ${pickedSkills.has(skill.id) ? "checked" : ""}>
        ${esc(skill.name)}</label>`).join("");
      return rows ? `<h4>${SKILL_TITLES[category]}</h4>${rows}` : "";
    }).join("");
    // Source generation is built into the API; only PDF compilation needs the
    // Typst executable. Browser-local content cannot be sent to either route.
    const canSource = sampleModeOn() || !state.browserLocal;
    const canPdf = sampleModeOn() || (!state.browserLocal && state.typstAvailable);

    view.innerHTML = `
      <div class="builder-grid">
        <div>
          <div class="resume-picker">
            <select id="resume-select" aria-label="Resume">${options}</select>
            <button class="button" data-action="new-resume">+ New</button>
            <button class="button danger" data-action="delete-resume">Delete</button>
          </div>
          <div class="builder-settings">
            <label>Name<input id="rs-name" value="${esc(resume.name)}"></label>
            <label>Variant<select id="rs-variant">
              ${["mlsys", "swe", "quant"].map((v) => `<option value="${v}" ${v === resume.variant ? "selected" : ""}>${v}</option>`).join("")}
            </select></label>
            <label class="full">Target job<select id="rs-job">${jobOptions}</select></label>
            <label>Availability line<input id="rs-availability" value="${esc(resume.availability_line)}" placeholder="Available for Spring 2027 co-op"></label>
            <label>Education note<input id="rs-eduemph" value="${esc(resume.education_note)}" placeholder="GPA: 3.8/4.0"></label>
          </div>
          <div class="builder-actions">
            <button class="button primary" data-action="save-resume"><svg aria-hidden="true"><use href="#icon-check"/></svg>Save selection</button>
            <button class="button" data-action="check-resume">Run checks</button>
            <button class="button" data-action="download-typ" ${canSource ? "" : "disabled title='Sign in to export browser-local resumes'"}><svg aria-hidden="true"><use href="#icon-download"/></svg>.typ source</button>
            <button class="button" data-action="open-pdf" ${canPdf ? "" : `disabled title='${state.browserLocal ? "Sign in to export browser-local resumes" : "Install Typst to export PDF"}'`}>Open PDF</button>
          </div>
          ${entryPicks || '<p class="empty">The content bank is empty — add entries first.</p>'}
        </div>
        <aside class="panel">
          <h2>Fit &amp; checks</h2>
          <div id="coverage" class="coverage">${coverageHtml()}</div>
          <div id="lint" class="lint">${lintHtml()}</div>
          <h3>Skills on this resume</h3>
          <div class="skills-pick">${skillPicks || '<p class="empty">No skills in the bank.</p>'}</div>
        </aside>
      </div>`;
  }

  function coverageHtml() {
    const resume = currentResume();
    if (!resume || !resume.target_job_id) {
      return '<p class="empty">Link a target job to see JD keyword coverage.</p>';
    }
    if (!state.coverage || state.coverage.job_id !== resume.target_job_id) {
      return '<p class="empty">Save, then run checks to compute coverage.</p>';
    }
    const { matched, have_not_shown, missing } = state.coverage;
    const item = (cls, label, hint) =>
      `<li class="${cls}"><span class="dot"></span><span>${esc(label)}${hint ? ` <span class="hint">${esc(hint)}</span>` : ""}</span></li>`;
    return `
      <h3>JD coverage</h3>
      <ul>
        ${matched.map((s) => item("matched", s.name, "on resume")).join("")}
        ${have_not_shown.map((s) => item("shown-gap", s.name, "in bank, not on resume — add & mirror the JD's wording")).join("")}
        ${missing.map((term) => item("missing", term, "JD asks; not in your bank — learn it or let it go, never fake it")).join("")}
      </ul>`;
  }

  function lintHtml() {
    if (!state.check) return "";
    const { problems, page_count } = state.check;
    const pageBadge = page_count
      ? `<span class="page-count ${page_count > 1 ? "bad" : "good"}">${page_count} page${page_count > 1 ? "s" : ""}</span>`
      : "";
    const rows = problems.map((p) => `<li class="${p.level}">${esc(p.message)}</li>`).join("");
    return `<h3>Checks</h3>${pageBadge}<ul>${rows || '<li class="info">All checks pass.</li>'}</ul>`;
  }

  /* ---------- builder actions ---------- */

  function collectSelection() {
    const picks = new Map();
    view.querySelectorAll("[data-pick-bullet]:checked").forEach((box) => {
      const entryId = Number(box.dataset.pickEntry);
      if (!picks.has(entryId)) picks.set(entryId, []);
      picks.get(entryId).push({ bullet_id: Number(box.dataset.pickBullet), text_override: null });
    });
    const skills = [...view.querySelectorAll("[data-pick-skill]:checked")]
      .map((box) => Number(box.dataset.pickSkill));
    return {
      entries: [...picks.entries()].map(([entry_id, bullets]) => ({ entry_id, bullets })),
      skills,
    };
  }

  async function saveResume() {
    const resume = currentResume();
    if (!resume) return;
    const jobValue = document.getElementById("rs-job").value;
    const updated = await api(`/resumes/${resume.id}`, {
      method: "PATCH",
      body: {
        name: document.getElementById("rs-name").value || resume.name,
        variant: document.getElementById("rs-variant").value,
        target_job_id: jobValue ? Number(jobValue) : null,
        availability_line: document.getElementById("rs-availability").value,
        education_note: document.getElementById("rs-eduemph").value,
        selection: collectSelection(),
      },
    });
    const index = state.resumes.findIndex((r) => r.id === resume.id);
    state.resumes[index] = updated;
    say("Resume saved");
    return updated;
  }

  async function checkResume() {
    const resume = await saveResume();
    if (!resume) return;
    state.check = sampleModeOn()
      ? {
          problems: [{ level: "info", message: "Sample mode: \"Open PDF\" shows a pre-rendered export of this sample resume. Turn Sample data off to run live page/lint checks on your own content." }],
          page_count: null,
        }
      : await api(`/resumes/${resume.id}/check`);
    if (resume.target_job_id) {
      state.coverage = await api(`/jobs/${resume.target_job_id}/coverage?resume_id=${resume.id}`);
    } else {
      state.coverage = null;
    }
    renderBuilder();
  }

  /* ---------- dialogs ---------- */

  function openDialog(id, values = {}, onSubmit) {
    const dialog = document.getElementById(id);
    const form = dialog.querySelector("form");
    form.reset();
    Object.entries(values).forEach(([key, value]) => {
      if (form.elements[key] !== undefined) form.elements[key].value = value ?? "";
    });
    form.onsubmit = async (event) => {
      event.preventDefault();
      try {
        await onSubmit(Object.fromEntries(new FormData(form).entries()));
        dialog.close();
        await refresh();
      } catch (error) {
        say(error.message);
      }
    };
    dialog.querySelectorAll("[data-action=close]").forEach((button) => {
      button.onclick = () => dialog.close();
    });
    dialog.showModal();
  }

  const parseTags = (raw) => raw.split(",").map((t) => t.trim()).filter(Boolean);

  /* ---------- event wiring ---------- */

  async function refresh() {
    await loadAll();
    render();
  }

  document.querySelector(".view-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button) return;
    state.tab = button.dataset.tab;
    render();
  });

  view.addEventListener("submit", async (event) => {
    const form = event.target;
    event.preventDefault();
    try {
      if (form.id === "profile-form") {
        const data = Object.fromEntries(new FormData(form).entries());
        const links = data.links.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
          const match = line.match(/^(.*?)\s*(\S+)$/);
          return { label: (match?.[1] || "").trim(), url: match?.[2] || line };
        });
        state.profile = await api("/profile", {
          method: "PUT",
          body: { full_name: data.full_name, email: data.email, phone: data.phone, location: data.location, links },
        });
        say("Profile saved");
      } else if (form.id === "skill-form") {
        const data = Object.fromEntries(new FormData(form).entries());
        await api("/skills", { method: "POST", body: data });
        await refresh();
      }
    } catch (error) {
      say(error.message);
    }
  });

  view.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const { action, id, kind, entry } = button.dataset;
    const numericId = Number(id);
    try {
      if (action === "add-entry") {
        openDialog("entry-dialog", { kind }, (data) => api("/entries", { method: "POST", body: data }));
      } else if (action === "edit-entry") {
        const record = state.entries.find((e) => e.id === numericId);
        openDialog("entry-dialog", record, (data) =>
          api(`/entries/${numericId}`, { method: "PATCH", body: data }));
      } else if (action === "delete-entry") {
        if (confirm("Delete this entry and all its bullets?")) {
          await api(`/entries/${numericId}`, { method: "DELETE" });
          await refresh();
        }
      } else if (action === "add-bullet") {
        const entryId = Number(entry);
        openDialog("bullet-dialog", {}, (data) =>
          api(`/entries/${entryId}/bullets`, {
            method: "POST",
            body: { ...data, tags: parseTags(data.tags || "") },
          }));
      } else if (action === "edit-bullet") {
        const parent = state.entries.find((e) => e.id === Number(entry));
        const record = parent.bullets.find((b) => b.id === numericId);
        openDialog("bullet-dialog", { ...record, tags: record.tags.join(", ") }, (data) =>
          api(`/bullets/${numericId}`, {
            method: "PATCH",
            body: { ...data, tags: parseTags(data.tags || "") },
          }));
      } else if (action === "delete-bullet") {
        await api(`/bullets/${numericId}`, { method: "DELETE" });
        await refresh();
      } else if (action === "delete-skill") {
        await api(`/skills/${numericId}`, { method: "DELETE" });
        await refresh();
      } else if (action === "add-job") {
        openDialog("job-dialog", {}, (data) => api("/jobs", { method: "POST", body: data }));
      } else if (action === "edit-job") {
        const record = state.jobs.find((j) => j.id === numericId);
        openDialog("job-dialog", record, (data) =>
          api(`/jobs/${numericId}`, { method: "PATCH", body: data }));
      } else if (action === "delete-job") {
        if (confirm("Delete this target posting?")) {
          await api(`/jobs/${numericId}`, { method: "DELETE" });
          await refresh();
        }
      } else if (action === "new-resume") {
        const name = prompt("Resume name (e.g. 'SWE — Summer 2027')");
        if (name) {
          const created = await api("/resumes", { method: "POST", body: { name } });
          state.resumeId = created.id;
          state.check = null;
          state.coverage = null;
          await refresh();
        }
      } else if (action === "delete-resume") {
        if (confirm("Delete this resume? The content bank is untouched.")) {
          await api(`/resumes/${state.resumeId}`, { method: "DELETE" });
          state.resumeId = null;
          state.check = null;
          state.coverage = null;
          await refresh();
        }
      } else if (action === "save-resume") {
        await saveResume();
      } else if (action === "check-resume") {
        await checkResume();
      } else if (action === "download-typ") {
        if (sampleModeOn()) {
          window.open(SAMPLE_TYP_URL, "_blank");
        } else {
          const resume = await saveResume();
          if (resume) window.open(`/api/resume/resumes/${resume.id}/source`, "_blank");
        }
      } else if (action === "open-pdf") {
        if (sampleModeOn()) {
          window.open(SAMPLE_PDF_URL, "_blank");
        } else {
          const resume = await saveResume();
          if (resume) window.open(`/api/resume/resumes/${resume.id}/pdf`, "_blank");
        }
      }
    } catch (error) {
      say(error.message);
    }
  });

  view.addEventListener("change", (event) => {
    if (event.target.id === "resume-select") {
      state.resumeId = Number(event.target.value);
      state.check = null;
      state.coverage = null;
      renderBuilder();
    }
  });

  refresh().catch((error) => {
    view.innerHTML = `<p class="empty">Failed to load: ${esc(error.message)}</p>`;
  });
})();
