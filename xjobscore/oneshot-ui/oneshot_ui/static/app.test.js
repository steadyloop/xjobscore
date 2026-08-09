const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {
  slug,
  normalizeFilters,
  matchesFilters,
  queryForFilters,
  sortJobs,
  duePresentation,
  relativeTime,
  sourceBoardLabel,
  postedPresentation,
  dateOnlyPresentation,
  dateInputValue,
} = require("./app.js");
const sampleData = require("./sample-data.js");
const { ROUTES, stubTransport } = require("./api-client.js");

test("filters retain known values and map to job types", () => {
  assert.deepEqual(
    normalizeFilters(["research", "research", "early_career", "bogus"]),
    ["research", "early_career"],
  );
  assert.equal(slug("Student Researcher"), "student_researcher");
  assert.equal(matchesFilters({opportunity_type:"Spring Internship"}, ["spring_coop"]), true);
  assert.equal(matchesFilters({opportunity_type:"Student Researcher"}, ["research"]), true);
  assert.equal(matchesFilters({opportunity_type:"Fall Co-op"}, ["research"]), false);
});

test("API query expands visible filters", () => {
  assert.equal(
    queryForFilters(["spring_coop","summer_internships","research","early_career"]),
    "opportunity_type=spring_internship&opportunity_type=spring_coop&opportunity_type=summer_internship&opportunity_type=student_researcher&opportunity_type=student_role",
  );
});

test("client sort uses objective job fields without a fit score", () => {
  const rows = [
    {id:1,title:"Zebra",company:"Alpha",posted_at:"2026-08-01"},
    {id:2,title:"Alpha",company:"Zebra",posted_at:"2026-08-08"},
  ];
  assert.deepEqual(sortJobs(rows,"fresh").map(x=>x.id),[2,1]);
  assert.deepEqual(sortJobs(rows,"company").map(x=>x.id),[1,2]);
  assert.deepEqual(sortJobs(rows,"title").map(x=>x.id),[2,1]);
  assert.deepEqual(rows.map(x=>x.id),[1,2]);
});

test("posting and application dates have short readable labels", () => {
  const now = new Date("2026-08-08T12:00:00Z");
  assert.equal(postedPresentation("2026-08-08T02:00:00Z", now), "Posted today");
  assert.equal(postedPresentation("2026-08-07T02:00:00Z", now), "Posted yesterday");
  assert.equal(postedPresentation(null, now), "Posted date unavailable");
  assert.deepEqual(duePresentation("2026-08-09T18:00:00Z", now), {label:"Due tomorrow",tone:"soon"});
  assert.equal(dateOnlyPresentation("2026-08-07T02:44:17.505Z"), "Aug 7, 2026");
  assert.equal(dateInputValue("2026-08-07T02:44:17.505Z"), "2026-08-07");
});

test("source coverage helpers remain limited to the separate coverage view", () => {
  const now = new Date("2026-08-08T12:00:00Z");
  assert.equal(relativeTime("2026-08-08T11:56:00Z", now), "4m ago");
  assert.equal(sourceBoardLabel("ATS", 1), "1 board");
  assert.equal(sourceBoardLabel("Web Board", 1), "");
});

test("sample mode supplies four selectable schema-shaped jobs", async () => {
  const state = sampleData.createState();
  const listing = await (await sampleData.handle("/jobs?limit=100", {}, state)).json();
  assert.equal(listing.total, 4);
  for (const row of listing.jobs) {
    const response = await sampleData.handle(`/jobs/${row.id}`, {}, state);
    const detail = await response.json();
    assert.equal(response.status, 200);
    assert.equal(detail.id, row.id);
    assert.ok(detail.company);
    assert.ok(detail.title);
    assert.ok(detail.description);
    assert.ok(detail.attributes.skills.length);
    assert.equal("score" in detail, false);
    assert.equal("source_evidence" in detail, false);
    assert.equal("observations" in detail, false);
  }
});

test("Add to Applications creates Considering once", async () => {
  const state = sampleData.createState();
  const firstResponse = await sampleData.handle("/jobs/sample-software/applications", {
    method: "POST",
    body: JSON.stringify({stage:"considering"}),
  }, state);
  const first = await firstResponse.json();
  assert.equal(firstResponse.status, 201);
  assert.equal(first.stage, "considering");
  assert.equal(first.job_id, "sample-software");

  const listing = await (await sampleData.handle("/jobs", {}, state)).json();
  assert.equal(listing.total, 3);
  assert.equal(listing.jobs.some(job => job.id === "sample-software"), false);

  const duplicate = await (await sampleData.handle("/jobs/sample-software/applications", {
    method: "POST",
    body: JSON.stringify({stage:"considering"}),
  }, state)).json();
  assert.equal(duplicate.id, first.id);
  assert.equal(duplicate.already_exists, true);
});

test("sample application editor updates manual fields, protects discovered fields, and supports delete", async () => {
  const state = sampleData.createState();
  const externalResponse = await sampleData.handle("/applications/external", {
    method: "POST",
    body: JSON.stringify({company:"Example Co",title:"Platform Intern",url:"https://example.com/apply",note:"Follow up"}),
  }, state);
  const external = await externalResponse.json();
  assert.equal(external.external, true);
  assert.equal(external.stage, "considering");

  const updateResponse = await sampleData.handle(`/applications/${external.id}`, {
    method: "PATCH",
    body: JSON.stringify({company:"Edited Co",title:"Edited Role",apply_url_snapshot:"https://example.com/edited",stage:"interview",next_action:"Technical screen",next_due_at:"2026-08-20",note:"Prep examples"}),
  }, state);
  const updated = await updateResponse.json();
  assert.equal(updated.stage, "interview");
  assert.equal(updated.company, "Edited Co");
  assert.equal(updated.next_action, "Technical screen");
  assert.equal(updated.note, "Prep examples");
  assert.equal(updated.events.at(-1).to_stage, "interview");

  const discovered = state.applications.find(application => !application.external);
  const protectedResponse = await sampleData.handle(`/applications/${discovered.id}`, {
    method: "PATCH",
    body: JSON.stringify({company:"Wrong Company",title:"Wrong Role",apply_url_snapshot:"https://example.com/wrong",next_action:"Review"}),
  }, state);
  const protectedApplication = await protectedResponse.json();
  assert.notEqual(protectedApplication.company, "Wrong Company");
  assert.equal(protectedApplication.next_action, "Review");

  const deleteResponse = await sampleData.handle(`/applications/${external.id}`, {method:"DELETE"}, state);
  assert.equal(deleteResponse.status, 204);
  assert.equal(state.applications.some(application => application.id === external.id), false);
});

test("frontend API exposes stable routes and safe empty stubs", async () => {
  assert.equal(ROUTES.job("job 1"), "/jobs/job%201");
  assert.equal(ROUTES.addToApplications("job 1"), "/jobs/job%201/applications");
  assert.equal(ROUTES.application("app 1"), "/applications/app%201");
  const jobs = await (await stubTransport("/jobs?q=python")).json();
  const applications = await (await stubTransport("/applications")).json();
  assert.deepEqual(jobs, {jobs:[],total:0});
  assert.deepEqual(applications, {applications:[],total:0});
  const unwired = await stubTransport("/jobs/abc/applications", {method:"POST"});
  assert.equal(unwired.status, 501);
});

test("Discover contains no fit-score or unified-source UI", () => {
  const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.doesNotMatch(source, /Why it fits you|Score explanation|Unified source evidence/);
  assert.doesNotMatch(source, /data-action=["'](?:queue|start-prep|classify|record-application)["']/);
  assert.match(source, /data-action=\"add-to-applications\"/);
  assert.doesNotMatch(source, /Submission confirmation|Résumé <b>|Source <b>External/);
  assert.match(source, /id=\"application-next-action\"/);
  assert.match(source, /data-action=\"application-save\"/);
});
