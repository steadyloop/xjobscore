const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { opportunityTypes, relativeTime, postedPresentation } = require("./app.js");
const sampleData = require("./sample-data.js");
const { ROUTES, query } = require("./api-client.js");
const { opportunityTotal } = require("./nav-count.js");

test("visible filters map to PostgreSQL opportunity types", () => {
  assert.deepEqual(
    opportunityTypes(["internships", "early_career", "working_student", "research", "review"]),
    ["summer_internship", "student_role", "spring_coop", "student_researcher", "fellowship", "review"],
  );
  assert.deepEqual(opportunityTypes([]), [], "no tab active defers to the API's default scope");
});

test("opportunity filters expose their pressed state", () => {
  const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(source, /type="button" data-filter="\$\{filter\.key\}" aria-pressed="\$\{active\}"/);
});

test("job freshness labels stay concise", () => {
  const now = new Date("2026-08-08T12:00:00Z");
  assert.equal(postedPresentation("2026-08-08T02:00:00Z", now), "Posted today");
  assert.equal(postedPresentation("2026-08-07T02:00:00Z", now), "Posted yesterday");
  assert.equal(postedPresentation(null, now), "Posted date unavailable");
  assert.equal(relativeTime("2026-08-08T11:56:00Z", now), "4m ago");
});

test("sample jobs match the live list contract", async () => {
  const state = sampleData.createState();
  const listing = await (await sampleData.handle("/jobs?limit=100", {}, state)).json();
  assert.equal(listing.total, 4);
  for (const job of listing.jobs) {
    assert.deepEqual(
      Object.keys(job).sort(),
      ["apply_url", "attributes", "company", "description", "first_seen_at", "id", "last_seen_at", "location", "posted_at", "source", "source_url", "status", "title"],
    );
    assert.equal(job.attributes.version, 1);
    assert.ok(job.attributes.opportunity_type);
    assert.ok(["remote", "hybrid", "on_site", "unknown"].includes(job.attributes.workplace));
  }
});

test("sample jobs honor limit and offset like the live API", async () => {
  const state = sampleData.createState();
  const first = await (await sampleData.handle("/jobs?limit=2&offset=0", {}, state)).json();
  assert.equal(first.jobs.length, 2);
  assert.equal(first.limit, 2);
  assert.equal(first.offset, 0);
  const second = await (await sampleData.handle("/jobs?limit=2&offset=2", {}, state)).json();
  assert.equal(second.jobs.length, 2);
  assert.equal(second.total, first.total);
  assert.deepEqual(
    second.jobs.map(job => job.id).filter(id => first.jobs.some(job => job.id === id)),
    [],
  );
});

test("Opportunities pages past the first hundred results instead of dropping them", () => {
  const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(source, /data-action="load-more"/);
  assert.match(source, /const offset = append \? \(state\.jobs \|\| \[\]\)\.length : 0;/);
  assert.match(source, /state\.total = payload\.total;/);
});

test("sample Add to Applications creates one Considering record", async () => {
  const state = sampleData.createState();
  const firstResponse = await sampleData.handle("/jobs/sample-software/applications", { method: "POST" }, state);
  const first = await firstResponse.json();
  assert.equal(firstResponse.status, 201);
  assert.equal(first.stage, "considering");
  assert.equal(first.job_id, "sample-software");
  assert.equal((await (await sampleData.handle("/jobs", {}, state)).json()).total, 3);

  const duplicate = await (await sampleData.handle("/jobs/sample-software/applications", { method: "POST" }, state)).json();
  assert.equal(duplicate.id, first.id);
  assert.equal(duplicate.already_exists, true);
});

test("sample application mutations match the editor", async () => {
  const state = sampleData.createState();
  const external = await (await sampleData.handle("/applications/external", {
    method: "POST",
    body: JSON.stringify({ company: "Example Co", title: "Platform Intern", url: "https://example.com/apply", note: "Follow up" }),
  }, state)).json();
  assert.equal(external.external, true);

  const updated = await (await sampleData.handle(`/applications/${external.id}`, {
    method: "PATCH",
    body: JSON.stringify({ company: "Edited Co", stage: "interview", next_action: "Technical screen" }),
  }, state)).json();
  assert.equal(updated.company, "Edited Co");
  assert.equal(updated.stage, "interview");
  assert.equal(updated.events.at(-1).to_stage, "interview");

  const discovered = state.applications.find(application => !application.external);
  const protectedApplication = await (await sampleData.handle(`/applications/${discovered.id}`, {
    method: "PATCH",
    body: JSON.stringify({ company: "Wrong Company", next_action: "Review" }),
  }, state)).json();
  assert.notEqual(protectedApplication.company, "Wrong Company");
  assert.equal(protectedApplication.next_action, "Review");

  assert.equal((await sampleData.handle(`/applications/${external.id}`, { method: "DELETE" }, state)).status, 204);
});

test("visitor workspace starts blank and stays separate from sample fixtures", async () => {
  const visitor = sampleData.createVisitorState();
  const sample = sampleData.createState();

  assert.deepEqual(visitor.applications, []);
  assert.deepEqual(visitor.resume.entries, []);
  assert.ok(sample.applications.length > 0);
  assert.ok(sample.resume.entries.length > 0);

  const application = await (await sampleData.handle("/applications/external", {
    method: "POST",
    body: JSON.stringify({ company: "Local Co", title: "Intern", url: "https://local.example/job" }),
  }, visitor)).json();
  assert.match(application.id, /^visitor-app-/);
  assert.equal((await (await sampleData.handle("/applications", {}, visitor)).json()).applications.length, 1);
  assert.equal(sample.applications.some(row => row.company === "Local Co"), false);

  await sampleData.handle("/resume/profile", {
    method: "PUT",
    body: JSON.stringify({ full_name: "Local Visitor", email: "local@example.test" }),
  }, visitor);
  const resume = await (await sampleData.handle("/resume/resumes", {
    method: "POST",
    body: JSON.stringify({ name: "Browser resume" }),
  }, visitor)).json();
  const check = await (await sampleData.handle(`/resume/resumes/${resume.id}/check`, {}, visitor)).json();
  assert.equal((await (await sampleData.handle("/resume/profile", {}, visitor)).json()).full_name, "Local Visitor");
  assert.ok(check.problems.some(problem => problem.message === "No bullets selected."));
  assert.equal(sample.resume.profile.full_name, "Wren Adeyemi");
});

test("frontend client builds current API routes", () => {
  assert.equal(ROUTES.addToApplications("job 1"), "/jobs/job%201/applications");
  assert.equal(ROUTES.application("app 1"), "/applications/app%201");
  assert.equal(query({ q: "python", opportunity_type: ["summer_internship", "student_role"] }), "?q=python&opportunity_type=summer_internship&opportunity_type=student_role");
  assert.equal(query({ exclude_job_id: [41, 42] }), "?exclude_job_id=41&exclude_job_id=42");
  const source = fs.readFileSync(require.resolve("./api-client.js"), "utf8");
  assert.match(source, /OneShotVisitorData\.isLocal\(\)/);
  assert.match(source, /applications:[\s\S]*privateData|applications:[\s\S]*, true\)/);
});

test("navigation count uses the unfiltered opportunity total on every page", () => {
  assert.equal(opportunityTotal({ total: 428 }), 428);
  assert.equal(opportunityTotal({ total: null }), 0);
  const helper = fs.readFileSync(require.resolve("./nav-count.js"), "utf8");
  assert.match(helper, /jobs\.list\(\{ limit: 1, offset: 0 \}\)/);
  for (const page of ["./index.html", "./application/index.html", "./source-coverage/index.html"]) {
    const html = fs.readFileSync(require.resolve(page), "utf8");
    assert.match(html, /data-opportunity-count/);
    assert.match(html, /nav-count\.js/);
  }
});

test("Opportunities exposes its action and workspace navigation", () => {
  const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
  assert.match(source, /data-action=\"add-to-applications\"/);
  assert.match(source, /class=\"discover-search-surface\".*class=\"discover-sort\".*class=\"discover-controls-pane\"/);
  assert.doesNotMatch(source, /<span>Sort<\/span>/);
  // Canonical page URLs, not the /static/ mount path the shells also answer on.
  assert.match(html, /href=\"\/application\/\"/);
  assert.match(html, /href=\"\/source-coverage\/\"/);
  assert.doesNotMatch(html, /href=\"\/static\/[a-z-]+\/index\.html\"/);
  assert.match(html, /href=\"\/static\/app\.css\?v=opportunities-/);
  assert.match(html, /href=\"\/static\/navigation\.css\?v=stable-nav-/);
});

test("the sample switch is per page, guest-only, and absent only from Source coverage", () => {
  assert.deepEqual(sampleData.PAGES, { "/": "opportunities", "/opportunities-v2/": "opportunities-v2", "/application/": "application", "/resume/": "resume" });

  const shellFor = page => fs.readFileSync(require.resolve(page), "utf8");
  const sidebarOf = shell => shell.slice(shell.indexOf("<aside"), shell.indexOf("</aside>"));

  // Applications and Resume write the button into a static header.
  for (const page of ["./application/index.html", "./resume/index.html"]) {
    const shell = shellFor(page);
    assert.match(shell, /sample-data\.js/);
    // In the page it applies to, not in the navigation menu — the sidebar is
    // for moving between pages, and this changes what one page is showing.
    assert.doesNotMatch(sidebarOf(shell), /sample-toggle/);
    const header = shell.slice(shell.indexOf('<header class="workspace-header"'), shell.indexOf("</header>"));
    assert.match(header, /class="workspace-actions"/);
    assert.match(header, /data-action="sample-toggle"/);
  }

  // Opportunities renders its whole header into #view, so its shell loads the
  // fixture script but carries no button — app.js emits it, into the loaded
  // page and into the error state, and re-syncs it on every paint because
  // sample-data.js's own sync runs long before the markup exists.
  const opportunities = shellFor("./index.html");
  assert.match(opportunities, /sample-data\.js/);
  assert.doesNotMatch(sidebarOf(opportunities), /sample-toggle/);
  const page = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(page, /data-action="sample-toggle"/);
  assert.match(page, /class="discover-page-head">[\s\S]*?\$\{sampleToggle\(\)\}/);
  assert.match(page, /OneShotSampleData\?\.sync\?\.\(\)/);
  // One write to #view, inside paint(). A second would be a paint that skipped
  // the re-sync, leaving a switch reading "Off" on a page serving fixtures.
  assert.equal(page.match(/view\.innerHTML/g).length, 1);

  // Source coverage is a read-only report on the harvest, so a demo mode there
  // would only hide what the page exists to show.
  assert.doesNotMatch(shellFor("./source-coverage/index.html"), /sample-toggle/);
  assert.doesNotMatch(shellFor("./source-coverage/index.html"), /sample-data\.js/);

  // Hidden until nav-count.js confirms nobody is signed in — on both header
  // shapes, and on the rendered one especially, since it would otherwise paint
  // straight into a signed-in view.
  const navigation = fs.readFileSync(require.resolve("./navigation.css"), "utf8");
  assert.match(navigation, /\.workspace-header \.sample-toggle,\s*\.discover-page-head \.sample-toggle \{\s*display: none/);
  assert.match(navigation, /body\.is-guest \.workspace-header \.sample-toggle,\s*body\.is-guest \.discover-page-head \.sample-toggle \{\s*display: inline-flex/);
  assert.doesNotMatch(navigation, /\.sidebar \.sample-toggle/);
  const helper = fs.readFileSync(require.resolve("./nav-count.js"), "utf8");
  assert.match(helper, /classList\.add\("is-guest"\)/);
  // A session clears any switch left on from browsing as a guest.
  assert.match(helper, /OneShotSampleData\?\.disableAll\?\.\(\)/);
});

test("shared navigation keeps active states from changing geometry", () => {
  const navigation = fs.readFileSync(require.resolve("./navigation.css"), "utf8");
  assert.match(navigation, /min-height: 38px/);
  assert.match(navigation, /font: 400 14px\/20px/);
  assert.match(navigation, /width: 24px/);
  assert.match(navigation, /a\.active \{[\s\S]*?order: initial/);
  assert.doesNotMatch(navigation, /a\.active \{\s*order: -1/);
});

test("Opportunities exposes only its canonical layout", () => {
  const pages = [
    fs.readFileSync(require.resolve("./index.html"), "utf8"),
    fs.readFileSync(require.resolve("./application/index.html"), "utf8"),
    fs.readFileSync(require.resolve("./source-coverage/index.html"), "utf8"),
  ];
  const styles = fs.readFileSync(require.resolve("./app.css"), "utf8");

  for (const page of pages) {
    assert.doesNotMatch(page, /opportunities-(?:full|split)|Opportunities \((?:Left|Full|Split)\)/);
  }
  assert.doesNotMatch(styles, /data-opportunities-layout/);
  assert.match(styles, /\.discover-search-surface \{[^}]*display: flex/);
  assert.match(styles, /\.discover-search-surface \{[^}]*gap: 12px/);
  assert.match(styles, /\.filter\.active \{[^}]*border-color: var\(--nav\); background: var\(--nav\); color: white/);
  assert.match(styles, /\.discover-sort select \{[^}]*width: 110px; height: 42px[^}]*font-size: 12px/);
  assert.match(styles, /grid-template-areas: "controls \." "search \." "list detail"/);
});
