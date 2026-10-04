// Light/dark theme: follows the OS by default, remembers a manual choice when storage allows.
(function () {
  const KEY = "handnotes-theme";
  const root = document.documentElement;

  function stored() {
    try { return localStorage.getItem(KEY); } catch (error) { return null; }
  }

  function remember(theme) {
    try { localStorage.setItem(KEY, theme); } catch (error) { /* private mode: theme just won't persist */ }
  }

  const saved = stored();
  if (saved === "light" || saved === "dark") root.dataset.theme = saved;

  function currentTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-theme-toggle]")) return;
    const next = currentTheme() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    remember(next);
  });
})();
