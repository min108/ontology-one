// Keep actual SDK execution out of the concurrent page/screenshot worker group.
export function acceptanceSelection(target, harness) {
  if (!["legacy", "foundation"].includes(target))
    throw new Error("Unknown acceptance target");
  if (target === "legacy") {
    if (harness) throw new Error("SDK acceptance requires foundation");
    return { testMatch: "**/legacy.spec.mjs" };
  }
  const projects = [
    {
      name: "foundation-pages",
      testMatch: ["**/legacy.spec.mjs", "**/foundation-shell.spec.mjs"],
    },
  ];
  if (harness)
    projects.push({
      name: "harness-sdk",
      testMatch: "**/foundation-harness.spec.mjs",
      dependencies: ["foundation-pages"],
      workers: 1,
      fullyParallel: false,
    });
  return { projects };
}
