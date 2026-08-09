const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { enrichJob, filterJobs, sortJobs, summarize, daysAgo } = require("./app.js");
const sampleData = require("../sample-data.js");

const now = new Date("2026-08-08T12:00:00Z");
const base = [
  enrichJob({ id: "sample-research", company: "Northwind", title: "Researcher", location_text: "Remote, US", opportunity_type: "Student Researcher", posted_at: "2026-08-08T02:00:00Z", attributes: { workplace: "Remote", term: "Spring", skills: ["Python", "JAX"] } }, now),
  enrichJob({ id: "sample-software", company: "Halcyon", title: "Backend Intern", location_text: "San Diego", opportunity_type: "Internship", posted_at: "2026-08-02T02:00:00Z", attributes: { workplace: "Hybrid", term: "Summer", skills: ["Go"] } }, now),
  enrichJob({ id: "sample-embedded", company: "Cobalt", title: "Embedded Co-op", location_text: "Austin", opportunity_type: "Fall Co-op", posted_at: "2026-07-20T02:00:00Z", attributes: { workplace: "On-site", term: "Fall", skills: ["C++"] } }, now),
];

test("sample jobs are enriched with explainable discovery evidence", () => {
  assert.equal(base[0].workplace, "Remote");
  assert.ok(base[0].reasons.length >= 3);
  assert.ok(base[0].required.includes("Python"));
  assert.match(base[0].source, /Employer careers/);
});

test("search includes skills and filters compose with scopes", () => {
  assert.deepEqual(filterJobs(base, { scope: "all", query: "jax", facets: {} }, {}, now).map(job => job.id), ["sample-research"]);
  assert.deepEqual(filterJobs(base, { scope: "remote", facets: {} }, {}, now).map(job => job.id), ["sample-research"]);
  assert.deepEqual(filterJobs(base, { scope: "all", facets: { workplaces: ["Hybrid"] } }, {}, now).map(job => job.id), ["sample-software"]);
  assert.deepEqual(filterJobs(base, { scope: "saved", facets: {} }, { saved: ["sample-embedded"] }, now).map(job => job.id), ["sample-embedded"]);
});

test("hidden roles stay out of active views and receive their own count", () => {
  const local = { hidden: ["sample-software"], saved: ["sample-research"] };
  assert.deepEqual(filterJobs(base, { scope: "all", facets: {} }, local, now).map(job => job.id), ["sample-research", "sample-embedded"]);
  assert.deepEqual(summarize(base, local), { all: 2, recommended: 1, remote: 1, saved: 1, hidden: 1 });
});

test("discovery sorts operate on the full supplied result set", () => {
  assert.deepEqual(sortJobs(base, "company").map(job => job.company), ["Cobalt", "Halcyon", "Northwind"]);
  assert.equal(sortJobs(base, "recommended")[0].id, "sample-research");
  assert.deepEqual(base.map(job => job.id), ["sample-research", "sample-software", "sample-embedded"]);
});

test("freshness is deterministic", () => {
  assert.equal(daysAgo("2026-08-08T02:00:00Z", now), 0);
  assert.equal(daysAgo("2026-08-02T02:00:00Z", now), 6);
});

test("the sample page never opts into the live API transport", () => {
  const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.doesNotMatch(source, /OneShotAPI\.useRemote/);
  assert.match(source, /OneShotSampleData\?\.isEnabled/);
});

test("sample discovery can combine available and already-tracked jobs", async () => {
  const sampleState = sampleData.createState();
  const available = await (await sampleData.handle("/jobs?limit=100", {}, sampleState)).json();
  const applications = await (await sampleData.handle("/applications", {}, sampleState)).json();
  const trackedIds = applications.applications.map(application => application.job_id).filter(Boolean);
  const tracked = await Promise.all(trackedIds.map(async id => await (await sampleData.handle(`/jobs/${id}`, {}, sampleState)).json()));
  assert.equal(available.jobs.length, 4);
  assert.equal(new Set([...available.jobs, ...tracked].map(job => job.id)).size, 6);
});
