const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {
  DEGREE_FILTERS,
  degreeFields,
  detailSkillsMarkup,
  safeHref,
  skillDetails,
  sourceLabel,
  relativeTime,
  workplaceLabel,
  listFacts,
  deadlineLabel,
  detailMetaMarkup,
  openPostingLabel,
  detailHeadMarkup,
  detailPostedMarkup,
  initials,
  cardStatusLine,
} = require("./app.js");

test("V2 exposes independent degree-field filters", () => {
  assert.deepEqual(DEGREE_FILTERS, [
    { key: "computer_science", label: "CS" },
    { key: "mathematics", label: "Math" },
    { key: "bioengineering", label: "Bioengineering" },
  ]);
  assert.deepEqual(degreeFields(["computer_science", "unknown", "mathematics", "bioengineering"]), [
    "computer_science",
    "mathematics",
    "bioengineering",
  ]);
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(design, /Degree field mentioned/);
  assert.match(design, /data-degree-filter/);
  assert.match(design, /degree_field: degreeFields\(state\.degreeFilters\)/);
});

test("V2 renders canonical skill metadata and keeps legacy string compatibility", () => {
  assert.deepEqual(skillDetails({
    skills: [
      { id: "go", name: "Go", category: "language" },
      { id: "postgresql", name: "PostgreSQL", category: "stack" },
      { id: "go", name: "Duplicate", category: "language" },
      { id: "broken" },
      null,
    ],
    attributes: { skills: ["Ignored fallback"] },
  }), [
    { id: "go", name: "Go", category: "language" },
    { id: "postgresql", name: "PostgreSQL", category: "stack" },
  ]);
  assert.deepEqual(skillDetails({ attributes: { skills: ["Go", "Kubernetes"] } }), [
    { id: "Go", name: "Go", category: null },
    { id: "Kubernetes", name: "Kubernetes", category: null },
  ]);
});

test("V2 skill section escapes labels and stays hidden when empty", () => {
  const markup = detailSkillsMarkup({
    skills: [{ id: 'unsafe"', name: "<script>alert(1)</script>", category: "stack" }],
  });
  assert.match(markup, /<div class="section-kicker">Skills<\/div>/);
  assert.match(markup, /data-skill-id="unsafe&quot;"/);
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script>/);
  assert.equal(detailSkillsMarkup({ skills: [] }), "");
  assert.equal(detailSkillsMarkup({ attributes: { skills: [] } }), "");
});

test("V2 job card shows the requested skill labels", () => {
  const job = { skills: ["Go", "Kubernetes", "PostgreSQL"] };
  assert.deepEqual(skillDetails(job).map(skill => skill.name), ["Go", "Kubernetes", "PostgreSQL"]);
  assert.match(detailSkillsMarkup(job), />Go<.*>Kubernetes<.*>PostgreSQL</);
});

test("V2 formats public job metadata into reader-facing labels", () => {
  assert.equal(sourceLabel("greenhouse:example"), "Greenhouse");
  assert.equal(sourceLabel("smartrecruiters:example"), "SmartRecruiters");
  assert.equal(sourceLabel("custom_board:example"), "Custom Board");
  assert.equal(workplaceLabel("hybrid"), "Hybrid");
  assert.equal(workplaceLabel("unknown"), "");
});

test("V2 omits the type badge and at-a-glance summary", () => {
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.doesNotMatch(design, /Summer internship|At a glance|badgesMarkup|factsMarkup/);
  assert.doesNotMatch(styles, /detail-badge|fact-grid/);
});

test("V2 provenance protects links and handles verification failures", () => {
  assert.equal(safeHref("https://example.com/job"), "https://example.com/job");
  assert.equal(safeHref("javascript:alert(1)"), "");
  assert.equal(relativeTime("not-a-date"), "Verification unavailable");
});

test("V2 leads the detail with the posted date, not an always-true liveness chip", () => {
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.doesNotMatch(design, /detail-freshness/);
  assert.doesNotMatch(styles, /detail-freshness/);
  assert.doesNotMatch(design, /class="provenance"/);

  // Polling makes every listing active, so the chip and its green are gone everywhere.
  assert.doesNotMatch(design, /status-chip/);
  assert.doesNotMatch(styles, /status-chip/);
  assert.doesNotMatch(design, /Active · confirmed/);
  assert.doesNotMatch(design, /live-dot/);
  assert.doesNotMatch(styles, /live-dot/);

  assert.match(styles, /\.posted-when \{[^}]*var\(--ink\)/);
  assert.match(styles, /\.posted-checked \{[^}]*var\(--faint\)/);
  assert.match(design, /detailHeadMarkup\(job\)\}<h2>/);
  assert.match(design, /detailMetaMarkup\(job\)\}\$\{detailPostedMarkup\(job\)\}<div class="detail-actions"/);
});

test("V2 detail header carries the company and the location on one line, with no initials mark", () => {
  const markup = detailHeadMarkup({ company: "Manatal", location: "Bangkok, Thailand" });
  assert.match(markup, /class="detail-headline"/);
  assert.match(markup, /class="detail-company">Manatal</);
  assert.match(markup, /class="detail-fact detail-place">.*Bangkok, Thailand/);
  assert.match(markup, /#icon-pin/);
  assert.doesNotMatch(markup, /job-logo/);

  assert.match(detailHeadMarkup({ company: "Acme" }), /Location not listed/);

  const escaped = detailHeadMarkup({ company: "<b>", location: "<i>" });
  assert.match(escaped, /&lt;b&gt;/);
  assert.match(escaped, /&lt;i&gt;/);
  assert.doesNotMatch(escaped, /<b>|<i>/);
});

test("V2 detail meta row carries every fact the header does not, shown with scan icons, and formats the apply deadline", () => {
  assert.equal(deadlineLabel("2027-01-15"), "Apply by Jan 15, 2027");
  assert.equal(deadlineLabel(""), "");
  assert.equal(deadlineLabel("not-a-date"), "");

  const job = { location: "NYC", remote: "yes", attributes: { term: "Summer 2027", deadline_at: "2027-01-15" } };
  const markup = detailMetaMarkup(job);
  assert.match(markup, /class="detail-meta"/);
  ["Summer 2027", "Remote", "Apply by Jan 15, 2027"].forEach(label => assert.match(markup, new RegExp(label)));
  ["icon-calendar", "icon-briefcase", "icon-clock"].forEach(icon => assert.match(markup, new RegExp(`#${icon}`)));

  // Location moved up into the header, so it is the one fact the row must not repeat.
  assert.doesNotMatch(markup, /#icon-pin/);
  assert.doesNotMatch(markup, /NYC/);
  assert.match(detailHeadMarkup(job), /NYC/);
  listFacts(job).filter(fact => fact !== job.location).forEach(fact => assert.match(markup, new RegExp(fact.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))));

  // With nothing but a location, the row disappears rather than rendering empty.
  assert.equal(detailMetaMarkup({ location: "NYC", attributes: {} }), "");
  assert.equal(detailMetaMarkup({}), "");

  const escaped = detailMetaMarkup({ attributes: { term: "<b>" } });
  assert.match(escaped, /&lt;b&gt;/);
  assert.doesNotMatch(escaped, /<b>/);
});

test("V2 detail meta icons are defined in the shared icon sprite", () => {
  for (const page of ["../index.html", "./index.html"]) {
    const html = fs.readFileSync(require.resolve(page), "utf8");
    for (const icon of ["icon-pin", "icon-calendar", "icon-briefcase", "icon-clock"]) {
      assert.match(html, new RegExp(`id="${icon}"`));
    }
  }
});

test("V2 open-posting link states its source", () => {
  assert.equal(openPostingLabel({ source: "greenhouse:acme" }), "Open on Greenhouse");
  assert.equal(openPostingLabel({}), "Open posting");
  assert.equal(openPostingLabel({ source: "" }), "Open posting");
});

test("V2 posted row weights the date and trails the check only when last_seen_at is known", () => {
  const now = new Date("2026-08-18T12:00:00Z");
  const withCheck = detailPostedMarkup({ last_seen_at: "2026-08-18T07:00:00Z", posted_at: "2026-08-17T00:00:00Z" }, now);
  assert.match(withCheck, /class="posted-when">Posted yesterday</);
  assert.match(withCheck, /class="posted-checked">checked 5h ago</);

  // No check time means no trailing clause, and no dangling separator either.
  const withoutCheck = detailPostedMarkup({ posted_at: "2026-08-17T00:00:00Z" }, now);
  assert.match(withoutCheck, /class="posted-when">Posted yesterday</);
  assert.doesNotMatch(withoutCheck, /posted-checked|posted-sep/);

  // No posted_at falls back to when we first saw it.
  assert.match(detailPostedMarkup({ first_seen_at: "2026-08-17T00:00:00Z" }, now), /Added yesterday/);
});

test("V2 list card leads with company, drops the left accent, and states the posted date", () => {
  assert.equal(initials({ company: "SAP" }), "SA");
  assert.equal(initials({ company: "Jane Street" }), "JS");
  assert.equal(initials({ company: "" }), "?");
  assert.equal(initials({}), "?");

  const now = new Date("2026-08-18T12:00:00Z");
  assert.equal(
    cardStatusLine({ last_seen_at: "2026-08-18T07:00:00Z", posted_at: "2026-08-17T00:00:00Z" }, now),
    "Posted yesterday",
  );
  assert.equal(cardStatusLine({ posted_at: "2026-08-17T00:00:00Z" }, now), "Posted yesterday");
  assert.equal(cardStatusLine({}, now), "Date unavailable");

  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.match(design, /class="job-company-line"><div class="job-company">\$\{esc\(job\.company\)\}<\/div>.*<\/div><h3>\$\{esc\(job\.title\)\}<\/h3>/);
  assert.doesNotMatch(design, /class="job-title-line"/);
  assert.doesNotMatch(styles, /\.job-title-line/);
  assert.doesNotMatch(styles, /inset 3px 0 0/);
  assert.match(styles, /\.opportunity\.selected \{[^}]*border-color: var\(--green\)/);
});

test("V2 is the main Opportunities design and exposes accessible disclosure", () => {
  const main = fs.readFileSync(require.resolve("../index.html"), "utf8");
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const shell = fs.readFileSync(require.resolve("./index.html"), "utf8");
  assert.match(main, /opportunities-v2\/styles\.css\?v=compact-card-/);
  assert.match(main, /opportunities-v2\/app\.js\?v=compact-card-/);
  assert.doesNotMatch(design, /Design preview|View current Opportunities/);
  assert.match(design, /aria-controls=/);
  assert.match(design, /aria-expanded=/);
  assert.match(design, /Show full description/);
  assert.match(design, /Show less/);
  assert.match(shell, /opportunities-v2\/styles\.css\?v=compact-card-/);
  assert.match(shell, /opportunities-v2\/app\.js\?v=compact-card-/);
});
