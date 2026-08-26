import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { Composer, type ComposerDynamicProps } from "@/components/business/composer";

function baseProps(overrides: Partial<ComposerDynamicProps> = {}): ComposerDynamicProps {
  return {
    draft: "refactor the shell",
    onDraftChange: () => {},
    onSend: () => {},
    onCancel: () => {},
    onResume: () => {},
    onReload: () => {},
    status: "following",
    canResume: false,
    ...overrides,
  };
}

function renderComposer(overrides: Partial<ComposerDynamicProps> = {}) {
  return render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <Composer {...baseProps(overrides)} />
    </NextIntlClientProvider>,
  );
}

function textbox(): HTMLTextAreaElement {
  return screen.getByRole("textbox", {
    name: zhMessages.composer.inputLabel,
  }) as HTMLTextAreaElement;
}

function sendButton(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: zhMessages.composer.send,
  }) as HTMLButtonElement;
}

describe("Composer draft (controlled)", () => {
  it("renders the controlled draft and forwards edits to onDraftChange", () => {
    const onDraftChange = vi.fn();
    renderComposer({ draft: "fix SSE jitter", onDraftChange });

    expect(textbox().value).toBe("fix SSE jitter");
    fireEvent.change(textbox(), { target: { value: "fix SSE reconnect jitter" } });
    expect(onDraftChange).toHaveBeenCalledTimes(1);
    expect(onDraftChange).toHaveBeenCalledWith("fix SSE reconnect jitter");
  });

  it("sends on Enter and keeps Shift+Enter as a newline", () => {
    const onSend = vi.fn();
    renderComposer({ onSend });

    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(textbox(), { key: "Enter", shiftKey: true });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does not send on Enter while an IME composition is active", () => {
    const onSend = vi.fn();
    renderComposer({ onSend });

    fireEvent.keyDown(textbox(), { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send a blank draft from the keyboard", () => {
    const onSend = vi.fn();
    renderComposer({ draft: "   ", onSend });

    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe("Composer send button", () => {
  it("renders an accessible send button that sends the draft", () => {
    const onSend = vi.fn();
    renderComposer({ onSend });

    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("disables send when the draft is blank", () => {
    renderComposer({ draft: "" });

    expect(sendButton().disabled).toBe(true);
  });

  it("disables send while busy (sending or cancelling)", () => {
    const sending = renderComposer({ status: "sending" });
    expect(sendButton().disabled).toBe(true);
    sending.unmount();

    renderComposer({ status: "cancelling" });
    expect(sendButton().disabled).toBe(true);
  });
});

describe("Composer cancel action", () => {
  it("shows a cancel action while sending that calls onCancel", () => {
    const onCancel = vi.fn();
    renderComposer({ status: "sending", onCancel });

    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.cancel }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps the cancel action visible while cancelling", () => {
    const onCancel = vi.fn();
    renderComposer({ status: "cancelling", onCancel });

    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.cancel }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("hides the cancel action when nothing is in flight", () => {
    renderComposer({ status: "following" });

    expect(screen.queryByRole("button", { name: zhMessages.composer.cancel })).toBeNull();
  });
});

describe("Composer resume action", () => {
  it("offers Resume when canResume (interrupted / failed / cancelled terminal)", () => {
    const onResume = vi.fn();
    renderComposer({ canResume: true, onResume });

    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.resume }));
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it("blocks Resume when the run completed (canResume is false)", () => {
    renderComposer({ canResume: false });

    expect(screen.queryByRole("button", { name: zhMessages.composer.resume })).toBeNull();
  });
});

describe("Composer transport states", () => {
  it("shows the replay-required note with a reload action", () => {
    const onReload = vi.fn();
    renderComposer({ status: "replay_required", onReload });

    expect(screen.getByText(zhMessages.composer.replayRequired)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.reload }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("makes Reload the only enabled action while replay is required", () => {
    const onSend = vi.fn();
    const onResume = vi.fn();
    const onCancel = vi.fn();
    const onReload = vi.fn();
    renderComposer({
      status: "replay_required",
      canResume: true,
      draft: "retry this turn",
      onSend,
      onResume,
      onCancel,
      onReload,
    });

    expect(sendButton().disabled).toBe(true);
    expect(screen.queryByRole("button", { name: zhMessages.composer.cancel })).toBeNull();
    expect(screen.queryByRole("button", { name: zhMessages.composer.resume })).toBeNull();

    const enabled = screen.getAllByRole("button").filter((button) => !(button as HTMLButtonElement).disabled);
    expect(enabled).toHaveLength(1);
    expect(enabled[0].textContent).toBe(zhMessages.composer.reload);

    fireEvent.click(sendButton());
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(onResume).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(enabled[0]);
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("shows an alert label on error", () => {
    renderComposer({ status: "error" });

    expect(screen.getByRole("alert").textContent).toContain(zhMessages.composer.error);
  });

  it("shows the reconnecting label", () => {
    renderComposer({ status: "reconnecting" });

    expect(screen.getByText(zhMessages.composer.reconnecting)).toBeDefined();
  });
});

describe("Composer shadcn controls (04 §3)", () => {
  it("renders the draft through the textarea primitive", () => {
    renderComposer();

    const field = textbox();
    expect(field.tagName).toBe("TEXTAREA");
    expect(field.getAttribute("data-slot")).toBe("textarea");
  });

  it("renders send, cancel, resume, and reload through the button primitive", () => {
    const sending = renderComposer({ status: "sending" });
    expect(sendButton().getAttribute("data-slot")).toBe("button");
    expect(
      screen.getByRole("button", { name: zhMessages.composer.cancel }).getAttribute("data-slot"),
    ).toBe("button");
    sending.unmount();

    const resumable = renderComposer({ canResume: true });
    expect(
      screen.getByRole("button", { name: zhMessages.composer.resume }).getAttribute("data-slot"),
    ).toBe("button");
    resumable.unmount();

    renderComposer({ status: "replay_required" });
    expect(
      screen.getByRole("button", { name: zhMessages.composer.reload }).getAttribute("data-slot"),
    ).toBe("button");
  });

  it("keeps uns slotted native controls out of the connected composer", () => {
    const sending = renderComposer({ status: "sending" });
    expect(sending.container.querySelectorAll(".composer button:not([data-slot='button'])")).toHaveLength(0);
    expect(sending.container.querySelectorAll(".composer textarea:not([data-slot='textarea'])")).toHaveLength(0);
    sending.unmount();

    const { container } = renderComposer({ status: "replay_required", canResume: true });
    expect(container.querySelectorAll(".composer button:not([data-slot='button'])")).toHaveLength(0);
    expect(container.querySelectorAll(".composer textarea:not([data-slot='textarea'])")).toHaveLength(0);
    expect(container.querySelectorAll(".composer button[data-slot='button']")).toHaveLength(2);
  });
});

describe("Composer shell", () => {
  it("keeps a single .composer shell with no nested scroll surface", () => {
    const { container } = renderComposer();

    expect(container.querySelectorAll(".composer")).toHaveLength(1);
    expect(container.querySelector(".composer .composer")).toBeNull();
    expect(container.querySelector(".composer-scroll")).toBeNull();
    expect(container.querySelector(".composer")?.contains(textbox())).toBe(true);
  });

  it("renders the Slice 1 static placeholder when no props are given", () => {
    const { container } = render(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <Composer />
      </NextIntlClientProvider>,
    );

    expect(container.querySelector(".composer")?.textContent).toBe(
      zhMessages.composer.placeholder,
    );
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});

describe("Composer model seat", () => {
  const modelSeat = {
    model: "claude-sonnet-4",
    provider: "anthropic",
    oauthProviders: [] as const,
    accounts: [] as const,
    modelOptions: ["claude-sonnet-4", "claude-opus-4"],
    modelStatus: "ready" as const,
  };

  function modelChip(): HTMLButtonElement {
    return screen.getByRole("button", { name: /claude-sonnet-4/ }) as HTMLButtonElement;
  }

  it("opens the remote list from a click and commits the chosen model", () => {
    const onModelChange = vi.fn();
    const onProviderChange = vi.fn();
    renderComposer({
      ...modelSeat,
      onModelChange,
      onProviderChange,
    });

    fireEvent.click(modelChip());
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
    expect(screen.queryByLabelText(zhMessages.composer.searchModels)).toBeNull();
    fireEvent.click(screen.getByRole("option", { name: "claude-opus-4" }));
    expect(onModelChange).toHaveBeenCalledWith("claude-opus-4");
  });

  it("keeps the model chip in the toolbar left of Send, not a labeled field above the textarea", () => {
    const { container } = renderComposer({
      ...modelSeat,
      onModelChange: vi.fn(),
      onProviderChange: vi.fn(),
    });

    const toolbar = container.querySelector(".composer-toolbar");
    const picker = container.querySelector(".model-picker");
    expect(toolbar).not.toBeNull();
    expect(picker).not.toBeNull();
    expect(toolbar?.contains(picker)).toBe(true);
    expect(toolbar?.contains(sendButton())).toBe(true);
    expect(container.querySelector(".composer label")).toBeNull();
    expect(screen.queryByText(zhMessages.composer.model, { selector: "label" })).toBeNull();

    const children = Array.from(toolbar?.children ?? []);
    expect(children.indexOf(picker as Element)).toBeLessThan(children.indexOf(sendButton()));
    expect(
      textbox().compareDocumentPosition(toolbar as Node) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("shows the current model name on the chip", () => {
    renderComposer({
      ...modelSeat,
      onModelChange: vi.fn(),
      onProviderChange: vi.fn(),
    });

    expect(modelChip().getAttribute("aria-label")).toContain("claude-sonnet-4");
    expect(modelChip().querySelector(".model-picker-model")?.textContent).toBe("claude-sonnet-4");
  });
});
