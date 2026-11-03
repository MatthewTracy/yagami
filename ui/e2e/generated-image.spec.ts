import { test, expect } from "./fixtures";

test("generated assistant image and caption survive reopening history after reload", async ({
  page,
}) => {
  const dataUrl = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "red";
    context.fillRect(0, 0, 1, 1);
    return canvas.toDataURL("image/png");
  });
  await page.route("**/api/sessions**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/sessions") {
      await route.fulfill({
        json: {
          sessions: [{ id: "generated-session", title: "Generated picture" }],
        },
      });
    } else if (path === "/api/sessions/generated-session") {
      await route.fulfill({
        json: {
          messages: [
            { role: "user", content: "Draw a picture" },
            {
              role: "assistant",
              content: "Generated preview",
              images: [
                { media_type: "image/png", data_b64: dataUrl.split(",")[1] },
              ],
            },
          ],
        },
      });
    } else {
      await route.fallback();
    }
  });
  await page.goto("/");
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const opener = page.getByRole("button", {
      name: "Open conversations",
      exact: true,
    });
    if (await opener.isVisible()) {
      await opener.click();
      await page
        .getByRole("dialog", { name: "Conversations", exact: true })
        .getByTitle("Generated picture", { exact: true })
        .click();
    } else {
      await page.getByTitle("Generated picture", { exact: true }).click();
    }
    const image = page.getByRole("img", { name: "generated", exact: true });
    await expect(image).toBeVisible();
    await expect(image).toHaveAttribute("src", dataUrl);
    await expect
      .poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth))
      .toBe(1);
    await expect(
      page.getByText("Generated preview", { exact: true }),
    ).toBeVisible();
    if (attempt === 0) await page.reload();
  }
});
