import assert from "node:assert/strict";
import test from "node:test";
import { activityEntries, enrichmentSummary, filterActivity, nextCheck } from "./activity-model.mjs";

const run = {
  id: 1, job_type: "feed", state: "complete", identity: "workday:acme",
  started_at: "2026-10-06T18:00:00Z", finished_at: "2026-10-06T18:01:00Z",
  detail: { stage_ms: { fetch: 56000, gold: 4000 }, gold: {
    postings: 123, duration_ms: 4000,
    counts: { skills: { postings: 123 }, summaries: { postings: 123 } },
    stage_ms: { job_type: 300, skills: 1100, degrees: 400, summaries: 1700, focus: 500 },
    errors: {},
  } },
};

test("enrichment displays a distinct total and its own duration, excluding fetch time", () => {
  const result = enrichmentSummary(run);
  assert.equal(result.postings, 123);
  assert.equal(result.duration, "4.0s");
  assert.equal(result.timings.find((step) => step.name === "summaries").elapsed, "1.7s");
});

test("legacy overlapping counts remain unknown; missing timings never become zero", () => {
  const legacy = structuredClone(run);
  delete legacy.detail.gold.postings;
  delete legacy.detail.gold.stage_ms;
  delete legacy.detail.gold.duration_ms;
  const result = enrichmentSummary(legacy);
  assert.equal(result.postings, null);
  assert.equal(result.duration, "4.0s");
  assert.ok(result.timings.every((step) => step.elapsed === "Not recorded"));
});

test("recorded zero and skipped steps are distinct from missing telemetry", () => {
  const empty = structuredClone(run);
  empty.detail.gold.postings = 0;
  empty.detail.gold.stage_ms = { skills: 0 };
  const result = enrichmentSummary(empty);
  assert.equal(result.postings, 0);
  assert.equal(result.timings[1].elapsed, "0.0s");
  assert.equal(result.timings[0].elapsed, "Not run");
});

test("step errors are available separately and mark partial completion", () => {
  const partial = structuredClone(run);
  partial.state = "degraded";
  partial.detail.gold.errors.summaries = "TimeoutError: database timeout";
  const result = enrichmentSummary(partial);
  assert.equal(result.outcome, "completed with errors");
  assert.deepEqual(result.errors, [{ label: "Summaries", detail: "TimeoutError: database timeout" }]);
  assert.equal(result.timings.find((step) => step.name === "summaries").failed, true);
});

test("next check uses the actual reported deadline and handles running/offline workers", () => {
  const now = Date.parse("2026-10-06T18:00:00Z");
  const worker = { alive: true, accepting: true, next_check_at: "2026-10-06T18:00:42Z" };
  assert.equal(nextCheck(worker, now).text, "Next check in 42s");
  assert.equal(nextCheck({ ...worker, checking: true }, now).text, "Checking now");
  assert.match(nextCheck({ ...worker, alive: false }, now).text, /offline/);
  assert.match(nextCheck(null, now).text, /unavailable/);
  assert.equal(nextCheck(worker, now + 60000).text, "Next check due");
});

test("unified activity retains all three sources in chronological order", () => {
  const entries = activityEntries({
    boot: "boot-1",
    logs: [{ seq: 1, ts: "2026-10-06T17:59:00Z", level: "WARNING", name: "xjobscore.run", msg: "rate limited", exc: "traceback" }],
    polls: [{ polled_at: "2026-10-06T18:00:00Z", source: "lever", key: "acme", status: "ok", new_postings: 2 }],
    runs: [run],
  });
  assert.deepEqual(entries.map((entry) => entry.process), ["run", "fetch", "enrich"]);
  assert.equal(entries[0].level, "WARN");
  assert.equal(entries[0].detail, "traceback");
  assert.match(entries[2].msg, /123 postings enriched 4.0s/);
});

test("routine polls are hidden by default, while errors survive all filters", () => {
  const entries = activityEntries({ polls: [
    { polled_at: "2026-10-06T18:00:00Z", source: "lever", key: "quiet", status: "unchanged", new_postings: 0 },
    { polled_at: "2026-10-06T18:00:01Z", source: "lever", key: "broken", status: "error", new_postings: 0, status_code: 429 },
  ] });
  assert.equal(filterActivity(entries).hidden, 1);
  assert.equal(filterActivity(entries).issues, 1);
  assert.equal(filterActivity(entries, { allPolls: true }).entries.length, 2);
  assert.equal(filterActivity(entries, { view: "issues", query: "429", process: "fetch" }).entries.length, 1);
  assert.equal(filterActivity(entries, { process: "enrich" }).entries.length, 0);
});

test("buffer restarts do not reuse log identities", () => {
  const logs = [{ seq: 1, ts: "2026-10-06T18:00:00Z", level: "INFO", name: "system", msg: "started" }];
  assert.notEqual(activityEntries({ logs, boot: "one" })[0].id, activityEntries({ logs, boot: "two" })[0].id);
});

function failedAttempt({ key = "acme", second = 0, id = 10, error = "NetError: HTTP 429", elapsed = 1 } = {}) {
  const start = new Date(Date.parse("2026-10-06T18:00:00Z") + second * 1000);
  const finish = new Date(start.getTime() + elapsed * 1000);
  return {
    poll: { source: "workable", key, status: "error", new_postings: 0, status_code: 429, polled_at: start.toISOString() },
    run: { id, job_type: "feed", identity: `workable:${key}`, state: "failed", error,
      started_at: new Date(start.getTime() + 3).toISOString(), finished_at: finish.toISOString(), detail: { error_stage: "fetch" } },
    log: { seq: id, name: "xjobscore.run", level: "ERROR", ts: finish.toISOString(),
      msg: `harvest failed source=workable:${key}`, exc: `Traceback\n${error}` },
  };
}

test("a failed poll, run, and traceback become one issue with full details", () => {
  const failure = failedAttempt();
  const entries = activityEntries({ polls: [failure.poll], runs: [failure.run], logs: [failure.log] });
  assert.equal(entries.length, 1);
  assert.equal(filterActivity(entries).issues, 1);
  assert.equal(entries[0].process, "fetch");
  assert.equal(entries[0].msg, "workable:acme fetch failed HTTP 429 1.0s");
  assert.equal(entries[0].detail, "Traceback\nNetError: HTTP 429");
  assert.equal(entries[0].ts, failure.run.finished_at);
});

test("separate attempts for the same feed remain separate issues", () => {
  const failures = [failedAttempt(), failedAttempt({ second: 2, id: 11 })];
  const entries = activityEntries({ polls: failures.map((item) => item.poll),
    runs: failures.map((item) => item.run), logs: failures.map((item) => item.log) });
  assert.equal(entries.length, 2);
  assert.equal(filterActivity(entries).issues, 2);
  assert.ok(entries.every((entry) => entry.detail === "Traceback\nNetError: HTTP 429"));
});

test("shared cooldown refusals explain the deferral instead of implying a remote request", () => {
  const failure = failedAttempt({ error: "NetError: Workable cooldown: 64699s remaining" });
  const entries = activityEntries({ polls: [failure.poll], runs: [failure.run], logs: [failure.log] });
  assert.equal(entries.length, 1);
  assert.equal(entries[0].msg, "workable:acme deferred (Workable cooldown 17h 58m remaining) 1.0s");
  assert.doesNotMatch(entries[0].msg, /HTTP 429/);
  assert.match(entries[0].detail, /64699s remaining/);
  const standalone = activityEntries({ logs: [failure.log] });
  assert.match(standalone[0].msg, /deferred \(Workable cooldown/);
  assert.equal(standalone[0].process, "fetch");
});

test("partial telemetry still merges matching reports and retains the recorded error", () => {
  const failure = failedAttempt();
  for (const data of [
    { polls: [failure.poll], logs: [failure.log] },
    { runs: [failure.run], logs: [failure.log] },
    { polls: [failure.poll], runs: [failure.run] },
  ]) {
    const entries = activityEntries(data);
    assert.equal(entries.length, 1);
    assert.match(entries[0].detail, /NetError: HTTP 429/);
  }
});

test("a long failed fetch matches its run interval; unrelated errors remain visible", () => {
  const failure = failedAttempt({ elapsed: 90 });
  const unrelated = { ...failure.log, seq: 99, msg: "could not finish system run source=workable:acme" };
  const entries = activityEntries({ polls: [failure.poll], runs: [failure.run], logs: [failure.log, unrelated] });
  assert.equal(entries.length, 2);
  assert.ok(entries.some((entry) => /fetch failed HTTP 429 1m 30s/.test(entry.msg)));
  assert.ok(entries.some((entry) => entry.msg === unrelated.msg));
  // An old poll without the run's interval cannot safely explain a later error.
  assert.equal(activityEntries({ polls: [failure.poll], logs: [failure.log] }).length, 2);
});

test("fetch and enrichment remain distinct stages for an enriched feed", () => {
  const failure = failedAttempt({ key: "acme" });
  const enriched = { ...run, identity: "workable:acme" };
  const entries = activityEntries({ polls: [failure.poll], runs: [enriched], logs: [failure.log] });
  assert.equal(entries.length, 2);
  assert.deepEqual(entries.map((entry) => entry.process), ["fetch", "enrich"]);
});
