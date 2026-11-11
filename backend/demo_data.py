"""Small, explicitly synthetic business examples. No network or model calls."""
from __future__ import annotations

PACK = 'business-demo-0.7'
SOURCE_ID = 'DEMO07-SOURCE-LEDGER'
SOURCE_CONFIG = {'fixture': PACK, 'allowed_objects': ['contracts', 'payments']}
CONTRACTS = [
    {'contract_id': 'DEMO-C001', 'amount_yuan': 100000, 'paid_yuan': 40000, 'attachment_ready': True},
    {'contract_id': 'DEMO-C002', 'amount_yuan': 150000, 'paid_yuan': 150000, 'attachment_ready': True},
    {'contract_id': 'DEMO-C003', 'amount_yuan': 50000, 'paid_yuan': 0, 'attachment_ready': None},
    {'contract_id': 'DEMO-C004', 'amount_yuan': 200000, 'paid_yuan': 80000, 'attachment_ready': True},
]
PAYMENTS = [
    {'payment_id': 'DEMO-P001', 'contract_id': 'DEMO-C001', 'payment_yuan': 40000},
    {'payment_id': 'DEMO-P002', 'contract_id': 'DEMO-C002', 'payment_yuan': 100000},
    {'payment_id': 'DEMO-P003', 'contract_id': 'DEMO-C002', 'payment_yuan': 50000},
    {'payment_id': 'DEMO-P004', 'contract_id': 'DEMO-C004', 'payment_yuan': 50000},
    {'payment_id': 'DEMO-P005', 'contract_id': 'DEMO-C004', 'payment_yuan': 30000},
]
TABLES = {'contracts': CONTRACTS, 'payments': PAYMENTS}
FIELDS = {
    'contract_id': ('合同编号', 'string', '无', '模拟合同的唯一编号，保留完整字符；付款明细通过此编号关联合同。'),
    'amount_yuan': ('合同金额', 'decimal', '元', '模拟合同约定的人民币金额，单位为元；不是付款金额。'),
    'paid_yuan': ('累计付款金额', 'decimal', '元', '模拟合同截至本次观察累计已支付的人民币金额，单位为元。'),
    'attachment_ready': ('附件齐备情况', 'boolean', '无', '模拟附件核对结果；空值表示未核实，不能据此认定违规或未履行。'),
    'payment_id': ('付款记录编号', 'string', '无', '模拟付款记录的唯一编号，同一合同可有多条付款。'),
    'payment_yuan': ('本次付款金额', 'decimal', '元', '模拟付款明细中本次支付的人民币金额，单位为元。'),
}
RULE_TEXT = '''# 【演示】合同付款核对说明

本文及全部编号、金额均为模拟资料，不具有制度效力。
模拟核对要求：合同金额大于100000元时，应核实审批依据；等于100000元不属于此条件。
累计付款金额与合同金额应按同一合同编号比较。未付款余额为合同金额减累计付款金额，最低为0。
附件信息为空只表示尚未核实，不能据此认定未履行合同、违规付款或责任归属。
本次观察日为2026年11月1日。结果仅作演示，正式业务结论须由人员核对原始凭证。
'''
INTERVIEW_TEXT = '''# 【演示】业务人员访谈记录

以下为模拟访谈，不涉及真实单位或人员。
业务人员：我希望先看合同金额超过十万元的记录，再核实审批材料，不把十万元整算进去。
业务人员：附件空白可能是还没补录，我会先追问经办人，不能直接说它违规。
业务人员：付款明细是一笔付款一行，合同台账是一份合同一行，关联时要先按合同编号汇总付款。
业务人员：核对结果应该写明用了什么口径、还缺什么材料，最后由审核人员确认。
'''
AUTHORITY_TEXT = '''# 【演示】付款申请职责与流程

全部岗位和要求均为模拟设置，不是实际制度或真实人员任职。
经办岗位提交付款申请并补齐合同、验收和审批依据；部门负责人核对业务真实性及必要性。
金额大于100000元时，部门负责人核对后交财务复核岗位；金额小于或等于100000元时，由部门负责人核准后交付款办理岗位。
资料不齐时，部门负责人退回经办岗位补正；财务复核发现口径问题时，退回部门负责人核对后重新提交。
付款办理岗位登记办理结果。任何审批结果均不得替代对真实合同和原始凭证的核实。
'''
SPECIAL_TEXT = '''# 【演示】专项事项与依据时间线

以下日期、事项和材料全部为模拟资料，仅供专项成果梳理练习。
模拟事项A最初计划在2026年4月10日完成，实际提交时间为2026年4月15日。
经模拟补充安排，完成期限调整为2026年4月20日；验收记录显示2026年4月18日已确认接收。
原计划日期、实际提交日期、调整后期限和验收日期应分别保留，不应把它们归并为一个时间。
是否构成延期、违反约定或责任归属，仍须核对适用约定、变更依据和完整原件，不能仅凭日期作结论。
'''
SKILL_FILES = {
    'SKILL.md': '''---
name: demo-contract-balance
description: 仅用模拟金额核对未付款余额，供平台演示，不作责任或合规结论。
---

# 【演示】合同付款余额核对

输入合同金额和累计付款金额，金额单位均为元。
未付款余额等于两者差额，最低为零；付款超过合同金额时另行提示。
结果只表示算术差额，不能据此认定违规、未履约或责任归属。
''',
    'scripts/balance.py': '''import json, sys
value = json.load(sys.stdin)
amount, paid = value['amount'], value['paid']
print(json.dumps({'unpaid': max(0, amount - paid), 'overpaid': paid > amount}))
''',
}
SKILL_MANIFEST = {
    'schema_version': '1.0', 'applicability': '只用于模拟合同金额核对；金额单位须同为元。',
    'inputs': {'type': 'object', 'properties': {'amount': {'type': 'number', 'minimum': 0, 'title': '合同金额', 'description': '合同约定的总金额，单位为元。'}, 'paid': {'type': 'number', 'minimum': 0, 'title': '累计付款金额', 'description': '截至观察时点已经支付的金额，单位为元。'}}, 'required': ['amount', 'paid'], 'additionalProperties': False},
    'outputs': {'type': 'object', 'properties': {'unpaid': {'type': 'number', 'minimum': 0, 'title': '未付款余额', 'description': '合同金额减累计付款金额，最低为零，单位为元。'}, 'overpaid': {'type': 'boolean', 'title': '是否超过合同金额', 'description': '只表示算术比较结果，不代表违规或责任认定。'}}, 'required': ['unpaid', 'overpaid'], 'additionalProperties': False},
    'steps': [{'title': '核对模拟金额', 'instruction': '按同一合同和相同单位比较合同金额与累计付款，计算非负余额并提示超付，不作责任判断。'}],
    'entrypoint': 'scripts/balance.py',
    'test_cases': [
        {'name': '部分付款', 'input': {'amount': 100, 'paid': 30}, 'expected_output': {'unpaid': 70, 'overpaid': False}},
        {'name': '刚好付清', 'input': {'amount': 100, 'paid': 100}, 'expected_output': {'unpaid': 0, 'overpaid': False}},
        {'name': '超过合同金额', 'input': {'amount': 100, 'paid': 120}, 'expected_output': {'unpaid': 0, 'overpaid': True}},
    ], 'dependencies': [], 'tool_dependencies': [],
}


def resources():
    """Return new values so discovery callers cannot mutate the examples."""
    import copy
    values = []
    for key, rows in TABLES.items():
        primary = 'contract_id' if key == 'contracts' else 'payment_id'
        values.append({'key': key, 'name': '【演示】合同台账' if key == 'contracts' else '【演示】付款明细',
                       'kind': 'table', 'description': '固定合成记录，无外部数据库连接。', 'primary_key': [primary],
                       'columns': [{'name': name, 'type': {'decimal': 'decimal', 'string': 'varchar', 'boolean': 'boolean'}[FIELDS[name][1]],
                                    'nullable': name == 'attachment_ready', 'primary_key': name == primary,
                                    'comment': FIELDS[name][3]} for name in rows[0]]})
    return copy.deepcopy(values)


def query(resource, selection):
    """Execute only the already validated projection over fixed synthetic rows."""
    import operator
    rows = [dict(row) for row in TABLES[resource]]
    comparisons = {'eq': operator.eq, 'ne': operator.ne, 'gt': operator.gt,
                   'gte': operator.ge, 'lt': operator.lt, 'lte': operator.le}
    def matches(row, condition):
        left, right, op = row[condition['column']], condition.get('value'), condition['op']
        if op == 'is_null': return (left is None) == (right is not False)
        if op == 'in': return left in right
        if op in {'eq', 'ne'}: return comparisons[op](left, right)
        if left is None or right is None: return False
        try: return comparisons[op](left, right)
        except TypeError: return False
    rows = [row for row in rows if all(matches(row, condition) for condition in selection['filters'])]
    for order in reversed(selection['order_by']):
        rows.sort(key=lambda row: (row[order['column']] is not None, row[order['column']]), reverse=order['direction'] == 'desc')
    count = len(rows)
    return {'columns': selection['columns'], 'rows': [{name: row[name] for name in selection['columns']} for row in rows[:selection['limit']]],
            'truncated': count > selection['limit'], 'elapsed_ms': 0}
