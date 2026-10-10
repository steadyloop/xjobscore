/* The feed inspection panel: header + Harvest / Parsed / Network tab triad.
   Used embedded on the Feeds page (master-detail) and standalone on /feed. */

import { api } from "/static/shared/api.js";
import { dropFeed, muteFeed } from "/static/shared/feed-policy.js";
import { setFeedEnabled } from "/static/shared/feed-policy.js";
import {
  el,
  formatBytes,
  formatMs,
  relativeTime,
  statusChip,
  statusLabel,
} from "/static/shared/format.js";
import { latencyChart, sparkline, stageBar } from "/static/shared/spark.js";

const PAGE_SIZE = 200;

const RESOLUTION_LABEL = {
  resolved: (r) => `resolved: ${r.company_name} (#${r.company_id})`,
  fallback: (r) => `fallback: feed company ${r.company_name ?? ""} (#${r.company_id})`,
  would_mint: (r) => `would create company: ${r.company_raw ?? "token slug"}`,
  would_park: () => "would park in company_new",
};

// Each archetype names a request-count multiplier — the tooltip says what a
// run pays for it, so a slow Elapsed reads as methodology, not mystery.
const ARCHETYPE_HINTS = {
  single: "one request per run",
  paginated: "walks multiple list pages per run",
  detail: "one extra request per item",
  "batch-detail": "detail rows fetched in batches",
  "link-discovery": "item URLs scraped from HTML, then fetched one by one",
  discovery: "extra request to locate the feed",
};
const EXPENSIVE_ARCHETYPES = new Set(["detail", "link-discovery"]);

export function createFeedDetail(container, { standalone = false, onClose, onMute, onDrop } = {}) {
  const state = {
    ref: null,
    feed: null,
    result: null,
    items: [],
    hasMore: false,
    parsed: null,
    network: null,
    history: [],
    parsedOnly: false,
  };

  /* Skeleton ------------------------------------------------------------- */

  const els = {};
  els.title = el("h1", {}, "…");
  els.description = el("div", { class: "muted" });
  els.badges = el("span", {});
  els.controls = el("span", {
    style: "display:inline-flex;align-items:center;gap:12px",
  });
  els.fetchBtn = el("button", { class: "btn primary", type: "button" }, "Fetch");
  els.muteBtn = el("button", { class: "btn danger", type: "button" }, "Mute");
  els.dropBtn = el("button", { class: "btn danger", type: "button" }, "Drop");
  els.stats = el("div", { class: "stat-row" });

  const headerRight = [els.badges, els.controls, els.fetchBtn, els.muteBtn, els.dropBtn];
  if (!standalone) {
    const closeBtn = el(
      "button",
      { class: "btn small", type: "button", title: "Close panel (Esc)" },
      "✕"
    );
    closeBtn.addEventListener("click", () => onClose?.());
    headerRight.push(closeBtn);
  }

  els.countItems = el("span", { class: "count" });
  els.countNetwork = el("span", { class: "count" });
  const tabButtons = [
    el("button", { class: "tab", role: "tab", "aria-selected": "true" }, "Items", els.countItems),
    el("button", { class: "tab", role: "tab", "aria-selected": "false" }, "Network", els.countNetwork),
  ];

  // One item list drives two synchronized panes: raw harvest payload on the
  // left, the parsed canonicals for the same item on the right.
  els.parseStats = el("div", { class: "stat-row", style: "padding:0 0 12px 0" });
  els.parsedOnlyToggle = el("input", { type: "checkbox" });
  els.parsedOnlyLabel = el(
    "label",
    {
      class: "muted",
      style: "display:none;align-items:center;gap:6px;padding:0 0 12px 0;font-size:13px",
      title: "Only items the parser produced a canonical for — limited to the parsed window loaded above (first 500 items)",
    },
    els.parsedOnlyToggle,
    "Show parsed only"
  );
  els.parsedOnlyToggle.addEventListener("change", () => {
    state.parsedOnly = els.parsedOnlyToggle.checked;
    loadResult();
  });
  els.itemList = el("div", { class: "card item-list item-list-compact" });
  els.harvestPane = el(
    "div",
    {},
    el("div", { class: "empty-state" }, "Fetch this feed to inspect its payload.")
  );
  els.parsedPane = el("div", {}, el("div", { class: "empty-state" }, "Select an item."));
  els.harvestPager = el("div", { class: "pager", style: "margin-top:8px" });
  const itemsPanel = el(
    "div",
    { class: "body", role: "tabpanel" },
    els.parseStats,
    els.parsedOnlyLabel,
    els.itemList,
    el(
      "div",
      { class: "split-2" },
      el("div", {}, el("h2", { style: "margin:0 0 8px" }, "Raw"), els.harvestPane),
      el("div", {}, el("h2", { style: "margin:0 0 8px" }, "Parsed"), els.parsedPane)
    ),
    els.harvestPager
  );

  els.networkStats = el("div", { class: "stat-row", style: "padding:0 0 12px 0" });
  els.stageHost = el("div", { style: "margin:0 0 16px" });
  els.latencyHost = el("div", {});
  els.requestDetail = el("div", { class: "request-detail card", style: "margin-top:12px", hidden: "" });
  els.historySpark = el("div", {});
  els.historyBody = el("tbody", {});
  const networkPanel = el(
    "div",
    { class: "body", role: "tabpanel", hidden: "" },
    els.networkStats,
    els.stageHost,
    els.latencyHost,
    els.requestDetail,
    el("h2", { style: "margin:20px 0 8px" }, "Run history"),
    els.historySpark,
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "data" },
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", {}, "Finished"), el("th", {}, "Trigger"), el("th", {}, "Status"),
            el("th", { class: "num" }, "HTTP"), el("th", { class: "num" }, "Items"),
            el("th", { class: "num" }, "Elapsed"), el("th", { class: "num" }, "Bytes"),
            el("th", { class: "num" }, "Retries")
          )
        ),
        els.historyBody
      )
    )
  );

  const panels = [itemsPanel, networkPanel];
  tabButtons.forEach((tab, index) => {
    tab.addEventListener("click", () => {
      tabButtons.forEach((other) => other.setAttribute("aria-selected", String(other === tab)));
      panels.forEach((panel, i) => (panel.hidden = i !== index));
    });
  });

  container.replaceChildren(
    el(
      "section",
      { class: "card" },
      el(
        "header",
        {},
        el("div", {}, els.title, els.description),
        el("div", { class: "grow" }),
        ...headerRight
      ),
      els.stats
    ),
    el(
      "section",
      { class: "card", style: "margin-top:16px" },
      el("div", { class: "tabs", role: "tablist" }, ...tabButtons),
      ...panels
    )
  );

  els.fetchBtn.addEventListener("click", () => fetchNow());
  els.muteBtn.addEventListener("click", () => muteCurrentFeed());
  els.dropBtn.addEventListener("click", () => dropCurrentFeed());

  /* Data loading ---------------------------------------------------------- */

  async function show(ref) {
    state.ref = ref;
    els.title.textContent = "Loading…";
    await loadFeed();
    await Promise.all([loadResult(), loadHistory()]);
    return state.feed;
  }

  async function refresh() {
    await Promise.all([loadResult(), loadHistory()]);
  }

  async function loadFeed() {
    const payload = await api(`/api/feeds/${state.ref}`);
    state.feed = payload.feed;
    renderHeader();
  }

  async function loadResult() {
    try {
      const parsedOnlyParam = state.parsedOnly ? "&parsed_only=true" : "";
      const payload = await api(`/api/results/${state.ref}?limit=${PAGE_SIZE}${parsedOnlyParam}`);
      state.result = payload;
      state.items = payload.items;
      state.hasMore = payload.has_more;
      state.network = payload.network;
    } catch {
      state.result = null;
      state.items = [];
      state.network = null;
    }
    await loadParsed();
    renderRunStats();
    renderItems();
    renderNetwork();
  }

  async function loadParsed() {
    if (!state.result) {
      state.parsed = null;
      return;
    }
    try {
      state.parsed = await api(`/api/results/${state.ref}/parsed?limit=500`);
    } catch {
      state.parsed = null;
    }
  }

  async function loadHistory() {
    try {
      state.history = (await api(`/api/feeds/${state.ref}/runs`)).runs;
    } catch {
      state.history = [];
    }
    renderHistory();
  }

  async function fetchNow() {
    const button = els.fetchBtn;
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Fetching…";
    try {
      await api(`/api/feeds/${state.ref}/fetch`, { body: {} });
      await refresh();
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function muteCurrentFeed() {
    const feed = state.feed;
    const feedId = feed?.policy?.feed_id;
    if (!feedId) return;
    const label = feed.company_name || feed.key;
    const proceed = window.confirm(
      `Mute ${label} (${feed.source}/${feed.key})? ` +
        "This removes its feed and prevents automatic promotion from adding " +
        "it back. Existing job postings are kept."
    );
    if (!proceed) return;
    const button = els.muteBtn;
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Muting…";
    try {
      await muteFeed(feed);
      if (onMute) await onMute();
      else onClose?.();
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      window.alert(`Failed to mute feed: ${error.message ?? error}`);
    }
  }

  async function dropCurrentFeed() {
    const feed = state.feed;
    const feedId = feed?.policy?.feed_id;
    if (!feedId) return;
    const label = feed.company_name || feed.key;
    const proceed = window.confirm(
      `Drop ${label} (${feed.source}/${feed.key})? ` +
        "This removes its feed and poll history, but leaves the token unmuted " +
        "and eligible for future promotion."
    );
    if (!proceed) return;
    const button = els.dropBtn;
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Dropping…";
    try {
      await dropFeed(feed);
      if (onDrop) await onDrop();
      else onClose?.();
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      window.alert(`Failed to drop feed: ${error.message ?? error}`);
    }
  }

  /* Header ---------------------------------------------------------------- */

  function renderHeader() {
    const feed = state.feed;
    els.title.textContent = feed.company_name || feed.key;
    els.description.textContent = feed.company_description || "";
    els.description.hidden = !feed.company_description;

    els.muteBtn.disabled = !feed.policy?.feed_id;
    els.muteBtn.title = feed.policy?.feed_id
      ? "Mute token and remove feed"
      : "Not in the feed table — nothing to mute";
    els.dropBtn.disabled = !feed.policy?.feed_id;
    els.dropBtn.title = feed.policy?.feed_id
      ? "Drop this feed without muting its token"
      : "Not in the feed table — nothing to drop";

    const showParsedOnlyToggle = feed.source === "simplify";
    els.parsedOnlyLabel.style.display = showParsedOnlyToggle ? "inline-flex" : "none";
    if (!showParsedOnlyToggle) {
      state.parsedOnly = false;
      els.parsedOnlyToggle.checked = false;
    }

    const profile = feed.profile ?? {};
    const badges = [el("span", { class: "badge" }, feed.category)];
    if (profile.adapter) {
      badges.push(el("span", { class: "badge", title: "adapter" }, profile.adapter));
    }
    const formats = profile.formats ?? [];
    formats.forEach((format, i) => {
      // The flavor (docxtract parser) describes the extracted document, which
      // is the last format in the chain — "xml · rss", "jsonld · schemaorg".
      const last = i === formats.length - 1;
      const text = last && profile.flavor ? `${format} · ${profile.flavor}` : format;
      badges.push(el("span", { class: "badge", title: "response format" }, text));
    });
    for (const archetype of profile.archetypes ?? []) {
      if (archetype === "single") continue;
      badges.push(
        el("span", {
          class: "badge",
          "data-kind": EXPENSIVE_ARCHETYPES.has(archetype) ? "would_park" : null,
          title: ARCHETYPE_HINTS[archetype] ?? null,
        }, archetype)
      );
    }
    badges.push(
      feed.parser_registered
        ? el("span", { class: "badge", "data-kind": "resolved" }, "parser")
        : el("span", { class: "badge", "data-kind": "would_park", title: "harvest-only source" }, "no parser")
    );
    if (feed.policy?.paused_at) {
      // Read-only here: pausing is a Scheduler action, the mirror image of
      // enable/disable being a Feeds action.
      badges.push(
        el("span", { class: "badge", "data-kind": "would_park", title: "Polling held from the Scheduler page (schedule.paused_at)" },
          "paused")
      );
    }
    els.badges.replaceChildren(...badges);

    // Enable/disable lives on the Feeds page's row and vendor toggles now,
    // not here -- this pane is embedded right next to that row. It still
    // owns the control on the standalone /feed page, which has no row to
    // toggle from.
    const controls = [];
    if (standalone && feed.policy) {
      if (feed.policy.in_postgres) {
        const enabled = el("input", { type: "checkbox" });
        enabled.checked = Boolean(feed.policy.enabled);
        enabled.addEventListener("change", async () => {
          const next = enabled.checked;
          enabled.disabled = true;
          try {
            await setFeedEnabled(feed, next);
            await loadFeed();
          } catch {
            enabled.checked = !next;
          } finally {
            enabled.disabled = false;
          }
        });
        controls.push(
          el("label", { class: "secondary", style: "display:inline-flex;align-items:center;gap:6px" },
            el("span", { class: "switch" }, enabled, el("span", { class: "track" })),
            "enabled")
        );
      } else {
        controls.push(
          el("button", {
            class: "chip action",
            type: "button",
            onclick: async (event) => {
              event.target.disabled = true;
              try {
                await api(`/api/feeds/${state.ref}/import`, { body: {} });
                await loadFeed();
              } catch {
                event.target.disabled = false;
              }
            },
          }, "Import to Postgres")
        );
      }
    }
    els.controls.replaceChildren(...controls);
  }

  function stat(label, value, title) {
    return el("div", { class: "stat", title: title ?? null },
      el("div", { class: "label" }, label),
      el("div", { class: "value" }, value));
  }

  function renderRunStats() {
    const run = state.result?.result;
    if (!run) {
      els.stats.replaceChildren(
        el("div", { class: "muted" }, "No stored run yet — fetch to inspect.")
      );
      return;
    }
    const cells = [
      el("div", { class: "stat" },
        el("div", { class: "label" }, "Status"),
        el("div", {}, statusChip(run.status))),
      stat("HTTP", run.http_status ?? "—"),
      stat("Items", String(run.item_count)),
      stat("Elapsed", formatMs(run.elapsed_ms)),
      stat("Requests", String(run.network?.request_count ?? 0),
        "HTTP requests this run — paginated and detail sources pay one per page or item"),
      ...(run.network?.reused_item_count
        ? [stat("Reused", String(run.network.reused_item_count),
            "Full detail payloads reused after stable list/detail identity fields matched")]
        : []),
      ...(run.network?.detail_fetched_count != null
        ? [stat("Details fetched", String(run.network.detail_fetched_count),
            "Detail payloads requested instead of reused")]
        : []),
      ...(run.network?.detail_skipped_count
        ? [stat("Details skipped", String(run.network.detail_skipped_count),
            "Rows a detail.only gate kept out of the detail leg — a deliberate skip, not a shortfall")]
        : []),
      ...(run.network?.retry_count
        ? [stat("Retries", String(run.network.retry_count),
            "Retried attempts — rate limiting shows up here")]
        : []),
      stat("Payload", formatBytes(run.raw_size_bytes)),
      stat("Fetched", relativeTime(run.finished_at), run.finished_at),
      stat("Trigger", run.trigger),
    ];
    if (run.status === "error") {
      cells.push(
        el("div", { class: "stat", style: "min-width:200px" },
          el("div", { class: "label" }, "Error"),
          el("div", { class: "mono", style: "color:var(--err);font-size:12px" },
            `${run.error_class}: ${run.error_detail ?? ""}`))
      );
    }
    els.stats.replaceChildren(...cells);
  }

  /* Items: raw payload + parsed canonicals, side by side ------------------ */

  function renderJson(host, value) {
    host.textContent = JSON.stringify(value, null, 2) ?? "null";
  }

  function truncate(text, max) {
    return text.length > max ? `${text.slice(0, max)}…` : text;
  }

  // Empty values carry no signal in a payload — render nothing for them.
  function isEmptyValue(value) {
    return (
      value == null ||
      value === "" ||
      (typeof value === "object" && Object.keys(value).length === 0)
    );
  }

  // One row of the JSON tree: scalars inline as `key: value`, nested
  // objects/arrays as a native <details> with a triangle. Children are
  // built lazily on first expand so huge payloads stay cheap.
  // Empty values ({}, [], null, "") keep their key visible with a blank
  // value column — the field existing is itself signal.
  function treeRow(key, value) {
    if (isEmptyValue(value)) {
      return el(
        "div",
        { class: "leaf" },
        el("span", { class: "key" }, key),
        el("span", { class: "val" })
      );
    }
    if (typeof value === "object") {
      const count = Array.isArray(value) ? `[${value.length}]` : `{${Object.keys(value).length}}`;
      const children = el("div", { class: "children" });
      const node = el(
        "details",
        {},
        el("summary", {}, el("span", { class: "key" }, key), el("span", { class: "count" }, count)),
        children
      );
      let built = false;
      node.addEventListener("toggle", () => {
        if (!node.open || built) return;
        built = true;
        for (const [childKey, childValue] of Object.entries(value)) {
          children.append(treeRow(childKey, childValue));
        }
      });
      return node;
    }
    return el(
      "div",
      { class: "leaf" },
      el("span", { class: "key" }, key),
      el("span", { class: `val ${typeof value}`, title: String(value) }, truncate(String(value), 300))
    );
  }

  // Parses an XML payload string into a pretty-printed, syntax-highlighted
  // <pre>. DOMParser never throws on malformed input -- it embeds a
  // <parsererror> element instead -- so a failed parse returns { ok: false }
  // and the caller falls back to plain text. Built entirely from DOM nodes
  // via el() (never innerHTML), so untrusted harvested markup can't be
  // interpreted as real HTML.
  function formatXml(xmlString) {
    let doc;
    try {
      doc = new DOMParser().parseFromString(xmlString, "application/xml");
    } catch {
      return { ok: false };
    }
    if (doc.querySelector("parsererror")) return { ok: false };

    const lines = [];
    const indent = (depth) => (depth ? "  ".repeat(depth) : "");

    function walk(node, depth) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        const open = el("div", {}, indent(depth), el("span", { class: "xml-punct" }, "<"), el("span", { class: "xml-tag" }, node.nodeName));
        for (const attr of node.attributes) {
          open.append(
            " ",
            el("span", { class: "xml-attr" }, attr.name),
            el("span", { class: "xml-punct" }, "="),
            el("span", { class: "xml-punct" }, '"'),
            el("span", { class: "xml-attr-val" }, attr.value),
            el("span", { class: "xml-punct" }, '"')
          );
        }
        const children = [...node.childNodes].filter(
          (c) => c.nodeType === Node.ELEMENT_NODE || (c.nodeType === Node.TEXT_NODE && c.textContent.trim())
        );
        if (!children.length) {
          open.append(el("span", { class: "xml-punct" }, "/>"));
          lines.push(open);
          return;
        }
        open.append(el("span", { class: "xml-punct" }, ">"));
        lines.push(open);
        for (const child of children) walk(child, depth + 1);
        lines.push(
          el(
            "div",
            {},
            indent(depth),
            el("span", { class: "xml-punct" }, "</"),
            el("span", { class: "xml-tag" }, node.nodeName),
            el("span", { class: "xml-punct" }, ">")
          )
        );
      } else if (node.nodeType === Node.TEXT_NODE) {
        lines.push(el("div", {}, indent(depth), el("span", { class: "xml-text" }, node.textContent.trim())));
      }
    }
    walk(doc.documentElement, 0);

    return { ok: true, node: el("pre", { class: "json-pane xml-pane" }, ...lines) };
  }

  // What the payload pane's format badge should say for one item. Item.kind
  // is a storage column, not the wire format — JSON-LD extracted from HTML is
  // stored as "json" — so the source profile breaks the tie.
  function payloadFormatLabel(kind) {
    const profile = state.feed?.profile ?? {};
    if (kind === "xml") {
      const flavored = ["rss", "atom", "xml_map"].includes(profile.flavor);
      return flavored ? `XML · ${profile.flavor}` : "XML";
    }
    return (profile.formats ?? []).includes("jsonld") ? "JSON-LD" : "JSON";
  }

  // Devtools-style collapsible tree inside the same card design as a Parsed
  // canonical: header badge, tree body, Full JSON toggle at the bottom.
  function renderReadablePayload(host, data, { label, kind, labelTitle } = {}) {
    if (kind === "xml" && typeof data === "string") {
      const formatted = formatXml(data);
      const body = formatted.ok ? formatted.node : el("pre", { class: "json-pane" }, data);
      host.replaceChildren(label ? el("div", { class: "canonical" }, el("header", {}, el("span", { class: "badge", title: labelTitle ?? null }, label)), body) : body);
      return;
    }
    if (data === null || typeof data !== "object") {
      host.replaceChildren(el("pre", { class: "json-pane" }, JSON.stringify(data, null, 2) ?? "null"));
      return;
    }
    const rows = Object.entries(data).map(([key, value]) => treeRow(key, value));

    const card = el(
      "div",
      { class: "canonical" },
      label ? el("header", {}, el("span", { class: "badge", title: labelTitle ?? null }, label)) : null,
      rows.length
        ? el("div", { class: "json-tree" }, ...rows)
        : el("div", { class: "empty-state" }, "Empty payload.")
    );

    const toggle = el("button", { class: "btn small", type: "button", style: "margin:0 12px 12px" }, "Full JSON");
    const pane = el("pre", { class: "json-pane", style: "margin:0 12px 12px", hidden: "" });
    toggle.addEventListener("click", () => {
      pane.hidden = !pane.hidden;
      if (!pane.hidden) renderJson(pane, data);
    });
    card.append(toggle, pane);
    host.replaceChildren(card);
  }

  function renderItems() {
    els.countItems.textContent = state.result ? ` ${state.result.result.item_count}` : "";
    renderParseStats();

    if (!state.result) {
      els.itemList.replaceChildren(el("div", { class: "empty-state" }, "No run yet."));
      els.harvestPane.replaceChildren(
        el("div", { class: "empty-state" }, "Fetch this feed to inspect its payload.")
      );
      els.parsedPane.replaceChildren();
      els.harvestPager.replaceChildren();
      return;
    }

    const rows = [];
    const envelopeRow = el(
      "button",
      { class: "item item-compact", type: "button", title: "Envelope — response root" },
      el("span", { class: "title" }, "◆")
    );
    envelopeRow.addEventListener("click", () => {
      select(envelopeRow);
      renderReadablePayload(els.harvestPane, state.result.envelope, { label: "envelope" });
      els.parsedPane.replaceChildren(
        el("div", { class: "empty-state" },
          "The envelope has no parsed form — parse() runs per item.")
      );
    });
    rows.push(envelopeRow);

    if (state.parsedOnly && state.items.length === 0) {
      rows.push(el("div", { class: "empty-state" }, "No parsed items for this feed."));
    }

    for (const item of state.items) {
      const row = el(
        "button",
        { class: "item item-compact", type: "button" },
        el("span", { class: "title" }, String(item.index))
      );
      row.addEventListener("click", async () => {
        select(row);
        els.harvestPane.replaceChildren(el("div", { class: "empty-state" }, "Loading…"));
        const detail = await api(`/api/results/${state.ref}/items/${item.index}`);
        renderReadablePayload(els.harvestPane, detail.payload, {
          kind: detail.kind,
          label: payloadFormatLabel(detail.kind),
          labelTitle: state.result?.wire_content_type ?? null,
        });
        await renderParsedForIndex(item.index);
      });
      rows.push(row);
    }
    els.itemList.replaceChildren(...rows);
    // Land on the first real item so both panes populate immediately.
    (rows[1] ?? rows[0]).click();

    const totalCount = state.result.total_count ?? state.result.result.item_count;
    els.harvestPager.replaceChildren(
      el("span", {}, state.parsedOnly
        ? `${state.items.length} of ${totalCount} parsed items loaded`
        : `${state.items.length} of ${totalCount} items loaded`),
      state.hasMore
        ? el("button", { class: "btn small", type: "button", onclick: loadMoreItems }, "Load more")
        : null
    );

    function select(row) {
      els.itemList.querySelectorAll(".item").forEach((other) =>
        other.setAttribute("aria-selected", String(other === row)));
    }
  }

  function renderParseStats() {
    const parsed = state.parsed;
    if (parsed?.status !== "ok") {
      els.parseStats.replaceChildren();
      return;
    }
    const s = parsed.summary;
    els.parseStats.replaceChildren(
      stat("Parsed", String(s.items_parsed)),
      stat("Errors", String(s.item_errors)),
      stat("Postings", String(s.postings)),
      stat("Resolved", String(s.resolved)),
      stat("Fallback", String(s.fallback_company)),
      stat("Would create", String(s.would_mint ?? 0)),
      stat("Would park", String(s.would_park)),
      stat("Unsupported", String(s.unsupported))
    );
  }

  async function renderParsedForIndex(index) {
    const parsed = state.parsed;
    if (!parsed) {
      els.parsedPane.replaceChildren(
        el("div", { class: "empty-state" }, "No parse data for this run.")
      );
      return;
    }
    if (parsed.status === "no_parser") {
      els.parsedPane.replaceChildren(
        el("div", { class: "empty-state" },
          `${state.feed.source} is harvest-only — no parse() is registered, so silver never sees these items. This is a source design fact, not an error.`)
      );
      return;
    }
    if (parsed.status === "skipped") {
      els.parsedPane.replaceChildren(
        el("div", { class: "empty-state" },
          "Parsing was skipped for this run (304 or harvest error).")
      );
      return;
    }
    els.parsedPane.replaceChildren(el("div", { class: "empty-state" }, "Loading…"));
    renderParsedDetail(await api(`/api/results/${state.ref}/parsed/${index}`));
  }

  async function loadMoreItems() {
    const parsedOnlyParam = state.parsedOnly ? "&parsed_only=true" : "";
    const payload = await api(
      `/api/results/${state.ref}?offset=${state.items.length}&limit=${PAGE_SIZE}${parsedOnlyParam}`
    );
    state.items = [...state.items, ...payload.items];
    state.hasMore = payload.has_more;
    state.result.total_count = payload.total_count;
    renderItems();
  }

  function renderParsedDetail(item) {
    if (item.status === "error") {
      els.parsedPane.replaceChildren(
        el("div", { class: "canonical" },
          el("header", {},
            el("span", { class: "badge", "data-kind": "error" }, item.error_class),
            el("span", { class: "mono" }, item.external_id)),
          el("pre", { class: "json-pane", style: "border:none;border-radius:0" },
            item.error_detail ?? ""))
      );
      return;
    }
    const cards = item.canonicals.map((canonical) => {
      const header = [
        el("span", { class: "badge" }, canonical.type),
        el("span", { class: "badge", "data-kind": canonical.classification === "posting" ? "resolved" : "would_park" },
          canonical.classification),
      ];
      const resolution = canonical.company_resolution;
      if (resolution) {
        header.push(
          el("span", { class: "badge", "data-kind": resolution.status },
            RESOLUTION_LABEL[resolution.status]?.(resolution) ?? resolution.status)
        );
      }
      if (canonical.title_warning) {
        header.push(el("span", { class: "badge", "data-kind": "error" }, "title warning"));
      }

      const fields = [];
      const data = canonical.data;
      const preferred = ["title", "company_raw", "location_raw", "url_posting", "employment_type", "state", "key", "source", "reason", "name", "value"];
      const rest = Object.keys(data).filter((key) => !preferred.includes(key));
      for (const key of [...preferred, ...rest]) {
        if (data[key] == null || data[key] === "") continue;
        const raw = typeof data[key] === "object" ? JSON.stringify(data[key]) : String(data[key]);
        fields.push(el("dt", {}, key), el("dd", {}, truncate(raw, 120)));
      }
      if (canonical.title_normalized != null) {
        fields.push(el("dt", {}, "title_normalized"),
          el("dd", { class: "mono" }, canonical.title_normalized));
      }
      if (canonical.title_warning) {
        fields.push(el("dt", {}, "warning"),
          el("dd", { style: "color:var(--err)" }, canonical.title_warning));
      }

      const card = el("div", { class: "canonical" },
        el("header", {}, ...header),
        el("dl", { class: "fields" }, ...fields));

      const toggle = el("button", { class: "btn small", type: "button", style: "margin:0 12px 12px" }, "Full JSON");
      const pane = el("pre", { class: "json-pane", style: "margin:0 12px 12px", hidden: "" });
      toggle.addEventListener("click", () => {
        pane.hidden = !pane.hidden;
        if (!pane.hidden) renderJson(pane, canonical.data);
      });
      card.append(toggle, pane);
      return card;
    });
    els.parsedPane.replaceChildren(
      ...(cards.length ? cards : [el("div", { class: "empty-state" }, "Parser returned no canonicals for this item.")])
    );
  }

  /* Network --------------------------------------------------------------- */

  function renderNetwork() {
    const summary = state.network?.summary;
    els.countNetwork.textContent = summary?.request_count ? ` ${summary.request_count}` : "";

    if (!summary) {
      els.networkStats.replaceChildren();
      els.stageHost.replaceChildren();
      els.latencyHost.replaceChildren(el("div", { class: "empty-state" }, "No network data yet."));
      els.requestDetail.hidden = true;
      return;
    }

    // The question this pane has to answer first is "was it them or us?".
    const stages = state.network.stages ?? [];
    const runMs = stages.reduce((sum, s) => sum + (s.elapsed_ms ?? 0), 0);
    const netMs = summary.elapsed_ms;
    els.networkStats.replaceChildren(
      stat("Requests", String(summary.request_count)),
      stat("Attempts", String(summary.attempt_count)),
      stat("Retries", String(summary.retry_count), "Rate limiting shows up here"),
      stat("Redirects", String(summary.redirect_count)),
      stat("Bytes", formatBytes(summary.response_bytes)),
      stat("Final", String(summary.final_status ?? "—")),
      stat("Errors", String(summary.error_count)),
      ...(summary.enumerated_count != null
        ? [stat("Enumerated", String(summary.enumerated_count), "Unique current list rows")]
        : []),
      ...(summary.partition_count
        ? [stat("Partitions", String(summary.partition_count),
            "Facet queries used to escape a capped vendor result window")]
        : []),
      ...(summary.result_window_truncated
        ? [stat("Window", "Split", "The vendor capped the unfiltered result; facet partitions recovered the complete list")]
        : []),
      ...(summary.reused_item_count
        ? [stat("Reused details", String(summary.reused_item_count),
            "Cached full payloads whose stable list/detail identity fields matched")]
        : []),
      ...(summary.detail_fetched_count != null
        ? [stat("Fetched details", String(summary.detail_fetched_count))]
        : []),
      ...(summary.detail_skipped_count
        ? [stat("Skipped details", String(summary.detail_skipped_count))]
        : []),
      ...(netMs == null
        ? []
        : [
            stat("Waiting on HTTP", formatMs(netMs), "Time inside requests"),
            ...(runMs
              ? [
                  stat(
                    "Local",
                    formatMs(Math.max(0, runMs - netMs)),
                    "Elapsed minus HTTP: throttle sleep, hashing, parse preview"
                  ),
                ]
              : []),
          ])
    );

    els.stageHost.replaceChildren(
      ...(stages.length
        ? [
            el("h2", { style: "margin:0 0 8px" }, "Where the time went"),
            stageBar(stages),
          ]
        : [])
    );

    const requests = state.network.requests ?? [];
    els.latencyHost.replaceChildren(
      ...(requests.length
        ? [
            el("h2", { style: "margin:0 0 4px" }, "Latency over the run"),
            latencyChart(requests, { onSelect: (index) => renderRequestDetail(requests[index]) }),
          ]
        : [el("div", { class: "empty-state" }, "No requests recorded.")])
    );
    els.requestDetail.hidden = true;
  }

  function renderRequestDetail(request) {
    els.requestDetail.hidden = false;
    const rows = [
      ["Request", `#${request.request_id} attempt ${request.attempt}`],
      ["Method", request.method],
      ["Requested", request.requested_url],
      ["Final", request.final_url ?? "—"],
      ["Status", request.error ? `${request.error.class} (transport error)` : `HTTP ${request.status}`],
      ["Elapsed", formatMs(request.elapsed_ms)],
      ["Bytes", formatBytes(request.response_bytes)],
      ["Conditional", request.conditional_headers?.length ? request.conditional_headers.join(", ") : "none sent"],
    ];
    if (request.redirects?.length) {
      rows.push(["Redirects", request.redirects.map((r) => `${r.status} → ${r.url}`).join("\n")]);
    }
    const table = el("table", {});
    for (const [label, value] of rows) {
      table.append(el("tr", {}, el("td", {}, label), el("td", {}, value)));
    }
    const headers = request.response_headers ?? {};
    for (const [name, value] of Object.entries(headers)) {
      const isRateLimit = /ratelimit|retry-after/.test(name);
      table.append(
        el("tr", {},
          el("td", {}, name),
          el("td", { class: isRateLimit ? "hl" : null }, value))
      );
    }
    els.requestDetail.replaceChildren(
      el("header", { style: "padding:8px 12px;border-bottom:1px solid var(--border)" },
        el("strong", {}, "Request detail"),
        el("span", { class: "muted" }, " — rate-limit headers highlighted")),
      el("div", { style: "padding:8px 4px" }, table)
    );
  }

  /* History --------------------------------------------------------------- */

  function renderHistory() {
    const runs = [...state.history].reverse(); // oldest -> newest for the sparkline

    if (!runs.length) {
      els.historySpark.replaceChildren();
      els.historyBody.replaceChildren(
        el("tr", {}, el("td", { colspan: 8, class: "empty-state" }, "No runs recorded yet."))
      );
      return;
    }

    const notModified = runs.filter((run) => run.status === "unchanged").length;
    els.historySpark.replaceChildren(
      el("div", { style: "display:flex;align-items:center;gap:16px;margin-bottom:8px" },
        sparkline(
          runs.map((run) => ({
            value: run.elapsed_ms,
            status: run.status,
            label: `${statusLabel(run.status)} · ${formatMs(run.elapsed_ms)} · ${run.finished_at}`,
          })),
          { width: 320, height: 40 }
        ),
        el("span", { class: "muted" },
          `${runs.length} runs · 304 rate ${Math.round((notModified / runs.length) * 100)}%`))
    );

    els.historyBody.replaceChildren(
      ...state.history.map((run) =>
        el("tr", {},
          el("td", { title: run.finished_at }, relativeTime(run.finished_at)),
          el("td", {}, run.trigger),
          el("td", {}, statusChip(run.status)),
          el("td", { class: "num" }, String(run.http_status ?? "—")),
          el("td", { class: "num" }, String(run.item_count)),
          el("td", { class: "num" }, formatMs(run.elapsed_ms)),
          el("td", { class: "num" }, formatBytes(run.raw_size_bytes)),
          el("td", { class: "num" }, String(run.network_summary?.retry_count ?? 0)))
      )
    );
  }

  return { show, refresh };
}
