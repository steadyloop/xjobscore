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
  async function transport(path, options = {}, privateData = false) {
    if (root.OneShotSampleData?.isEnabled?.()) {
      return root.OneShotSampleData.request(path, options);
    }
    if (privateData && root.OneShotVisitorData && await root.OneShotVisitorData.isLocal()) {
      return root.OneShotVisitorData.request(path, options);
    }
    return remoteTransport(path, options);
  }

  const LOGIN_PATH = "/login/";

  // A 401 means the session expired or was never established. Send the browser
  // to the login page rather than surfacing "Not authenticated" as a toast that
  // disappears after three seconds and explains nothing.
  //
  // The returned promise never settles, on purpose: navigation is already under
  // way, and resolving or rejecting here would let the caller's catch block
  // paint an error over a page that is about to be replaced.
  function redirectToLogin() {
    if (root.location && root.location.pathname !== LOGIN_PATH) {
      root.location.assign(LOGIN_PATH);
    }
    return new Promise(() => {});
  }

  async function request(path, options = {}, privateData = false) {
    const response = await transport(path, options, privateData);
    // Sample-data mode short-circuits before fetch, so it never yields a 401.
    if (response.status === 401) return redirectToLogin();
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
      list: async filters => {
        const values = { ...(filters || {}) };
        if (!root.OneShotSampleData?.isEnabled?.() && root.OneShotVisitorData
            && await root.OneShotVisitorData.isLocal()) {
          values.exclude_job_id = root.OneShotVisitorData.applicationJobIds();
        }
        return request(`${ROUTES.jobs}${query(values)}`);
      },
      addToApplications: (id, job) => {
        if (!root.OneShotSampleData?.isEnabled?.()) root.OneShotVisitorData?.rememberJob(job);
        return request(ROUTES.addToApplications(id), { method: "POST" }, true);
      },
    },
    applications: {
      list: () => request(ROUTES.applications, {}, true),
      get: id => request(ROUTES.application(id), {}, true),
      createExternal: body => request(ROUTES.externalApplications, { method: "POST", body: JSON.stringify(body) }, true),
      update: (id, body) => request(ROUTES.application(id), { method: "PATCH", body: JSON.stringify(body) }, true),
      remove: id => request(ROUTES.application(id), { method: "DELETE" }, true),
    },
    sources: { coverage: () => request(ROUTES.sourceCoverage) },
  };

  if (typeof module !== "undefined" && module.exports) module.exports = { ROUTES, query };
})(typeof window !== "undefined" ? window : globalThis);
