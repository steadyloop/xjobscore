/* Standalone feed page: the shared inspection panel, full width. */

import { createInspector } from "/static/shared/inspect.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Feeds");

const ref = new URLSearchParams(location.search).get("ref");
const root = document.getElementById("inspector-root");

if (!ref) {
  root.textContent = "Missing ?ref=";
} else {
  // Top-level await keeps the load event behind the first full render.
  await createInspector(root, {
    standalone: true,
    onMute: () => {
      location.href = "/feeds";
    },
    onDrop: () => {
      location.href = "/feeds";
    },
  }).show(ref);
}
