import assert from "node:assert/strict";
import test from "node:test";

import { applyOrderedRules, compileRules, guideStartsOpen, inputLines } from "./model.mjs";

test("rules consume only the values left by earlier rules", () => {
  const result = applyOrderedRules(
    ["remote us", "remote uk", "new york", "london"],
    [
      { id: "one", pattern: "remote", flags: "i" },
      { id: "two", pattern: "us|new york", flags: "i" },
      { id: "three", pattern: "london", flags: "i" },
    ]
  );

  assert.deepEqual(result.stages[0].caught, ["remote us", "remote uk"]);
  assert.deepEqual(result.stages[1].caught, ["new york"]);
  assert.deepEqual(result.stages[2].caught, ["london"]);
  assert.deepEqual(result.remaining, []);
});

test("an empty rule catches nothing and does not consume input", () => {
  const result = applyOrderedRules(
    ["alpha", "beta"],
    [
      { id: "one", pattern: "", flags: "i" },
      { id: "two", pattern: "beta", flags: "i" },
    ]
  );

  assert.deepEqual(result.stages[0].caught, []);
  assert.equal(result.stages[0].remainingCount, 2);
  assert.deepEqual(result.stages[1].caught, ["beta"]);
  assert.deepEqual(result.remaining, ["alpha"]);
});

test("global expressions are reset for every input value", () => {
  const result = applyOrderedRules(
    ["alpha", "alpha", "alpha"],
    [{ id: "one", pattern: "alpha", flags: "g" }]
  );
  assert.equal(result.stages[0].caught.length, 3);
});

test("invalid regex reports its ordered rule", () => {
  assert.throws(
    () => compileRules([
      { id: "one", pattern: "ok", flags: "i" },
      { id: "two", pattern: "(", flags: "i" },
    ]),
    (error) => error.ruleIndex === 1 && /Regex 2/.test(error.message)
  );
});

test("input is newline-delimited, preserves whitespace, and ignores empty lines", () => {
  assert.deepEqual(inputLines("alpha\r\n beta \n\n"), ["alpha", " beta "]);
});

test("the guide opens by default and restores an explicit collapse", () => {
  assert.equal(guideStartsOpen(null), true);
  assert.equal(guideStartsOpen("open"), true);
  assert.equal(guideStartsOpen("closed"), false);
});
