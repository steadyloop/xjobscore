// Activity telemetry is deliberately separate from scheduler controls.
export const ENRICHMENT_STEPS = ["job_type", "skills", "degrees", "summaries", "focus"];
export const stepLabel = (name) => name === "job_type" ? "Job type"
  : name.charAt(0).toUpperCase() + name.slice(1);

function record(value) {
  if (typeof value === "string") {
    try { return JSON.parse(value) ?? {}; } catch { return {}; }
  }
  return value ?? {};
}

export function secondsLabel(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.max(0, seconds).toFixed(1)}s`;
  const rounded = Math.max(0, Math.round(seconds));
  return rounded < 3600 ? `${Math.floor(rounded / 60)}m ${rounded % 60}s`
    : `${Math.floor(rounded / 3600)}h ${Math.floor((rounded % 3600) / 60)}m`;
}

export function enrichmentSummary(run) {
  const detail = record(run?.detail);
  const gold = record(detail.gold);
  const errors = Object.entries(record(gold.errors)).map(([name, error]) => ({
    label: stepLabel(name), detail: String(error),
  }));
  if (run?.error && !errors.length) errors.push({ label: "Enrichment", detail: run.error });
  if (run && ["failed", "degraded", "interrupted"].includes(run.state) && !errors.length) {
    errors.push({ label: "Enrichment", detail: `Run ${run.state}; no further error details were recorded.` });
  }
  let durationMs = gold.duration_ms ?? (run?.job_type === "feed" ? record(detail.stage_ms).gold : null);
  if (durationMs == null && run?.job_type === "gold_catchup" && run.finished_at) {
    const elapsed = Date.parse(run.finished_at) - Date.parse(run.started_at);
    if (Number.isFinite(elapsed) && elapsed >= 0) durationMs = elapsed;
  }
  // Legacy records contain overlapping step counts, not a distinct total.
  const postings = Number.isInteger(gold.postings) && gold.postings >= 0 ? gold.postings : null;
  return {
    postings, duration: secondsLabel(durationMs == null ? null : durationMs / 1000), errors,
    outcome: !run ? "" : run.state === "complete" ? "completed"
      : run.state === "degraded" ? "completed with errors" : run.state,
    tone: run?.state === "complete" ? "complete" : "failed",
    timings: ENRICHMENT_STEPS.map((name) => ({
      name, label: stepLabel(name), failed: Boolean(record(gold.errors)[name]),
      elapsed: gold.stage_ms?.[name] != null ? secondsLabel(gold.stage_ms[name] / 1000)
        : gold.stage_ms != null ? "Not run" : "Not recorded",
    })),
  };
}

export function nextCheck(worker, now = Date.now()) {
  if (!worker) return { text: "Next check unavailable", title: "Enrichment worker telemetry is unavailable." };
  if (!worker.alive || !worker.accepting) return { text: "Next check unavailable (worker offline)", title: "" };
  if (worker.checking) return { text: "Checking now", title: "The background enrichment worker is checking for work." };
  const target = Date.parse(worker.next_check_at);
  if (!Number.isFinite(target)) return { text: "Next check unavailable", title: "No next check has been reported." };
  const seconds = Math.max(0, Math.ceil((target - now) / 1000));
  return {
    text: seconds ? `Next check in ${seconds < 60 ? `${seconds}s` : secondsLabel(seconds)}` : "Next check due",
    title: `${new Date(target).toLocaleString()}; runs sooner when feeds change.`,
  };
}

export const isIssue = (entry) => ["WARN", "ERROR", "CRITICAL"].includes(entry.level);

function processName(name = "") {
  if (name === "poll_log") return "fetch";
  if (/heartbeat/.test(name)) return "heartbeat";
  if (/pipeline|gold/.test(name)) return "enrich";
  if (/publish/.test(name)) return "publish";
  return name.replace(/^xjobscore\./, "").split(".").at(-1) || "system";
}

function fetchFailure(identity, statusCode, error = "") {
  const cooldown = /Workable cooldown: (\d+)s remaining/.exec(error);
  if (cooldown) return `${identity} deferred (Workable cooldown ${secondsLabel(Number(cooldown[1]))} remaining)`;
  const status = statusCode ?? /\bHTTP (\d{3})\b/.exec(error)?.[1];
  return `${identity} fetch failed${status ? ` HTTP ${status}` : ""}`;
}

export function activityEntries({ logs = [], polls = [], runs = [], boot = "" }) {
  const entries = [];
  const attempts = [];
  for (const poll of polls) {
    const level = poll.status === "error" ? "ERROR" : poll.status === "partial" ? "WARN" : "INFO";
    const result = poll.new_postings ? `${poll.new_postings} new postings`
      : poll.status === "unchanged" ? "unchanged" : poll.status === "error" ? "fetch failed" : "no new postings";
    const entry = {
      id: `poll-${poll.source}-${poll.key}-${poll.polled_at}`, ts: poll.polled_at, level,
      process: "fetch", msg: `${poll.source}:${poll.key} ${result}${poll.status_code ? ` HTTP ${poll.status_code}` : ""}`,
      routine: level === "INFO" && !poll.new_postings,
    };
    entries.push(entry);
    if (poll.status === "error") attempts.push({
      identity: `${poll.source}:${poll.key}`, start: Date.parse(poll.polled_at),
      statusCode: poll.status_code, entry,
    });
  }
  for (const run of runs) {
    const detail = record(run.detail);
    const isEnrichment = run.job_type === "gold_catchup" || (run.job_type === "feed" && detail.gold != null);
    const process = isEnrichment ? "enrich"
      : { token_heartbeat: "heartbeat", feed: "fetch" }[run.job_type] ?? run.job_type;
    const summary = enrichmentSummary(run);
    const elapsed = run.finished_at ? secondsLabel((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000) : "";
    const count = isEnrichment && summary.postings != null ? `${summary.postings} postings enriched`
      : detail.postings != null ? `${detail.postings} postings` : "";
    const entry = {
      id: `run-${run.id}`, ts: run.finished_at ?? run.started_at, process,
      level: run.state === "failed" ? "ERROR" : ["degraded", "interrupted"].includes(run.state) ? "WARN" : "INFO",
      msg: [run.identity, run.state, count, isEnrichment ? summary.duration : elapsed].filter(Boolean).join(" "),
      detail: isEnrichment ? summary.errors.map((error) => `${error.label}: ${error.detail}`).join("\n") : run.error,
      routine: false,
    };
    if (run.job_type === "feed" && run.state === "failed" && !isEnrichment) {
      const start = Date.parse(run.started_at);
      // poll_log timestamps the attempt just before system_run is opened.
      // Match starts, rather than requiring polled_at to fall inside the run.
      const attempt = attempts.filter((item) => !item.run && item.identity === run.identity
        && Math.abs(item.start - start) <= 1000)
        .sort((a, b) => Math.abs(a.start - start) - Math.abs(b.start - start))[0];
      if (attempt) {
        attempt.run = run;
        attempt.entry.ts = entry.ts;
        attempt.entry.detail = entry.detail;
        attempt.entry.msg = `${fetchFailure(run.identity, attempt.statusCode, run.error)} ${elapsed}`.trim();
        continue;
      }
      entry.msg = `${fetchFailure(run.identity, null, run.error)} ${elapsed}`.trim();
      entries.push(entry);
      attempts.push({ identity: run.identity, start, run, entry });
    } else entries.push(entry);
  }
  for (const line of logs) {
    const level = line.level === "WARNING" ? "WARN" : line.level;
    const harvest = line.name === "xjobscore.run" && level === "ERROR"
      ? /^harvest failed source=(\S+)$/.exec(line.msg) : null;
    const process = harvest ? "fetch" : processName(line.name);
    if (harvest) {
      const ts = Date.parse(line.ts);
      const attempt = attempts.filter((item) => !item.logged && item.identity === harvest[1]
        && (item.run
          ? ts >= Date.parse(item.run.started_at) - 1000 && ts <= Date.parse(item.run.finished_at) + 1000
          // Without a run interval, only merge a nearby, fast failure.
          : ts >= item.start - 1000 && ts <= item.start + 5000))
        .sort((a, b) => Math.abs(ts - Date.parse(a.run?.finished_at ?? a.entry.ts))
          - Math.abs(ts - Date.parse(b.run?.finished_at ?? b.entry.ts)))[0];
      if (attempt) {
        attempt.logged = true;
        attempt.entry.detail = line.exc || attempt.entry.detail;
        attempt.entry.logger = line.name;
        const elapsed = attempt.run?.finished_at
          ? secondsLabel((Date.parse(attempt.run.finished_at) - Date.parse(attempt.run.started_at)) / 1000) : "";
        attempt.entry.msg = `${fetchFailure(attempt.identity, attempt.statusCode, attempt.entry.detail)} ${elapsed}`.trim();
        continue;
      }
    }
    entries.push({
      id: `log-${boot}-${line.seq}`, ts: line.ts, level, process,
      msg: harvest && /Workable cooldown:/.test(line.exc ?? "")
        ? fetchFailure(harvest[1], null, line.exc) : line.msg,
      detail: line.exc, logger: line.name,
      routine: level === "INFO" && process === "fetch" && /unchanged|no new\b/i.test(line.msg),
    });
  }
  return [...new Map(entries.map((entry) => [entry.id, entry])).values()]
    .sort((a, b) => (Date.parse(a.ts) || 0) - (Date.parse(b.ts) || 0) || a.id.localeCompare(b.id))
    .slice(-500);
}

export function filterActivity(entries, { process = "all", query = "", allPolls = false, view = "all" } = {}) {
  const needle = query.trim().toLowerCase();
  const scoped = entries.filter((entry) => (process === "all" || entry.process === process)
    && [entry.ts, entry.level, entry.process, entry.msg, entry.detail].join(" ").toLowerCase().includes(needle));
  const shown = scoped.filter((entry) => allPolls || !entry.routine || isIssue(entry));
  return {
    entries: shown.filter((entry) => view !== "issues" || isIssue(entry)),
    total: shown.length, issues: shown.filter(isIssue).length,
    hidden: scoped.length - shown.length,
  };
}
