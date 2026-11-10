"""One dictionary for business reading; never mutate a stored relationship.

semantic_relation returns both the original predicate and its optional, lawful
inverse for a left-to-right business reading. A dependency is not an allegation,
and an explanation / collection / ownership link is not an application record.
"""
from collections.abc import Mapping

# type: (forward reading, inverse reading, reverse for business reading, family)
RELATION_SEMANTICS = {
    'derived_from': ('提炼自', '提炼形成', True, 'provenance'),
    'derived_from_evidence': ('提炼依据', '支撑提炼', True, 'provenance'),
    'sourced_from': ('来源于', '登记形成', True, 'provenance'),
    'references_source': ('引用原文', '被引用为依据', True, 'provenance'),
    'references_material': ('关联原始材料', '为事项提供材料', True, 'provenance'),
    'anchored_in': ('定位原始出处', '提供引用锚点', True, 'provenance'),
    'based_on': ('依据规则', '提供规则依据', True, 'input'),
    'uses_data': ('使用数据口径', '被用作数据口径', True, 'input'),
    'requires_data': ('声明数据依赖', '提供所需数据口径', True, 'input'),
    'references_data': ('数据口径参考', '被作为数据参考', True, 'reference'),
    'references_skill': ('方法参考', '被作为方法参考', True, 'reference'),
    'implements_rule': ('落实规则', '由方法落实', True, 'input'),
    'uses_asset': ('引用已有资产', '供工作引用', True, 'application'),
    'uses_method': ('采用核查方法', '用于核查事项', True, 'application'),
    'produces': ('形成成果', '由工作形成', False, 'formation'),
    'applies_in_demo': ('演示事项适用约定', '关联演示规则', False, 'application'),
    'explains': ('解释规则', '由知识解释', False, 'interpretation'),
    'informs': ('提供核查参考', '得到经验参考', False, 'interpretation'),
    'summarizes': ('总结事项经验', '沉淀形成经验', True, 'provenance'),
    'member_of': ('任职于', '所属任职人员', False, 'organization'),
    'applies_to': ('规范适用于', '受权责规范约束', False, 'organization'),
    'belongs_to': ('归属于', '归集成果', False, 'organization'),
    'owned_by': ('台账归属', '管理台账', False, 'organization'),
    'coordinated_by': ('归口协调', '协调专题', False, 'organization'),
    'collaborates_with': ('协同参与', '参与协同', False, 'organization'),
    'contains': ('归集对象', '归集于', False, 'organization'),
    'contains_matter': ('纳入业务事项', '纳入专题', False, 'organization'),
    'includes_knowledge': ('收录知识', '收入专栏', False, 'curation'),
    'handles': ('跟进事项', '由任务跟进', False, 'organization'),
    'raised_in': ('记录于事项', '形成核对记录', False, 'organization'),
    'maps_to': ('字段对应标准', '由台账字段对应', False, 'input'),
    'supported_by': ('有材料支撑', '支撑事项', True, 'evidence'),
    'supports': ('支撑分析', '得到支撑', False, 'evidence'),
    'supports_matter': ('支持事项', '得到证据支持', False, 'evidence'),
    'explains_matter': ('提供合理解释', '存在合理解释', False, 'evidence'),
    'contradicts_matter': ('提示证据矛盾', '存在相反证据', False, 'evidence'),
    'gap_for_matter': ('指出材料缺口', '尚有材料缺口', False, 'evidence'),
    'reviews': ('复核材料', '由任务复核', False, 'reference'),
    'inspects': ('核对数据口径', '被事项核对', True, 'reference'),
    'related_case': ('参考案例', '被作为案例参考', False, 'reference'),
    'references': ('业务引用', '被引用', False, 'reference'),
    'related_to': ('业务相关', '业务相关', False, 'reference'),
    'refines': ('补充完善', '得到补充', False, 'evolution'),
    'supersedes': ('替代历史版本', '由新版本承接', True, 'evolution'),
    'merged_into': ('并入现行成果', '承接历史内容', False, 'evolution'),
}
FAMILY_LABELS = {'provenance':'来源与形成','input':'输入与依据','formation':'形成与交付','application':'运用与引用','interpretation':'解释与辨析','organization':'工作归属与协同','curation':'知识归集','evidence':'证据与核验','reference':'业务参考','evolution':'版本与承接'}
ROLE_LABELS = {'origins':'来源与形成','inputs':'输入与依据','outputs':'形成的成果','usage':'使用与依赖','interpretation':'解释与辨析','organization':'归属与协同','curation':'专栏与归集','evidence':'证据与核验','reference':'关联参考','history':'历史与承接'}
ROLE_LANES = {'origins':'left','inputs':'left','evidence':'left','outputs':'right','usage':'right','interpretation':'lower','organization':'lower','curation':'lower','reference':'lower','history':'lower'}

def semantic_relation(row, source_kind='', target_kind='', reading='business'):
    """A plain dict usable by graph/detail APIs; original IDs always retained."""
    d = dict(row) if isinstance(row, Mapping) or hasattr(row, 'keys') else row._asdict()
    typ = d.get('type', '')
    forward, inverse, reverse, family = RELATION_SEMANTICS.get(typ, (d.get('label') or typ or '关联', '', False, 'reference'))
    original_source, original_target = d['source'], d['target']
    reversed_for_reading = bool(reading == 'business' and reverse and inverse)
    return {**d, 'source': original_target if reversed_for_reading else original_source,
            'target': original_source if reversed_for_reading else original_target,
            'label': inverse if reversed_for_reading else (d.get('label') or forward),
            'original_source': original_source, 'original_target': original_target,
            'original_type': typ, 'original_label': d.get('label') or forward,
            'forward_label': forward, 'inverse_label': inverse, 'family': family,
            'family_label': FAMILY_LABELS[family], 'display_reversed': reversed_for_reading,
            'source_kind': target_kind if reversed_for_reading else source_kind,
            'target_kind': source_kind if reversed_for_reading else target_kind}

def classify_relation_role(row, center_id, source_kind='', target_kind=''):
    s = semantic_relation(row, source_kind, target_kind)
    family = s['family']; incoming = s['target'] == center_id
    if family == 'provenance': return 'origins' if incoming else 'outputs'
    if family == 'input': return 'inputs' if incoming else 'usage'
    if family == 'formation': return 'origins' if incoming else 'outputs'
    if family == 'application': return 'inputs' if incoming else 'usage'
    return {'evidence':'evidence','interpretation':'interpretation','organization':'organization','curation':'curation','evolution':'history'}.get(family, 'reference')
