import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium, expect } from "./runtime.mjs";
import {
  accounts,
  authDirectory,
  evidence,
  fixture,
  roles,
  target,
} from "./fixture.mjs";

export default async function setup(config) {
  mkdirSync(authDirectory, { recursive: true });
  mkdirSync(evidence, { recursive: true });
  const browser = await chromium.launch(config.projects[0].use.launchOptions);
  try {
    for (const role of [...roles, "superadmin"]) {
      const context = await browser.newContext({
        ...config.projects[0].use,
        storageState: undefined,
      });
      const page = await context.newPage();
      await page.goto("/");
      await page
        .getByLabel("账号", { exact: true })
        .fill(accounts[role].username);
      await page
        .getByLabel("密码", { exact: true })
        .fill(accounts[role].password);
      await page.getByRole("button", { name: /登录/ }).click();
      await expect(page.locator("main").first()).toBeVisible();
      await expect(
        page.getByText("首次登录，请修改密码", { exact: true }),
      ).toHaveCount(0);
      await context.storageState({
        path: resolve(authDirectory, `${role}.json`),
      });
      await context.close();
    }
    // No account passwords, cookies, model output or application data enter this report.
    writeFileSync(
      resolve(evidence, "environment.json"),
      JSON.stringify(
        {
          target,
          sourceCommit: process.env.E2E_SOURCE_COMMIT || null,
          fixtureCommit:
            fixture.source_commit || fixture.source?.commit || null,
          browserVersion: browser.version(),
          viewport: { width: 1366, height: 768 },
          locale: "zh-CN",
          timezoneId: "Asia/Shanghai",
          reducedMotion: "reduce",
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await browser.close();
  }
}
