/* Restore saved preferences before the first paint on every console page. */
(() => {
  for (const [oldKey, newKey] of [
    ["inspector-theme", "console-theme"],
    ["inspector-list-width", "console-list-width"],
  ]) {
    const saved = localStorage.getItem(oldKey);
    if (saved !== null) {
      if (localStorage.getItem(newKey) === null) localStorage.setItem(newKey, saved);
      localStorage.removeItem(oldKey);
    }
  }
  const theme = localStorage.getItem("console-theme");
  if (theme) document.documentElement.dataset.theme = theme;
})();
