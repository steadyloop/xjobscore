const test = require("node:test");
const assert = require("node:assert/strict");
const {
  groupForStage,
  dueState,
  buildMetrics,
  filterApplications,
  sortApplications,
} = require("./app.js");

const now = new Date("2026-08-08T12:00:00Z");
const rows = [
  { id: "saved", company: "Beta", title: "Engineer", stage: "considering", next_due_at: "2026-08-07T12:00:00Z" },
  { id: "interview", company: "Alpha", title: "Researcher", stage: "interview", next_due_at: "2026-08-09T12:00:00Z", submitted_at: "2026-08-01" },
  { id: "offer", company: "Delta", title: "Intern", stage: "offer", next_due_at: "2026-08-18T12:00:00Z" },
  { id: "closed", company: "Gamma", title: "Developer", stage: "rejected", next_due_at: null },
];

test("detailed stages collapse into five active workflow groups", () => {
  assert.equal(groupForStage("considering"), "saved");
  assert.equal(groupForStage("assessment"), "applied");
  assert.equal(groupForStage("screen"), "interviewing");
  assert.equal(groupForStage("rejected"), "archive");
});

test("due presentation distinguishes attention from upcoming work", () => {
  assert.deepEqual(dueState("2026-08-07T12:00:00Z", now).key, "overdue");
  assert.deepEqual(dueState("2026-08-09T12:00:00Z", now).label, "Due today");
  assert.deepEqual(dueState(null, now).key, "none");
});

test("summary metrics keep archived outcomes out of active work", () => {
  assert.deepEqual(buildMetrics(rows, now), { active: 3, overdue: 1, interviews: 1, offers: 1, archive: 1 });
});

test("status, search, and quick filters compose", () => {
  assert.deepEqual(filterApplications(rows, { statusView: "active", query: "alpha" }, now).map(row => row.id), ["interview"]);
  assert.deepEqual(filterApplications(rows, { statusView: "active", quickFilter: "attention" }, now).map(row => row.id), ["saved", "interview"]);
  assert.deepEqual(filterApplications(rows, { statusView: "archive" }, now).map(row => row.id), ["closed"]);
});

test("application-specific sorts are deterministic and non-mutating", () => {
  assert.deepEqual(sortApplications(rows, "company").map(row => row.company), ["Alpha", "Beta", "Delta", "Gamma"]);
  assert.deepEqual(rows.map(row => row.id), ["saved", "interview", "offer", "closed"]);
});
