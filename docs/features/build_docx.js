// Build the annotatable feature inventory (Word) from features.json.
// Usage: node build_docx.js <features.json> <out.docx> <yyyy-mm-dd>
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, HeadingLevel, PageOrientation, Footer, PageNumber, BorderStyle, VerticalAlign,
  TableLayoutType,
} = require("docx");

const [, , input, output, today] = process.argv;
const data = JSON.parse(fs.readFileSync(input, "utf8"));
const L1 = data.l1;            // [{id:"1", name:"建本体", desc:"..."}]
const L2 = data.l2;            // [{id:"1.1", l1:"1", name, desc, status}]
const items = data.items;      // [{id,l2,parent,name,desc,status,status_note,when}]

const FONT = { ascii: "Arial", hAnsi: "Arial", eastAsia: "Microsoft YaHei", cs: "Arial" };
const STATUS = {
  "已实现": { fill: "E3F2E6", color: "1E6B34" },
  "部分实现": { fill: "FFF3D6", color: "8A5A00" },
  "已规划": { fill: "EEF1F4", color: "3D4852" },
  "待设计": { fill: "F1E8FB", color: "5B2D91" },
};
const ORDER = ["已实现", "部分实现", "已规划", "待设计"];
const GREY = "6B7280";
const BORDER = { style: BorderStyle.SINGLE, size: 4, color: "C9CED6" };
const BORDERS = { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER };
const WIDTH = 14838;
const COLS = [1100, 2700, 5900, 1900, 1500, 1738];

const run = (text, o = {}) => new TextRun({ text, font: FONT, size: o.size ?? 19, bold: o.bold, color: o.color, italics: o.italics });
const para = (children, o = {}) => new Paragraph({ children: Array.isArray(children) ? children : [children], spacing: { before: o.before ?? 0, after: o.after ?? 0, line: o.line ?? 276 }, alignment: o.align, indent: o.indent, keepNext: o.keepNext, heading: o.heading });
const cell = (paragraphs, width, o = {}) => new TableCell({
  children: paragraphs, width: { size: width, type: WidthType.DXA }, borders: BORDERS,
  shading: o.fill ? { fill: o.fill, type: ShadingType.CLEAR, color: "auto" } : undefined,
  margins: { top: 60, bottom: 60, left: 90, right: 90 }, verticalAlign: o.valign ?? VerticalAlign.TOP,
  columnSpan: o.span,
});
const headerRow = (labels, widths) => new TableRow({
  tableHeader: true, cantSplit: true,
  children: labels.map((label, i) => cell([para(run(label, { bold: true, size: 19 }))], widths[i], { fill: "DDE3EA" })),
});
const table = (rows, widths) => new Table({ width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, columnWidths: widths, rows, layout: TableLayoutType.FIXED });

function natural(a, b) {
  const x = a.split(".").map(Number), y = b.split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? -1) - (y[i] ?? -1);
    if (d) return d;
  }
  return 0;
}

function statusCell(status, note, width) {
  const s = STATUS[status] || STATUS["已规划"];
  const ps = [para(run(status, { bold: true, color: s.color, size: 19 }))];
  if (note) ps.push(para(run(note, { size: 17, color: s.color })));
  return cell(ps, width, { fill: s.fill });
}

function itemRow(it) {
  const sub = !!it.parent;
  const nameRuns = sub ? [run("└ ", { color: GREY }), run(it.name, { size: 19 })] : [run(it.name, { bold: true, size: 19 })];
  return new TableRow({
    cantSplit: true,
    children: [
      cell([para(run(it.id, { size: 17, color: GREY }))], COLS[0]),
      cell([para(nameRuns, { indent: sub ? { left: 160 } : undefined })], COLS[1]),
      cell([para(run(it.desc || "", { size: 19 }))], COLS[2]),
      statusCell(it.status, it.status_note, COLS[3]),
      cell([para(run(it.when || "", { size: 18 }))], COLS[4]),
      cell([para(run(""))], COLS[5]),
    ],
  });
}
function blankRow() {
  return new TableRow({
    cantSplit: true,
    children: [
      cell([para(run("新增", { size: 17, color: "A0A6AE" }))], COLS[0]),
      ...COLS.slice(1).map((w) => cell([para(run(""))], w)),
    ],
  });
}

// ---------- counts ----------
const counted = items.filter((it) => it.id.split(".").length >= 3);
const byL1 = {};
for (const it of counted) {
  const l1 = it.id.split(".")[0];
  byL1[l1] ??= { total: 0, advanced: 0 };
  byL1[l1][it.status] = (byL1[l1][it.status] || 0) + 1;
  byL1[l1].total++;
  if ((it.when || "").startsWith("进阶")) byL1[l1].advanced++;
}
const total = { total: 0, advanced: 0 };
for (const v of Object.values(byL1)) for (const [k, n] of Object.entries(v)) total[k] = (total[k] || 0) + n;

// ---------- document body ----------
const body = [];
body.push(para(run("知元（Ontology One）功能全景", { bold: true, size: 44 }), { after: 120 }));
body.push(para(run(`供负责人批注 · ${today} · 规划方整理`, { size: 21, color: GREY }), { after: 240 }));

const H1 = (text) => para(run(text, { bold: true, size: 32 }), { heading: HeadingLevel.HEADING_1, before: 360, after: 160, keepNext: true });
const H2 = (text) => para(run(text, { bold: true, size: 26 }), { heading: HeadingLevel.HEADING_2, before: 280, after: 80, keepNext: true });
const P = (text, o = {}) => para(run(text, { size: o.size ?? 21, color: o.color, bold: o.bold }), { after: o.after ?? 80, keepNext: o.keepNext });

body.push(H1("怎么读、怎么批注"));
for (const line of data.howto) body.push(P(line));
body.push(P("状态的四种标记：", { bold: true, keepNext: true }));
const legend = [
  ["已实现", "现在就能在界面上用。只在旧页面上有的也算，状态下方注明“旧页面”。有已知问题的注明待办编号（见仓库 docs/blueprint/BACKLOG.md）。"],
  ["部分实现", "只做了一部分，或后台做好了界面还没有；状态下方写缺什么。"],
  ["已规划", "蓝图里有设计，还没做。“何时做”写建设第几轮；写“进阶，未排期”的是蓝图列为可做可不做的。"],
  ["待设计", "蓝图只提了一句或完全没写，但业务上看应该有。这些最需要您拿主意：要不要、怎么做。"],
];
body.push(table([headerRow(["状态", "含义"], [2000, 12838]), ...legend.map(([s, d]) => new TableRow({ cantSplit: true, children: [statusCell(s, "", 2000), cell([para(run(d, { size: 19 }))], 12838)] }))], [2000, 12838]));

body.push(H1("总数"));
body.push(P(`共 ${total.total} 个功能点（含子功能点）。其中“已规划”里有 ${total.advanced} 个是蓝图列为进阶、尚未排期的。`));
const cw = [3438, 1900, 1900, 1900, 1900, 1900, 1900];
const sumRows = [headerRow(["大类", "已实现", "部分实现", "已规划", "待设计", "合计", "其中进阶未排期"], cw)];
for (const l1 of L1) {
  const v = byL1[l1.id] || { total: 0, advanced: 0 };
  sumRows.push(new TableRow({ cantSplit: true, children: [cell([para(run(`${l1.id}、${l1.name}`, { size: 19 }))], cw[0]), ...ORDER.map((s, i) => cell([para(run(String(v[s] || 0), { size: 19 }), { align: AlignmentType.CENTER })], cw[i + 1])), cell([para(run(String(v.total), { size: 19, bold: true }), { align: AlignmentType.CENTER })], cw[5]), cell([para(run(String(v.advanced), { size: 19 }), { align: AlignmentType.CENTER })], cw[6])] }));
}
sumRows.push(new TableRow({ cantSplit: true, children: [cell([para(run("合计", { bold: true, size: 19 }))], cw[0], { fill: "F4F6F8" }), ...ORDER.map((s, i) => cell([para(run(String(total[s] || 0), { bold: true, size: 19 }), { align: AlignmentType.CENTER })], cw[i + 1], { fill: "F4F6F8" })), cell([para(run(String(total.total), { bold: true, size: 19 }), { align: AlignmentType.CENTER })], cw[5], { fill: "F4F6F8" }), cell([para(run(String(total.advanced), { bold: true, size: 19 }), { align: AlignmentType.CENTER })], cw[6], { fill: "F4F6F8" })] }));
body.push(table(sumRows, cw));

// overview of modules
body.push(H1("全景：七大类、各模块一览"));
const ow = [1100, 3200, 7100, 1900, 1538];
for (const l1 of L1) {
  body.push(H2(`${l1.id}、${l1.name}`));
  if (l1.desc) body.push(P(l1.desc, { color: GREY, keepNext: true }));
  const rows = [headerRow(["编号", "模块", "管什么", "整体状态", "功能点数"], ow)];
  for (const m of L2.filter((x) => x.l1 === l1.id)) {
    const n = counted.filter((it) => it.l2 === m.id).length;
    rows.push(new TableRow({ cantSplit: true, children: [cell([para(run(m.id, { size: 17, color: GREY }))], ow[0]), cell([para(run(m.name, { bold: true, size: 19 }))], ow[1]), cell([para(run(m.desc || "", { size: 19 }))], ow[2]), statusCell(m.status, m.status_note, ow[3]), cell([para(run(String(n), { size: 19 }), { align: AlignmentType.CENTER })], ow[4])] }));
  }
  body.push(table(rows, ow));
}

// to-design list
const design = counted.filter((it) => it.status === "待设计");
body.push(H1(`需要您拿主意的：待设计功能点（${design.length} 个）`));
body.push(P("这些功能蓝图没写清楚或完全没写。请在“您的意见”里写“要 / 不要 / 怎么做”。它们在后面各模块里也都出现，在这里批注或在模块里批注都可以。"));
const dw = [1100, 2700, 7400, 3638];
body.push(table([headerRow(["编号", "功能点", "说明", "您的意见"], dw), ...design.map((it) => new TableRow({ cantSplit: true, children: [cell([para(run(it.id, { size: 17, color: GREY }))], dw[0]), cell([para(run(it.name, { bold: true, size: 19 }))], dw[1]), cell([para(run(it.desc + (it.status_note ? `（${it.status_note}）` : ""), { size: 19 }))], dw[2]), cell([para(run(""))], dw[3])] }))], dw));

// details
for (const l1 of L1) {
  body.push(H1(`${l1.id}、${l1.name}`));
  for (const m of L2.filter((x) => x.l1 === l1.id)) {
    body.push(H2(`${m.id} ${m.name}`));
    if (m.desc) body.push(P(m.desc, { color: GREY, keepNext: true, after: 100 }));
    const rows = [headerRow(["编号", "功能点", "说明", "状态", "何时做", "您的意见"], COLS)];
    for (const it of items.filter((x) => x.l2 === m.id && x.id.split(".").length >= 3).sort((a, b) => natural(a.id, b.id))) rows.push(itemRow(it));
    rows.push(blankRow(), blankRow());
    body.push(table(rows, COLS));
  }
}

const doc = new Document({
  creator: "Claude（规划方）",
  title: "知元功能全景",
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 32, bold: true }, paragraph: { spacing: { before: 360, after: 160 }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 26, bold: true }, paragraph: { spacing: { before: 280, after: 80 }, keepNext: true, outlineLevel: 1 } },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838, orientation: PageOrientation.LANDSCAPE }, margin: { top: 900, bottom: 900, left: 1000, right: 1000 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [run("知元功能全景 · 第 ", { size: 16, color: GREY }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: GREY }), run(" 页", { size: 16, color: GREY })] })] }) },
    children: body,
  }],
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(output, buf); console.log("written", output, buf.length, "bytes;", counted.length, "items"); });
