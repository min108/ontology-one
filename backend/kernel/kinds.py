"""Canonical element kinds shared by the kernel and every presentation binding."""

from dataclasses import asdict, dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class KindDefinition:
    kind: str
    pillar: str
    family: str | None
    name: str
    icon: str
    color_token: str
    id_prefix: str
    payload_schema: str | None
    governed: bool
    module_eligible: bool
    card_template: str


def _kind(
    kind: str,
    pillar: str,
    name: str,
    prefix: str,
    *,
    family: str | None = None,
    new: bool = False,
    icon: str = "file-text",
) -> KindDefinition:
    return KindDefinition(
        kind,
        pillar,
        family,
        name,
        icon,
        "--zy-color-text-secondary",
        prefix,
        f"schemas/{kind}.json" if new else None,
        pillar == "asset",
        pillar == "asset",
        kind,
    )


KINDS = MappingProxyType(
    {
        item.kind: item
        for item in (
            _kind("organization", "identity", "单位 / 部门", "ORG-", icon="building"),
            _kind("position", "identity", "岗位", "POS-", new=True, icon="briefcase"),
            _kind("person", "identity", "人员", "PERSON-USER-", icon="user"),
            _kind("party", "identity", "外部主体", "PTY-", new=True, icon="building"),
            _kind("material", "source", "材料", "MATERIAL-USER-"),
            _kind("dataset", "source", "数据台账", "DATASET-USER-", icon="table"),
            _kind("term", "asset", "术语", "TM-", family="data_standard", new=True, icon="book-open"),
            _kind("object_type", "asset", "业务对象", "OT-", family="data_standard", new=True, icon="box"),
            _kind("link_type", "asset", "对象关系", "LT-", family="data_standard", new=True, icon="link"),
            _kind("data", "asset", "数据项", "DATA-USER-", family="data_standard", icon="table"),
            _kind("metric", "asset", "指标口径", "MT-", family="data_standard", new=True, icon="chart"),
            _kind("rule", "asset", "规则", "RULE-USER-", family="rule_boundary", icon="shield"),
            _kind("skill", "asset", "技能", "SKILL-USER-", family="work_method", icon="tool"),
            _kind("process", "asset", "业务流程", "PRC-", family="work_method", new=True, icon="workflow"),
            _kind("knowledge", "asset", "知识经验", "KNOWLEDGE-USER-", family="knowledge", icon="book-open"),
            _kind("project", "event", "场景工作室", "PROJECT-USER-"),
            _kind("task", "event", "任务", "TASK-USER-"),
            _kind("matter", "event", "核查事项", "MATTER-USER-"),
            _kind("evidence", "event", "依据引用", "EVIDENCE-USER-"),
            _kind("issue", "event", "问题线索", "ISSUE-USER-"),
            _kind("case", "event", "案例", "CASE-USER-"),
            _kind("analysis", "event", "分析记录", "ANALYSIS-USER-"),
            _kind("collection", "aux", "知识专栏", "COLLECTION-USER-", icon="folder"),
        )
    }
)


def get_kind(kind: str) -> KindDefinition:
    # Historic algorithm rows remain readable without rewriting their contents.
    canonical = "skill" if kind == "algorithm" else kind
    try:
        return KINDS[canonical]
    except (KeyError, TypeError):
        raise ValueError("未知的要素种类。") from None


def list_kinds() -> list[dict]:
    return [asdict(item) for item in KINDS.values()]


def persisted_kind(kind: str) -> KindDefinition:
    """Read/edit an existing legacy row without registering new creation types."""
    try:
        return get_kind(kind)
    except ValueError:
        return _kind(kind, "aux", "历史内容", "LEGACY-")


def requires_quality_gates(kind: str) -> bool:
    """Gate applicability is independent of asset workflow/module ownership.

    Blueprint §4.9 also requires QG2 and QG5 for positions/external parties.
    Their existing administrative write paths and role policy stay unchanged.
    """
    definition = persisted_kind(kind)
    return definition.governed or definition.kind in {"position", "party"}
