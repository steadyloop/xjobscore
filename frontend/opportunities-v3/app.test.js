const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const { initials } = require("./app.js");

const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
const shell = fs.readFileSync(require.resolve("./index.html"), "utf8");
const design = fs.readFileSync(require.resolve("./app.js"), "utf8");
const coverage = fs.readFileSync(require.resolve("../source-coverage/styles.css"), "utf8");

test("V3 carries no match-count stats line", () => {
  assert.doesNotMatch(shell, /page-stats/);
  assert.doesNotMatch(design, /statsLine|renderStats|pageStats/);
  assert.doesNotMatch(styles, /\.page-stats|\.stat-sep|\.stat-add/);
});

test("V3 is self-contained and never reaches into the live v2 page", () => {
  // Prose may name the live page; a load path must never point at it.
  for (const file of [styles, shell, design]) {
    assert.doesNotMatch(file, /(?:href|src|url\()\s*=?\s*["'(]?[^"')\n]*opportunities-v2/);
  }
  assert.match(shell, /opportunities-v3\/styles\.css\?v=coverage-look-/);
  assert.match(shell, /opportunities-v3\/app\.js\?v=coverage-look-/);
  // navigation.css loads last so it keeps overriding the page stylesheet.
  assert.ok(shell.indexOf("opportunities-v3/styles.css") < shell.indexOf("navigation.css"));
});

test("V3 carries the source-coverage palette and typeface verbatim", () => {
  for (const token of ["--canvas: #f4f3ee", "--surface: #fffefa", "--surface-muted: #edeee8",
                       "--subtle: #7b837d", "--line: #dddfd6", "--line-strong: #c6cabf"]) {
    assert.match(styles, new RegExp(token.replace(/[-[\]{}()*+?.,\\^$|#]/g, "\\$&")), token);
    assert.match(coverage, new RegExp(token.replace(/[-[\]{}()*+?.,\\^$|#]/g, "\\$&")), `${token} drifted from source-coverage`);
  }
  assert.match(styles, /--sans: Inter, ui-sans-serif/);
  // The v2 token names must not survive the port, or the two systems read as one.
  assert.doesNotMatch(styles, /--paper:|--ground:|--faint:/);
});

test("V3 header borrows the page-header baseline and mono clock", () => {
  assert.match(styles, /\.page-header \{[^}]*align-items: baseline/);
  assert.match(styles, /\.page-header h1 \{[^}]*font-size: 24px/);
  assert.match(styles, /\.clock \{[^}]*font: 600 12px var\(--mono\)/);
  // Both nodes are static shell, outside #view, so paint() cannot orphan the interval.
  const header = shell.indexOf('id="clock"'), view = shell.indexOf('id="view"');
  assert.ok(header > -1 && view > -1 && header < view);
  assert.match(design, /setInterval\(updateClock, 1000\)/);
  // The 32px green-kicker head from v2 is gone.
  assert.doesNotMatch(design, /class="discover-page-head"/);
  assert.doesNotMatch(styles, /discover-page-head/);
});

test("V3 controls stack degree focus above job type, with no heading of their own", () => {
  assert.match(styles, /\.section-header \{[^}]*border-bottom: 1px solid var\(--line\)/);
  assert.match(styles, /\.section-controls \{[^}]*display: grid/);
  // No "Results" heading, and no green kicker in its place either.
  assert.doesNotMatch(design, /<h2>Results<\/h2>/);
  assert.doesNotMatch(styles, /\.section-header h2/);
  assert.match(design, /<header class="section-header" aria-label="Opportunity filters">/);
  assert.doesNotMatch(styles, /\.section-kicker \{[^}]*var\(--green\)/);
  // Degree focus narrows the set the job-type tabs then count, so it renders first.
  const controls = /<div class="section-controls">([\s\S]*?)<\/header>/.exec(design)[1];
  assert.ok(controls.indexOf("degree-filterbar") < controls.indexOf("discover-filterbar"), "degree filters must precede job-type tabs");
});

test("V3 list rows are hairline-ruled, not cards", () => {
  assert.match(styles, /\.opportunity \{[^}]*grid-template-columns: 158px minmax\(0, 1fr\)/);
  assert.match(styles, /\.opportunity \{[^}]*border-bottom: 1px solid var\(--line\)/);
  assert.match(styles, /\.opportunity \{[^}]*border-radius: 0/);
  assert.match(styles, /\.opportunity\.selected \{[^}]*box-shadow: inset 2px 0 0 var\(--ink\)/);
  // No radius, no shadow, no hover lift anywhere on a row.
  assert.doesNotMatch(styles, /\.opportunity[^{]*\{[^}]*transform: translateY/);
  assert.doesNotMatch(styles, /\.opportunity \{[^}]*box-shadow: 0/);
  // The company is the fixed label column; the logo tile is gone.
  assert.match(design, /<div class="job-company-line"><div class="job-company">/);
  assert.doesNotMatch(design, /class="job-logo"/);
  assert.doesNotMatch(styles, /\.job-logo/);
});

test("V3 keeps initials exported even though the tile no longer renders", () => {
  assert.equal(initials({ company: "Jane Street" }), "JS");
});

test("V3 breaks at the same width navigation.css does", () => {
  assert.match(styles, /@media \(max-width: 850px\)/);
  assert.doesNotMatch(styles, /@media \(max-width: 8[012]0px\)/);
});

test("V3 is linked from every shell and never steals the active state from /", () => {
  for (const page of ["../index.html", "../opportunities-v2/index.html", "../application/index.html",
                      "../resume/index.html", "../source-coverage/index.html"]) {
    const html = fs.readFileSync(require.resolve(page), "utf8");
    assert.match(html, /href="\/opportunities-v3\/"[^>]*>.*Opportunities \(v2\)/, page);
    assert.doesNotMatch(html, /class="active" href="\/opportunities-v3\/"/, page);
  }
  assert.match(shell, /class="active" href="\/opportunities-v3\/" aria-current="page"/);
});

test("V3 metadata icons are defined in its own sprite", () => {
  for (const icon of ["icon-search", "icon-pin", "icon-calendar", "icon-globe", "icon-clock", "icon-compass"]) {
    assert.match(shell, new RegExp(`id="${icon}"`), icon);
  }
});
