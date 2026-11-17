import assert from "node:assert/strict";

// These contracts are shared by real browser checks and executable counterexamples.
export function assertRecipeTrayStage(stage) {
  assert.match(
    stage.trim(),
    /^(?:已收到材料，正在等待整理|正在按条款拆分原文|正在提炼制度中的要求|正在逐字核对引文与原文位置|建设方案已就绪，请对照原文核对|智能整理暂不可用，已按条款拆分，请逐条完善)$/,
    "A2 requires the stage message, not the task title",
  );
}

export function assertReturnedReason(entity, id, reason) {
  assert.equal(entity.id, id, "A8 builder must read the returned entity");
  assert.equal(entity.status, "returned");
  const returned = entity.activities.find(
    (event) => event.action === "退回补充",
  );
  assert.ok(reason.trim(), "A8 expected reason cannot be empty");
  assert.equal(
    returned?.detail,
    reason,
    "A8 server must retain the return reason",
  );
}

// This function runs in the browser as well as the DOM counterexample tests.
export function homeMetricSnapshot() {
  return {
    roots: document.querySelectorAll("[data-home-workbench]").length,
    metrics: [...document.querySelectorAll("[data-home-count]")]
      .filter(
        (node) =>
          node.getClientRects().length &&
          getComputedStyle(node).visibility !== "hidden",
      )
      .map((node) => Number(node.getAttribute("data-home-count"))),
  };
}

export function assertNoZeroHomeMetrics(snapshot) {
  assert.equal(snapshot.roots, 1, "A1 must inspect the actual workbench");
  assert.ok(
    snapshot.metrics.every((value) => Number.isFinite(value) && value > 0),
    "A1 visible metrics must be positive",
  );
}

export function assertRestrictionLabels(snapshot, expected) {
  assert.equal(
    snapshot.popovers,
    0,
    "U3 must be checked with the identity popover closed",
  );
  assert.ok(expected.length > 0, "U3 must name a required restriction");
  for (const label of expected)
    assert.ok(
      snapshot.labels.includes(label),
      `U3 restriction must remain visible in topbar: ${label}`,
    );
}

export function restrictionSnapshot() {
  const visible = (node) =>
    node.getClientRects().length &&
    getComputedStyle(node).visibility !== "hidden";
  return {
    popovers: [...document.querySelectorAll(".ant-popover")].filter(visible)
      .length,
    labels: [...document.querySelectorAll("[data-topbar] .ant-tag")]
      .filter(visible)
      .map((node) => node.textContent.trim()),
  };
}

export function assertOneTitleLine(rows) {
  const groups = new Map();
  for (const row of rows) {
    assert.ok(Number.isFinite(row.titleX), "B1 requires an actual page title");
    assert.ok(
      ["true", "false"].includes(row.navCollapsed),
      "B1 requires a known navigation state",
    );
    const key = `${row.width}x${row.height}:${row.navCollapsed}`;
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key).add(row.titleX);
  }
  for (const [key, values] of groups)
    assert.equal(
      values.size,
      1,
      `B1 all pages must share one title line at ${key}`,
    );
}

export function assertDatasetCardsLayout(snapshot) {
  assert.equal(
    snapshot.display,
    "grid",
    "dataset entry choices must use their grid styles",
  );
  assert.equal(snapshot.cards.length, 2);
  for (const card of snapshot.cards) {
    assert.equal(
      card.display,
      "flex",
      "dataset entry card must use its flex styles",
    );
    assert.ok(
      card.width > 0 && card.height > 0,
      "dataset entry card must have visible dimensions",
    );
    assert.equal(
      card.overflow,
      false,
      "dataset entry card text must fit inside its card",
    );
  }
}
