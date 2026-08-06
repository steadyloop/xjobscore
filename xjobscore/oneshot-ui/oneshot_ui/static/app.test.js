const test = require("node:test");
const assert = require("node:assert/strict");
const { slug, normalizeFilters, matchesFilters, queryForFilters, sortJobs, classificationNeeded, duePresentation, statusClass, applicationReady, sourceSelectionState } = require("./app.js");

test("filters retain only known values and remove duplicates", () => {
  assert.deepEqual(
    normalizeFilters(["all", "spring_internship", "spring_internship", "bogus"]),
    ["all", "spring_internship"],
  );
  assert.equal(slug("Student Researcher"), "student_researcher");
});
test("opportunity filters support six types and needs classification", () => {
  assert.equal(matchesFilters({opportunity_type:"Spring Internship"}, ["spring_internship"]), true);
  assert.equal(matchesFilters({opportunity_type:"Spring Co-op"}, ["spring_internship"]), false);
  assert.equal(matchesFilters({title:"Unknown"}, ["needs_classification"]), true);
  assert.equal(classificationNeeded({opportunity_type:"Fall Co-op"}), false);
});
test("API query repeats type fields and includes classification review", () => {
  assert.equal(
    queryForFilters(["all","summer_internship","fall_coop","needs_classification"]),
    "opportunity_type=summer_internship&opportunity_type=fall_coop&needs_classification=true",
  );
});
test("client sort works on score and deadline without mutating input", () => {
  const rows=[{id:1,score:5,deadline:"2026-09-20"},{id:2,score:9,deadline:"2026-08-10"}];
  assert.deepEqual(sortJobs(rows,"score").map(x=>x.id),[2,1]);
  assert.deepEqual(sortJobs(rows,"deadline").map(x=>x.id),[2,1]);
  assert.deepEqual(rows.map(x=>x.id),[1,2]);
});
test("pipeline due cues distinguish overdue, today, and upcoming work", () => {
  const now = new Date("2026-08-04T12:00:00Z");
  assert.deepEqual(duePresentation("2026-08-03T12:00:00Z", now), {label:"1d overdue",tone:"overdue"});
  assert.deepEqual(duePresentation("2026-08-04T18:00:00Z", now), {label:"Due today",tone:"soon"});
  assert.deepEqual(duePresentation("2026-08-09T12:00:00Z", now), {label:"Due in 5d",tone:"upcoming"});
  assert.equal(duePresentation(null, now), null);
});
test("ineligible eligibility never receives the good status treatment", () => {
  assert.equal(statusClass("ineligible"), "blocked");
  assert.equal(statusClass("eligible"), "good");
  assert.equal(statusClass("review"), "needs");
});
test("only open eligible jobs can enter the application workflow", () => {
  assert.equal(applicationReady({eligibility:"eligible",status:"open"}), true);
  assert.equal(applicationReady({eligibility:"review",status:"open"}), false);
  assert.equal(applicationReady({eligibility:"eligible",status:"conflicting"}), false);
});
test("ATS source toggles expose all, mixed, and none states", () => {
  assert.deepEqual(sourceSelectionState(4, 4), {state:"all", aria:"true", next:false});
  assert.deepEqual(sourceSelectionState(2, 4), {state:"some", aria:"mixed", next:true});
  assert.deepEqual(sourceSelectionState(0, 4), {state:"none", aria:"false", next:true});
});
