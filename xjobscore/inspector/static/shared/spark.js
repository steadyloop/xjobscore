/* Inline-SVG micro-charts: elapsed-time sparkline and request latency chart.
   No libraries. Colors come from CSS custom properties so both themes work;
   status is never carried by color alone -- every mark has text alongside
   (tooltip title + the row's status text in the caller's table). */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value != null) node.setAttribute(name, value);
  }
  return node;
}

const STATUS_COLOR = {
  ok: "var(--viz-series)",
  unchanged: "var(--viz-series)",
  partial: "var(--warn)",
  error: "var(--err)",
};

/** Sparkline of run durations.
 * points: oldest→newest [{value, status, label}]
 */
export function sparkline(points, { width = 120, height = 28 } = {}) {
  const svg = svgEl("svg", {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    class: "spark",
    role: "img",
  });
  if (!points.length) return svg;

  const pad = 4;
  const max = Math.max(...points.map((p) => p.value ?? 0), 1);
  const stepX =
    points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const y = (value) =>
    height - pad - ((value ?? 0) / max) * (height - pad * 2);
  const x = (index) =>
    points.length > 1 ? pad + index * stepX : width / 2;

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(" ");
  svg.append(
    svgEl("path", {
      d: path,
      fill: "none",
      stroke: "var(--viz-series)",
      "stroke-width": 1.5,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      opacity: 0.75,
    })
  );

  points.forEach((p, i) => {
    const bad = p.status === "error" || p.status === "partial";
    const dot = svgEl("circle", {
      cx: x(i).toFixed(1),
      cy: y(p.value).toFixed(1),
      r: bad ? 3 : 1.75,
      fill: STATUS_COLOR[p.status] ?? "var(--viz-muted)",
      stroke: "var(--surface)",
      "stroke-width": bad ? 1 : 0,
    });
    if (p.label) {
      const title = svgEl("title");
      title.textContent = p.label;
      dot.append(title);
    }
    svg.append(dot);
  });
  return svg;
}

/** Column chart of a dense per-period count series, oldest→newest.
 * points: [{value, label}]. Bars, not a line, because these are counts over
 * discrete buckets -- a line would imply a continuous quantity between days and
 * would make a run of zeros look like a downward slope rather than nothing.
 * A zero draws no bar at all, only the baseline, so "no postings" and "a very
 * small number of postings" stay visually distinct.
 */
export function sparkBars(points, { width = 316, height = 56, fluid = true } = {}) {
  const svg = svgEl("svg", {
    width: fluid ? "100%" : width,
    viewBox: `0 0 ${width} ${height + 1}`,
    class: "spark",
    role: "img",
  });
  svg.append(
    svgEl("line", {
      x1: 0,
      y1: height,
      x2: width,
      y2: height,
      stroke: "var(--grid)",
      "stroke-width": 1,
    })
  );
  if (!points.length) return svg;

  const max = Math.max(...points.map((p) => p.value ?? 0), 1);
  const slot = width / points.length;
  const barWidth = Math.max(1, slot - 2);
  points.forEach((point, index) => {
    const value = point.value ?? 0;
    if (!value) return;
    const barHeight = Math.max(3, Math.round((value / max) * (height - 8)));
    const rect = svgEl("rect", {
      x: (index * slot).toFixed(1),
      y: height - barHeight,
      width: barWidth.toFixed(1),
      height: barHeight,
      rx: 1.5,
      fill: "var(--viz-series)",
    });
    if (point.label) {
      const title = svgEl("title");
      title.textContent = point.label;
      rect.append(title);
    }
    svg.append(rect);
  });
  return svg;
}

/** Stacked bar of where one run's wall clock went.
 * stages: in-order [{name, elapsed_ms}], partitioning the run's elapsed_ms.
 * Segments are shaded by depth rather than hue -- there is no categorical
 * palette in tokens.css, and every segment carries its name and duration as
 * text below, so nothing is encoded by appearance alone.
 */
export function stageBar(stages, { width = 640, height = 14 } = {}) {
  const wrap = document.createElement("div");
  if (!stages.length) return wrap;

  const total = stages.reduce((sum, s) => sum + (s.elapsed_ms ?? 0), 0) || 1;
  const svg = svgEl("svg", {
    width: "100%",
    viewBox: `0 0 ${width} ${height}`,
    class: "stage-bar",
    role: "img",
    preserveAspectRatio: "none",
  });

  let cursor = 0;
  stages.forEach((stage, index) => {
    const ms = stage.elapsed_ms ?? 0;
    const segW = Math.max(1, (ms / total) * width - 1);
    const rect = svgEl("rect", {
      x: ((cursor / total) * width).toFixed(1),
      y: 0,
      width: segW.toFixed(1),
      height,
      rx: 2,
      fill: "var(--viz-series)",
      opacity: (1 - (index % 3) * 0.28).toFixed(2),
    });
    const title = svgEl("title");
    title.textContent = `${stage.name} — ${Math.round(ms)} ms (${Math.round((ms / total) * 100)}%)`;
    rect.append(title);
    svg.append(rect);
    cursor += ms;
  });
  wrap.append(svg);

  const legend = document.createElement("div");
  legend.className = "stage-legend";
  stages.forEach((stage, index) => {
    const ms = stage.elapsed_ms ?? 0;
    const item = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.opacity = (1 - (index % 3) * 0.28).toFixed(2);
    item.append(swatch, `${stage.name} ${Math.round(ms)}ms`);
    legend.append(item);
  });
  wrap.append(legend);
  return wrap;
}

// Round a y-axis top up to a readable number. The steps are finer than the
// usual 1/2/5 so a 1.1s peak does not get a 2s axis and waste half the plot.
const NICE_STEPS = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];

function niceCeiling(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const scaled = value / magnitude;
  return (NICE_STEPS.find((step) => scaled <= step) ?? 10) * magnitude;
}

/** Latency over the course of one run.
 * requests: InspectorNet events in issue order (the Net policy is serial per
 * host, so order is time). y is elapsed time, so the shape answers "did it get
 * slower as we paged deeper?". x is evenly spaced rather than scaled to each
 * request's start offset — one slow request would otherwise crush the rest
 * into the left edge, and "Where the time went" above already carries the
 * wall-clock proportions.
 * Every point's exact numbers stay reachable: hover or focus writes them into
 * the readout line above the plot, and each point carries a native tooltip.
 * onSelect(index) fires on point click.
 */
export function latencyChart(requests, { width = 640, height = 150, onSelect } = {}) {
  const svg = svgEl("svg", {
    width: "100%",
    viewBox: `0 0 ${width} ${height}`,
    class: "latency-chart",
    role: "img",
    preserveAspectRatio: "xMidYMid meet",
  });
  if (!requests.length) return svg;

  const padL = 46;
  const padR = 10;
  const padT = 22; // readout line
  const padB = 20; // x labels
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const elapsed = (r) => Math.max(0, r.elapsed_ms ?? 0);
  const total = requests.reduce((sum, r) => sum + elapsed(r), 0);
  const yMax = niceCeiling(Math.max(...requests.map(elapsed), 1) * 1.1);

  const x = (index) =>
    requests.length > 1
      ? padL + (index / (requests.length - 1)) * plotW
      : padL + plotW / 2;
  const y = (ms) => padT + plotH - (ms / yMax) * plotH;

  // Recessive grid: baseline, midpoint, ceiling — labelled in ms so a value is
  // always readable without hovering.
  for (const ms of [0, yMax / 2, yMax]) {
    const gy = y(ms);
    svg.append(
      svgEl("line", {
        x1: padL,
        x2: width - padR,
        y1: gy.toFixed(1),
        y2: gy.toFixed(1),
        stroke: "var(--grid)",
        "stroke-width": 1,
      })
    );
    const label = svgEl("text", {
      x: padL - 6,
      y: (gy + 3).toFixed(1),
      fill: "var(--text-muted)",
      "font-size": 10,
      "text-anchor": "end",
    });
    label.textContent = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
    svg.append(label);
  }

  const readout = svgEl("text", {
    x: padL,
    y: 12,
    fill: "var(--text-secondary)",
    "font-size": 11,
    "font-family": "var(--font-mono)",
  });
  const idleSummary = `${requests.length} request${requests.length === 1 ? "" : "s"} · total ${Math.round(total)} ms`;
  readout.textContent = idleSummary;
  svg.append(readout);

  const path = requests
    .map((r, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(elapsed(r)).toFixed(1)}`)
    .join(" ");
  svg.append(
    svgEl("path", {
      d: path,
      fill: "none",
      stroke: "var(--viz-series)",
      "stroke-width": 1.5,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      opacity: 0.8,
    })
  );

  // Dense runs get smaller dots; past ~200 requests the line alone carries it.
  const dotR = requests.length > 60 ? 1.5 : requests.length > 24 ? 2.5 : 3.5;
  const hitW = Math.max(8, plotW / requests.length);

  requests.forEach((request, index) => {
    const ms = elapsed(request);
    const status = request.error ? "error" : request.status >= 400 ? "partial" : "ok";
    const bad = status !== "ok";
    const isRetry = request.attempt > 1;
    const point = svgEl("g", { class: "point", tabindex: onSelect ? 0 : null });

    point.append(
      svgEl("circle", {
        cx: x(index).toFixed(1),
        cy: y(ms).toFixed(1),
        r: bad ? dotR + 1.5 : dotR,
        fill: STATUS_COLOR[status],
        stroke: "var(--surface)",
        "stroke-width": bad || isRetry ? 1.5 : 0,
        opacity: isRetry ? 0.7 : 1,
      })
    );

    const detail = `#${request.request_id}${isRetry ? ` · try ${request.attempt}` : ""} · ${
      request.error ? request.error.class : `HTTP ${request.status ?? "—"}`
    } · ${Math.round(ms)} ms`;
    const title = svgEl("title");
    title.textContent = `${detail}\n${request.method ?? "GET"} ${
      request.final_url ?? request.requested_url ?? ""
    }\n${request.response_bytes ?? 0} bytes`;
    point.append(title);

    // Invisible full-height hit target — the dot itself is too small to aim at.
    const hit = svgEl("rect", {
      x: Math.max(padL, x(index) - hitW / 2).toFixed(1),
      y: padT,
      width: hitW.toFixed(1),
      height: plotH,
      fill: "transparent",
    });
    point.append(hit);

    const show = () => { readout.textContent = detail; };
    const clear = () => { readout.textContent = idleSummary; };
    point.addEventListener("pointerenter", show);
    point.addEventListener("pointerleave", clear);
    point.addEventListener("focus", show);
    point.addEventListener("blur", clear);
    if (onSelect) {
      point.style.cursor = "pointer";
      point.addEventListener("click", () => onSelect(index));
      point.addEventListener("keydown", (event) => {
        if (event.key === "Enter") onSelect(index);
      });
    }
    svg.append(point);
  });

  // x axis: first and last request, so the ordering is unambiguous.
  const tick = (index, anchor) => {
    const text = svgEl("text", {
      x: x(index).toFixed(1),
      y: height - 6,
      fill: "var(--text-muted)",
      "font-size": 10,
      "text-anchor": anchor,
    });
    text.textContent = `#${requests[index].request_id}`;
    return text;
  };
  svg.append(tick(0, requests.length > 1 ? "start" : "middle"));
  if (requests.length > 1) svg.append(tick(requests.length - 1, "end"));

  return svg;
}
