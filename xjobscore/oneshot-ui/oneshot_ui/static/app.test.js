const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { opportunityTypes, relativeTime, postedPresentation } = require("./app.js");
const sampleData = require("./sample-data.js");
const { ROUTES, query } = require("./api-client.js");
const { opportunityTotal } = require("./nav-count.js");

test("visible filters map to PostgreSQL opportunity types", () => {
  assert.deepEqual(
    opportunityTypes(["spring_coop", "summer_internships", "research", "early_career"]),
    ["spring_internship", "spring_coop", "summer_internship", "student_researcher", "student_role"],
  );
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
      ["apply_url", "attributes", "company", "description", "id", "last_seen_at", "location", "posted_at", "title"],
    );
    assert.equal(job.attributes.version, 1);
    assert.ok(job.attributes.opportunity_type);
    assert.ok(["remote", "hybrid", "on_site", "unknown"].includes(job.attributes.workplace));
  }
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

test("frontend client builds current API routes", () => {
  assert.equal(ROUTES.addToApplications("job 1"), "/jobs/job%201/applications");
  assert.equal(ROUTES.application("app 1"), "/applications/app%201");
  assert.equal(query({ q: "python", opportunity_type: ["summer_internship", "student_role"] }), "?q=python&opportunity_type=summer_internship&opportunity_type=student_role");
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
  assert.match(html, /href=\"\/static\/application\/index\.html\"/);
  assert.match(html, /href=\"\/static\/source-coverage\/index\.html\"/);
  assert.match(html, /href=\"\/static\/app\.css\?v=opportunities-/);
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
  assert.match(styles, /\.filter\.active \{[^}]*background: var\(--green-soft\); color: var\(--green\)/);
  assert.match(styles, /\.discover-sort select \{[^}]*width: 110px; height: 42px[^}]*font-size: 12px/);
  assert.match(styles, /grid-template-areas: "controls \." "search \." "list detail"/);
});
