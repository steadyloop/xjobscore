/* Shared top navigation: links, health chips, theme toggle. */

import { api } from "/static/shared/api.js";
import { el } from "/static/shared/format.js";

const LINKS = [
  ["Tokens", "/tokens"],
  ["Feeds", "/"],
  ["Scheduler", "/scheduler"],
  ["Postings", "/postings"],
  ["Review", "/review"],
  ["Query", "/query"],
  ["Fields", "/fields"],
  ["Regex", "/regex"],
];

const THEME_KEY = "inspector-theme";

function applyTheme(theme) {
  if (theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  } else {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem(THEME_KEY);
  }
}

function nextTheme() {
  const current = document.documentElement.dataset.theme;
  const osDark = matchMedia("(prefers-color-scheme: dark)").matches;
  if (!current) return osDark ? "light" : "dark";
  return current === "dark" ? "light" : "dark";
}

export function renderNav(active) {
  const header = document.getElementById("topnav");
  header.classList.add("topnav");

  const pgChip = el(
    "span",
    { class: "health-chip", "data-state": "down", title: "PostgreSQL policy store" },
    el("span", { class: "dot" }),
    el("span", { class: "label" }, "Postgres")
  );

  header.replaceChildren(
    el("div", { class: "brand" }, "xJobscore"),
    el(
      "nav",
      {},
      LINKS.map(([label, href]) =>
        el("a", { href, "aria-current": label === active ? "page" : null }, label)
      )
    ),
    el("div", { class: "spacer" }),
    el("div", { class: "health-chips" }, pgChip),
    el(
      "button",
      {
        class: "theme-toggle",
        type: "button",
        title: "Toggle color theme",
        onclick: () => applyTheme(nextTheme()),
      },
      "Theme"
    )
  );

  refreshHealth(pgChip);
  setInterval(() => refreshHealth(pgChip), 30_000);
}

async function refreshHealth(pgChip) {
  try {
    const health = await api("/api/health");
    pgChip.dataset.state = health.postgres?.connected ? "up" : "down";
    pgChip.title = health.postgres?.error
      ? `PostgreSQL: ${health.postgres.error}`
      : "PostgreSQL policy store";
    document.dispatchEvent(new CustomEvent("inspector-health", { detail: health }));
  } catch {
    pgChip.dataset.state = "down";
  }
}
