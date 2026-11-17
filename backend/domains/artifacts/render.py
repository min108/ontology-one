"""Deterministic text-only HTML and Word renderers; no model-authored markup."""

import io
import json
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from fastapi import HTTPException

LAYOUT = json.loads((Path(__file__).parent / "layouts/official.json").read_text())
NUMBER = r"(?<![A-Za-z0-9_.])[-+]?\d[\d,]*(?:\.\d+)?"
MAGNITUDES = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000, "万亿": 1000000000000}
SCALE = r"万亿|亿|万|千|百|十"
UNITS = r"元人民币|人民币|元|%|％|笔|份|条|家|项|人|天|日|小时|分钟|个月|月|年|次|个|件|吨|倍"
QUANTITY = re.compile(rf"({NUMBER})\s*({SCALE})?\s*({UNITS})?")
CHINESE_DIGITS = dict(
    zip(
        "零〇一二两三四五六七八九壹贰叁肆伍陆柒捌玖",
        (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        strict=True,
    )
)
CHINESE_UNITS = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
    "万": 10000,
    "萬": 10000,
    "亿": 100000000,
    "億": 100000000,
}
CHINESE_NUMBER = (
    "["
    + "".join(CHINESE_DIGITS)
    + "十拾]["
    + "".join(CHINESE_DIGITS)
    + "".join(CHINESE_UNITS)
    + "]*"
    + "(?:点["
    + "".join(CHINESE_DIGITS)
    + "]+)?"
)
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"


def normalized_number(value: str, unit: str = "") -> tuple[Decimal, str]:
    number = Decimal(value.replace(",", ""))
    match = re.fullmatch(rf"({SCALE})?({UNITS})?", unit)
    if match:
        number *= MAGNITUDES.get(match[1], 1)
        unit = match[2] or ""
    return number, {"元人民币": "元", "人民币": "元", "％": "%", "天": "日", "个月": "月"}.get(unit, unit)


def chinese_number(value: str) -> str:
    whole, _, fraction = value.partition("点")
    if not any(character in CHINESE_UNITS for character in whole):
        result = int("".join(str(CHINESE_DIGITS[c]) for c in whole) or "0")
    else:
        result, section, digit = 0, 0, 0
        for character in whole:
            if character in CHINESE_DIGITS:
                digit = CHINESE_DIGITS[character]
            else:
                unit = CHINESE_UNITS[character]
                if unit < 10000:
                    section += (digit or 1) * unit
                elif unit == 10000:
                    result += (section + digit) * unit
                    section = 0
                else:
                    result = (result + section + digit) * unit
                    section = 0
                digit = 0
        result += section + digit
    return str(result) + ("." + "".join(str(CHINESE_DIGITS[c]) for c in fraction) if fraction else "")


def quantities(text: str, default_unit: str = "") -> list[tuple[Decimal, str]]:
    text = xml_text(text)
    # Citation/list/article identifiers are labels, not quantitative claims.
    text = re.sub(rf"第\s*(?:\d+|{CHINESE_NUMBER})\s*[条款章节项]|\[(?:c?\d+)(?:,\s*c?\d+)*\]", "", text)
    text = re.sub(r"(?m)^\s*(?:\d+[.、)）](?!\d)|[（(]\d+[）)])\s*", "", text)
    text = re.sub(
        rf"百分之({CHINESE_NUMBER}|\d+(?:\.\d+)?)",
        lambda m: (chinese_number(m[1]) if m[1][0] in CHINESE_DIGITS or m[1][0] in CHINESE_UNITS else m[1]) + "%",
        text,
    )
    text = re.sub(rf"({CHINESE_NUMBER})(?=\s*(?:{SCALE})?\s*(?:{UNITS}))", lambda m: chinese_number(m[1]), text)
    # Calendar labels are outside numerical verification; durations still count.
    text = re.sub(r"(?<!\d)\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}(?!\d)", "", text)
    text = re.sub(r"(?<!\d)\d{4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)?", "", text)
    text = re.sub(r"(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*[日号]", "", text)
    result = []

    # Retain the quantitative meaning of Chinese magnitudes without inventing
    # a unit; converting them to bare Arabic digits would otherwise drop them.
    def chinese_magnitude(match: re.Match) -> str:
        if match[2] or any(char in "百佰千仟万萬亿億" for char in match[1]):
            unit = normalized_number("1", default_unit)[1]
            number = Decimal(chinese_number(match[1])) * MAGNITUDES.get(match[2], 1)
            result.append((number, unit))
            return " "
        return match[0]

    text = re.sub(rf"({CHINESE_NUMBER})({SCALE})?", chinese_magnitude, text)
    for match in QUANTITY.finditer(text):
        scale, unit = match[2] or "", match[3] or ""
        if not scale and not unit and not default_unit:
            continue
        if not scale and not unit and default_unit == "年" and re.fullmatch(r"\d{4}", match[1]):
            continue
        if scale and not unit:
            # An explicit magnitude overrides the header magnitude, not its unit.
            unit = normalized_number("1", default_unit)[1]
        result.append(normalized_number(match[1], scale + unit if scale or unit else default_unit))
    return result


def close_number(left: Decimal, right: Decimal) -> bool:
    return left == right or (right != 0 and abs(left - right) <= abs(right) * Decimal("0.005"))


def supports(number: Decimal, unit: str, evidence: list[tuple[Decimal, str]]) -> bool:
    return any(
        unit == other_unit and (close_number(number, other) if unit == "元" else number == other)
        for other, other_unit in evidence
    )


def without_source_labels(text: str, sources: list[dict]) -> str:
    for source in sources:
        for key in ("entity_id", "source_version", "anchor_id", "locator", "path"):
            if source.get(key):
                text = text.replace(str(source[key]), "")
        version = re.escape(str(source["version"]))
        text = re.sub(rf"版本\s*[：:]?\s*v?{version}(?![\d.])\s*版?|第\s*{version}\s*版", "", text)
    return text


def plain(text: str) -> str:
    # Never preserve executable links or Markdown/HTML as executable markup.
    text = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text).replace("**", "").replace("`", "").strip()


def cited_sources(text: str, sources: dict) -> list[str]:
    """Resolve only references written in this block, before stripping links."""
    text = unquote(text)
    matched = []
    for key, source in sources.items():
        identifiers = (source.get("entity_id"), source.get("anchor_id"))
        identified = any(
            value and re.search(r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])", text)
            for value in identifiers
        )
        named = False
        for label, identity in (("title", "entity_id"), ("source_title", "material_id")):
            name = source.get(label)
            if name and name in plain(text):
                owners = {item.get(identity) for item in sources.values() if item.get(label) == name}
                named = named or len(owners) == 1
        if identified or named:
            matched.append(key)
    return matched


def blocks_from_answer(answer: str, citations: dict | None = None) -> list[dict]:
    if not answer.strip() or len(answer) > 64000:
        raise HTTPException(422, "回答为空或超过报告大小限制。")
    blocks, paragraph, table, table_raw = [], [], [], []

    def append(block: dict, raw: str) -> None:
        if citations is not None:
            block["citations"] = cited_sources(raw, citations)
        blocks.append(block)

    def flush_paragraph() -> None:
        if paragraph:
            raw = "\n".join(paragraph)
            append({"type": "paragraph", "text": plain(raw)}, raw)
            paragraph.clear()

    def flush_table() -> None:
        if table:
            width = len(table[0])
            if len(table) > 51 or width > 20 or any(len(row) != width for row in table):
                raise HTTPException(422, "报告表格需为规则表格，正文最多显示 50 行、20 列。请在助手中缩小结果。")
            block = {"type": "table", "columns": table[0], "rows": table[1:]}
            if citations is not None:
                block["row_citations"] = [cited_sources(row, citations) for row in table_raw[1:]]
            append(block, "\n".join(table_raw))
            table.clear()
            table_raw.clear()

    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            cells = [plain(cell.strip()) for cell in stripped[1:-1].split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                table.append(cells)
                table_raw.append(stripped)
        else:
            flush_table()
            if stripped:
                paragraph.append(stripped)
            else:
                flush_paragraph()
    flush_table()
    flush_paragraph()
    if len(blocks) > 200:
        raise HTTPException(422, "报告段落过多，请先让助手精简回答。")
    return blocks


def verify(spec: dict) -> None:
    sources = spec["citations"]
    issues = []
    for index, block in enumerate(
        [{"type": "paragraph", "text": spec["title"], "citations": list(sources)}, *spec["blocks"]]
    ):
        evidence = [sources[key] for key in block.get("citations", []) if key in sources]
        quoted = "\n".join(item["quote"] for item in evidence)
        if index == 0:
            # The generated title can name the cited, versioned source itself.
            quoted += "\n" + "\n".join(item["title"] for item in evidence)
        supported = quantities(quoted)
        text = block.get("text", "")
        if block["type"] == "table":
            text = "\n".join(block["columns"])
            for row_index, row in enumerate(block["rows"]):
                row_evidence, row_supported = evidence, supported
                if "row_citations" in block:
                    references = block["row_citations"]
                    keys = references[row_index] if row_index < len(references) else []
                    row_evidence = [sources[key] for key in keys if key in sources]
                    row_supported = quantities("\n".join(item["quote"] for item in row_evidence))
                for column, cell in zip(block["columns"], row, strict=True):
                    hint = re.search(rf"(?:{SCALE})?(?:{UNITS})", column)
                    values = quantities(without_source_labels(cell, row_evidence), hint.group() if hint else "")
                    for number, unit in values:
                        if not supports(number, unit, row_supported):
                            issues.append(f"表格 {index}：{cell}")
        for number, unit in quantities(without_source_labels(text, evidence)):
            if not supports(number, unit, supported):
                issues.append(f"段落 {index}：{number}{unit}")
    if issues:
        raise HTTPException(
            422,
            {
                "code": "NUMERIC_UNVERIFIED",
                "message": "以下数字未能对应到本次引用，请让助手核对后重新生成。",
                "issues": issues[:20],
            },
        )


def html(spec: dict) -> bytes:
    verify(spec)
    root = ET.Element("html", lang="zh-CN")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "meta", charset="UTF-8")
    ET.SubElement(head, "meta", {"http-equiv": "Content-Security-Policy", "content": CSP})
    ET.SubElement(head, "title").text = spec["title"]
    ET.SubElement(head, "style").text = (
        "body{font:16px/1.8 system-ui,sans-serif;color:#20242a;background:#fff;"
        "max-width:900px;margin:48px auto;padding:0 24px}"
        "h1{font-size:28px}table{border-collapse:collapse;width:100%;font-size:14px}"
        "td,th{border:1px solid #bbc1ca;padding:8px;text-align:left}"
        "p{white-space:pre-wrap}a{color:#3559a0}blockquote{border-left:3px solid #bbc1ca;padding-left:16px}"
        "@media print{body{margin:0;max-width:none}tr{break-inside:avoid}a{color:inherit}}"
    )
    body = ET.SubElement(root, "body")
    ET.SubElement(body, "h1").text = spec["title"]
    ET.SubElement(body, "p").text = spec["source_label"]
    for block in spec["blocks"]:
        if block["type"] == "paragraph":
            ET.SubElement(body, "p").text = block["text"]
        else:
            table = ET.SubElement(body, "table")
            for n, row in enumerate([block["columns"], *block["rows"]]):
                tr = ET.SubElement(table, "tr")
                for cell in row:
                    ET.SubElement(tr, "th" if n == 0 else "td").text = cell
        refs = ET.SubElement(body, "p")
        for key in block["citations"]:
            ET.SubElement(refs, "a", href="#" + key).text = f"[{key}] "
    ET.SubElement(body, "h2").text = "依据清单"
    for key, source in spec["citations"].items():
        section = ET.SubElement(body, "section", id=key)
        ET.SubElement(section, "h3").text = f"[{key}]{source['title']} · 第 {source['version']} 版"
        ET.SubElement(section, "blockquote").text = source["quote"]
        ET.SubElement(section, "p").text = source["locator"]
        ET.SubElement(section, "p").text = "原文位置：" + source["path"]
    ET.SubElement(
        body, "footer"
    ).text = "本报告依据生成时的已核验出处保存；引用可在离线文件中查看，平台中可回到对应原文版本。"
    return b"<!doctype html>\n" + ET.tostring(root, encoding="utf-8", method="html")


def _font(run: Run, style: dict) -> None:
    run.font.name = "Times New Roman"
    run.font.size = Pt(style["size_pt"])
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), style["font"])


def xml_text(text: str) -> str:
    # XML 1.0 permits TAB/LF/CR but excludes controls, surrogates and FFFE/FFFF.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text)


def docx(spec: dict) -> bytes:
    verify(spec)
    document = Document()
    section = document.sections[0]
    for name, key in (
        ("page_width", "width_cm"),
        ("page_height", "height_cm"),
        ("top_margin", "top_cm"),
        ("bottom_margin", "bottom_cm"),
        ("left_margin", "left_cm"),
        ("right_margin", "right_cm"),
    ):
        setattr(section, name, Cm(LAYOUT["page"][key]))

    def paragraph(text: str, title: bool = False) -> Paragraph:
        p = document.add_paragraph()
        fmt = p.paragraph_format
        fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        fmt.line_spacing = Pt(LAYOUT["line_pt"])
        fmt.space_before = fmt.space_after = Pt(0)
        fmt.first_line_indent = Pt(0 if title else 32)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER if title else WD_ALIGN_PARAGRAPH.JUSTIFY
        _font(p.add_run(xml_text(text)), LAYOUT["title" if title else "body"])
        return p

    paragraph(spec["title"], title=True)
    paragraph(spec["source_label"])
    for block in spec["blocks"]:
        if block["type"] == "paragraph":
            paragraph(block["text"])
        else:
            table = document.add_table(rows=0, cols=len(block["columns"]))
            table.style = "Table Grid"
            for row in [block["columns"], *block["rows"]]:
                cells = table.add_row().cells
                for cell, text in zip(cells, row, strict=True):
                    cell.text = xml_text(text)  # Literal text: never a Word field or formula.
                    p = cell.paragraphs[0]
                    p.paragraph_format.line_spacing = 1
                    for run in p.runs:
                        _font(run, LAYOUT["table"])
        p = paragraph("")
        for key in block["citations"]:
            link = OxmlElement("w:hyperlink")
            link.set(qn("w:anchor"), key)
            run = OxmlElement("w:r")
            text = OxmlElement("w:t")
            text.text = f"[{key}] "
            run.append(text)
            link.append(run)
            p._p.append(link)
    paragraph("依据清单")
    for index, (key, source) in enumerate(spec["citations"].items(), 1):
        p = paragraph(f"[{key}]{source['title']} · 第 {source['version']} 版")
        start, end = OxmlElement("w:bookmarkStart"), OxmlElement("w:bookmarkEnd")
        start.set(qn("w:id"), str(index))
        start.set(qn("w:name"), key)
        end.set(qn("w:id"), str(index))
        p._p.insert(0, start)
        p._p.append(end)
        paragraph(source["quote"])
        paragraph(source["locator"] + " · " + source["path"])
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(footer.add_run("— "), LAYOUT["page_number"])
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    _font(footer.add_run(" —"), LAYOUT["page_number"])
    stream = io.BytesIO()
    document.save(stream)
    # Fixed ZIP timestamps make a saved spec reproducible and hash-verifiable.
    stable = io.BytesIO()
    with zipfile.ZipFile(stream) as original, zipfile.ZipFile(stable, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in original.namelist():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, original.read(name))
    return stable.getvalue()
