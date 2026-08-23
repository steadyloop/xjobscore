export function inputLines(text) {
  return String(text ?? "")
    .split(/\r?\n/)
    .filter((line) => line.length > 0);
}

export function guideStartsOpen(storedPreference) {
  return storedPreference !== "closed";
}

export function compileRules(rules) {
  return rules.map((rule, index) => {
    const pattern = String(rule.pattern ?? "");
    const flags = String(rule.flags ?? "");
    if (!pattern) {
      return { ...rule, index, pattern, flags, regex: null };
    }
    try {
      return { ...rule, index, pattern, flags, regex: new RegExp(pattern, flags) };
    } catch (error) {
      const problem = new Error(`Regex ${index + 1}: ${error.message}`);
      problem.ruleIndex = index;
      throw problem;
    }
  });
}

function matches(regex, value) {
  // Global and sticky expressions carry state between test() calls. Resetting
  // makes them behave like every other rule without silently changing flags.
  regex.lastIndex = 0;
  return regex.test(value);
}

export function applyOrderedRules(values, rules) {
  let remaining = [...values];
  const stages = [];

  for (const rule of compileRules(rules)) {
    const caught = [];
    const next = [];
    if (rule.regex) {
      for (const value of remaining) {
        (matches(rule.regex, value) ? caught : next).push(value);
      }
      remaining = next;
    }
    stages.push({
      id: rule.id,
      index: rule.index,
      pattern: rule.pattern,
      flags: rule.flags,
      caught,
      remainingCount: remaining.length,
    });
  }

  return { inputCount: values.length, stages, remaining };
}
