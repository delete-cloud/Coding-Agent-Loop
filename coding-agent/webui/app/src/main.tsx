import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// Apply the persisted theme before first paint to avoid a flash of the wrong theme.
try {
  const stored = localStorage.getItem("coding-agent-webui-theme");
  document.documentElement.dataset.theme = stored === "light" ? "light" : "dark";
} catch {
  document.documentElement.dataset.theme = "dark";
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
