import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("./preferences.js", import.meta.url), "utf8");

function browser(entries) {
  const values = new Map(entries);
  const context = {
    document: { documentElement: { dataset: {} } },
    localStorage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
  };
  return { values, context, load: () => runInNewContext(source, context) };
}

test("legacy preferences survive the rename and a second page load", () => {
  const page = browser([["inspector-theme", "dark"], ["inspector-list-width", "480"]]);
  page.load();
  page.load();
  assert.equal(page.context.document.documentElement.dataset.theme, "dark");
  assert.equal(page.values.get("console-list-width"), "480");
  assert.equal(page.values.has("inspector-theme"), false);
  assert.equal(page.values.has("inspector-list-width"), false);
});

test("preferences saved under console names take precedence", () => {
  const page = browser([
    ["inspector-theme", "dark"], ["console-theme", "light"],
    ["inspector-list-width", "480"], ["console-list-width", "600"],
  ]);
  page.load();
  assert.equal(page.context.document.documentElement.dataset.theme, "light");
  assert.equal(page.values.get("console-list-width"), "600");
});

test("a new browser keeps its default theme and unrelated preferences", () => {
  const page = browser([["other-setting", "value"]]);
  page.load();
  assert.equal(page.context.document.documentElement.dataset.theme, undefined);
  assert.deepEqual([...page.values], [["other-setting", "value"]]);
});
