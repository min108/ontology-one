import { resolve } from "node:path";
import { evidence } from "./fixture.mjs";
import { expect } from "./runtime.mjs";
import { button, read, postResponse } from "./r3-support.mjs";
import { post } from "./r4b-search-support.mjs";

async function chooseOriginal(page, dialog, label, materialId) {
  const loading = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/entities" &&
      url.searchParams.get("q") === "付款" &&
      url.searchParams.get("kind")?.includes("material")
    );
  });
  await dialog.getByRole("textbox", { name: label }).fill("付款");
  const response = await loading;
  expect(response.ok()).toBe(true);
  const original = (await response.json()).items.filter(
    (row) =>
      row.payload?.material_id === materialId &&
      row.payload.chunk_id === row.id,
  );
  expect(new Set(original.map((hit) => hit.id)).size).toBe(2);
  return original;
}

export async function verifyMaterialPickers(page, materialId) {
  const special = await post(page, "/api/special-projects", {
    title: "制度档案归集",
    scope: "复核合同档案的现有原件。",
  });
  const base = "/api/special-projects/" + special.project.id;
  await page.goto("/projects/" + special.project.id + "?panel=materials");
  await button(page, "归集已有资料").click();
  const dialog = page.getByRole("dialog", { name: "归集已有资料到本专项" });
  const hits = await chooseOriginal(
    page,
    dialog,
    "搜索已有专项资料",
    materialId,
  );
  // Reproduce the former invalid submission against the real project transaction.
  const session = await read(page, "/api/auth/session");
  const invalid = await page.request.post(base + "/materials", {
    headers: {
      "X-Workspace-Id": session.workspace.tenant_id,
      "X-CSRF-Token": session.csrf_token,
    },
    data: { material_ids: [hits[0].id] },
  });
  expect(invalid.status()).toBe(404);
  expect((await read(page, base + "/workspace")).materials).toHaveLength(0);
  const label = dialog.locator("label").filter({ hasText: materialId });
  await expect(label).toHaveCount(1);
  await label.getByRole("checkbox").check();
  await page.screenshot({
    animations: "disabled",
    path: resolve(evidence, "r4b-special-material-picker-1366x768.png"),
  });
  const submit = postResponse(page, base + "/materials");
  await button(dialog, "归集所选 1 份资料").click();
  const saved = await submit;
  expect(saved.request().postDataJSON().material_ids).toEqual([materialId]);
  expect(saved.ok()).toBe(true);
  await expect(dialog).toHaveCount(0);
  expect(
    (await read(page, base + "/workspace")).materials.map((item) => item.id),
  ).toEqual([materialId]);
  const project = await post(page, "/api/entities", {
    kind: "project",
    title: "制度原件复核",
    status: "draft",
    summary: "收集本次核对所用的依据材料。",
    payload: { scope: "依据材料复核" },
  });
  await page.goto("/projects/" + project.id);
  await button(page, "关联已有内容").click();
  const objects = page.getByRole("dialog", { name: "配置工作对象与成果" });
  await button(objects, "依据材料").click();
  await chooseOriginal(page, objects, "搜索可关联内容", materialId);
  const candidates = objects
    .locator("button.pw-search-result")
    .filter({ hasText: materialId });
  await expect(candidates).toHaveCount(1);
  await candidates.click();
  await page.screenshot({
    animations: "disabled",
    path: resolve(evidence, "r4b-work-material-picker-1366x768.png"),
  });
  const linking = postResponse(page, "/api/work/" + project.id + "/objects");
  await button(objects, "确认关联").click();
  const linked = await linking;
  expect(linked.request().postDataJSON()).toMatchObject({
    target_id: materialId,
    role: "source_material",
  });
  expect(linked.ok()).toBe(true);
  await expect(objects).toHaveCount(0);
  const workspace = await read(
    page,
    "/api/projects/" + project.id + "/workspace",
  );
  expect(
    workspace.objects.filter(
      (row) => row.target === materialId && row.role === "source_material",
    ),
  ).toHaveLength(1);
  return {
    special_project_id: special.project.id,
    project_id: project.id,
    material_id: materialId,
    chunk_hits: hits.map((row) => row.id),
    invalid_chunk_status: invalid.status(),
  };
}
