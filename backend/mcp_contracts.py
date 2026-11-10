"""Versioned public contracts for the fixed, read-only ontology MCP tools."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator

from .sources import QueryBody, QueryFilter

CONTRACT_VERSION = '1.0'
MAX_RESULT_BYTES = 2 * 1024 * 1024
RETENTION = {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'}
Identifier = Annotated[str, Field(min_length=1, max_length=191)]
SemanticField = Annotated[str, Field(min_length=1, max_length=64)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ToolInput(StrictModel):
    contract_version: Literal['1.0'] = '1.0'
    timeout_ms: Annotated[StrictInt, Field(ge=1000, le=30000)] = 30000


class DiscoverInput(ToolInput):
    query: Annotated[str, Field(max_length=200)] = ''
    kind: Literal['data', 'rule', 'algorithm', 'knowledge', 'organization'] | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 30


class SemanticQueryInput(ToolInput):
    mapping_id: Identifier
    query: QueryBody = Field(default_factory=QueryBody)


class TraverseInput(ToolInput):
    entity_id: Identifier
    depth: Annotated[StrictInt, Field(ge=1, le=3)] = 1
    max_nodes: Annotated[StrictInt, Field(ge=1, le=100)] = 50
    max_edges: Annotated[StrictInt, Field(ge=1, le=200)] = 100


class ExplainInput(ToolInput):
    mapping_id: Identifier | None = None
    entity_id: Identifier | None = None

    @model_validator(mode='after')
    def one_target(self):
        if (self.mapping_id is None) == (self.entity_id is None):
            raise ValueError('Exactly one explanation target is required')
        return self


class JoinLeft(StrictModel):
    mapping_id: Identifier
    query: QueryBody = Field(default_factory=QueryBody)

    @model_validator(mode='after')
    def bounded_left(self):
        if self.query.limit > 50:
            raise ValueError('The left join step is limited to 50 rows')
        return self


class JoinRight(StrictModel):
    mapping_id: Identifier
    fields: Annotated[list[SemanticField] | None, Field(min_length=1, max_length=200)] = None
    filters: Annotated[list[QueryFilter], Field(max_length=11)] = Field(default_factory=list)


class JoinInput(ToolInput):
    left: JoinLeft
    right: JoinRight
    left_key: SemanticField
    right_key: SemanticField
    join_type: Literal['inner', 'left'] = 'inner'
    max_rows: Annotated[StrictInt, Field(ge=1, le=200)] = 100
    allow_partial: StrictBool = False

    @model_validator(mode='after')
    def distinct_mappings(self):
        if self.left.mapping_id == self.right.mapping_id:
            raise ValueError('Join requires two distinct source mappings')
        return self


class Receipt(StrictModel):
    purpose: Literal['query', 'diagnostic'] = 'query'
    tenant_id: Identifier
    source_id: Identifier
    mapping_id: Identifier
    mapping_version: Annotated[StrictInt, Field(ge=1)]
    source_version: Annotated[StrictInt, Field(ge=1)]
    schema_hash: Annotated[str, Field(min_length=64, max_length=64)]


class SafeError(StrictModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=500)]
    retryable: StrictBool = False


class WarningMessage(StrictModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=500)]


class Retention(StrictModel):
    result: Literal['response_only'] = 'response_only'
    cache: Literal['disabled'] = 'disabled'
    audit: Literal['metadata_only'] = 'metadata_only'


class ToolEnvelope(StrictModel):
    contract_version: Literal['1.0'] = '1.0'
    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    status: Literal['ok', 'partial', 'error']
    data: dict[str, Any] | None = None
    error: SafeError | None = None
    warnings: Annotated[list[WarningMessage], Field(max_length=20)] = Field(default_factory=list)
    provenance: Annotated[list[Receipt], Field(max_length=100)] = Field(default_factory=list)
    retention: Retention = Field(default_factory=Retention)

    @model_validator(mode='after')
    def consistent_status(self):
        if self.status == 'error':
            if self.error is None or self.data is not None or self.provenance:
                raise ValueError('Errors must not disclose partial business data')
        elif self.error is not None or self.data is None:
            raise ValueError('Successful results require data and no error')
        return self


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


TOOL_MODELS = {
    'ontology_discover': DiscoverInput,
    'ontology_query': SemanticQueryInput,
    'ontology_traverse': TraverseInput,
    'ontology_explain': ExplainInput,
    'ontology_join': JoinInput,
}
TOOL_DESCRIPTIONS = {
    'ontology_discover': {'title': '本体发现', 'description': '发现当前授权机构已发布的本体要素与显式授权的数据映射；仅返回元数据。'},
    'ontology_query': {'title': '语义查询', 'description': '依据已发布映射在授权源端只读查询；不接受SQL，不持久保存业务行。'},
    'ontology_traverse': {'title': '关系穿透', 'description': '沿已确认关系遍历当前机构已发布本体，最多3层、100节点、200关系。'},
    'ontology_explain': {'title': '来源解释', 'description': '解释本体要素或授权映射的归属、业务口径、结构及版本；不返回连接凭据或业务明细。'},
    'ontology_join': {'title': '跨源分步关联', 'description': '通过已审核语义键在同机构两个授权来源分步关联；最多50关联键、200结果行，显式报告截断与部分失败。'},
}


def parse_tool(name: str, arguments: Any) -> ToolInput:
    model = TOOL_MODELS.get(name)
    if model is None:
        raise ToolFailure('UNKNOWN_TOOL', '工具不存在或不受支持。')
    try:
        if not isinstance(arguments, dict):
            raise ValueError()
        return model.model_validate(arguments, strict=True)
    except (ValidationError, ValueError, TypeError):
        raise ToolFailure('INVALID_ARGUMENT', '工具参数不符合1.0契约，请核对字段、类型和范围。') from None


def safe_failure(error: Exception, request_id: str) -> ToolEnvelope:
    """Translate exceptions without retaining upstream text or rejected inputs."""
    if isinstance(error, ToolFailure):
        code, message, retryable = error.code, error.message, error.retryable
    elif isinstance(error, (ValidationError, ValueError, TypeError)):
        code, message, retryable = 'INVALID_ARGUMENT', '工具参数不符合1.0契约。', False
    elif isinstance(error, HTTPException):
        raw_code = error.detail.get('code') if isinstance(error.detail, dict) else None
        if error.status_code == 401:
            code, message, retryable = 'UNAUTHENTICATED', '应用凭据或委托账号已失效。', False
        elif error.status_code == 403:
            code, message, retryable = 'FORBIDDEN', '当前调用方无权访问此工具、映射或数据源。', False
        elif error.status_code == 409:
            code, message, retryable = 'STALE_MAPPING', '映射、标准或授权版本已变化，请重新核对后调用。', False
        elif error.status_code in (400, 404, 422):
            code, message, retryable = 'INVALID_ARGUMENT', '目标不可用或参数不符合工具契约。', False
        elif error.status_code == 504 or raw_code == 'SOURCE_TIMEOUT':
            code, message, retryable = 'TIMEOUT', '源端操作超时，未返回业务结果。', True
        elif error.status_code == 429:
            code, message, retryable = 'BUSY', '服务正在处理其他调用，请稍后重试。', True
        elif raw_code in ('SOURCE_RESULT_LIMIT', 'SOURCE_CELL_LIMIT', 'SOURCE_METADATA_LIMIT'):
            code, message, retryable = 'RESULT_LIMIT', '源端结果超过允许范围，请缩小查询。', False
        elif error.status_code in (502, 503):
            code, message, retryable = 'SOURCE_UNAVAILABLE', '源端或依赖服务暂时不可用。', True
        else:
            code, message, retryable = 'INTERNAL_ERROR', '工具执行未完成，请提供请求编号联系管理员。', False
    elif isinstance(error, TimeoutError):
        code, message, retryable = 'TIMEOUT', '调用超过时限，未返回业务结果。', True
    else:
        code, message, retryable = 'INTERNAL_ERROR', '工具执行未完成，请提供请求编号联系管理员。', False
    return ToolEnvelope(request_id=request_id, status='error', error=SafeError(code=code, message=message, retryable=retryable))


def bounded_envelope(envelope: ToolEnvelope) -> ToolEnvelope:
    try:
        encoded = json.dumps(envelope.model_dump(mode='python'), ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ToolFailure('INTERNAL_ERROR', '工具返回了不支持的数据格式。') from None
    if len(encoded) > MAX_RESULT_BYTES:
        raise ToolFailure('RESULT_LIMIT', '结果超过2MiB，请缩小查询范围。')
    return envelope
