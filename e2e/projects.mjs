// Keep actual SDK execution out of the concurrent page/screenshot worker group.
export function acceptanceSelection(
  target,
  harness,
  visual = false,
  visualOnly = false,
) {
  if (!["legacy", "foundation"].includes(target))
    throw new Error("Unknown acceptance target");
  if (target === "legacy") {
    if (harness || visual || visualOnly)
      throw new Error("SDK/visual acceptance requires foundation");
    return { testMatch: "**/legacy.spec.mjs" };
  }
  if (visualOnly) {
    if (harness)
      throw new Error(
        "Visual-only scope must be separate from SDK mutation tests",
      );
    return {
      projects: [
        {
          name: "foundation-visual",
          testMatch: "**/foundation-visual.spec.mjs",
          workers: 1,
          fullyParallel: false,
        },
      ],
    };
  }
  const projects = [
    {
      name: "foundation-pages",
      testMatch: ["**/legacy.spec.mjs", "**/foundation-shell.spec.mjs"],
    },
    {
      name: "foundation-home",
      testMatch: "**/foundation-home.spec.mjs",
      dependencies: ["foundation-pages"],
      workers: 1,
      fullyParallel: false,
    },
    {
      name: "foundation-identity",
      testMatch: "**/foundation-identity.spec.mjs",
      dependencies: ["foundation-home"],
      workers: 1,
      fullyParallel: false,
    },
  ];
  if (visual)
    projects.push({
      name: "foundation-visual",
      testMatch: "**/foundation-visual.spec.mjs",
      dependencies: ["foundation-identity"],
      workers: 1,
      fullyParallel: false,
    });
  if (harness)
    projects.push({
      name: "harness-sdk",
      testMatch: "**/foundation-harness.spec.mjs",
      dependencies: [visual ? "foundation-visual" : "foundation-identity"],
      workers: 1,
      fullyParallel: false,
    });
  return { projects };
}
