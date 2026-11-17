import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import {
  authDirectory,
  evidence,
  legacyRoutes,
  roles,
  target,
} from "./fixture.mjs";

for (const role of roles) {
  test.describe(`${role}: retained 0.7.0 pages`, () => {
    test.use({ storageState: resolve(authDirectory, `${role}.json`) });
    for (const [key, path] of legacyRoutes) {
      test(`${key} opens with real APIs and no browser errors`, async ({
        page,
      }, testInfo) => {
        const errors = [];
        const failures = [];
        page.on("pageerror", (error) => errors.push(error.message));
        page.on("console", (message) => {
          if (message.type() === "error") errors.push(message.text());
        });
        page.on("response", (response) => {
          if (
            new URL(response.url()).pathname.startsWith("/api/") &&
            response.status() >= 400
          ) {
            failures.push(
              `${response.status()} ${new URL(response.url()).pathname}`,
            );
          }
        });
        page.on("requestfailed", (request) => {
          // Route navigation deliberately aborts obsolete requests; all other failures matter.
          if (!request.failure()?.errorText.includes("ERR_ABORTED"))
            failures.push(request.url());
        });
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        const content = page.locator("main").first();
        await expect(content).toBeVisible();
        await expect(content.locator("h1, h2, h3").first()).toHaveText(/\S/);
        await expect(
          page.getByText("内容暂时未能加载", { exact: true }),
        ).toHaveCount(0);
        await expect(
          page.getByText("没有找到这个页面", { exact: true }),
        ).toHaveCount(0);
        if (target === "foundation") {
          if (key === "integration-applications") {
            await expect(
              page.locator('[data-page-template="list"]'),
            ).toBeVisible();
            await expect(page.locator("[data-page-header]")).toHaveCount(1);
            await expect(page.locator("[data-page-title]")).toHaveText(
              "集成应用",
            );
          } else {
            await expect(page.locator("[data-legacy-page]")).toBeVisible();
          }
        }
        await page.evaluate(() => document.fonts.ready);
        const directory = resolve(evidence, "screenshots", role);
        mkdirSync(directory, { recursive: true });
        const screenshot = resolve(directory, `${key}.png`);
        await page.screenshot({
          path: screenshot,
          fullPage: true,
          animations: "disabled",
        });
        await testInfo.attach("page", {
          path: screenshot,
          contentType: "image/png",
        });
        expect(failures, "All business requests must succeed").toEqual([]);
        expect(errors, "No console or uncaught errors").toEqual([]);
      });
    }
  });
}
