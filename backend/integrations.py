"""Legacy session-authenticated tools and integration capability discovery.

No transport may supply a trusted Principal or database name. The HTTP transport
uses SecurityMiddleware. The /mcp/ transport uses independent application identity
and the versioned mcp_contracts instead. These session tools remain read-only.
"""
from __future__ import annotations
import uuid
from contextlib import closing
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .security.context import require_context, use_context
from .security.store import authenticate_session, assert_principal_current, append_audit
from .db import connect, business_readonly, entity_json

router = APIRouter(prefix='/integrations', tags=['集成调用边界'])
TOOLS = ('entities.search', 'entities.get', 'group.search', 'group.graph')


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: str = Field(default='', max_length=300)
    kind: str = Field(default='', max_length=64)
    tenant_id: str = Field(default='', max_length=64)
    entity_id: str = Field(default='', max_length=191)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=30, ge=1, le=100)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Literal['entities.search', 'entities.get', 'group.search', 'group.graph']
    arguments: Arguments = Field(default_factory=Arguments)


@router.get('/capabilities')
def capabilities():
    p = assert_principal_current(require_context())
    from .mcp_contracts import TOOL_MODELS, CONTRACT_VERSION, RETENTION
    return {'version': '0.4.0', 'transport': 'authenticated-http', 'mcp_transport': 'streamable-http',
            'mcp_client': {'transport': 'streamable-http', 'sdk_version': '2.3.0', 'profile': 'ontology-source/1'},
            'mcp_server': {'enabled': True, 'endpoint': '/mcp/', 'contract_version': CONTRACT_VERSION,
                           'identity': 'delegated-application', 'tools': list(TOOL_MODELS),
                           'tenant_scope': 'fixed-tenant', 'retention': RETENTION},
            'tools': [{'name': name, 'read_only': True} for name in TOOLS],
            'tenant_id': p.tenant_id, 'identity': 'server-session',
            'policy': '与交互界面共用机构、角色、授权、撤权及审计服务。'}


def _invoke(call):
    p = assert_principal_current(require_context())
    args = call.arguments
    with business_readonly(True):
        if call.name.startswith('entities.'):
            if args.tenant_id and args.tenant_id != p.tenant_id:
                raise HTTPException(403, '单机构工具不能覆盖会话工作区；跨机构查询使用group工具。')
            with closing(connect()) as conn:
                if call.name == 'entities.get':
                    row = conn.execute('SELECT * FROM entities WHERE id=?', (args.entity_id,)).fetchone()
                    if not row:
                        raise HTTPException(404, '当前机构中未找到对象。')
                    result = {'entity': entity_json(row)}
                else:
                    where = ' WHERE (title LIKE ? OR id LIKE ?)'
                    params = ['%' + args.q + '%'] * 2
                    if args.kind:
                        where += ' AND kind=?'; params.append(args.kind)
                    total = conn.execute('SELECT COUNT(*) FROM entities' + where, params).fetchone()[0]
                    rows = conn.execute('SELECT * FROM entities' + where + ' ORDER BY id LIMIT ? OFFSET ?', params + [args.page_size, (args.page - 1) * args.page_size])
                    result = {'items': [entity_json(row) for row in rows], 'total': total, 'page': args.page}
        else:
            from . import federation
            result = federation.search(args.q, args.kind, '', args.tenant_id, args.page, args.page_size) if call.name == 'group.search' else federation.graph(args.tenant_id, args.kind, args.q, args.page_size)
    assert_principal_current(p)
    append_audit(p, 'tool_call', call.name, 'success', {'read_only': True, 'workspace': p.tenant_id})
    return result


@router.post('/tools/call')
def call_tool(call: ToolCall):
    return _invoke(call)


def invoke_as_session(raw_session: str, call: ToolCall):
    """Adapter API; validates live credentials instead of accepting an actor object."""
    authenticated = authenticate_session(raw_session, uuid.uuid4().hex)
    if not authenticated:
        raise HTTPException(401, '集成会话已失效。')
    principal, _ = authenticated
    if principal.must_change_password:
        raise HTTPException(403, '请先完成账户密码更新。')
    with use_context(principal):
        return _invoke(call)
