(function (root) {
  "use strict";

  const ROUTES = Object.freeze({
    health: "/health",
    jobs: "/jobs",
    addToApplications: id => `/jobs/${encodeURIComponent(id)}/applications`,
    applications: "/applications",
    application: id => `/applications/${encodeURIComponent(id)}`,
    externalApplications: "/applications/external",
    sourceCoverage: "/source-coverage",
  });

  const remoteTransport = (path, options = {}) => root.fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const transport = () => root.OneShotSampleData?.isEnabled?.()
    ? root.OneShotSampleData.request
    : remoteTransport;

  async function request(path, options = {}) {
    const response = await transport()(path, options);
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status})`);
    return payload;
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

  root.OneShotAPI = {
    health: () => request(ROUTES.health),
    jobs: {
      list: filters => request(`${ROUTES.jobs}${query(filters)}`),
      addToApplications: id => request(ROUTES.addToApplications(id), { method: "POST" }),
    },
    applications: {
      list: () => request(ROUTES.applications),
      get: id => request(ROUTES.application(id)),
      createExternal: body => request(ROUTES.externalApplications, { method: "POST", body: JSON.stringify(body) }),
      update: (id, body) => request(ROUTES.application(id), { method: "PATCH", body: JSON.stringify(body) }),
      remove: id => request(ROUTES.application(id), { method: "DELETE" }),
    },
    sources: { coverage: () => request(ROUTES.sourceCoverage) },
  };

  if (typeof module !== "undefined" && module.exports) module.exports = { ROUTES, query };
})(typeof window !== "undefined" ? window : globalThis);
