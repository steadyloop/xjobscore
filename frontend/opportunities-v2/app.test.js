const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {
  safeHref,
  sourceLabel,
  relativeTime,
  deadlineLabel,
  detailFacts,
  eligibilityLabel,
  opportunityLabel,
  workplaceLabel,
} = require("./app.js");

test("V2 formats public job metadata into reader-facing labels", () => {
  assert.equal(sourceLabel("greenhouse:example"), "Greenhouse");
  assert.equal(sourceLabel("smartrecruiters:example"), "SmartRecruiters");
  assert.equal(sourceLabel("custom_board:example"), "Custom Board");
  assert.equal(opportunityLabel("student_researcher"), "Student research");
  assert.equal(eligibilityLabel("review"), "Review eligibility");
  assert.equal(workplaceLabel("on_site"), "On-site");
});

test("V2 uses selective placeholders for missing decision-critical facts", () => {
  assert.deepEqual(detailFacts({ location: null, attributes: {} }), [
    { label: "Location", value: "Not listed", placeholder: true },
    { label: "Deadline", value: "Not listed", placeholder: true },
    { label: "Eligibility", value: "Not evaluated", tone: "unknown" },
  ]);
  assert.deepEqual(detailFacts({
    location: "New York, NY",
    attributes: { deadline_at: "2027-01-12", eligibility: "eligible", term: "Summer 2027", workplace: "remote" },
  }).map(fact => [fact.label, fact.value]), [
    ["Location", "New York, NY"],
    ["Deadline", "Jan 12, 2027"],
    ["Eligibility", "Eligible"],
    ["Term", "Summer 2027"],
    ["Work setting", "Remote"],
  ]);
  assert.equal(deadlineLabel("not-a-date"), "Deadline needs review");
});

test("V2 provenance protects links and handles verification failures", () => {
  assert.equal(safeHref("https://example.com/job"), "https://example.com/job");
  assert.equal(safeHref("javascript:alert(1)"), "");
  assert.equal(relativeTime("not-a-date"), "Verification unavailable");
});

test("V2 is isolated, linked for comparison, and exposes accessible disclosure", () => {
  const current = fs.readFileSync(require.resolve("../app.js"), "utf8");
  const preview = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const shell = fs.readFileSync(require.resolve("./index.html"), "utf8");
  assert.match(current, /href="\/opportunities-v2\/" target="_blank"/);
  assert.match(preview, /href="\/" target="_blank"/);
  assert.match(preview, /aria-controls=/);
  assert.match(preview, /aria-expanded=/);
  assert.match(preview, /Show full description/);
  assert.match(preview, /Show less/);
  assert.match(shell, /opportunities-v2\/styles\.css\?v=/);
  assert.match(shell, /opportunities-v2\/app\.js\?v=/);
});
