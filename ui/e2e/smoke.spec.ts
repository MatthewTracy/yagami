import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";

test("workspace fits its viewport and passes accessibility checks", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Yagami", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Yagami" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  if (process.env.YAGAMI_PREVIEW_DIR)
    await page.screenshot({
      path: `${process.env.YAGAMI_PREVIEW_DIR}/${testInfo.project.name}.png`,
      fullPage: true,
    });
});

test("settings supports keyboard focus, privacy controls, and Escape", async ({
  page,
}) => {
  await page.goto("/");
  const opener = page.getByRole("button", { name: "Settings", exact: true });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
  await expect(dialog.getByLabel("Generation model")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Close", exact: true }).first(),
  ).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    dialog.getByRole("button", { name: "Close", exact: true }).last(),
  ).toBeFocused();
  await dialog.getByRole("button", { name: "routing", exact: true }).click();
  await expect(dialog.getByText("ON · locked")).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("conversation flows through send, streaming, cancel, and new chat", async ({
  page,
}) => {
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Message Yagami" });
  await input.fill("Summarize this idea");
  await input.press("Enter");
  await expect(
    page.getByRole("button", { name: "Stop", exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    window.yagamiTest.receive({
      type: "routing",
      backend: "ollama",
      is_local: true,
      reason: "Local policy route",
      classification: { sensitivity: "none" },
    });
    window.yagamiTest.receive({
      type: "text",
      content: "A streamed response.",
      meta: {},
    });
    window.yagamiTest.receive({ type: "done", content: "", meta: {} });
  });
  await expect(
    page.getByText("A streamed response.", { exact: true }),
  ).toBeVisible();
  await input.fill("Second turn");
  await input.press("Enter");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  expect(await page.evaluate(() => window.yagamiTest.sent.at(-1))).toEqual({
    type: "cancel",
  });
  await page.evaluate(() =>
    window.yagamiTest.receive({
      type: "done",
      content: "",
      meta: { cancelled: true },
    }),
  );
  if (
    await page.getByRole("button", { name: "Open conversations" }).isVisible()
  )
    await page.getByRole("button", { name: "Open conversations" }).click();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(
    page.getByText("A streamed response.", { exact: true }),
  ).toBeHidden();
  await expect(input).toHaveValue("");
  await expect(
    page.getByRole("button", { name: /Explore a topic/ }),
  ).toBeVisible();
});

test("disconnection keeps drafts and reconnects without replaying a user turn", async ({
  page,
}) => {
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Message Yagami" });
  await input.fill("An unsent draft");
  await page.evaluate(() => window.yagamiTest.disconnect());
  await expect(page.getByText("Disconnected from gateway")).toBeVisible();
  await expect(input).toHaveValue("An unsent draft");
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(input).toBeEnabled();
  expect(await page.evaluate(() => window.yagamiTest.sent)).toEqual([
    { type: "load_session", session_id: "test-session" },
  ]);
});

test("failed settings requests are recoverable", async ({ page }) => {
  await page.route("**/api/config", (route) =>
    route.fulfill({ status: 503, json: { detail: "unavailable" } }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Settings could not be loaded.Try again",
  );
  await page.unroute("**/api/config");
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByLabel("Generation model")).toBeVisible();
});

test("long replies, code, and tables stay inside the workspace", async ({
  page,
}) => {
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Message Yagami" });
  await input.fill("Show a detailed response");
  await input.press("Enter");
  await page.evaluate(() => {
    window.yagamiTest.receive({
      type: "routing",
      backend: "ollama",
      is_local: true,
      reason: "Local policy route",
      classification: { sensitivity: "none" },
    });
    window.yagamiTest.receive({
      type: "text",
      content:
        "Long word: " +
        "x".repeat(500) +
        "\n\n```python\n" +
        "a".repeat(500) +
        "\n```\n\n| Wide column | Another column |\n|---|---|\n| " +
        "b".repeat(500) +
        " | data |",
      meta: {},
    });
    window.yagamiTest.receive({ type: "done", content: "", meta: {} });
  });
  await expect(page.getByRole("button", { name: "Regenerate" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

for (const panel of [
  { button: "Cross-session memory", title: "Cross-session memory" },
  { button: "Stats dashboard", title: "Stats" },
]) {
  test(`${panel.title} is accessible and keeps keyboard focus inside the dialog`, async ({
    page,
  }) => {
    await page.goto("/");
    const opener = page.getByRole("button", {
      name: panel.button,
      exact: true,
    });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: panel.title, exact: true });
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    expect(
      await dialog.evaluate((el) => el.contains(document.activeElement)),
    ).toBe(true);
    const accessibility = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(accessibility.violations).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
}
