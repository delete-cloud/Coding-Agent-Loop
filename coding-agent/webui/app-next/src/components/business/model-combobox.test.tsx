import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { ModelCombobox, ModelPicker } from "@/components/business/model-combobox";

const labeled = "codex:kina0630test-gmail-com";
const globalsCss = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../../app/globals.css"),
  "utf8",
);

function renderPicker(
  overrides: Partial<Parameters<typeof ModelPicker>[0]> = {},
) {
  const onProviderChange = overrides.onProviderChange ?? vi.fn();
  const onModelChange = overrides.onModelChange ?? vi.fn();
  const result = render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <ModelPicker
        provider="anthropic"
        model="claude-sonnet-4"
        oauthProviders={[]}
        accounts={[]}
        models={["claude-sonnet-4", "claude-opus-4"]}
        status="ready"
        {...overrides}
        onProviderChange={onProviderChange}
        onModelChange={onModelChange}
      />
    </NextIntlClientProvider>,
  );
  return { ...result, onProviderChange, onModelChange };
}

function trigger(): HTMLButtonElement {
  const el = document.querySelector(".model-picker-trigger");
  if (!(el instanceof HTMLButtonElement)) {
    throw new Error("missing model picker trigger");
  }
  return el;
}

describe("ModelPicker open-on-click list", () => {
  it("opens the full remote model list when the seat is clicked", () => {
    renderPicker();

    expect(screen.queryByRole("option", { name: "claude-opus-4" })).toBeNull();
    fireEvent.click(trigger());
    expect(screen.getByRole("option", { name: "claude-sonnet-4" })).toBeDefined();
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
  });

  it("shows every remote model with no search field in the popup", () => {
    renderPicker();
    fireEvent.click(trigger());

    expect(screen.getByRole("option", { name: "claude-sonnet-4" })).toBeDefined();
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
    expect(document.querySelector(".model-picker-search")).toBeNull();
    expect(document.querySelector(".model-picker-menu input")).toBeNull();
    expect(screen.queryByLabelText(zhMessages.composer.searchModels)).toBeNull();
  });

  it("commits the selected remote model", () => {
    const { onModelChange } = renderPicker();
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("option", { name: "claude-opus-4" }));

    expect(onModelChange).toHaveBeenCalledTimes(1);
    expect(onModelChange).toHaveBeenCalledWith("claude-opus-4");
    expect(screen.queryByRole("option", { name: "claude-opus-4" })).toBeNull();
  });
});

describe("ModelPicker providers", () => {
  it("lists connected Codex accounts by label and hides bare codex", () => {
    renderPicker({
      provider: "codex",
      model: "gpt-5.4",
      oauthProviders: [labeled],
      accounts: [{ provider: labeled, label: "kina" }],
      models: ["gpt-5.4"],
    });

    fireEvent.click(trigger());

    expect(trigger().textContent).toContain("kina");
    expect(trigger().textContent).not.toContain("kina0630test-gmail-com");
    expect(screen.getByRole("option", { name: "Codex · kina" })).toBeDefined();
    expect(screen.queryByRole("option", { name: "codex" })).toBeNull();
    expect(screen.queryByRole("option", { name: labeled })).toBeNull();
  });

  it("commits a connected labeled Codex account from the provider list", () => {
    const { onProviderChange } = renderPicker({
      provider: "anthropic",
      oauthProviders: [labeled],
      accounts: [{ provider: labeled, label: "kina" }],
    });

    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("option", { name: "Codex · kina" }));

    expect(onProviderChange).toHaveBeenCalledWith(labeled);
  });
});

describe("ModelPicker remote-only catalog", () => {
  it("does not invent preset models when the remote list is empty", () => {
    renderPicker({ models: [], status: "ready" });
    fireEvent.click(trigger());

    expect(screen.getByText(zhMessages.settings.modelsEmpty)).toBeDefined();
    expect(screen.queryByRole("option", { name: "claude-sonnet-4" })).toBeNull();
    expect(screen.queryByRole("option", { name: "gpt-5.4" })).toBeNull();
  });
});

describe("ModelPicker chip", () => {
  it("shows the model name with an optional provider suffix", () => {
    renderPicker();

    const name = trigger().querySelector(".model-picker-model");
    const suffix = trigger().querySelector(".model-picker-provider-label");
    expect(name?.textContent).toBe("claude-sonnet-4");
    expect(suffix?.textContent).toBe("anthropic");
    expect(name && suffix ? name.compareDocumentPosition(suffix) & Node.DOCUMENT_POSITION_FOLLOWING : 0).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(trigger().getAttribute("aria-label")).toContain("claude-sonnet-4");
  });

  it("opens the popup above a compact chip", () => {
    renderPicker();
    fireEvent.click(trigger());

    const menu = document.querySelector(".model-picker-menu");
    expect(menu).not.toBeNull();
    expect(trigger().closest(".model-picker")?.contains(menu)).toBe(true);

    const pickerRule = globalsCss.match(/\.model-picker\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(pickerRule).not.toMatch(/flex:\s*1\b/);
    const menuRule = globalsCss.match(/\.model-picker-menu\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(menuRule).toMatch(/bottom:\s*calc\(100%/);
    expect(menuRule).not.toMatch(/(?:^|[^-])top:/);
    expect(menuRule).toContain("var(--bg-raise)");
    expect(menuRule).toContain("var(--hairline)");
    expect(menuRule).toContain("var(--radius)");
    expect(menuRule).not.toContain("var(--amber");
    expect(globalsCss).not.toMatch(/\.model-picker-search\b/);

    const triggerRule = globalsCss.match(/\.model-picker-trigger\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(triggerRule).toContain("transparent");
    expect(triggerRule).not.toMatch(/border:\s*1px solid var\(--hairline/);
    expect(triggerRule).not.toMatch(/background:\s*var\(--bg\)/);

    const selectedRule =
      globalsCss.match(
        /\.model-picker-models \[role="option"\]:hover,\s*\.model-picker-models \[role="option"\]\[aria-selected="true"\]\s*\{([^}]*)\}/,
      )?.[1] ?? "";
    expect(selectedRule).toContain("var(--bg-hover)");
    expect(selectedRule).not.toContain("var(--amber");

    expect(globalsCss).toMatch(/\.model-picker-(?:providers|models)[^{]*\{[^}]*scrollbar-width:\s*thin/);
  });

  it("gives picker columns a definite max-height so long catalogs scroll", () => {
    const columnRule =
      globalsCss.match(
        /\.model-picker-providers,\s*\.model-picker-models\s*\{([^}]*)\}/,
      )?.[1] ?? "";
    expect(columnRule).toMatch(/max-height:\s*280px/);
    expect(globalsCss).toMatch(/\.model-picker-providers\s*\{[^}]*overflow-y:\s*auto/);
    expect(globalsCss).toMatch(/\.model-picker-models\s*\{[^}]*overflow-y:\s*auto/);
  });

  it("exposes model rows as listbox options, not implicit list items", () => {
    renderPicker();
    fireEvent.click(trigger());

    const rows = document.querySelectorAll(".model-picker-models > li");
    expect(rows.length).toBe(2);
    for (const row of rows) {
      expect(["option", "presentation"]).toContain(row.getAttribute("role"));
    }
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
  });
});

describe("ModelCombobox", () => {
  it("opens the full remote list on click and filters as the user types", () => {
    function Harness() {
      const [value, setValue] = useState("claude-sonnet-4");
      return (
        <>
          <label htmlFor="settings-model">{zhMessages.settings.model}</label>
          <ModelCombobox
            id="settings-model"
            value={value}
            onChange={setValue}
            models={["claude-sonnet-4", "claude-opus-4"]}
            status="ready"
          />
        </>
      );
    }
    render(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <Harness />
      </NextIntlClientProvider>,
    );

    const input = screen.getByLabelText(zhMessages.settings.model);
    fireEvent.click(input);
    expect(screen.getByRole("option", { name: "claude-sonnet-4" })).toBeDefined();
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();

    fireEvent.change(input, { target: { value: "opus" } });
    expect(screen.queryByRole("option", { name: "claude-sonnet-4" })).toBeNull();
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
  });
});
