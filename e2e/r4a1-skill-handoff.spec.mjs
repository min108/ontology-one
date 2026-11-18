import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { login, button, read, postResponse } from "./r3-support.mjs";
import {
  startRegulation,
  acceptItem,
  readProposal,
  pickItem,
} from "./r4a1-changeset-support.mjs";

test("R4-A.1 skill handoff: refresh after package creation opens the existing specialist detail", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    const skill = proposal.items.find(
      (item) => item.after_data.kind === "skill",
    );
    expect(skill).toBeTruthy();
    await acceptItem(page, skill);
    const before = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === skill.id,
    );
    const id = before.applied_entity_id;
    expect(before.handoff.href).toBe(
      "/skill-development?legacy=" + encodeURIComponent(id),
    );
    await page
      .getByRole("link", { name: before.handoff.label, exact: true })
      .click();
    const converting = page.getByRole("dialog", {
      name: "继续建设已有技能",
      exact: true,
    });
    await expect(converting).toBeVisible();
    await converting
      .getByLabel("技能建设需求", { exact: true })
      .fill("完善年度合同付款核对技能");
    const creating = postResponse(page, "/skills");
    await button(converting, "开始建设技能").click();
    const response = await creating;
    expect(response.status()).toBe(200);
    const created = await response.json();
    expect(created.entity.id).toBe(id);
    expect(created.entity.payload.managed_skill).toBe(true);
    await expect(page).toHaveURL(
      (url) => url.pathname === "/skill-development/" + id,
    );
    await expect(
      page.getByRole("heading", {
        name: created.entity.title,
        exact: true,
        level: 1,
      }),
    ).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    await page.goto("/build/changesets/" + proposal.id);
    const refreshed = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === skill.id,
    );
    expect(refreshed.handoff.href).toBe(
      "/skill-development/" + encodeURIComponent(id),
    );
    expect(refreshed.handoff.href).not.toBe(before.handoff.href);
    await pickItem(page, refreshed);
    const link = page.getByRole("link", {
      name: refreshed.handoff.label,
      exact: true,
    });
    await expect(link).toHaveAttribute("href", "/skill-development/" + id);
    const opening = page.waitForResponse(
      (value) =>
        new URL(value.url()).pathname === "/api/skills/" + id &&
        value.request().method() === "GET",
    );
    await link.click();
    expect((await opening).status()).toBe(200);
    await expect(page).toHaveURL(
      (url) => url.pathname === "/skill-development/" + id,
    );
    await expect(
      page.getByRole("dialog", { name: "继续建设已有技能", exact: true }),
    ).toHaveCount(0);
    expect((await read(page, "/api/skills/" + id)).entity.id).toBe(id);
    await page.screenshot({
      path: resolve(
        process.env.E2E_EVIDENCE_DIR,
        "r4a1-managed-skill-handoff.png",
      ),
      animations: "disabled",
    });
  } finally {
    await owner.context.close();
  }
});
