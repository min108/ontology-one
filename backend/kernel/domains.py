"""Initial read-only institution domain map (blueprint §8.2)."""

from types import MappingProxyType

DOMAINS = MappingProxyType(
    {
        "procurement": "招标采购",
        "discipline": "纪检监察",
        "finance": "财务资金",
        "hr": "干部人事",
        "investment": "投资并购",
        "tech_security": "科技安全",
        "audit": "审计整改",
        "governance": "公司治理",
        "related_party": "关联交易",
        "integrity": "廉洁从业",
        "conduct": "作风建设",
        "asset": "资产管理",
        "construction": "工程建设",
        "contract": "合同管理",
        "payment": "资金支付",
        "authority": "权限管理",
        "data_governance": "数据治理",
        "risk_compliance": "风险合规",
        "inspection": "巡视巡察",
        "tax": "税务管理",
        "safety": "安全生产",
        "overseas": "境外经营",
        "logistics": "后勤保障",
        "general": "综合",
    }
)
