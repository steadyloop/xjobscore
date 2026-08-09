/* Frontend data contract. The default transport is intentionally stubbed until
   the database API is connected. Sample mode swaps in the browser-local fixture. */
(function (root) {
  "use strict";

  const ROUTES = Object.freeze({
    health: "/health",
    jobs: "/jobs",
    job: id => `/jobs/${encodeURIComponent(id)}`,
    addToApplications: id => `/jobs/${encodeURIComponent(id)}/applications`,
    applications: "/applications",
    application: id => `/applications/${encodeURIComponent(id)}`,
    applicationEvents: id => `/applications/${encodeURIComponent(id)}/events`,
    externalApplications: "/applications/external",
    sourceCoverage: "/source-coverage",
  });

  const response = (payload, status = 200) => new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });

  async function stubTransport(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const pathname = new URL(path, "http://oneshot.local").pathname;
    if (method === "GET" && pathname === ROUTES.health) return response({ ok: true, mode: "stub" });
    if (method === "GET" && pathname === ROUTES.jobs) return response({ jobs: [], total: 0 });
    if (method === "GET" && pathname === ROUTES.applications) return response({ applications: [], total: 0 });
    if (method === "GET" && pathname === ROUTES.sourceCoverage) {
      return response({ sources: [], source_count: 0, total_coverage: 0, total_added_today: 0 });
    }
    return response({ detail: `Endpoint stubbed until the database is connected: ${method} ${pathname}` }, 501);
  }

  let connectedTransport = stubTransport;
  const activeTransport = () => root.OneShotSampleData?.isEnabled?.()
    ? root.OneShotSampleData.request
    : connectedTransport;

  async function request(path, options = {}) {
    const result = await activeTransport()(path, options);
    if (!result.ok) {
      const detail = await result.text().catch(() => "");
      throw new Error(detail || `Request failed (${result.status})`);
    }
    if (result.status === 204) return null;
    return result.json().catch(() => null);
  }

  const query = values => {
    const params = new URLSearchParams();
    Object.entries(values || {}).forEach(([key, value]) => {
      if (value == null || value === "") return;
      (Array.isArray(value) ? value : [value]).forEach(item => params.append(key, item));
    });
    const encoded = params.toString();
    return encoded ? `?${encoded}` : "";
  };

  const client = {
    ROUTES,
    request,
    connect(transport) { connectedTransport = transport || stubTransport; },
    useRemote() {
      connectedTransport = (path, options = {}) => root.fetch(`/api${path}`, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
    },
    health: () => request(ROUTES.health),
    jobs: {
      list: filters => request(`${ROUTES.jobs}${query(filters)}`),
      get: id => request(ROUTES.job(id)),
      addToApplications: id => request(ROUTES.addToApplications(id), {
        method: "POST",
        body: JSON.stringify({ stage: "considering" }),
      }),
    },
    applications: {
      list: filters => request(`${ROUTES.applications}${query(filters)}`),
      get: id => request(ROUTES.application(id)),
      createExternal: body => request(ROUTES.externalApplications, { method: "POST", body: JSON.stringify(body) }),
      update: (id, body) => request(ROUTES.application(id), { method: "PATCH", body: JSON.stringify(body) }),
      remove: id => request(ROUTES.application(id), { method: "DELETE" }),
      addEvent: (id, body) => request(ROUTES.applicationEvents(id), { method: "POST", body: JSON.stringify(body) }),
    },
    sources: { coverage: () => request(ROUTES.sourceCoverage) },
  };

  if (typeof module !== "undefined" && module.exports) module.exports = { ROUTES, stubTransport };
  root.OneShotAPI = client;
})(typeof window !== "undefined" ? window : globalThis);
