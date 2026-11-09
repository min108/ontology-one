"""Deterministic free-text structuring for authority drafts.

The local path is always available. It preserves the submitted wording, marks
unknown authority as pending review, and emits a connected, editable graph.
Online models may improve a proposal, but are never required to produce one.
"""
from __future__ import annotations

import math
import re

ROLE_WORDS = (
    '经办人', '经办部门', '申请人', '报销人', '需求部门', '业务部门', '部门负责人',
    '财务复核岗', '财务部门', '财务岗', '财务人员', '会计', '出纳', '采购归口岗',
    '采购部门', '采购岗', '归口部门', '归口管理岗', '法务部门', '法务岗', '合规部门',
    '法务部门', '数据管理部门', '运营部门', '分管领导', '主要负责人', '总经理', '董事长',
    '管理委员会', '董事会', '总经理办公会', '有权审批人', '审批人', '审核人', '人事部门',
    '组织人事部门', '项目负责人', '部门领导', '上级部门', '主管部门', '评审专家',
    '评审小组', '工作组', '办公室', '招标代理机构', '供应商', '组织部门', '业务机构',
)
ACTION_WORDS = (
    '填写', '提交', '提出', '发起', '申请', '说明', '审核', '审批', '审查', '复核', '核实',
    '组织', '登记', '支付', '归档', '办理', '验收', '汇总', '形成', '出具', '签署', '备案',
    '公示', '决定', '会签', '检查', '管理', '跟踪', '评估', '确认', '核对', '审定', '审议',
    '补充', '补正', '退回', '驳回', '终止', '停止', '移交', '报送', '批准', '同意', '执行',
    '编制', '制定', '记录', '留存', '保管', '下达', '通知', '公示', '报告', '回避', '落实',
)
ACTION_RE = re.compile('|'.join(map(re.escape, ACTION_WORDS)))
CONDITION_RE = re.compile(r'如果|若|如|当|是否|否则|反之|未通过|不通过|不符合|超出|超过|不足|不齐|未同意|不同意|未批准|不批准')
BRANCH_RE = re.compile(r'如果|若|如|当|是否|否则|反之|通过|符合|同意|批准|齐全|完整|有效|超出|超过|不足|不齐')
RETURN_RE = re.compile(r'退回|补正|补充|重新|返工|修改后|完善后')
END_RE = re.compile(r'终止|结束|办结|不予办理|不再办理|归档|完成办理|流程完毕')
META_RE = re.compile(r'^(?:事项名称|流程名称|规范名称|标题|适用范围|范围|规范目的|目的|目标|制度依据|授权依据|依据|生效日期|失效日期)\s*[：:]')
LEADING_RE = re.compile(r'^(?:首先|第一|其次|第二|再次|第三|接着|随后|然后|而后|最后|下一步|下一步骤|再则)\s*[，,、：:]?\s*')
NUMBER_RE = re.compile(r'^\s*(?:[（(]?\d{1,2}[）)]?[.、．]|[一二三四五六七八九十]+[、.．]|[①②③④⑤⑥⑦⑧⑨⑩]|步骤\s*\d+\s*[：:]?)\s*')


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip(' \t\r\n，,。；;：:')


def strip_prefix(value):
    result = NUMBER_RE.sub('', clean(value))
    previous = None
    while previous != result:
        previous = result
        result = LEADING_RE.sub('', result).strip()
    return result


def role_from(text):
    value = strip_prefix(text)
    value = re.sub(r'^(?:由|请|待)\s*', '', value)
    if re.search(r'退回经办人|退回申请人', value):
        return '经办人' if '经办人' in value else '申请人'
    for role in sorted(ROLE_WORDS, key=len, reverse=True):
        if re.match(r'(?:提交|报送|提请|移交)' + re.escape(role) + r'(?:审批|审核|审查|审定|审议)', value):
            return role
    # Conditions and convergence phrases often precede the actor: "材料齐全后由…审核".
    for role in sorted(ROLE_WORDS, key=len, reverse=True):
        if re.search(r'由\s*' + re.escape(role) + r'(?=\s*(?:' + '|'.join(ACTION_WORDS) + '))', value):
            return role
    delegated = re.search(r'由\s*([\u4e00-\u9fa5A-Za-z0-9（）()·]{1,22}?(?:部门|岗位|岗|负责人|领导|经办人|申请人|审批人|审核人|委员会|办公会|小组|会计|出纳|主任|专家))(?=\s*(?:' + '|'.join(ACTION_WORDS) + '))', value)
    if delegated:
        return delegated.group(1)
    if re.match(r'(?:提交|报送|移交|退回|通知|请示|审核|审批|复核|批准|同意)', value):
        return ''
    for role in sorted(ROLE_WORDS, key=len, reverse=True):
        if value.startswith(role) and (len(value) == len(role) or ACTION_RE.search(value[len(role):len(role) + 8])):
            return role
    match = re.match(r'([\u4e00-\u9fa5A-Za-z0-9（）()·]{1,22}?(?:部门|岗位|岗|负责人|领导|经办人|申请人|审批人|审核人|委员会|办公会|小组|会计|出纳|主任|专家))(?=\s*(?:负责|对|将|在|按|根据|' + '|'.join(ACTION_WORDS) + '))', value)
    return match.group(1) if match else ''


def action_title(text, role=''):
    value = strip_prefix(text)
    if role:
        value = re.sub(r'^.*?由\s*' + re.escape(role), role, value, count=1)
    value = re.sub(r'^由\s*', '', value)
    if role and value.startswith(role):
        value = value[len(role):].lstrip(' ：:，,')
    value = re.sub(r'^(?:负责|应当|须|需|要)\s*', '', value)
    value = clean(value) or clean(text) or '待明确办理事项'
    if len(value) > 42:
        # Prefer a complete action phrase over a hard cut in the middle of a condition.
        pieces = [p for p in re.split(r'(?<=审核)|(?<=审批)|(?<=复核)|(?<=提交)|(?<=登记)|(?<=支付)', value) if p]
        value = pieces[0] if pieces and len(pieces[0]) >= 6 else value[:42]
    return value[:120]


def condition_segment(segment):
    """Return condition and outcome from one free-form conditional clause."""
    value = strip_prefix(segment)
    match = re.match(r'(?:如果|若|如|当)?\s*(.+?)(?:的情况下|时|后|则|，|,)(.*)$', value)
    if match and BRANCH_RE.search(match.group(1)):
        return clean(re.sub(r'^(?:如果|若|如|当)\s*', '', match.group(1))), clean(match.group(2))
    match = re.match(r'(.*?(?:未通过|不通过|不符合|未同意|不同意|未批准|不批准|超出[^，,]*|超过[^，,]*|不足[^，,]*|不齐[^，,]*|是否[^，,]*))\s*(.*)$', value)
    if match:
        return clean(re.sub(r'^(?:如果|若|如|当)\s*', '', match.group(1))), clean(match.group(2))
    return clean(re.sub(r'^(?:如果|若|如|当)\s*', '', value)), ''


def is_conditional(sentence):
    if re.search(r'如果|若|否则|反之|未通过|不通过|不符合|未同意|不同意|未批准|不批准|超出|超过|不足|不齐', sentence):
        return True
    if re.search(r'通过|符合|同意|批准|齐全|完整|有效', sentence) and re.search(r'则|时|后', sentence):
        return True
    return '是否' in sentence and bool(re.search(r'，|,|？|\?', sentence))


def split_sentences(text):
    normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    normalized = re.sub(r'([。；;])\s*', r'\1\n', normalized)
    output = []
    for block in normalized.split('\n'):
        block = clean(block)
        if not block or META_RE.match(block):
            continue
        # Ordered list markers may occur inside one paragraph.
        pieces = re.split(r'(?=(?:首先|其次|再次|接着|随后|然后|最后|第[一二三四五六七八九十\d]+步)[，,、：:]?)', block)
        for piece in pieces:
            piece = strip_prefix(piece)
            if piece:
                output.append(piece)
    return output


def split_parallel(sentence):
    if not re.search(r'同时|并行|同步|分别', sentence):
        return None
    parts = [strip_prefix(x) for x in re.split(r'[，,；;]', sentence) if strip_prefix(x)]
    marker = next((i for i, part in enumerate(parts) if re.search(r'同时|并行|同步|分别', part)), None)
    if marker is None:
        return None
    marked = re.search(r'同时|并行|同步|分别', parts[marker])
    actors, shared_action = parts[marker][:marked.start()], parts[marker][marked.end():]
    roles = [role for role in sorted(ROLE_WORDS, key=len, reverse=True) if role in actors]
    if len(roles) >= 2:
        # "甲岗和乙岗并行办理，双方完成后…" — split actors, retain the shared action,
        # and let the first suffix node join both branches.
        action = clean(shared_action) or '办理'
        return parts[:marker], [role + action for role in roles], parts[marker + 1:]
    start = marker - 1 if marker > 0 and role_from(parts[marker - 1]) else marker
    end = marker + 1
    while end < len(parts) and re.search(r'同时|并行|同步|分别', parts[end]):
        end += 1
    parallel = parts[start:end]
    if len(parallel) < 2 or sum(bool(role_from(x)) for x in parallel) < 2:
        return None
    return parts[:start], parallel, parts[end:]


def split_actions(sentence):
    parts = [strip_prefix(x) for x in re.split(r'[，,]', sentence) if strip_prefix(x)]
    if len(parts) == 1:
        return parts
    # Keep short enumerations together; split when the next phrase has an actor or action.
    output, current = [], ''
    for part in parts:
        if not current:
            current = part
        elif role_from(part) or ACTION_RE.match(re.sub(r'^(?:并|再|同时|然后|随后)\s*', '', part)):
            output.append(current)
            current = part
        else:
            current += '，' + part
    if current:
        output.append(current)
    return output


def metadata(text):
    result = {}
    pattern = re.compile(r'(?:^|\n)\s*(事项名称|流程名称|规范名称|标题|适用范围|范围|规范目的|目的|目标|制度依据|授权依据|依据|生效日期|失效日期)\s*[：:]\s*([^\n。；]+)')
    for match in pattern.finditer(str(text or '')):
        result[match.group(1)] = clean(match.group(2))
    return result


def node(ident, title, kind='step', role='', responsibility='', org_id=''):
    return {
        'id': ident, 'kind': kind, 'title': clean(title)[:120], 'org_id': org_id,
        'role': role[:160], 'person_id': '', 'responsibility': str(responsibility or '')[:3000],
        'authority': '', 'control': '', 'evidence': '', 'time_limit': '', 'x': 0, 'y': 0,
    }


def edge(source, target, label=''):
    return {'id': f'e-{source}-{target}-{abs(hash((source, target, label))) % 100000}', 'source': source, 'target': target, 'label': clean(label)[:160]}


def match_org(text, role, organizations):
    for org in organizations:
        title = clean(org.get('title'))
        if title and (title in text or title in role):
            return org['id']
    return ''


def enrich(item, raw, organizations):
    if re.search(r'不得|严禁|应当|必须|须按|管理|回避|分离|制约', raw):
        item['control'] = raw[:3000]
    if re.search(r'授权|权限|批准|审批|决定|审定|审议', raw):
        item['authority'] = raw[:3000]
    if re.search(r'留痕|留存|归档|形成.*(?:记录|材料|报告|意见|凭证)|保存', raw):
        item['evidence'] = raw[:3000]
    limit = re.search(r'[^，。；]{0,24}\d+\s*(?:个)?(?:工作日|日|天|小时)(?:内|以内)?', raw)
    if limit:
        item['time_limit'] = clean(limit.group(0))[:200]
    item['org_id'] = match_org(raw, item.get('role', ''), organizations)


def layout(nodes, edges):
    ids = {n['id'] for n in nodes}
    degrees = {n['id']: 0 for n in nodes}
    outgoing = {n['id']: [] for n in nodes}
    for item in edges:
        if item['source'] in ids and item['target'] in ids and item['source'] != item['target']:
            degrees[item['target']] += 1
            outgoing[item['source']].append(item['target'])
    queue = [n['id'] for n in nodes if degrees[n['id']] == 0]
    levels = {ident: 0 for ident in queue}
    for ident in queue:
        for target in outgoing[ident]:
            levels[target] = max(levels.get(target, 0), levels[ident] + 1)
            degrees[target] -= 1
            if degrees[target] == 0:
                queue.append(target)
    visited = set(queue)
    base = max(levels.values(), default=-1) + 1
    for item in nodes:
        if item['id'] in visited:
            continue
        pending = [item['id']]
        visited.add(item['id'])
        levels[item['id']] = base
        for current in pending:
            for target in outgoing[current]:
                if target not in visited:
                    visited.add(target)
                    levels[target] = levels[current] + 1
                    pending.append(target)
        base = max(levels.values(), default=base) + 1
    columns = {}
    for item in nodes:
        columns.setdefault(levels.get(item['id'], 0), []).append(item['id'])
    height = max((len(v) for v in columns.values()), default=1)
    for level, members in columns.items():
        for index, ident in enumerate(members):
            item = next(n for n in nodes if n['id'] == ident)
            item['x'] = 48 + level * 292
            item['y'] = 48 + (height - len(members)) * 84 + index * 168


def compact_units(units):
    if len(units) <= 14:
        return units, []
    size = math.ceil(len(units) / 14)
    groups = ['；'.join(units[i:i + size]) for i in range(0, len(units), size)]
    return groups, [f'原始描述包含 {len(units)} 个语句，为保持画布可读性已按原顺序合并为 {len(groups)} 组；完整语句保留在各节点职责中。']


def workflow_spec(subtype, text, sources, organizations, people, org_ids, person_ids):
    meta = metadata(text)
    units, compact_warnings = compact_units(split_sentences(text))
    if not units:
        units = [clean(text) or '待明确办理事项']
    nodes = [node('start', '事项发起', 'start', responsibility='根据原始描述启动本事项。')]
    edges, history = [], []
    frontier = [('start', '')]
    counter = 0
    decision_counter = 0
    end_counter = 0
    warnings = list(compact_warnings)

    def connect(from_id, to_id, label=''):
        if from_id == to_id or any(e['source'] == from_id and e['target'] == to_id and e['label'] == clean(label) for e in edges):
            return
        edges.append(edge(from_id, to_id, label))

    def add_step(raw, from_frontier, label_override=None):
        nonlocal counter
        counter += 1
        role = role_from(raw) or '责任主体待核对'
        ident = f'step-{counter}'
        item = node(ident, action_title(raw, '' if role == '责任主体待核对' else role), role=role, responsibility=raw)
        enrich(item, raw, organizations)
        nodes.append(item)
        history.append(item)
        for source_id, label in from_frontier:
            connect(source_id, ident, label_override if label_override is not None else label)
        return [(ident, '')]

    def add_end(from_frontier, title='流程结束'):
        nonlocal end_counter
        end_counter += 1
        ident = f'end-{end_counter}'
        nodes.append(node(ident, title, 'end', responsibility='本办理路径结束；归档与留痕要求按实际制度核对。'))
        for source_id, label in from_frontier:
            connect(source_id, ident, label)
        return ident

    def add_return(raw, from_frontier):
        nonlocal counter
        counter += 1
        role = role_from(raw) or '补正责任主体待核对'
        ident = f'step-{counter}'
        item = node(ident, action_title(raw, '' if role == '补正责任主体待核对' else role) or '补充后重新办理', role=role, responsibility=raw)
        enrich(item, raw, organizations)
        nodes.append(item)
        for source_id, label in from_frontier:
            connect(source_id, ident, label)
        candidates = [n for n in history if not role or role in n.get('role', '') or n.get('role', '') in role]
        if re.search(r'重新提交|重新申请|重新填写|退回经办|退回申请', raw) and history:
            target = history[0]
        else:
            target = candidates[-1] if candidates else (history[-1] if history else None)
        if target:
            connect(ident, target['id'], '补正后重新办理')
        elif from_frontier and from_frontier[0][0] != ident:
            connect(ident, from_frontier[0][0], '补正后重新核对')
        return []

    for sentence in units:
        parallel = split_parallel(sentence)
        if parallel:
            prefix, concurrent, suffix = parallel
            for part in prefix:
                if is_conditional(part):
                    condition, _ = condition_segment(part)
                    frontier = [(source_id, condition or label) for source_id, label in frontier]
                else:
                    frontier = add_step(part, frontier)
            branches = []
            for part in concurrent:
                branches.extend(add_step(part, frontier, '并行办理'))
            frontier = branches
            for part in suffix:
                if END_RE.search(part) and len(part) <= 24 and not re.search(r'提交|审核|审批|复核|形成|保存', part):
                    add_end(frontier, action_title(part))
                    frontier = []
                else:
                    frontier = add_step(part, frontier)
            continue
        if is_conditional(sentence):
            if not frontier and history:
                anchor_text = re.split(r'未通过|不通过|不符合|通过后|符合后|同意后|批准后|不齐全|齐全后', sentence, maxsplit=1)[0]
                anchor = next((n for n in reversed(history) if anchor_text and anchor_text in n.get('responsibility', '')), history[-1])
                frontier = [(anchor['id'], '')]
            parts = [strip_prefix(x) for x in re.split(r'[，,]', sentence) if strip_prefix(x)]
            condition_indexes = [i for i, part in enumerate(parts) if CONDITION_RE.search(part)]
            first = condition_indexes[0] if condition_indexes else 0
            for prefix in split_actions('，'.join(parts[:first])) if first else []:
                frontier = add_step(prefix, frontier)
            remaining = parts[first:]
            branch_texts = []
            conditional_suffix = []
            else_index = next((i for i, part in enumerate(remaining) if re.match(r'否则|反之', part)), None)
            if else_index is not None:
                first_text = '，'.join(remaining[:else_index])
                second_text = re.sub(r'^(?:否则|反之)[，,：:]?\s*', '', '，'.join(remaining[else_index:]))
                condition1, outcome1 = condition_segment(first_text)
                branch_texts = [(condition1, outcome1), ('其他情况', second_text)]
            else:
                second_condition = next((i for i in range(1, len(remaining)) if BRANCH_RE.search(remaining[i]) and re.search(r'时|后|则', remaining[i])), None)
                if second_condition is not None:
                    condition1, outcome1 = condition_segment('，'.join(remaining[:second_condition]))
                    condition2, outcome2 = condition_segment(remaining[second_condition])
                    conditional_suffix = remaining[second_condition + 1:]
                    branch_texts = [(condition1, outcome1), (condition2, outcome2)]
                else:
                    condition1, outcome1 = condition_segment(remaining[0])
                    if not outcome1 and len(remaining) > 1:
                        outcome1 = '，'.join(remaining[1:])
                    branch_texts = [(condition1, outcome1)]
            if not branch_texts or not branch_texts[0][0]:
                branch_texts = [('是否符合原始描述所列条件', sentence)]
            decision_counter += 1
            decision_id = f'decision-{decision_counter}'
            decision_title = branch_texts[0][0]
            if '是否' not in decision_title:
                decision_title = '条件判断：' + decision_title
            role_key = re.split(r'未通过|不通过|不符合|通过后|符合后|同意后|批准后|不齐全|齐全后', sentence, maxsplit=1)[0]
            role_source = next((n for n in reversed(history) if role_key and role_key in n.get('responsibility', '')), history[-1] if history else None)
            decision = node(decision_id, decision_title, 'decision', role=role_source.get('role', '') if role_source else '条件判断责任主体待核对', responsibility=sentence)
            decision['org_id'] = match_org(sentence, decision['role'], organizations)
            nodes.append(decision)
            for source_id, label in frontier:
                connect(source_id, decision_id, label)
            next_frontier = []
            for condition, outcome in branch_texts:
                start_branch = [(decision_id, condition or '符合条件')]
                if not outcome:
                    next_frontier.extend(start_branch)
                elif RETURN_RE.search(outcome):
                    next_frontier.extend(add_return(outcome, start_branch))
                elif END_RE.search(outcome) and not ACTION_RE.search(re.sub(END_RE, '', outcome)):
                    add_end(start_branch, action_title(outcome))
                else:
                    next_frontier.extend(add_step(outcome, start_branch))
            if len(branch_texts) == 1:
                next_frontier.append((decision_id, '其他情况（原文未明确）'))
                warnings.append(f'“{sentence[:70]}”仅明确了一种条件路径，另一分支已标注为待核对，请在采用前补充。')
            frontier = next_frontier
            for suffix in conditional_suffix:
                for action in split_actions(suffix):
                    frontier = add_step(action, frontier)
            continue
        for action in split_actions(sentence):
            if not action:
                continue
            if END_RE.search(action) and len(action) <= 24 and not re.search(r'提交|审核|审批|复核|形成|保存', action):
                add_end(frontier, action_title(action))
                frontier = []
            else:
                frontier = add_step(action, frontier)
    if not history:
        frontier = add_step(clean(text) or '待明确办理事项', frontier)
    if frontier:
        add_end(frontier)
    if not any(n['kind'] == 'end' for n in nodes):
        # A completely cyclic description still needs an explicit, reviewable exit.
        source = next((n for n in reversed(nodes) if n['kind'] == 'decision'), nodes[-1])
        add_end([(source['id'], '满足办结条件（待核对）')])
        warnings.append('原始描述未明确正常办结出口，已增加“满足办结条件（待核对）”分支，请人工确认。')
    layout(nodes, edges)
    scope = meta.get('适用范围') or meta.get('范围') or '依据本次原始描述所涉事项；具体适用组织、业务范围和例外条件待人工核对。'
    purpose = meta.get('规范目的') or meta.get('目的') or meta.get('目标') or '将原始描述中的办理主体、先后顺序、条件分支与补正路径转化为可编辑流程，供人工核对。'
    basis = meta.get('制度依据') or meta.get('授权依据') or meta.get('依据') or '原始描述未注明正式制度或授权依据，提交前须补充并核验。'
    refs = [{'material_id': s['material_id'], 'quote': s['text'], 'source_version': s['source_version']} for s in sources if s.get('text')]
    return {
        'type': 'workflow', 'subtype': subtype, 'purpose': purpose[:3000], 'basis': basis[:5000],
        'scope': scope[:3000], 'effective_from': meta.get('生效日期', '')[:10], 'effective_to': meta.get('失效日期', '')[:10],
        'org_ids': list(dict.fromkeys(org_ids + [n['org_id'] for n in nodes if n.get('org_id')])),
        'person_ids': list(dict.fromkeys(person_ids)), 'nodes': nodes[:80], 'edges': edges[:180], 'sections': [],
        'source_refs': refs[:30], 'notes': '本地结构化解析按原始语句生成，不补写金额、时限、任职或授权。条件缺失、并行汇合与退回目标均需人工复核。',
    }, warnings


def macro_spec(subtype, text, sources, organizations, people, org_ids, person_ids):
    units = [action for sentence in split_sentences(text) for action in split_actions(sentence)] or [clean(text)]
    nodes, edges = [], []
    for index, raw in enumerate(units[:60], 1):
        role = role_from(raw)
        title = role or action_title(raw, role)
        item = node(f'unit-{index}', title, 'unit', role=role, responsibility=raw)
        item['org_id'] = match_org(raw, role, organizations)
        nodes.append(item)
    for index in range(1, len(nodes)):
        edges.append(edge(nodes[0]['id'], nodes[index]['id'], '职责协同（待核对）'))
    layout(nodes, edges)
    meta = metadata(text)
    refs = [{'material_id': s['material_id'], 'quote': s['text'], 'source_version': s['source_version']} for s in sources if s.get('text')]
    return {
        'type': 'workflow', 'subtype': subtype,
        'purpose': (meta.get('规范目的') or meta.get('目的') or '按原始描述整理机构职责单元及协同关系，具体隶属与职责边界待人工核对。')[:3000],
        'basis': (meta.get('制度依据') or meta.get('依据') or '原始描述未注明正式组织职责或授权文件，提交前须补充并核验。')[:5000],
        'scope': (meta.get('适用范围') or meta.get('范围') or clean(text)[:500])[:3000],
        'effective_from': '', 'effective_to': '', 'org_ids': list(dict.fromkeys(org_ids + [n['org_id'] for n in nodes if n['org_id']])),
        'person_ids': list(dict.fromkeys(person_ids)), 'nodes': nodes, 'edges': edges, 'sections': [], 'source_refs': refs,
        'notes': '职责协同线为结构化整理建议，不表示已核定的行政隶属关系。',
    }, ['组织职责的层级与协同关系须依据正式文件逐项核对；本地解析不会推定上下级关系。']


def position_spec(subtype, text, sources, organizations, people, org_ids, person_ids):
    headings = [
        ('duties', '主要职责', re.compile(r'职责|负责|组织|办理|管理|审核|复核|落实|推进|协调')),
        ('authority', '行权范围与权限边界', re.compile(r'权限|授权|审批|批准|决定|不得|严禁|禁止|范围|边界')),
        ('control', '行权程序与内控要求', re.compile(r'程序|流程|管理|制约|回避|分离|报告|检查|风险')),
        ('accountability', '责任落实与履职记录', re.compile(r'责任|留痕|留存|记录|归档|交接|台账|材料|考核|追责')),
    ]
    groups = {key: [] for key, _, _ in headings}
    custom = []
    for raw in split_sentences(text):
        heading = re.match(r'([^：:]{2,24})[：:](.+)$', raw)
        if heading and not META_RE.match(raw):
            custom.append((clean(heading.group(1)), clean(heading.group(2))))
            continue
        matches = {key for key, _, pattern in headings if pattern.search(raw)}
        target = next((key for key in ('authority', 'control', 'accountability', 'duties') if key in matches), 'duties')
        groups[target].append(raw)
    sections = []
    if custom:
        for index, (title, content) in enumerate(custom[:30], 1):
            sections.append({'id': f'custom-{index}', 'title': title[:120], 'content': content[:8000]})
    else:
        for key, title, _ in headings:
            values = groups[key]
            if not values:
                continue
            content = '\n'.join(values)
            for part in range(0, len(content), 7000):
                sections.append({'id': key + (f'-{part // 7000 + 1}' if part else ''), 'title': title + ('（续）' if part else ''), 'content': content[part:part + 7000]})
    if not sections:
        sections = [{'id': 'duties', 'title': '主要职责', 'content': clean(text)[:8000]}]
    meta = metadata(text)
    refs = [{'material_id': s['material_id'], 'quote': s['text'], 'source_version': s['source_version']} for s in sources if s.get('text')]
    return {
        'type': 'position', 'subtype': subtype,
        'purpose': (meta.get('规范目的') or meta.get('目的') or '根据原始描述整理岗位职责、权限边界、内控要求与履职记录，供人工核对。')[:3000],
        'basis': (meta.get('制度依据') or meta.get('授权依据') or meta.get('依据') or '原始描述未注明正式制度或授权依据，提交前须补充并核验。')[:5000],
        'scope': (meta.get('适用范围') or meta.get('范围') or clean(text)[:500])[:3000],
        'effective_from': '', 'effective_to': '', 'org_ids': list(dict.fromkeys(org_ids)), 'person_ids': list(dict.fromkeys(person_ids)),
        'nodes': [], 'edges': [], 'sections': sections[:40], 'source_refs': refs[:30],
        'notes': '本地结构化解析仅归类原始语句，不推定未提供的任职、权限或制度条款。',
    }, []


def build_local_proposal(kind, subtype, text, sources, organizations, people, org_ids, person_ids):
    combined = '\n'.join([str(text or '').strip()] + [s.get('text', '') for s in sources]).strip()
    if kind == 'position':
        spec, warnings = position_spec(subtype, combined, sources, organizations, people, org_ids, person_ids)
    elif subtype == '组织职责方案':
        spec, warnings = macro_spec(subtype, combined, sources, organizations, people, org_ids, person_ids)
    else:
        spec, warnings = workflow_spec(subtype, combined, sources, organizations, people, org_ids, person_ids)
    meta = metadata(combined)
    title = meta.get('事项名称') or meta.get('流程名称') or meta.get('规范名称') or meta.get('标题') or (subtype or ('岗位权责规范' if kind == 'position' else '业务审批流程')) + '（原始描述整理）'
    count = len(spec['sections']) if kind == 'position' else len([n for n in spec['nodes'] if n['kind'] not in ('start', 'end')])
    summary = f'根据提供的自由文本整理为 {count} 个' + ('职责内容卡片' if kind == 'position' else '办理与判断环节') + '；保留条件、补正及并行关系，采用前请逐项核对。'
    return {'title': title[:120], 'summary': summary[:3000], 'spec': spec,
            'warnings': warnings, 'model': '本地结构化解析（无需在线模型）', 'mode': 'local'}
