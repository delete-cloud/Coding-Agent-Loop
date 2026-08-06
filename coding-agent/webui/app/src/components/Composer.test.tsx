// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Composer from "./Composer";

afterEach(cleanup);

describe("Composer", () => {
  it("does not shrink in the main flex column", () => {
    const { container } = render(
      <Composer
        prompt=""
        onPromptChange={vi.fn()}
        onSend={vi.fn()}
        onCancel={vi.fn()}
        disabled={false}
        streaming={false}
      />,
    );

    expect(container.querySelector("footer")?.className).toContain("shrink-0");
  });
});
