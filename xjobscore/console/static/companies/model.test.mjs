import test from "node:test";
import assert from "node:assert/strict";

import { boardUrl, guessDomain, isAutoDomain, links, readable, splitToken, toRow } from "./model.mjs";

/* The shape /api/companies actually returns (see companies._shape). */
const apiRow = (over = {}) => ({
  id: 41, name: "Tines", domain: null, description: null, tags: {},
  review_state: "unverified", review_at: null, active_postings: 25,
  alias_count: 2, has_logo: false, tokens: ["greenhouse:tines"],
  sample_title: "Software Engineer, Intern",
  sample_apply_url: "https://job-boards.greenhouse.io/tines/jobs/1",
  sample_source_url: "https://job-boards.greenhouse.io/tines/jobs/1",
  ...over,
});

test("an api row becomes an editable row with a board link", () => {
  const row = toRow(apiRow());
  assert.equal(row.id, 41);
  assert.equal(row.domain, "", "null becomes an empty box, not the string 'null'");
  assert.equal(row.board, "https://job-boards.greenhouse.io/tines");
  assert.deepEqual(row.tokens, [{ ats: "greenhouse", token: "tines" }]);
});

test("has_logo survives the row conversion", () => {
  assert.equal(toRow(apiRow()).hasLogo, false);
  assert.equal(toRow(apiRow({ has_logo: true })).hasLogo, true);
});

test("automatic provenance labels only the successfully added current domain", () => {
  const tags = { domain_guess: { candidate: "tines.com", added: true } };
  const row = toRow(apiRow({ domain: "tines.com", tags }));
  assert.equal(isAutoDomain(row), true);
  row.domain = "corrected.example";
  assert.equal(isAutoDomain(row), false, "typing a correction hides the label");
  assert.equal(isAutoDomain(toRow(apiRow({ domain: "corrected.example", tags }))), false);
  assert.equal(isAutoDomain(toRow(apiRow({ domain: "tines.com" }))), false);
  assert.equal(isAutoDomain(toRow(apiRow({
    domain: "tines.com", tags: { domain_guess: { candidate: "tines.com", added: false } },
  }))), false);
  assert.equal(isAutoDomain(toRow(apiRow({ tags }))), false, "cleared domains have no label");
});

test("tokens split on the first colon only", () => {
  assert.deepEqual(splitToken("workday:nvidia/wd5/Site"), { ats: "workday", token: "nvidia/wd5/Site" });
  assert.equal(splitToken("nocolon"), null);
});

test("board urls are rebuilt from the token, or absent", () => {
  assert.equal(boardUrl("lever", "palantir"), "https://jobs.lever.co/palantir");
  assert.equal(boardUrl("workable", "deliveroo"), "https://apply.workable.com/deliveroo/");
  assert.equal(boardUrl("workday", "nvidia/wd5/Ext"), "https://nvidia.wd5.myworkdayjobs.com/en-US/Ext");
  assert.equal(boardUrl("oracle", "egcu.fa.us6/cx"),
    "https://egcu.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/cx");
  assert.equal(boardUrl("workday", "nvidia"), null, "a half token builds nothing");
  assert.equal(boardUrl("smartrecruiters", "acme"), null);
});

test("a company with no token still gets what links it can", () => {
  const row = toRow(apiRow({ tokens: [], name: "Acme Bio" }));
  assert.equal(row.board, null);
  assert.deepEqual(links(row).map((l) => l.label), ["A job", "Search"]);
});

test("greenhouse gets the name endpoint; others do not", () => {
  assert.deepEqual(links(toRow(apiRow())).map((l) => l.label),
    ["Board", "Name (API)", "A job", "Search"]);
  const ashby = toRow(apiRow({
    tokens: ["ashby:bolt-farm"], name: null,
    sample_apply_url: null, sample_source_url: null,
  }));
  assert.deepEqual(links(ashby).map((l) => l.label), ["Board", "Search"]);
});

test("search uses the name, else the slug, else the sample title", () => {
  assert.match(links(toRow(apiRow())).at(-1).href, /q=Tines%20careers/);
  const slug = toRow(apiRow({ name: null, tokens: ["ashby:bolt-farm"] }));
  assert.match(links(slug).at(-1).href, /bolt%20farm/);
  const shard = toRow(apiRow({ name: null, tokens: ["oracle:egcu.fa.us6/cx"] }));
  assert.match(links(shard).at(-1).href, /Software%20Engineer/);
});

test("readable rejects vendor shards", () => {
  assert.equal(readable("bolt-farm"), "bolt farm");
  assert.equal(readable("egcu.fa.us6/cx"), null);
  assert.equal(readable("alleima/wd3/x"), "alleima");
});

test("guessDomain strips legal words and punctuation", () => {
  assert.equal(guessDomain("Masimo Corporation"), "masimo.com");
  assert.equal(guessDomain("LMAX Group"), "lmax.com");
  assert.equal(guessDomain("Vaayu Tech"), "vaayutech.com");
  assert.equal(guessDomain(null), "");
});
