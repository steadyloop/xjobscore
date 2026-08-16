(function (root) {
  "use strict";

  const opportunityTotal = payload => Math.max(0, Number(payload?.total) || 0);

  if (typeof module !== "undefined" && module.exports) module.exports = { opportunityTotal };
  if (!root.document) return;

  async function refresh() {
    const badges = [...root.document.querySelectorAll("[data-opportunity-count]")];
    if (!badges.length) return;
    try {
      const total = opportunityTotal(await root.OneShotAPI.jobs.list({ limit: 1, offset: 0 }));
      badges.forEach(badge => { badge.textContent = total.toLocaleString(); });
    } catch (_) {
      badges.forEach(badge => { badge.textContent = "—"; });
    }
  }

  root.OneShotOpportunityCount = { refresh };
  refresh();
})(typeof window !== "undefined" ? window : globalThis);
