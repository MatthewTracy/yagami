import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AssistantBubble } from "./AssistantBubble";

describe("AssistantBubble", () => {
  it("renders markdown without executing raw HTML", () => {
    const { container } = render(
      <AssistantBubble
        text={'**safe** <script data-testid="unsafe">alert(1)</script>'}
        pending={false}
        isLastAssistant={false}
      />,
    );

    expect(screen.getByText("safe")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script/)).toBeInTheDocument();
  });

  it("renders tool and recall provenance", () => {
    render(
      <AssistantBubble
        text="answer"
        pending={false}
        isLastAssistant={false}
        toolCalls={[{ name: "calc.eval", input: { expression: "2+2" }, ok: true, result: "4" }]}
        recall={[
          {
            id: 1,
            role: "user",
            text: "earlier context",
            session_id: "session-123",
            source: "memory",
            distance: 0.1,
          },
        ]}
      />,
    );

    expect(screen.getByText(/recalled 1 from prior session/)).toBeInTheDocument();
    expect(screen.getByText(/calc\.eval/)).toBeInTheDocument();
  });
});
