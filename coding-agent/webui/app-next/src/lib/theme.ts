export const THEME_LS_KEY = "coding-agent-webui-theme";

export type Theme = "dark" | "light";

export function readStoredTheme(): Theme {
  try {
    return localStorage.getItem(THEME_LS_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_LS_KEY, theme);
  } catch {
    /* quota / private mode: theme still applies for this page */
  }
}

export function toggleTheme(current: Theme): Theme {
  return current === "dark" ? "light" : "dark";
}
