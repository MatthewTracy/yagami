import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";

afterEach(cleanup);

describe("dialog keyboard access", () => {
  it("traps forward/reverse focus and restores the opener on close", async () => {
    const user = userEvent.setup();
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();
    const { unmount } = render(
      <Dialog title="Test panel" onClose={() => {}}>
        <button>First</button>
        <button disabled>Disabled</button>
        <button>Last</button>
      </Dialog>,
    );
    expect(screen.getByRole("dialog", { name: "Test panel" })).toHaveAttribute(
      "aria-modal",
      "true",
    );
    expect(screen.getByText("First")).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByText("Last")).toHaveFocus();
    await user.tab();
    expect(screen.getByText("First")).toHaveFocus();
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("Escape closes only the dialog and does not invoke background shortcuts", async () => {
    const user = userEvent.setup();
    const close = vi.fn();
    const background = vi.fn();
    window.addEventListener("keydown", background);
    render(
      <Dialog title="Test panel" onClose={close}>
        <button>Control</button>
      </Dialog>,
    );
    await user.keyboard("{Escape}");
    expect(close).toHaveBeenCalledOnce();
    expect(background).not.toHaveBeenCalled();
    window.removeEventListener("keydown", background);
  });

  it("handles an empty panel, an updated close callback, and backdrop clicks", async () => {
    const user = userEvent.setup();
    const first = vi.fn();
    const latest = vi.fn();
    const { rerender } = render(
      <Dialog title="Empty" onClose={first}>
        Empty panel
      </Dialog>,
    );
    await user.tab();
    expect(screen.getByRole("dialog")).toHaveFocus();
    rerender(
      <Dialog title="Empty" onClose={latest}>
        Empty panel
      </Dialog>,
    );
    await user.click(screen.getByText("Empty panel"));
    expect(latest).not.toHaveBeenCalled();
    await user.click(screen.getByRole("dialog").parentElement!);
    expect(latest).toHaveBeenCalledOnce();
    expect(first).not.toHaveBeenCalled();
  });
});
