const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {
  FILTERS,
  DEGREE_FILTERS,
  SKILL_CATEGORIES,
  SKILL_BUNDLES,
  degreeFields,
  tabCount,
  detailSkillsMarkup,
  safeHref,
  skillDetails,
  orderedSkillDetails,
  skillBundles,
  listTags,
  sourceLabel,
  sourceChoices,
  sourceDisclosureMarkup,
  relativeTime,
  workplaceLabel,
  listFacts,
  deadlineLabel,
  detailMetaMarkup,
  cardMetaMarkup,
  openPostingLabel,
  detailHeadMarkup,
  detailPostedMarkup,
  summaryMarkup,
  initials,
  cardStatusLine,
  recencyBucket,
} = require("./app.js");

test("V2 job type tabs default to all student opportunities", () => {
  const counts = { intern: 42, coop: 7, student_program: 11, research: 3, part_time: 5 };
  assert.deepEqual(FILTERS.map(filter => filter.label), ["All", "Internships", "Co-ops", "Student Programs", "Part Time"]);
  assert.equal(tabCount(counts, FILTERS[0]), 68);
  assert.equal(tabCount(counts, FILTERS[1]), 42);
  assert.equal(tabCount(counts, FILTERS[2]), 7);
  assert.equal(tabCount(counts, FILTERS[3]), 11);
  assert.equal(tabCount(counts, FILTERS[4]), 5);
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(design, /class="filter-count"/);
  assert.match(design, /payload\.job_type_counts/);
  assert.match(design, /filters: \["all"\]/);
});

test("V2 labels the outer recency buckets by calendar day", () => {
  const now = new Date("2026-08-21T18:00:00Z");
  assert.deepEqual(recencyBucket({ posted_at: "2026-08-21T16:00:00Z" }, now), {
    key: "2026-08-21", label: "Opened today",
  });
  assert.deepEqual(recencyBucket({ posted_at: "2026-08-20T16:00:00Z" }, now), {
    key: "2026-08-20", label: "Opened yesterday",
  });
  assert.equal(recencyBucket({ first_seen_at: "2026-08-19T16:00:00Z" }, now).label, "Opened Aug 19");
  assert.equal(recencyBucket({}, now).label, "Date unavailable");
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(design, /class="recency-bucket"/);
});

test("V2 exposes the three bio-career focus filters", () => {
  assert.deepEqual(DEGREE_FILTERS, [
    { key: "computer_science", label: "Software" },
    { key: "mathematics", label: "Finance" },
    { key: "med_device", label: "MedTech" },
    { key: "biotech", label: "BioTech" },
    { key: "clinical_affairs", label: "Regulatory" },
  ]);
  assert.deepEqual(degreeFields(["bioengineering", "computer_science", "unknown", "med_device", "biotech", "clinical_affairs"]), [
    "computer_science",
    "med_device",
    "biotech",
    "clinical_affairs",
  ]);
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  assert.match(design, /degreeFilters: \["computer_science"\]/);
  assert.match(design, /Focus area mentioned/);
  assert.match(design, /data-degree-filter/);
  assert.match(design, /degree_field: degreeFields\(state\.degreeFilters\)/);
});

test("V2 renders canonical skill metadata and keeps legacy string compatibility", () => {
  assert.deepEqual(skillDetails({
    skills: [
      { id: "go", name: "Go", label: "Go", category: "language" },
      { id: "postgresql", name: "PostgreSQL", label: "Postgres", category: "datastore" },
      { id: "go", name: "Duplicate", category: "language" },
      { id: "broken" },
      null,
    ],
    attributes: { skills: ["Ignored fallback"] },
  }), [
    { id: "go", name: "Go", label: "Go", category: "language" },
    { id: "postgresql", name: "PostgreSQL", label: "Postgres", category: "datastore" },
  ]);
  assert.deepEqual(skillDetails({ attributes: { skills: ["Go", "Kubernetes"] } }), [
    { id: "Go", name: "Go", label: "Go", category: null },
    { id: "Kubernetes", name: "Kubernetes", label: "Kubernetes", category: null },
  ]);
});

test("V2 skill section escapes labels and stays hidden when empty", () => {
  const markup = detailSkillsMarkup({
    skills: [{ id: 'unsafe"', name: '<Full & "name">', label: "<script>alert(1)</script>", category: "stack" }],
  });
  assert.match(markup, /<div class="section-kicker">Skills<\/div>/);
  assert.match(markup, /class="skill-group" data-skill-group="unknown"/);
  assert.match(markup, /class="skill-group-title">Other</);
  assert.match(markup, /data-skill-id="unsafe&quot;"/);
  assert.match(markup, /data-skill-category="unknown"/);
  assert.match(markup, /title="&lt;Full &amp; &quot;name&quot;&gt; · Other"/);
  assert.match(markup, /aria-label="&lt;Full &amp; &quot;name&quot;&gt;, Other"/);
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script>/);
  assert.equal(detailSkillsMarkup({ skills: [] }), "");
  assert.equal(detailSkillsMarkup({ attributes: { skills: [] } }), "");
});

test("V2 job card shows the requested skill labels", () => {
  const job = { skills: [
    { id: "ai", name: "Artificial intelligence", label: "AI" },
    "Go",
    "Kubernetes",
    "PostgreSQL",
  ] };
  assert.deepEqual(skillDetails(job).map(skill => skill.name), [
    "Artificial intelligence", "Go", "Kubernetes", "PostgreSQL",
  ]);
  assert.match(detailSkillsMarkup(job), />AI<.*>Go<.*>Kubernetes<.*>PostgreSQL</);
  const tags = listTags(job);
  assert.match(tags, />AI<.*>Go<.*>Kubernetes<.*>PostgreSQL</);
  assert.doesNotMatch(tags, />\+\d+</);
});

test("V2 orders every skill by category then label and groups detail labels", () => {
  assert.deepEqual(SKILL_CATEGORIES.map(category => category.key), [
    "language", "framework", "library", "platform", "tool", "datastore",
    "protocol", "format", "hardware", "method", "concept",
  ]);
  const job = { skills: [
    { id: "ai", name: "Artificial intelligence", label: "AI", category: "concept" },
    { id: "postgresql", name: "PostgreSQL", label: "Postgres", category: "datastore" },
    { id: "python", name: "Python", label: "Python", category: "language" },
    { id: "cpp", name: "C++", label: "C++", category: "language" },
    { id: "legacy", name: "Legacy skill", label: "Legacy", category: "stack" },
    { id: "pytorch", name: "PyTorch", label: "PyTorch", category: "library" },
  ] };

  assert.deepEqual(orderedSkillDetails(job).map(skill => skill.id), [
    "cpp", "python", "pytorch", "postgresql", "ai", "legacy",
  ]);
  const markup = detailSkillsMarkup(job);
  assert.match(markup, /Languages.*C\+\+.*Python.*Libraries.*PyTorch.*Datastores.*Postgres.*Concepts.*AI.*Other.*Legacy/);
});

test("V2 card skills use fixed populated bundles with category then alphabetical ordering", () => {
  assert.deepEqual(SKILL_BUNDLES, [
    { key: "languages", label: "Languages", categories: ["language"] },
    { key: "stack", label: "Stack", categories: ["framework", "library", "datastore"] },
    { key: "systems", label: "Systems", categories: ["platform", "tool", "protocol", "format", "hardware"] },
    { key: "knowledge", label: "Knowledge", categories: ["method", "concept"] },
    { key: "other", label: "Other", categories: [] },
  ]);
  const job = { skills: [
    { id: "legacy", name: "Legacy", label: "Legacy", category: "retired" },
    { id: "postgresql", name: "PostgreSQL", label: "Postgres", category: "datastore" },
    { id: "pytorch", name: "PyTorch", label: "PyTorch", category: "library" },
    { id: "react", name: "React", label: "React", category: "framework" },
    { id: "cpp", name: "C++", label: "C++", category: "language" },
    { id: "python", name: "Python", label: "Python", category: "language" },
    { id: "git", name: "Git", label: "Git", category: "tool" },
    { id: "linux", name: "Linux", label: "Linux", category: "platform" },
    { id: "ai", name: "Artificial intelligence", label: "AI", category: "concept" },
  ] };

  assert.deepEqual(skillBundles(job).map(bundle => [bundle.key, bundle.skills.map(skill => skill.id)]), [
    ["languages", ["cpp", "python"]],
    ["stack", ["react", "pytorch", "postgresql"]],
    ["systems", ["linux", "git"]],
    ["knowledge", ["ai"]],
    ["other", ["legacy"]],
  ]);
  const markup = listTags(job);
  assert.match(markup, /data-skill-bundle="languages".*C\+\+.*Python.*data-skill-bundle="stack".*React.*PyTorch.*Postgres.*data-skill-bundle="systems".*Linux.*Git.*data-skill-bundle="knowledge".*AI.*data-skill-bundle="other".*Legacy/);
  assert.doesNotMatch(listTags({ skills: job.skills.filter(skill => ["language", "concept"].includes(skill.category)) }), /data-skill-bundle="(?:stack|systems|other)"/);
});

test("V2 card chips expose canonical identity, category, and accessible names", () => {
  const tags = listTags({ skills: [
    { id: "amazon-web-services", name: "Amazon Web Services (AWS)", label: "AWS", category: "platform" },
    { id: "python", name: "Python", label: "Python", category: "language" },
  ] });

  assert.match(tags, /^<div class="skill-bundle" data-skill-bundle="languages"><div class="skill-bundle-title">Languages<\/div><div class="skill-bundle-list"><span class="tag" data-skill-id="python" data-skill-category="language"/);
  assert.match(tags, /title="Python · Languages" aria-label="Python, Languages">Python</);
  assert.match(tags, /data-skill-id="amazon-web-services" data-skill-category="platform"/);
  assert.match(tags, /title="Amazon Web Services \(AWS\) · Platforms"/);
  assert.match(tags, /aria-label="Amazon Web Services \(AWS\), Platforms">AWS</);
});

test("V2 skill presentation uses neutral chips and restrained responsive bundle labels", () => {
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.match(styles, /\.tags \{[^}]*display: grid/);
  assert.match(styles, /\.skill-bundle \{[^}]*grid-template-columns: minmax\(62px, 72px\) minmax\(0, 1fr\)/);
  assert.match(styles, /\.skill-bundle-title \{[^}]*color: var\(--green\)[^}]*text-transform: uppercase/);
  assert.match(styles, /\.tag, \.skill \{[^}]*border: 0[^}]*background: #edeee8[^}]*color: var\(--muted\)/);
  assert.match(styles, /font: 600 10px\/1\.25 var\(--mono\)/);
  assert.match(styles, /@media \(max-width: 560px\)[\s\S]*\.skill-bundle \{ grid-template-columns: 1fr; gap: 3px; \}/);
  assert.doesNotMatch(styles, /\[data-skill-category=/);
  assert.doesNotMatch(styles, /(?:tag|skill):first-child/);
});

test("V2 formats public job metadata into reader-facing labels", () => {
  assert.equal(sourceLabel("greenhouse:example"), "Greenhouse");
  assert.equal(sourceLabel("smartrecruiters:example"), "SmartRecruiters");
  assert.equal(sourceLabel("custom_board:example"), "Custom Board");
  assert.equal(workplaceLabel("hybrid"), "Hybrid");
  assert.equal(workplaceLabel("unknown"), "");
});

test("V2 quietly reduces alternate postings to usable distinct providers", () => {
  const choices = sourceChoices({
    source: "greenhouse:acme",
    apply_url: "https://greenhouse.example/job",
    posted_at: "2026-08-18T00:00:00Z",
    other_sources: [
      { source: "greenhouse:mirror", url: "https://greenhouse.example/mirror" },
      { source: "workday:acme", url: "https://workday.example/job", posted_at: "2026-08-17T00:00:00Z" },
      { source: "lever:acme", url: "javascript:alert(1)" },
      { source: "custom_board:acme", url: "https://custom.example/job" },
      { source: "workday:older", url: "https://workday.example/older" },
    ],
  });
  assert.deepEqual(choices.map(choice => choice.label), ["Greenhouse", "Workday", "Custom Board"]);
  assert.deepEqual(choices.map(choice => choice.url), [
    "https://greenhouse.example/job",
    "https://workday.example/job",
    "https://custom.example/job",
  ]);
});

test("V2 source disclosure is absent for one provider and muted behind an accessible control", () => {
  assert.deepEqual(sourceDisclosureMarkup({
    id: 1,
    source: "greenhouse:acme",
    apply_url: "https://greenhouse.example/job",
    other_sources: [{ source: "greenhouse:mirror", url: "https://greenhouse.example/mirror" }],
  }), { inline: "", panel: "" });

  const now = new Date("2026-08-18T12:00:00Z");
  const markup = detailPostedMarkup({
    id: 'unsafe"id',
    source: "greenhouse:acme",
    apply_url: "https://greenhouse.example/job",
    posted_at: "2026-08-18T00:00:00Z",
    other_sources: [
      { source: "workday:acme", url: "https://workday.example/job", posted_at: "2026-08-17T00:00:00Z" },
    ],
  }, now);
  assert.match(markup, />2 sources<\/button>/);
  assert.match(markup, /data-action="toggle-sources"/);
  assert.match(markup, /aria-expanded="false"/);
  assert.match(markup, /aria-controls="job-sources-unsafe-id"/);
  assert.match(markup, /id="job-sources-unsafe-id"[^>]*hidden/);
  assert.match(markup, /Also available on/);
  assert.match(markup, /Workday ↗/);
  assert.match(markup, /Posted yesterday/);
  assert.doesNotMatch(markup, /duplicate|primary/i);
});

test("V2 omits the old type badge and fact-grid treatment", () => {
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.doesNotMatch(design, /Summer internship|At a glance|badgesMarkup|factsMarkup/);
  assert.doesNotMatch(styles, /detail-badge|fact-grid/);
});

test("V2 renders only the deterministic role summary", () => {
  assert.equal(
    summaryMarkup({ summary: "Build <safe> services & ship them." }),
    '<p class="role-summary">Build &lt;safe&gt; services &amp; ship them.</p>',
  );
  assert.match(summaryMarkup({ summary: null }), /Summary unavailable/);

  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.match(design, /Role summary/);
  assert.doesNotMatch(design, /job\.description|Show full description|toggle-description|expandedDescriptions/);
  assert.match(styles, /\.role-summary/);
  assert.doesNotMatch(styles, /description-shell|description-toggle|description-body/);
});

test("V2 provenance protects links and handles verification failures", () => {
  assert.equal(safeHref("https://example.com/job"), "https://example.com/job");
  assert.equal(safeHref("javascript:alert(1)"), "");
  assert.equal(relativeTime("not-a-date"), "Verification unavailable");
});

test("V2 leads the detail with the posted date, not an always-true liveness chip", () => {
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.doesNotMatch(design, /detail-freshness/);
  assert.doesNotMatch(styles, /detail-freshness/);
  assert.doesNotMatch(design, /class="provenance"/);

  // Polling makes every listing active, so the chip and its green are gone everywhere.
  assert.doesNotMatch(design, /status-chip/);
  assert.doesNotMatch(styles, /status-chip/);
  assert.doesNotMatch(design, /Active · confirmed/);
  assert.doesNotMatch(design, /live-dot/);
  assert.doesNotMatch(styles, /live-dot/);

  assert.match(styles, /\.posted-when \{[^}]*var\(--ink\)/);
  assert.match(styles, /\.posted-checked \{[^}]*var\(--faint\)/);
  assert.match(design, /detailHeadMarkup\(job\)\}<h2>/);
  assert.match(design, /detailMetaMarkup\(job\)\}\$\{detailPostedMarkup\(job\)\}<div class="detail-actions"/);
});

test("V2 detail header carries the company and the location on one line, with no initials mark", () => {
  const markup = detailHeadMarkup({ company: "Manatal", location: "Bangkok, Thailand" });
  assert.match(markup, /class="detail-headline"/);
  assert.match(markup, /class="detail-company">Manatal</);
  assert.match(markup, /class="detail-fact detail-place">.*Bangkok, Thailand/);
  assert.match(markup, /#icon-pin/);
  assert.doesNotMatch(markup, /job-logo/);

  assert.match(detailHeadMarkup({ company: "Acme" }), /Location not listed/);

  const escaped = detailHeadMarkup({ company: "<b>", location: "<i>" });
  assert.match(escaped, /&lt;b&gt;/);
  assert.match(escaped, /&lt;i&gt;/);
  assert.doesNotMatch(escaped, /<b>|<i>/);
});

test("V2 detail meta row carries every fact the header does not, shown with scan icons, and formats the apply deadline", () => {
  assert.equal(deadlineLabel("2027-01-15"), "Apply by Jan 15, 2027");
  assert.equal(deadlineLabel(""), "");
  assert.equal(deadlineLabel("not-a-date"), "");

  const job = { location: "NYC", remote: "yes", attributes: { term: "Summer 2027", deadline_at: "2027-01-15" } };
  const markup = detailMetaMarkup(job);
  assert.match(markup, /class="detail-meta"/);
  ["Summer 2027", "Remote", "Apply by Jan 15, 2027"].forEach(label => assert.match(markup, new RegExp(label)));
  ["icon-calendar", "icon-globe", "icon-clock"].forEach(icon => assert.match(markup, new RegExp(`#${icon}`)));

  // Location moved up into the header, so it is the one fact the row must not repeat.
  assert.doesNotMatch(markup, /#icon-pin/);
  assert.doesNotMatch(markup, /NYC/);
  assert.match(detailHeadMarkup(job), /NYC/);
  listFacts(job).filter(fact => fact !== job.location).forEach(fact => assert.match(markup, new RegExp(fact.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))));

  // With nothing but a location, the row disappears rather than rendering empty.
  assert.equal(detailMetaMarkup({ location: "NYC", attributes: {} }), "");
  assert.equal(detailMetaMarkup({}), "");

  const escaped = detailMetaMarkup({ attributes: { term: "<b>" } });
  assert.match(escaped, /&lt;b&gt;/);
  assert.doesNotMatch(escaped, /<b>/);
});

test("V2 card gives remote work a quiet cue distinct from skill chips", () => {
  const remote = cardMetaMarkup({
    location: "United States",
    remote: "yes",
    attributes: { term: "Summer 2027" },
  });
  assert.match(remote, /class="card-location">United States</);
  assert.match(remote, /class="workplace-cue" data-workplace="yes">/);
  assert.match(remote, /#icon-globe/);
  assert.match(remote, />Remote</);
  assert.match(remote, /class="card-term">Summer 2027</);
  assert.doesNotMatch(remote, /class="(?:tag|skill)"/);

  assert.match(cardMetaMarkup({ remote: "hybrid" }), /data-workplace="hybrid".*Hybrid/);
  assert.doesNotMatch(cardMetaMarkup({ remote: "unknown" }), /workplace-cue|icon-globe/);

  const escaped = cardMetaMarkup({ location: "<Anywhere>", remote: "yes" });
  assert.match(escaped, /&lt;Anywhere&gt;/);
  assert.doesNotMatch(escaped, /<Anywhere>/);
});

test("V2 opportunity metadata icons are defined in the shared icon sprite", () => {
  for (const page of ["../index.html", "./index.html"]) {
    const html = fs.readFileSync(require.resolve(page), "utf8");
    for (const icon of ["icon-pin", "icon-calendar", "icon-globe", "icon-clock"]) {
      assert.match(html, new RegExp(`id="${icon}"`));
    }
  }
});

test("V2 open-posting link states its source", () => {
  assert.equal(openPostingLabel({ source: "greenhouse:acme" }), "Open on Greenhouse");
  assert.equal(openPostingLabel({}), "Open posting");
  assert.equal(openPostingLabel({ source: "" }), "Open posting");
});

test("V2 posted row weights the date and trails the check only when last_seen_at is known", () => {
  const now = new Date("2026-08-18T12:00:00Z");
  const withCheck = detailPostedMarkup({ last_seen_at: "2026-08-18T07:00:00Z", posted_at: "2026-08-17T00:00:00Z" }, now);
  assert.match(withCheck, /class="posted-when">Posted yesterday</);
  assert.match(withCheck, /class="posted-checked">checked 5h ago</);

  // No check time means no trailing clause, and no dangling separator either.
  const withoutCheck = detailPostedMarkup({ posted_at: "2026-08-17T00:00:00Z" }, now);
  assert.match(withoutCheck, /class="posted-when">Posted yesterday</);
  assert.doesNotMatch(withoutCheck, /posted-checked|posted-sep/);

  // No posted_at falls back to when we first saw it.
  assert.match(detailPostedMarkup({ first_seen_at: "2026-08-17T00:00:00Z" }, now), /Added yesterday/);
});

test("V2 list card leads with company, drops the left accent, and states the posted date", () => {
  assert.equal(initials({ company: "SAP" }), "SA");
  assert.equal(initials({ company: "Jane Street" }), "JS");
  assert.equal(initials({ company: "" }), "?");
  assert.equal(initials({}), "?");

  const now = new Date("2026-08-18T12:00:00Z");
  assert.equal(
    cardStatusLine({ last_seen_at: "2026-08-18T07:00:00Z", posted_at: "2026-08-17T00:00:00Z" }, now),
    "Posted yesterday",
  );
  assert.equal(cardStatusLine({ posted_at: "2026-08-17T00:00:00Z" }, now), "Posted yesterday");
  assert.equal(cardStatusLine({}, now), "Date unavailable");

  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
  assert.match(design, /class="job-company-line"><div class="job-company">\$\{esc\(job\.company\)\}<\/div>.*<\/div><h3>\$\{esc\(job\.title\)\}<\/h3>/);
  assert.doesNotMatch(design, /class="job-title-line"/);
  assert.doesNotMatch(styles, /\.job-title-line/);
  assert.doesNotMatch(styles, /inset 3px 0 0/);
  assert.match(styles, /\.opportunity\.selected \{[^}]*border-color: var\(--green\)/);
});

test("V2 is the main Opportunities design and exposes accessible source disclosure", () => {
  const main = fs.readFileSync(require.resolve("../index.html"), "utf8");
  const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
  const shell = fs.readFileSync(require.resolve("./index.html"), "utf8");
  assert.match(main, /opportunities-v2\/styles\.css\?v=skill-bundles-/);
  assert.match(main, /opportunities-v2\/app\.js\?v=skill-bundles-/);
  assert.doesNotMatch(design, /Design preview|View current Opportunities/);
  assert.match(design, /aria-controls=/);
  assert.match(design, /aria-expanded=/);
  assert.match(design, /data-action="toggle-sources"/);
  assert.doesNotMatch(design, /Show full description|Show less/);
  assert.match(shell, /opportunities-v2\/styles\.css\?v=skill-bundles-/);
  assert.match(shell, /opportunities-v2\/app\.js\?v=skill-bundles-/);
});
