/* Pure logic for the Companies page: no DOM, no fetch, unit-tested. */

/* Rebuild a vendor's public board URL from the token keyscan stored. The
   shapes match keyscan/rules.py `board_url`; a vendor whose token cannot be
   turned back into a URL is simply absent, and the row falls back to the
   sample posting's own link. */
const BOARD = {
  greenhouse: ([t]) => `https://job-boards.greenhouse.io/${t}`,
  lever: ([t]) => `https://jobs.lever.co/${t}`,
  ashby: ([t]) => `https://jobs.ashbyhq.com/${t}`,
  workable: ([t]) => `https://apply.workable.com/${t}/`,
  workday: ([tenant, wd, site]) =>
    site && `https://${tenant}.${wd}.myworkdayjobs.com/en-US/${site}`,
  oracle: ([host, site]) =>
    site && `https://${host}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/${site}`,
};

/* "greenhouse:tines" -> {ats, token}. Split on the first colon only: workday
   and oracle tokens carry their own separators. */
export function splitToken(pair) {
  const at = (pair || "").indexOf(":");
  return at < 0 ? null : { ats: pair.slice(0, at), token: pair.slice(at + 1) };
}

export function boardUrl(ats, token) {
  const build = BOARD[ats];
  return (build && build(String(token).split("/"))) || null;
}

/* An /api/companies row in the shape the page edits: the name/domain/
   description boxes write straight back onto it, so a failed save leaves the
   operator's typing intact and Save can simply be pressed again. */
export function toRow(api) {
  const tokens = (api.tokens || []).map(splitToken).filter(Boolean);
  // The token worth showing is the one that leads somewhere: an eightfold
  // token sorts first but is a URL fragment nobody can read, while the
  // workday one beside it opens the board.
  const primary = tokens.find((t) => boardUrl(t.ats, t.token)) || tokens[0] || null;
  return {
    id: api.id,
    name: api.name || "",
    domain: api.domain || "",
    description: api.description || "",
    state: api.review_state,
    postings: api.active_postings || 0,
    title: api.sample_title || "",
    tokens,
    primary,
    board: primary && boardUrl(primary.ats, primary.token),
    job: api.sample_apply_url || api.sample_source_url || null,
    hasLogo: Boolean(api.has_logo),
    error: "",
  };
}

/* Where to look to work out who this company is, most useful first. */
export function links(row) {
  const out = [];
  if (row.board) out.push({ label: "Board", href: row.board });
  const gh = row.tokens.find((t) => t.ats === "greenhouse");
  // Returns {"name": ...} -- the employer's own name, straight from the vendor.
  if (gh) {
    out.push({
      label: "Name (API)",
      href: `https://boards-api.greenhouse.io/v1/boards/${gh.token}`,
    });
  }
  if (row.job && row.job !== row.board) out.push({ label: "A job", href: row.job });
  // A search on the token slug is the fastest route for ashby/lever, whose
  // API says nothing and whose slug is usually the company.
  const term = row.name || readable(row.tokens[0]?.token) || row.title;
  if (term) {
    out.push({
      label: "Search",
      href: `https://duckduckgo.com/?q=${encodeURIComponent(`${term} careers`)}`,
    });
  }
  return out;
}

/* The slug as a search term; null for an Oracle/Workday shard. */
export function readable(token) {
  const head = (token || "").split("/")[0];
  if (!head || head.includes(".")) return null;
  return /[aeiou]/.test(head) ? head.replace(/-/g, " ") : null;
}

/* A first guess at the domain from the name. Always shown as a guess. */
export function guessDomain(name) {
  if (!name) return "";
  const slug = name
    .normalize("NFKC").toLowerCase()
    .replace(/\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|group|plc)\b\.?/g, "")
    .replace(/[^a-z0-9]/g, "");
  return slug ? `${slug}.com` : "";
}
