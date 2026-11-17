// Preserve numeric-gate failure categories without recording model prose or values.
export function reportIssueSummary(issues) {
  const kinds = new Map([
    ["缺少本行的来源引用", "missing_row_reference"],
    ["缺少本段的来源引用", "missing_paragraph_reference"],
    ["数值或单位与本行引用原文不符", "row_quantity_mismatch"],
    ["数值或单位与本段引用原文不符", "paragraph_quantity_mismatch"],
  ]);
  const units = new Set(["元", "%", "笔", "份", "条", "家", "项", "人", "日", "小时", "分钟", "月", "年", "次", "个", "件", "吨", "倍"]);
  return (Array.isArray(issues) ? issues : []).map((issue) => {
    const match = String(issue).match(/^(段落|表格) (\d+)(?: 第(\d+)行)?：[-+\d.,E]+([^（]*)（([^）]*)）$/);
    if (!match) return { kind: "other_validation_issue" };
    return {
      kind: kinds.get(match[5]) || "other_validation_issue",
      block: Number(match[2]),
      ...(match[3] ? { row: Number(match[3]) } : {}),
      unit: units.has(match[4]) ? match[4] : "other",
    };
  });
}
