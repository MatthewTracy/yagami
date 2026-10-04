import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationsSidebar } from "./ConversationsSidebar";
import { ToastHost } from "./Toast";
import { json } from "../test/fixtures";

const row = { id: "s1", title: "A conversation", created_at: 0, updated_at: 0 };
function setup() {
  const onChange = vi.fn(); const onNew = vi.fn(); const onSelect = vi.fn();
  render(<><ConversationsSidebar activeSessionId="s1" refreshKey={0} onChange={onChange} onNew={onNew} onSelect={onSelect} /><ToastHost /></>);
  return { onChange, onNew, onSelect, user: userEvent.setup() };
}
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ sessions: [row] })));
  vi.stubGlobal("confirm", vi.fn(() => true));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("conversation management", () => {
  it("allows selection and exposes the active conversation", async () => {
    const { onSelect, user } = setup();
    const button = await screen.findByRole("button", { name: "A conversation" });
    expect(button).toHaveAttribute("aria-current", "page");
    await user.click(button);
    expect(onSelect).toHaveBeenCalledWith("s1");
  });

  it("Escape cancels a rename instead of saving it on blur", async () => {
    const { user } = setup();
    await user.click(await screen.findByRole("button", { name: "Rename A conversation" }));
    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "changed{Escape}");
    await user.tab();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
    expect(screen.getByRole("button", { name: "A conversation" })).toBeVisible();
  });

  it("commits a rename only once when Enter also causes blur", async () => {
    const { user, onChange } = setup();
    await user.click(await screen.findByRole("button", { name: "Rename A conversation" }));
    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "new title{Enter}");
    await user.tab();
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(1);
  });

  it("reports failed renames without pretending the title was saved", async () => {
    const { user, onChange } = setup();
    await user.click(await screen.findByRole("button", { name: "Rename A conversation" }));
    vi.mocked(fetch).mockResolvedValueOnce(json({}, 500));
    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "new title{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not rename");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not delete when confirmation is cancelled", async () => {
    const { user } = setup(); vi.mocked(confirm).mockReturnValue(false);
    await user.click(await screen.findByRole("button", { name: "Delete A conversation" }));
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });

  it("reports a deletion failure and resets an active session only after success", async () => {
    const { user, onNew } = setup();
    await screen.findByRole("button", { name: "Delete A conversation" });
    vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
    await user.click(screen.getByRole("button", { name: "Delete A conversation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not delete");
    expect(onNew).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete A conversation" }));
    await waitFor(() => expect(onNew).toHaveBeenCalledOnce());
  });
});
