import { test, expect } from "./runtime.mjs";
import {
  login,
  read,
  postResponse,
  button,
  confirmAction,
} from "./r3-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";

test("R3 A9 cross-role: editor A, submitter B, independent C; both review entries reject A", async ({
  browser,
}) => {
  const editor = await login(browser, "admin"),
    adopter = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    const page = editor.page;
    await selectSavedViewMode(page, "simple");
    const text =
      "第一条 付款前经办人应核对交叉审核依据；验收通过后30日内提交付款。\n第二条 审核人员独立核对原始材料。";
    const dropped = await page.evaluateHandle((text) => {
      const data = new DataTransfer();
      data.items.add(
        new File([text], "交叉角色制度.txt", { type: "text/plain" }),
      );
      return data;
    }, text);
    await page.dispatchEvent("body", "dragenter", { dataTransfer: dropped });
    await page.dispatchEvent("body", "drop", { dataTransfer: dropped });
    await button(page.getByRole("dialog"), "开始整理").click();
    await expect(
      page.getByRole("link", { name: "核对建设方案", exact: true }),
    ).toBeVisible({ timeout: 180_000 });
    const getting = page.waitForResponse((r) =>
      /\/api\/v2\/changesets\/CS-/.test(new URL(r.url()).pathname),
    );
    await page.getByRole("link", { name: "核对建设方案", exact: true }).click();
    const proposal = (await (await getting).json()).data,
      url = page.url(),
      target = proposal.items[0];
    await page.locator(`button[data-item-id="${target.id}"]`).click();
    await button(page.locator("[data-selected-item-id]"), "改一改").click();
    const modal = page.getByRole("dialog", { name: "改一改", exact: true });
    const title = "甲改写乙提交的交叉审核内容";
    await modal.getByLabel("名称", { exact: true }).fill(title);
    await modal
      .getByRole("textbox", { name: "正文与适用条件", exact: true })
      .fill(target.after_data.content + "\n甲补充：须核对完整原件。");
    const applied = postResponse(page, "/changeset.apply:invoke");
    await button(modal, "保存并采纳").click();
    const entityId = (await (await applied).json()).data.entity_ids[0];
    await expect(modal).not.toBeVisible();
    const editorId = (await read(page, "/api/auth/session")).user.id;
    expect(
      (await read(page, "/api/entities/" + entityId)).payload.r2_review
        .last_editor_id,
    ).toBe(editorId);
    await adopter.page.goto(url);
    for (const item of proposal.items.filter((i) => i.id !== target.id)) {
      await adopter.page.locator(`button[data-item-id="${item.id}"]`).click();
      const accepted = postResponse(
        adopter.page,
        "/changeset.decide_items:invoke",
      );
      const appliedOther = postResponse(
        adopter.page,
        "/changeset.apply:invoke",
      );
      await button(
        adopter.page.locator("[data-selected-item-id]"),
        "采纳",
      ).click();
      expect(
        (await (await accepted).json()).data.items.find((i) => i.id === item.id)
          .decision,
      ).toBe("accepted");
      expect((await appliedOther).ok()).toBe(true);
    }
    const submitting = postResponse(
      adopter.page,
      "/changeset.apply_and_submit:invoke",
    );
    await button(adopter.page, "采纳并提交审核").click();
    await confirmAction(adopter.page, "采纳并提交审核");
    expect((await submitting).ok()).toBe(true);
    const entity = await read(page, "/api/entities/" + entityId);
    expect(entity.payload.r2_review.last_editor_id).toBe(editorId);
    expect(entity.payload.r2_review.submitted_by).toBe(
      (await read(adopter.page, "/api/auth/session")).user.id,
    );
    await page.goto("/govern/reviews");
    await page
      .locator('[aria-label="选择一项待审内容"] button')
      .filter({ hasText: title })
      .click();
    await expect(button(page, "通过并发布")).toBeDisabled();
    await expect(button(page, "退回")).toBeDisabled();
    await expect(
      page.getByText("不能审核自己的内容", { exact: true }),
    ).toBeVisible();
    expect(
      (await read(page, "/api/v2/reviews")).data.items.find(
        (item) => item.entity.id === entityId,
      ).can_decide,
    ).toBe(false);
    await page.goto("/maintenance");
    await page.getByLabel("搜索维护内容", { exact: true }).fill(title);
    const row = page
      .getByRole("row")
      .filter({ has: page.getByRole("link", { name: title, exact: true }) });
    await button(row, "审核通过").click();
    await page
      .getByRole("dialog")
      .getByRole("textbox")
      .fill("甲尝试审核自己改写的内容，应由服务端拒绝。");
    const denied = postResponse(page, "/entities/" + entityId + "/action");
    await button(page.getByRole("dialog"), "确认办理").click();
    expect((await denied).status()).toBe(409);
    expect((await read(page, "/api/entities/" + entityId)).status).toBe(
      "review",
    );
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(
      page.getByText(/不能审核|最后编辑|独立审核/).last(),
    ).toBeVisible();
    await reviewer.page.goto("/govern/reviews");
    await reviewer.page
      .locator('[aria-label="选择一项待审内容"] button')
      .filter({ hasText: title })
      .click();
    const approved = postResponse(
      reviewer.page,
      "/reviews/" + entityId + ":decide",
    );
    await button(reviewer.page, "通过并发布").click();
    await confirmAction(reviewer.page, "通过并发布");
    expect((await (await approved).json()).data.entity.status).toBe(
      "published",
    );
  } finally {
    await Promise.allSettled([
      editor.context.close(),
      adopter.context.close(),
      reviewer.context.close(),
    ]);
  }
});
