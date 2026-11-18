import assert from "node:assert/strict";

export function assertPublishedRepair(
  detail,
  { id, kind, previousVersion = 0, application, waiver },
) {
  assert.equal(
    detail.entity.id,
    id,
    "The original asset identity must survive repair",
  );
  assert.equal(detail.entity.kind, kind);
  assert.equal(
    detail.entity.status,
    "published",
    "The saved result must actually be published",
  );
  assert.ok(
    detail.entity.version > previousVersion,
    "The repair must retain a newer saved version",
  );
  assert.equal(
    detail.gates.checks.find((check) => check.code === "QG1").status,
    "waived",
  );
  assert.equal(
    detail.gates.checks.find((check) => check.code === "QG2").status,
    "pass",
  );
  const activities = (detail.entity.activities || [])
    .map((event) => event.detail)
    .join("\n");
  assert.ok(
    activities.includes(application),
    "Application explanation must be persisted in activity",
  );
  assert.ok(
    activities.includes(waiver),
    "Independent waiver reason must be persisted in activity",
  );
}
export function assertVisibleFacts(fields, rows) {
  for (const field of fields) {
    const row = rows.find((item) => item.key === field.key);
    assert.ok(row, `Missing visible fact ${field.key}`);
    assert.ok(
      row.text.includes(field.value),
      `Wrong visible value ${field.key}`,
    );
    assert.equal(row.source, field.source, `Wrong fact origin ${field.key}`);
  }
}
