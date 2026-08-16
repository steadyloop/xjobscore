/* Small fetch wrapper: JSON in/out, normalized errors, degraded detection. */

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, options = {}) {
  const init = { headers: {}, ...options };
  if (init.body !== undefined && typeof init.body !== "string") {
    init.method = init.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail ?? response.statusText);
  }
  return payload;
}

export function isDegraded(error) {
  return error instanceof ApiError && error.status === 503;
}
