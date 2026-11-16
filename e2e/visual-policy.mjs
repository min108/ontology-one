export const canonicalBrowser = "159.0.8154.6";

export function validateVisualPolicy(policy) {
  if (
    !["compare", "capture"].includes(policy.mode) ||
    (policy.mode === "capture" &&
      (typeof policy.reason !== "string" || !policy.reason.trim()))
  )
    throw new Error("Invalid explicit visual baseline policy");
  return {
    mode: policy.mode,
    updateSnapshots: policy.mode === "capture" ? "all" : "none",
  };
}

export function assertCanonicalCapture({
  githubActions,
  browserVersion,
  executablePath,
}) {
  if (
    githubActions !== "true" ||
    executablePath ||
    browserVersion !== canonicalBrowser
  )
    throw new Error(
      "Visual capture requires canonical CI Chromium 159.0.8154.6 without an executable override",
    );
}

export function visualResultLabel(policy) {
  validateVisualPolicy(policy);
  return policy.mode === "capture"
    ? "baseline-capture-not-acceptance"
    : "acceptance";
}
