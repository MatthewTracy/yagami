import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsModal } from "./SettingsModal";
import { ToastHost } from "./Toast";
import { deferred, json, settingsFixture } from "../test/fixtures";

async function setup() {
  const close = vi.fn();
  const view = render(
    <>
      <SettingsModal open onClose={close} />
      <ToastHost />
    </>,
  );
  await screen.findByLabelText("Generation model");
  return { ...view, close, user: userEvent.setup() };
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json(settingsFixture())),
  );
  vi.stubGlobal(
    "confirm",
    vi.fn(() => true),
  );
  vi.stubGlobal(
    "prompt",
    vi.fn(() => "DELETE"),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("settings and privacy controls", () => {
  it("keeps saves disabled until an edit and saves the edited configuration", async () => {
    const { user } = await setup();
    expect(screen.getByRole("button", { name: "Saved" })).toBeDisabled();
    const model = screen.getByLabelText("Generation model");
    await user.clear(model);
    await user.type(model, "my-local-model");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Saved" })).toBeDisabled(),
    );
    const write = vi
      .mocked(fetch)
      .mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(String(write[1]?.body)).ollama.model).toBe(
      "my-local-model",
    );
  });

  it.each(["http", "network"])(
    "keeps unsaved edits after a %s save failure",
    async (failure) => {
      const { user } = await setup();
      const model = screen.getByLabelText("Generation model");
      await user.clear(model);
      await user.type(model, "draft-model");
      if (failure === "http")
        vi.mocked(fetch).mockResolvedValueOnce(
          json({ detail: "invalid" }, 422),
        );
      else vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Save failed");
      expect(model).toHaveValue("draft-model");
      expect(
        screen.getByRole("button", { name: "Save changes" }),
      ).toBeEnabled();
    },
  );

  it("does not run retention cleanup when saving changes fails", async () => {
    const { user } = await setup();
    await user.click(
      screen.getByRole("button", { name: "privacy" }),
    );
    const days = screen.getByLabelText("Retention (days)");
    await user.clear(days);
    await user.type(days, "7");
    vi.mocked(fetch).mockResolvedValueOnce(json({}, 500));
    await user.click(
      screen.getByRole("button", { name: "Save and clean up now" }),
    );
    await screen.findByRole("alert");
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url) === "/api/privacy/cleanup"),
    ).toBe(false);
  });

  it("keeps PHI containment locked even when routing is edited", async () => {
    const { user } = await setup();
    await user.click(
      screen.getByRole("button", { name: "routing" }),
    );
    expect(screen.getByText("ON · locked")).toBeVisible();
    await user.click(screen.getByLabelText("Block all cloud routes"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    const write = vi
      .mocked(fetch)
      .mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(String(write[1]?.body)).routing).toMatchObject({
      block_cloud: true,
      phi_must_be_local: true,
    });
  });

  it("adds, selects, and removes a profile without keeping a deleted active profile", async () => {
    const { user } = await setup();
    await user.click(
      screen.getByRole("button", { name: "profiles" }),
    );
    await user.type(screen.getByLabelText("Name"), "work");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.selectOptions(screen.getByLabelText("Profile"), "work");
    expect(screen.getByText("ACTIVE")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Delete profile" }));
    expect(screen.getByLabelText("Profile")).toHaveValue("");
    expect(screen.getByText("No profiles yet.")).toBeVisible();
  });

  it.each(["cancel", "wrong-confirmation"])(
    "never deletes data with %s",
    async (scenario) => {
      const { user } = await setup();
      await user.click(
        screen.getByRole("button", { name: "privacy" }),
      );
      if (scenario === "cancel") vi.mocked(confirm).mockReturnValue(false);
      else vi.mocked(prompt).mockReturnValue("delete");
      await user.click(
        screen.getByRole("button", { name: /Delete chats/ }),
      );
      expect(
        vi
          .mocked(fetch)
          .mock.calls.some(([, init]) => init?.method === "DELETE"),
      ).toBe(false);
    },
  );

  it("shows a recoverable loading failure instead of an endless spinner", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
    render(<SettingsModal open onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be loaded",
    );
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Generation model")).toHaveValue(
      "llama3.2",
    );
  });

  it("ignores an old settings response when the panel is reopened", async () => {
    const old = deferred<Response>();
    vi.mocked(fetch).mockImplementationOnce(() => old.promise);
    const { rerender } = render(<SettingsModal open onClose={() => {}} />);
    rerender(<SettingsModal open={false} onClose={() => {}} />);
    rerender(<SettingsModal open onClose={() => {}} />);
    await screen.findByLabelText("Generation model");
    const outdated = settingsFixture();
    outdated.config.ollama.model = "obsolete";
    await act(async () => old.resolve(json(outdated)));
    expect(
      within(screen.getByRole("dialog")).getByLabelText("Generation model"),
    ).toHaveValue("llama3.2");
  });
});
