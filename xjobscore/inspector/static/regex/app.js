import { el, formatCount } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";
import { applyOrderedRules, guideStartsOpen, inputLines } from "/static/regex/model.mjs";

renderNav("Regex");

const RULES_KEY = "inspector-regex-rules";
const INPUT_KEY = "inspector-regex-input";
const GUIDE_KEY = "inspector-regex-guide";
const INPUT_STORAGE_LIMIT = 1_000_000;

const inputEditor = document.getElementById("input-editor");
const inputCount = document.getElementById("input-count");
const rulesEl = document.getElementById("rules");
const addRuleBtn = document.getElementById("add-rule-btn");
const applyBtn = document.getElementById("apply-btn");
const runStatus = document.getElementById("run-status");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");
const tabsEl = document.getElementById("result-tabs");
const resultOutput = document.getElementById("result-output");
const resultSummary = document.getElementById("result-summary");
const copyBtn = document.getElementById("copy-btn");
const regexGuide = document.getElementById("regex-guide");

let nextRuleId = Date.now();

const state = {
  rules: loadRules(),
  result: null,
  activePane: "remaining",
  errorRuleIndex: null,
};

inputEditor.value = loadInput();
if (state.rules.length === 0) state.rules.push(newRule());
restoreGuidePreference();

renderRules();
updateInputCount();
renderResults();

inputEditor.addEventListener("input", () => {
  updateInputCount();
  persistInput();
  invalidateResults();
});

addRuleBtn.addEventListener("click", () => {
  state.rules.push(newRule());
  persistRules();
  invalidateResults();
  renderRules();
  rulesEl.querySelector(".regex-rule:last-child .regex-pattern")?.focus();
});

applyBtn.addEventListener("click", applyRules);

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    applyRules();
  }
});

copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(resultOutput.value);
    copyBtn.textContent = "Copied";
    setTimeout(() => { copyBtn.textContent = "Copy pane"; }, 1200);
  } catch {
    resultOutput.select();
    document.execCommand("copy");
  }
});

regexGuide.addEventListener("toggle", () => {
  try {
    localStorage.setItem(GUIDE_KEY, regexGuide.open ? "open" : "closed");
  } catch {
    // The native details control still works when storage is unavailable.
  }
});

function newRule() {
  return { id: `rule-${nextRuleId++}`, pattern: "", flags: "i" };
}

function loadRules() {
  try {
    const parsed = JSON.parse(localStorage.getItem(RULES_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((rule) => rule && typeof rule === "object")
      .map((rule) => ({
        id: String(rule.id ?? `rule-${nextRuleId++}`),
        pattern: String(rule.pattern ?? ""),
        flags: String(rule.flags ?? "i"),
      }));
  } catch {
    return [];
  }
}

function loadInput() {
  try {
    return localStorage.getItem(INPUT_KEY) ?? "";
  } catch {
    return "";
  }
}

function restoreGuidePreference() {
  try {
    regexGuide.open = guideStartsOpen(localStorage.getItem(GUIDE_KEY));
  } catch {
    regexGuide.open = true;
  }
}

function persistRules() {
  try {
    localStorage.setItem(RULES_KEY, JSON.stringify(state.rules));
  } catch {
    // The workbench remains usable when browser storage is unavailable.
  }
}

function persistInput() {
  try {
    if (inputEditor.value.length <= INPUT_STORAGE_LIMIT) {
      localStorage.setItem(INPUT_KEY, inputEditor.value);
    } else {
      localStorage.removeItem(INPUT_KEY);
    }
  } catch {
    // Large experiments can exceed localStorage; the textarea is still live.
  }
}

function updateInputCount() {
  const count = inputLines(inputEditor.value).length;
  inputCount.textContent = `${formatCount(count)} ${count === 1 ? "line" : "lines"}`;
}

function invalidateResults() {
  state.result = null;
  state.errorRuleIndex = null;
  errorBanner.hidden = true;
  runStatus.textContent = "Changes not applied";
  renderResults();
  clearRuleResults();
}

function clearRuleResults() {
  for (const node of rulesEl.querySelectorAll(".regex-rule-result")) {
    node.textContent = "";
  }
  for (const node of rulesEl.querySelectorAll(".regex-rule")) {
    node.dataset.error = "false";
  }
}

function renderRules() {
  rulesEl.replaceChildren(
    ...state.rules.map((rule, index) => {
      const pattern = el("input", {
        class: "regex-pattern",
        type: "text",
        value: rule.pattern,
        placeholder: "Regex source",
        spellcheck: "false",
        autocomplete: "off",
        "aria-label": `Regex ${index + 1} pattern`,
        oninput: (event) => {
          rule.pattern = event.target.value;
          persistRules();
          invalidateResults();
        },
      });
      const flags = el("input", {
        class: "regex-flags",
        type: "text",
        value: rule.flags,
        placeholder: "flags",
        spellcheck: "false",
        autocomplete: "off",
        title: "JavaScript flags, e.g. i, im, gi",
        "aria-label": `Regex ${index + 1} flags`,
        oninput: (event) => {
          rule.flags = event.target.value;
          persistRules();
          invalidateResults();
        },
      });
      const result = el("div", { class: "regex-rule-result" });
      const row = el(
        "div",
        {
          class: "regex-rule",
          "data-error": state.errorRuleIndex === index ? "true" : "false",
          dataset: { ruleId: rule.id },
        },
        el("div", { class: "regex-rule-label" }, `Regex ${index + 1}`),
        pattern,
        flags,
        el(
          "div",
          { class: "regex-rule-actions" },
          iconButton("↑", "Move earlier", index === 0, () => moveRule(index, -1)),
          iconButton("↓", "Move later", index === state.rules.length - 1, () => moveRule(index, 1)),
          iconButton("×", "Remove regex", false, () => removeRule(index))
        ),
        result
      );
      if (state.errorRuleIndex === index) result.className = "regex-rule-error";
      return row;
    })
  );
  renderRuleResults();
}

function iconButton(label, title, disabled, onclick) {
  return el("button", {
    class: "regex-icon-btn",
    type: "button",
    title,
    disabled,
    onclick,
  }, label);
}

function moveRule(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= state.rules.length) return;
  [state.rules[index], state.rules[target]] = [state.rules[target], state.rules[index]];
  persistRules();
  invalidateResults();
  renderRules();
}

function removeRule(index) {
  state.rules.splice(index, 1);
  if (state.rules.length === 0) state.rules.push(newRule());
  persistRules();
  invalidateResults();
  renderRules();
}

function applyRules() {
  errorBanner.hidden = true;
  state.errorRuleIndex = null;
  try {
    const values = inputLines(inputEditor.value);
    state.result = applyOrderedRules(values, state.rules);
    state.activePane = "remaining";
    const matched = state.result.inputCount - state.result.remaining.length;
    runStatus.textContent = `${formatCount(matched)} matched · ${formatCount(state.result.remaining.length)} remaining`;
    renderRules();
    renderResults();
  } catch (error) {
    state.result = null;
    state.errorRuleIndex = Number.isInteger(error.ruleIndex) ? error.ruleIndex : null;
    errorText.textContent = error.message;
    errorBanner.hidden = false;
    runStatus.textContent = "Regex error";
    renderRules();
    renderResults();
  }
}

function renderRuleResults() {
  if (state.errorRuleIndex != null) {
    const node = rulesEl.children[state.errorRuleIndex]?.querySelector(".regex-rule-error");
    if (node) node.textContent = "Fix this expression before applying the sequence.";
    return;
  }
  if (!state.result) return;
  for (const stage of state.result.stages) {
    const node = rulesEl.children[stage.index]?.querySelector(".regex-rule-result");
    if (!node) continue;
    node.textContent = stage.pattern
      ? `${formatCount(stage.caught.length)} caught · ${formatCount(stage.remainingCount)} remaining`
      : `Empty regex skipped · ${formatCount(stage.remainingCount)} remaining`;
  }
}

function renderResults() {
  if (!state.result) {
    tabsEl.replaceChildren();
    resultOutput.value = "";
    resultSummary.textContent = "Apply rules to inspect results.";
    copyBtn.disabled = true;
    return;
  }

  const panes = [
    ...state.result.stages.map((stage, index) => ({
      key: `rule:${stage.id}`,
      label: `Regex ${index + 1}`,
      values: stage.caught,
      summary: `${formatCount(stage.caught.length)} caught at stage ${index + 1} · ${formatCount(stage.remainingCount)} remained afterward`,
    })),
    {
      key: "remaining",
      label: "Remaining",
      values: state.result.remaining,
      summary: `${formatCount(state.result.remaining.length)} of ${formatCount(state.result.inputCount)} input lines remain unmatched`,
    },
  ];

  if (!panes.some((pane) => pane.key === state.activePane)) state.activePane = "remaining";
  tabsEl.replaceChildren(
    ...panes.map((pane) =>
      el(
        "button",
        {
          class: "tab",
          type: "button",
          role: "tab",
          "aria-selected": pane.key === state.activePane ? "true" : "false",
          onclick: () => {
            state.activePane = pane.key;
            renderResults();
          },
        },
        pane.label,
        el("span", { class: "count" }, formatCount(pane.values.length))
      )
    )
  );

  const active = panes.find((pane) => pane.key === state.activePane);
  resultOutput.value = active.values.join("\n");
  resultSummary.textContent = active.summary;
  copyBtn.disabled = active.values.length === 0;
}
