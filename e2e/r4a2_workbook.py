"""Fill three rows of the production download template for the original Excel UI drill."""

import sys

from openpyxl import load_workbook


def fill_template(path: str, output: str) -> None:
    book = load_workbook(path)
    sheet = book["资产导入"]
    positions = {cell.value: cell.column for cell in sheet[1]}
    for index, (title, unit) in enumerate((("归档资料份数", "份"), ("复核人员数", "人"), ("合同页数", "页")), 1):
        values = {
            "数据项编码": f"D.05.88.{index:03d}",
            "二级分类": "付款复核",
            "三级分类": "档案",
            "数据项名称": title,
            "业务含义": "按已核对的原始资料登记" + title,
            "数据形态": "结构化",
            "资产归属": "经营管理",
            "敏感级别": "内部",
            "单位": unit,
            "粒度": "每笔归档记录",
        }
        for header, value in values.items():
            sheet.cell(index + 1, positions[header], value)
    book.save(output)
    book.close()


if __name__ == "__main__":
    fill_template(*sys.argv[1:])
