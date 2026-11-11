"""Explicit role policy for the business API.

New routes are denied until reviewed here. The table is deliberately not derived
from the running app: registering a route must never grant it permissions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

ROLES = frozenset({'admin', 'modeler', 'reviewer'})
ADMIN = frozenset({'admin'})
BUILD = frozenset({'admin', 'modeler'})
REVIEW = frozenset({'admin', 'reviewer'})

# Paths are the existing business contract, including downloads.
READ_PATHS = (
    '/api/harness/runs', '/api/harness/runs/{id}', '/api/harness/runs/{id}/events',
    '/api/agent/runs', '/api/agent/sessions/{ident}/runs/{run_id}/events',
    '/api/demo/status', '/api/demo/files/{name}',
    '/api/agent/capabilities', '/api/agent/options', '/api/agent/sessions',
    '/api/agent/sessions/{ident}', '/api/agent/sessions/{ident}/runs/{run_id}',
    '/api/skills',
    '/api/skills/options',
    '/api/skills/harness/status',
    '/api/skills/{id}',
    '/api/skills/{id}/versions',
    '/api/skills/{id}/versions/{revision}',
    '/api/skills/{id}/export',
    '/api/skills/{id}/load',
    '/api/integrations/applications',
    '/api/integrations/applications/options',
    '/api/sources',
    '/api/sources/lineage/{asset_id}',
    '/api/sources/{source_id}',
    '/api/sources/{source_id}/resources/{resource_id}/versions',
    '/api/sources/{source_id}/mappings/{mapping_id}/versions',
    '/api/ai/status',
    '/api/integrations/capabilities',
    '/api/applications/scenarios',
    '/api/applications/{run_id}/export',
    '/api/asset-catalog/tree',
    '/api/asset-catalog/items',
    '/api/knowledge/collections',
    '/api/knowledge/collections/{cid}',
    '/api/knowledge/memberships/{aid}',
    '/api/navigation/context',
    '/api/asset-import/template/{kind}',
    '/api/authority/catalog',
    '/api/authority/assets/{eid}',
    '/api/data-instances/summary',
    '/api/data-instances/{standard_id}',
    '/api/data-instances/{standard_id}/rows',
    '/api/data-instances/{standard_id}/graph',
    '/api/datasets',
    '/api/datasets/{dataset_id}',
    '/api/datasets/{dataset_id}/rows',
    '/api/datasets/{dataset_id}/rows/{row_no}',
    '/api/datasets/{dataset_id}/field-candidates',
    '/api/datasets/{dataset_id}/mapping-recognitions',
    '/api/datasets/{dataset_id}/quality',
    '/api/datasets/{dataset_id}/versions',
    '/api/datasets/{dataset_id}/source',
    '/api/datasets/{dataset_id}/export',
    '/api/experience/guide',
    '/api/experience/sessions',
    '/api/experience/{session_id}',
    '/api/experience/{session_id}/progress',
    '/api/experience/{session_id}/runs',
    '/api/graph/search',
    '/api/graph/edge/{relation_id}',
    '/api/graph',
    '/api/graph/objects',
    '/api/impact/sources',
    '/api/impact/preview',
    '/api/impact/history',
    '/api/catalog',
    '/api/entities',
    '/api/search',
    '/api/entities/facets',
    '/api/entities/{eid}/versions',
    '/api/entities/{eid}/route',
    '/api/entities/{eid}',
    '/api/activity',
    '/api/relations',
    '/api/overview',
    '/api/materials/{material_id}/chunks',
    '/api/materials/{material_id}/source',
    '/api/materials/{material_id}/reading',
    '/api/materials',
    '/api/materials/{material_id}/download',
    '/api/materials/{material_id}',
    '/api/jobs/{job_id}',
    '/api/placement/{asset_id}/candidates',
    '/api/placement/{asset_id}/history',
    '/api/work/catalog',
    '/api/projects/{pid}/workspace',
    '/api/tasks/{tid}/workspace',
    '/api/special-projects/{pid}/workspace',
    '/api/special-projects/{pid}/source/{sid}',
    '/api/special-projects/{pid}/deliveries/{did}',
    '/api/special-projects/{pid}/deliveries/{did}/export',
    '/api/system/release',
    '/api/system/backup',
)

# Auth/admin/federation routes perform their own, narrower account or grant checks.
CONTROL_PREFIXES = ("/api/auth/", "/api/admin/", "/api/federation/")

WRITE_RULES = (
    ('POST', '/api/harness/tasks', ROLES),
    ('POST', '/api/harness/runs/{id}/cancel', ROLES),
    ('POST', '/api/skills/{id}/assist-runs', BUILD),
    ('POST', '/api/demo/load', ADMIN),
    ('POST', '/api/demo/clear', ADMIN),
    ('POST', '/api/demo/prepare-mappings', ADMIN),
    ('POST', '/api/agent/sessions', ROLES),
    ('POST', '/api/agent/sessions/{ident}/turns', ROLES),
    ('POST', '/api/agent/sessions/{ident}/runs/{run_id}/cancel', ROLES),
    ('DELETE', '/api/agent/sessions/{ident}', ROLES),
    ('POST', '/api/skills', BUILD),
    ('POST', '/api/skills/import', BUILD),
    ('PUT', '/api/skills/{id}', BUILD),
    ('POST', '/api/skills/{id}/validate', ROLES),
    ('POST', '/api/skills/{id}/test', BUILD),
    ('POST', '/api/skills/{id}/assist', BUILD),
    # Strict handler validates action-specific role, CAS and separate reviewer.
    ('POST', '/api/skills/{id}/action', ROLES),
    ('POST', '/api/skills/harness/config', ADMIN),
    ('POST', '/api/skills/harness/test', ADMIN),
    ('POST', '/api/integrations/applications', ADMIN),
    ('PATCH', '/api/integrations/applications/{application_id}', ADMIN),
    ('POST', '/api/integrations/applications/{application_id}/credentials', ADMIN),
    ('POST', '/api/integrations/applications/{application_id}/revoke', ADMIN),
    ('POST', '/api/sources', ADMIN),
    ('PATCH', '/api/sources/{source_id}', ADMIN),
    ('PUT', '/api/sources/{source_id}/grants', ADMIN),
    ('POST', '/api/sources/{source_id}/discover', BUILD),
    ('POST', '/api/sources/{source_id}/mappings', BUILD),
    ('PUT', '/api/sources/{source_id}/mappings/{mapping_id}', BUILD),
    # Action-specific BUILD/REVIEW checks and separation of duties are enforced
    # again by the strict source handler; no arbitrary source writes are open.
    ('POST', '/api/sources/{source_id}/mappings/{mapping_id}/action', ROLES),
    ('POST', '/api/sources/{source_id}/mappings/{mapping_id}/query', ROLES),
    ('POST', '/api/entities/{eid}/restore', ADMIN),
    ('POST', '/api/integrations/tools/call', ROLES),
    ('POST', '/api/ai/config', ADMIN),
    ('POST', '/api/ai/test', ADMIN),
    ('POST', '/api/ai/ask', ROLES),
    ('POST', '/api/applications/run', BUILD),
    ('POST', '/api/knowledge/collections', BUILD),
    ('POST', '/api/knowledge/collections/{cid}/members', BUILD),
    ('POST', '/api/asset-import/{kind}', BUILD),
    ('POST', '/api/authority/assets', BUILD),
    ('PUT', '/api/authority/assets/{eid}', BUILD),
    ('POST', '/api/authority/people', BUILD),
    ('POST', '/api/authority/assist', BUILD),
    ('POST', '/api/datasets/upload', BUILD),
    ('PUT', '/api/datasets/{dataset_id}/sheets/{sheet_name}/grain', BUILD),
    ('PUT', '/api/datasets/{dataset_id}/mappings', BUILD),
    ('POST', '/api/datasets/{dataset_id}/mapping-preview', BUILD),
    ('POST', '/api/datasets/{dataset_id}/mapping-recognize', BUILD),
    ('POST', '/api/datasets/{dataset_id}/dictionary-drafts', BUILD),
    ('POST', '/api/datasets/{dataset_id}/publish', REVIEW),
    ('POST', '/api/experience/sessions', BUILD),
    ('POST', '/api/experience/{session_id}/turns', BUILD),
    ('POST', '/api/experience/{session_id}/question', BUILD),
    ('POST', '/api/experience/import', BUILD),
    ('POST', '/api/experience/{session_id}/analyze', BUILD),
    ('POST', '/api/experience/{session_id}/candidates/{candidate_id}/review', BUILD),
    ('POST', '/api/experience/{session_id}/proposals', BUILD),
    ('POST', '/api/experience/{session_id}/proposals/{proposal_id}/decision', BUILD),
    ('POST', '/api/experience/{session_id}/candidates/{candidate_id}/updates/{update_id}', BUILD),
    ('POST', '/api/experience/{session_id}/draft', BUILD),
    ('PATCH', '/api/experience/{session_id}/draft', BUILD),
    ('POST', '/api/experience/{session_id}/adopt', BUILD),
    ('POST', '/api/impact/review-tasks', REVIEW),
    ('POST', '/api/entities', BUILD),
    ('PATCH', '/api/entities/{eid}', BUILD),
    ('POST', '/api/entities/{eid}/action', ROLES),
    ('PATCH', '/api/relations/{rid}', REVIEW),
    ('POST', '/api/materials/upload', BUILD),
    ('POST', '/api/materials/{material_id}/reading/operation', BUILD),
    ('POST', '/api/materials/{material_id}/reading/ai', BUILD),
    ('POST', '/api/materials/{material_id}/reading/apply', BUILD),
    ('POST', '/api/materials/{material_id}/reading/confirm', BUILD),
    ('POST', '/api/materials/{material_id}/reading/discard-suggestions', BUILD),
    ('POST', '/api/materials/{material_id}/segment-preview', BUILD),
    ('POST', '/api/materials/{material_id}/segment-confirm', BUILD),
    ('POST', '/api/materials/{material_id}/extract', BUILD),
    ('PATCH', '/api/materials/{material_id}/candidates/{candidate_id}', BUILD),
    ('POST', '/api/materials/{material_id}/candidates/merge', BUILD),
    ('POST', '/api/materials/{material_id}/candidates/{candidate_id}/adopt', BUILD),
    ('POST', '/api/placement/{asset_id}/suggest', BUILD),
    ('POST', '/api/placement/{asset_id}/confirm', REVIEW),
    ('POST', '/api/projects/{pid}/tasks', BUILD),
    ('PATCH', '/api/tasks/{tid}/workspace', BUILD),
    ('PATCH', '/api/projects/{pid}/workspace', BUILD),
    ('POST', '/api/work/{eid}/objects', BUILD),
    ('PATCH', '/api/work/{eid}/objects/{rid}', BUILD),
    ('POST', '/api/tasks/{tid}/action', ROLES),
    ('POST', '/api/projects/{pid}/action', ROLES),
    ('POST', '/api/projects/{pid}/plan', BUILD),
    ('POST', '/api/special-projects', BUILD),
    ('PATCH', '/api/special-projects/{pid}/scope', BUILD),
    ('POST', '/api/special-projects/{pid}/materials', BUILD),
    ('POST', '/api/special-projects/{pid}/matters', BUILD),
    ('PATCH', '/api/special-projects/{pid}/matters/{mid}', BUILD),
    ('POST', '/api/special-projects/{pid}/evidence', BUILD),
    ('PATCH', '/api/special-projects/{pid}/evidence/{eid}', BUILD),
    ('POST', '/api/special-projects/{pid}/candidates', BUILD),
    ('PATCH', '/api/special-projects/{pid}/candidates/{cid}', BUILD),
    ('POST', '/api/special-projects/{pid}/candidates/{cid}/review', BUILD),
    ('POST', '/api/special-projects/{pid}/refinements', BUILD),
    ('POST', '/api/special-projects/{pid}/deliveries', REVIEW),
    ('POST', '/api/jobs/{job_id}/retry', BUILD),
 )


def _pattern(template: str) -> re.Pattern:
    return re.compile('^' + re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(template)) + '$')


_READ = tuple((_pattern(path), path) for path in READ_PATHS)
_WRITE = tuple((method, _pattern(path), path, roles) for method, path, roles in WRITE_RULES)
_ACTIONS = re.compile(r'^/api/(entities|tasks|projects)/[^/]+/action$')
_ENTITY_BODY = re.compile(r'^/api/entities(?:/[^/]+)?$')
_MATTER_REVIEW = re.compile(r'^/api/special-projects/[^/]+/matters/[^/]+$')
_EVIDENCE_REVIEW = re.compile(r'^/api/special-projects/[^/]+/evidence(?:/[^/]+)?$')


def is_control_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in CONTROL_PREFIXES)


def needs_body(method: str, path: str) -> bool:
    return method in {'POST', 'PUT', 'PATCH', 'DELETE'} and bool(
        _ACTIONS.fullmatch(path) or _ENTITY_BODY.fullmatch(path)
        or _MATTER_REVIEW.fullmatch(path) or _EVIDENCE_REVIEW.fullmatch(path))


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: int = 403
    reason: str = '当前角色无权执行此操作。'


def authorize(principal, method: str, path: str, body: Mapping | None = None) -> Decision:
    """Authorize a known business operation. Identity never comes from the body."""
    if principal.identity_kind != 'session':
        return Decision(False, 403, '浏览器业务接口只接受用户会话。')
    if path.startswith('/api/integrations/applications') and principal.role != 'admin':
        return Decision(False, 403, '应用管理需要机构管理员权限。')
    if principal.role not in ROLES:
        return Decision(False)
    if getattr(principal, 'must_change_password', False) and not path.startswith('/api/auth/'):
        return Decision(False, 403, '请先修改初始密码。')
    if is_control_path(path):
        return Decision(True)
    # Cross-tenant read grants are consumed only by dedicated federation routes.
    # Never expose the original raw business APIs through a read-only context.
    if principal.readonly and not path.startswith('/api/agent/'):
        return Decision(False, 403, '跨机构只读授权请通过集团资产检索入口访问。')
    if method in {'GET', 'HEAD'}:
        if path in {'/docs', '/redoc', '/openapi.json'}:
            return Decision(principal.role == 'admin')
        if not any(pattern.fullmatch(path) for pattern, _ in _READ):
            return Decision(False, 403, '此业务接口尚未配置访问策略。')
        if path == '/api/system/release':
            return Decision(principal.role == 'admin' and principal.is_superadmin and principal.home_tenant_id == 'hq')
        if path == '/api/system/backup':
            return Decision(principal.role == 'admin')
        return Decision(True)
    rule = next((r for r in _WRITE if r[0] == method and r[1].fullmatch(path)), None)
    if rule is None:
        return Decision(False, 403, '此业务操作尚未配置授权策略。')
    if _ACTIONS.fullmatch(path):
        action = (body or {}).get('action')
        if not isinstance(action, str):
            return Decision(False, 422, '工作流操作须为有效名称。')
        if action in {'approve', 'publish', 'return', 'archive'}:
            roles = REVIEW
        elif action in {'claim', 'submit', 'revise', 'complete'}:
            roles = BUILD
        else:
            return Decision(False, 422, '不支持此工作流操作。')
    elif method == 'PATCH' and _MATTER_REVIEW.fullmatch(path):
        review_fields = {'review_state', 'review_note'}
        if review_fields.intersection(body or {}):
            roles = REVIEW
            if principal.role == 'reviewer' and set(body or {}) - {'review_state', 'review_note', 'version'}:
                return Decision(False, 403, '审核人员只能提交审核状态和审核意见。')
        else:
            roles = BUILD
    elif _EVIDENCE_REVIEW.fullmatch(path):
        roles = REVIEW if method == 'PATCH' or (body or {}).get('review_state') == 'confirmed' else BUILD
    else:
        roles = rule[3]
    if principal.role not in roles:
        return Decision(False)
    if principal.role == 'modeler' and _ENTITY_BODY.fullmatch(path):
        # Prevent a generic payload from bypassing the action-specific workflow.
        status = (body or {}).get('status')
        if status is not None and not isinstance(status, str):
            return Decision(False, 422, '状态须为有效名称。')
        if status is not None and status not in {'draft', 'active', 'returned'}:
            return Decision(False, 403, '建模人员须通过提交审核流程申请发布。')
    return Decision(True)
