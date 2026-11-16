import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const summaryFiles = [
  "runner.json",
  "harness-result.json",
  "environment.json",
  "results.json",
  "npm-ci.log",
  "frontend-build.log",
  "fixture-load.log",
  "migration.log",
  "api.log",
  "agent.log",
  "playwright.log",
];
const roles = ["modeler", "reviewer", "admin"];
// Reserve room for ZIP headers and the per-package index below Sediment's 32 MiB limit.
const downloadBudget = 30 * 1024 * 1024;

function assertNoAuthentication(value) {
  if (!value || typeof value !== "object") return;
  if (
    Array.isArray(value.cookies) ||
    Array.isArray(value.origins) ||
    Array.isArray(value.localStorage)
  )
    throw new Error("Authentication storage cannot enter browser evidence");
  for (const child of Object.values(value)) assertNoAuthentication(child);
}

function checkedFile(path, json) {
  const stat = lstatSync(path);
  if (stat.isSymbolicLink() || !stat.isFile())
    throw new Error("Evidence cannot contain symbolic links or special files");
  const data = readFileSync(path);
  if (json) assertNoAuthentication(JSON.parse(data.toString("utf8")));
  if (
    /\.(log|json)$/.test(path) &&
    /(?:authorization["']?\s*[:=]\s*["']?(?:bearer|basic)\s+\S+|set-cookie\s*:)/i.test(
      data.toString("utf8"),
    )
  )
    throw new Error("Authentication header cannot enter browser evidence");
  return data;
}

export function packageEvidence(
  source,
  destination,
  completeArtifact,
  budget = downloadBudget,
) {
  if (existsSync(destination))
    throw new Error("Evidence destination must be new");
  if (existsSync(source) && realpathSync(source) !== resolve(source))
    throw new Error("Evidence cannot contain symbolic source directories");
  const screenshots = resolve(source, "screenshots");
  if (existsSync(screenshots) && lstatSync(screenshots).isSymbolicLink())
    throw new Error("Evidence cannot contain symbolic directories");
  const index = { completeArtifact, packages: {} };
  const groups = {
    summary: summaryFiles.filter((name) => existsSync(resolve(source, name))),
  };
  for (const role of [...roles, "superadmin"]) {
    const paths = [];
    for (const kind of ["screenshots", "shell-screenshots"]) {
      const parent = resolve(source, kind);
      const directory = resolve(parent, role);
      if (existsSync(parent) && lstatSync(parent).isSymbolicLink())
        throw new Error("Evidence cannot contain symbolic directories");
      if (!existsSync(directory)) continue;
      if (lstatSync(directory).isSymbolicLink())
        throw new Error("Evidence cannot contain symbolic directories");
      paths.push(
        ...readdirSync(directory)
          .filter((name) => name.endsWith(".png"))
          .map((name) => `${kind}/${role}/${name}`),
      );
    }
    if (roles.includes(role) || paths.length) groups[role] = paths;
  }
  const harnessScreenshots = resolve(source, "harness-screenshots");
  if (existsSync(harnessScreenshots)) {
    if (lstatSync(harnessScreenshots).isSymbolicLink())
      throw new Error("Evidence cannot contain symbolic directories");
    groups.harness = readdirSync(harnessScreenshots)
      .filter((name) => name.endsWith(".png"))
      .map((name) => `harness-screenshots/${name}`);
  }
  // Validate every source and size before copying any package.
  for (const [group, paths] of Object.entries(groups)) {
    const files = paths.map((path) => {
      const data = checkedFile(resolve(source, path), path.endsWith(".json"));
      return {
        path,
        bytes: data.length,
        sha256: createHash("sha256").update(data).digest("hex"),
      };
    });
    const bytes = files.reduce((sum, file) => sum + file.bytes, 0);
    const record = { bytes, files };
    const indexBytes = Buffer.byteLength(
      JSON.stringify({ completeArtifact, ...record }, null, 2) + "\n",
    );
    if (bytes + indexBytes > budget)
      throw new Error(`${group} exceeds evidence download budget`);
    index.packages[group] = record;
  }
  for (const [group, record] of Object.entries(index.packages)) {
    const directory = resolve(destination, group);
    mkdirSync(directory, { recursive: true });
    for (const file of record.files) {
      const destinationPath = resolve(directory, file.path);
      mkdirSync(dirname(destinationPath), { recursive: true });
      copyFileSync(resolve(source, file.path), destinationPath);
    }
    writeFileSync(
      resolve(directory, "index.json"),
      JSON.stringify({ completeArtifact, ...record }, null, 2) + "\n",
    );
  }
  return index;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  const [, , source, destination] = process.argv;
  if (!source || !destination)
    throw new Error("Usage: node e2e/evidence.mjs SOURCE NEW_DESTINATION");
  const index = packageEvidence(
    resolve(source),
    resolve(destination),
    process.env.E2E_COMPLETE_ARTIFACT_URL || "",
  );
  console.log(JSON.stringify(index, null, 2));
}
