import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export const target = process.env.E2E_TARGET || "foundation";
if (!["legacy", "foundation"].includes(target))
  throw new Error("E2E_TARGET must be legacy or foundation");
export const fixturePath = resolve(
  process.env.E2E_FIXTURE_MANIFEST || "tests/fixtures/v070/manifest.json",
);
export const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
export const evidence = resolve(
  process.env.E2E_EVIDENCE_DIR || `work/evidence/r1/e2e-${target}`,
);
export const authDirectory = resolve("work/e2e-auth", target);
export const roles = ["modeler", "reviewer", "admin"];
export const accounts = fixture.browser.accounts;
for (const role of [...roles, "superadmin"]) {
  if (!accounts[role]?.username || !accounts[role]?.password)
    throw new Error(`Missing synthetic browser account: ${role}`);
}
const object = (kind) => {
  const id = fixture.objects[kind];
  if (!id || typeof id !== "string")
    throw new Error(`Missing browser fixture object: ${kind}`);
  return encodeURIComponent(id);
};

// All 25 routes retained in R1, including every detail-route pattern.
export const legacyRoutes = [
  ["group", "/group"],
  ["briefing", "/briefing"],
  ["experience", "/experience"],
  ["impact", "/impact"],
  ["work", "/work"],
  ["materials", "/materials"],
  ["material", `/materials/${object("material")}`],
  ["projects", "/projects"],
  ["project", `/projects/${object("project")}`],
  ["task", `/tasks/${object("task")}`],
  ["assets", "/assets"],
  ["asset", `/assets/${object("asset")}`],
  ["authority", "/authority"],
  ["authority-detail", `/authority/${object("authority")}`],
  ["collection", `/knowledge/collections/${object("collection")}`],
  ["datasets", "/datasets"],
  ["dataset", `/datasets/${object("dataset")}`],
  ["data-sources", "/data-sources"],
  ["data-source", `/data-sources/${object("source")}`],
  ["graph", "/graph"],
  ["applications", "/applications"],
  ["maintenance", "/maintenance"],
  ["integration-applications", "/integration-applications"],
  ["skill-development", "/skill-development"],
  ["skill", `/skill-development/${object("skill")}`],
];
if (legacyRoutes.length !== 25)
  throw new Error(
    "The retained-route inventory changed; review the R1 compatibility contract",
  );
