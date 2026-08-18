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
} = require("./app.js");
const sampleData = require("../sample-data.js");

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

test("V2 sample Halcyon card shows the requested skill labels", () => {
  const job = sampleData.createState().jobs.find(value => value.id === "sample-software");
  assert.deepEqual(skillDetails(job).map(skill => skill.name), ["Go", "Kubernetes", "PostgreSQL"]);
  assert.match(detailSkillsMarkup(job), />Go<.*>Kubernetes<.*>PostgreSQL</);
});

test("V2 formats public job metadata into reader-facing labels", () => {
  assert.equal(sourceLabel("greenhouse:example"), "Greenhouse");
  assert.equal(sourceLabel("smartrecruiters:example"), "SmartRecruiters");
  assert.equal(sourceLabel("custom_board:example"), "Custom Board");
  assert.equal(workplaceLabel("on_site"), "On-site");
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

test("V2 uses the V1 detail footer", () => {
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.match(design, /class="detail-freshness"/);
  assert.match(design, /Last confirmed active/);
  assert.doesNotMatch(design, /class="provenance"/);
  assert.match(styles, /\.detail-freshness \{ border-top: 1px solid var\(--line\); padding-top: 13px; color: var\(--muted\); font-size: 9px; \}/);
});

test("V2 is the main Opportunities design and exposes accessible disclosure", () => {
  const main = fs.readFileSync(require.resolve("../index.html"), "utf8");
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const shell = fs.readFileSync(require.resolve("./index.html"), "utf8");
  assert.match(main, /opportunities-v2\/styles\.css\?v=degree-fields-/);
  assert.match(main, /opportunities-v2\/app\.js\?v=degree-fields-/);
  assert.doesNotMatch(design, /Design preview|View current Opportunities/);
  assert.match(design, /aria-controls=/);
  assert.match(design, /aria-expanded=/);
  assert.match(design, /Show full description/);
  assert.match(design, /Show less/);
  assert.match(shell, /opportunities-v2\/styles\.css\?v=degree-fields-/);
  assert.match(shell, /opportunities-v2\/app\.js\?v=degree-fields-/);
});
