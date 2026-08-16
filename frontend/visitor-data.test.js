const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function browserContext() {
  const values = new Map();
  const fetches = [];
  const context = {
    console,
    Date,
    JSON,
    Map,
    Promise,
    RegExp,
    Response,
    Set,
    URL,
    URLSearchParams,
    location: { pathname: "/application/", assign() {}, reload() {} },
    localStorage: {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: key => values.delete(key),
    },
    document: {
      addEventListener() {},
      querySelector() { return null; },
    },
    fetch: async (url, options = {}) => {
      fetches.push({ url: String(url), options });
      if (url === "/api/auth/me") {
        return new Response(JSON.stringify({ detail: "Not signed in" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (String(url).startsWith("/api/jobs")) {
        return new Response(JSON.stringify({ jobs: [], total: 0, limit: 100, offset: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  for (const file of ["sample-data.js", "api-client.js"]) {
    vm.runInContext(fs.readFileSync(require.resolve(`./${file}`), "utf8"), context, { filename: file });
  }
  return { context, fetches, values };
}

test("signed-out application CRUD persists locally and never calls the private API", async () => {
  const { context, fetches, values } = browserContext();

  assert.deepEqual((await context.OneShotAPI.applications.list()).applications, []);
  const created = await context.OneShotAPI.applications.createExternal({
    company: "Browser Co",
    title: "Software Intern",
    url: "https://example.test/job",
    location: "Remote",
    note: "Local only",
  });
  const updated = await context.OneShotAPI.applications.update(created.id, {
    stage: "interview",
    next_action: "Prepare",
  });

  assert.equal(updated.stage, "interview");
  assert.equal((await context.OneShotAPI.applications.list()).applications.length, 1);
  assert.ok(values.has("oneshot.visitor-data.state.v1"));
  assert.deepEqual(fetches.map(call => call.url), ["/api/auth/me"]);

  await context.OneShotAPI.applications.remove(created.id);
  assert.deepEqual((await context.OneShotAPI.applications.list()).applications, []);
});

test("sample fixtures and visitor state remain independent", async () => {
  const { context, values } = browserContext();
  await context.OneShotAPI.applications.createExternal({
    company: "Visitor Co", title: "Intern", url: "https://example.test/visitor",
  });

  values.set("oneshot.sample-data.enabled.application", "1");
  const sampleRows = (await context.OneShotAPI.applications.list()).applications;
  assert.ok(sampleRows.length > 1);
  assert.equal(sampleRows.some(row => row.company === "Visitor Co"), false);

  values.set("oneshot.sample-data.enabled.application", "0");
  const visitorRows = (await context.OneShotAPI.applications.list()).applications;
  assert.deepEqual(visitorRows.map(row => row.company), ["Visitor Co"]);
});

test("signed-out resume CRUD and checks use the browser-local workspace", async () => {
  const { context, fetches, values } = browserContext();
  assert.equal(await context.OneShotVisitorData.isLocal(), true);

  await context.OneShotVisitorData.request("/resume/profile", {
    method: "PUT",
    body: JSON.stringify({ full_name: "Local Person", email: "local@example.test" }),
  });
  const entry = await (await context.OneShotVisitorData.request("/resume/entries", {
    method: "POST",
    body: JSON.stringify({ kind: "project", org: "Local project" }),
  })).json();
  const bullet = await (await context.OneShotVisitorData.request(`/resume/entries/${entry.id}/bullets`, {
    method: "POST",
    body: JSON.stringify({ text: "Built 3 browser-local workflows" }),
  })).json();
  const resume = await (await context.OneShotVisitorData.request("/resume/resumes", {
    method: "POST",
    body: JSON.stringify({
      name: "Local resume",
      availability_line: "Available now",
      selection: { entries: [{ entry_id: entry.id, bullets: [{ bullet_id: bullet.id }] }], skills: [] },
    }),
  })).json();
  const check = await (await context.OneShotVisitorData.request(`/resume/resumes/${resume.id}/check`)).json();

  assert.equal(check.problems.some(problem => problem.level === "error"), false);
  assert.ok(values.has("oneshot.visitor-data.state.v1"));
  assert.deepEqual(fetches.map(call => call.url), ["/api/auth/me"]);
});

test("guest opportunity queries exclude locally tracked job ids", async () => {
  const { context, fetches } = browserContext();
  const job = {
    id: 41,
    company: "Example Labs",
    title: "SWE Intern",
    location: "Remote",
    apply_url: "https://example.test/apply",
  };
  await context.OneShotAPI.jobs.addToApplications(job.id, job);
  await context.OneShotAPI.jobs.list({ limit: 100, offset: 0 });

  assert.ok(fetches.at(-1).url.includes("exclude_job_id=41"));
});
