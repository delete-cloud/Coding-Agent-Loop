// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RuntimeConfigPatch } from "../lib/api";
import SettingsPanel from "./SettingsPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderPanel(onUpdate: (patch: RuntimeConfigPatch) => Promise<void>) {
  return render(
    <SettingsPanel
      sessionId="s1"
      providerName="kimi"
      modelName="kimi-for-coding"
      onUpdate={onUpdate}
    />,
  );
}

describe("SettingsPanel", () => {
  it("marks the thinking initial values as defaults, not the session's config", () => {
    renderPanel(() => Promise.resolve());
    expect(
      screen.getByText(/Initial values are defaults, not read from this session/),
    ).toBeTruthy();
  });

  it("does not present the new-session approval default as current session state", () => {
    renderPanel(() => Promise.resolve());
    expect((screen.getByTitle("session approval policy") as HTMLSelectElement).value).toBe("");
    expect(
      screen.getByText(
        /current policy is not exposed by the server/i,
      ),
    ).toBeTruthy();
  });

  it("sends the user's last-known enabled state when only the effort changes", async () => {
    const onUpdate = vi.fn(() => Promise.resolve());
    renderPanel(onUpdate);

    // Turn thinking off first, then change only the effort.
    fireEvent.click(screen.getByTitle("thinking enabled"));
    await waitFor(() =>
      expect(onUpdate).toHaveBeenLastCalledWith({
        thinking: { enabled: false, effort: "medium" },
      }),
    );

    fireEvent.change(screen.getByTitle("thinking effort"), { target: { value: "high" } });
    await waitFor(() =>
      expect(onUpdate).toHaveBeenLastCalledWith({
        thinking: { enabled: false, effort: "high" },
      }),
    );
    expect(onUpdate).toHaveBeenCalledTimes(2);
  });

  it("serializes patches per field and never lets a stale success hide a later failure", async () => {
    const deferreds: Array<{ resolve: () => void; reject: (e: Error) => void }> = [];
    const onUpdate = vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          deferreds.push({ resolve, reject });
        }),
    );
    renderPanel(onUpdate);

    const select = screen.getByTitle("session approval policy");
    fireEvent.change(select, { target: { value: "yolo" } });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(onUpdate).toHaveBeenLastCalledWith({ approval: "yolo" });

    // A second change to the same control must queue behind the in-flight one.
    fireEvent.change(select, { target: { value: "interactive" } });
    await act(async () => undefined);
    expect(onUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      deferreds[0].resolve();
    });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(2));
    expect(onUpdate).toHaveBeenLastCalledWith({ approval: "interactive" });
    expect(await screen.findByText("Saved")).toBeTruthy();

    // The later apply fails: its error feedback must win.
    await act(async () => {
      deferreds[1].reject(new Error("boom"));
    });
    expect(await screen.findByText("save failed: boom")).toBeTruthy();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("does not serialize different fields against each other", async () => {
    const deferreds: Array<{ resolve: () => void; reject: (e: Error) => void }> = [];
    const onUpdate = vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          deferreds.push({ resolve, reject });
        }),
    );
    renderPanel(onUpdate);

    fireEvent.change(screen.getByTitle("session approval policy"), {
      target: { value: "yolo" },
    });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));

    // A different field applies immediately despite the pending approval patch.
    fireEvent.change(screen.getByTitle("thinking effort"), { target: { value: "low" } });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(2));
    expect(onUpdate).toHaveBeenLastCalledWith({
      thinking: { enabled: true, effort: "low" },
    });
  });
});
