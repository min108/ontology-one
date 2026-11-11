"""Fixed ontology tools sharing live application, tenant and source policy.

Only metadata or bounded in-memory results leave these functions. No arbitrary
SQL, executable expression, caller-supplied identity or result persistence exists.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
import json
import math
from typing import Any

from fastapi import HTTPException

from . import sources
from .db import business_readonly, connect
from .mcp_contracts import (
    MAX_RESULT_BYTES, DiscoverInput, ExplainInput, JoinInput, Receipt, SemanticQueryInput, ToolEnvelope,
    ToolFailure, ToolInput, TraverseInput, WarningMessage, bounded_envelope, parse_tool,
)
from .security.context import require_context
from .security.store import assert_principal_current

ONTOLOGY_KINDS = ('data', 'rule', 'skill', 'knowledge', 'organization')


_SESSION_POLICY = ContextVar('ontology_mcp_session_policy', default=None)


def _auth():
    policy = _SESSION_POLICY.get()
    if policy is not None:
        return policy
    from .security import applications
    return applications


def _principal():
    principal = require_context()
    if (getattr(principal, 'identity_kind', 'session') != 'application'
            and not (_SESSION_POLICY.get() is not None and principal.identity_kind == 'session')):
        raise ToolFailure('FORBIDDEN', '此工具入口仅接受已登记的应用身份。')
    return assert_principal_current(principal)


def _visible(alias='e'):
    # Payload content is never projected. These predicates match the UI's active
    # publication boundary while suppressing the synthetic headquarters root.
    return (f"{alias}.kind IN ('data','rule','skill','knowledge','organization') "
            f"AND {alias}.status='published' "
            f"AND COALESCE(json_extract({alias}.payload,'$.active_asset'),1)!=0 "
            f"AND COALESCE(json_extract({alias}.payload,'$.hide_in_graph'),0)=0")


def _node(row):
    return {'id': row['id'], 'kind': row['kind'], 'title': str(row['title'] or '')[:2000],
            'summary': str(row['summary'] or '')[:2000], 'version': row['version'],
            'owner_tenant_id': require_context().tenant_id}


def _entity(conn, entity_id):
    row = conn.execute('SELECT e.id,e.kind,e.title,e.summary,e.version FROM entities e WHERE e.id=? AND ' + _visible(), (entity_id,)).fetchone()
    if not row:
        raise ToolFailure('FORBIDDEN', '目标不是当前机构可访问的已发布本体要素。')
    return _node(row)


def _binding(conn, mapping_id, *, current=True, allow_stale=False):
    principal = _principal()
    _auth().require_mapping(principal, mapping_id)
    row = conn.execute('SELECT * FROM source_mappings WHERE id=?', (mapping_id,)).fetchone()
    if not row:
        raise ToolFailure('STALE_MAPPING', '授权映射已不存在，请联系管理员重新授权。')
    mapping = sources._mapping(row)
    source = sources._authorize(conn, mapping['source_id'], 'query')
    if mapping['status'] != 'published' and not allow_stale:
        raise ToolFailure('STALE_MAPPING', '映射尚未发布或已失效，请重新审核。')
    resource = (sources._validate_mapping_current(conn, source, mapping) if current else
                conn.execute('SELECT * FROM source_resources WHERE id=? AND source_id=?', (mapping['resource_id'], source['id'])).fetchone())
    return source, mapping, resource


def _receipt(source, mapping, *, purpose='query'):
    return Receipt(purpose=purpose, tenant_id=require_context().tenant_id, source_id=source['id'], mapping_id=mapping['id'],
                   mapping_version=mapping['version'], source_version=source['version'], schema_hash=mapping['schema_hash'])


def _safe_binding(source, mapping, resource):
    metadata = sources._decode(resource['metadata']) if resource else {}
    return {'id': mapping['id'], 'title': mapping['title'], 'version': mapping['version'],
            'status': mapping['status'], 'owner_tenant_id': require_context().tenant_id,
            'source': {'id': source['id'], 'name': source['title'], 'kind': source['kind'], 'version': source['version']},
            'resource': {'id': mapping['resource_id'], 'name': metadata.get('name', ''), 'kind': metadata.get('kind', ''),
                         'schema_version': resource['schema_version'] if resource else None,
                         'schema_hash': mapping['schema_hash']},
            'business_meaning': mapping['business_meaning'], 'grain': mapping['grain'],
            'object_key': list(mapping['object_key']),
            'fields': [{key: field[key] for key in ('column', 'field', 'asset_id', 'asset_version', 'meaning', 'unit')}
                       for field in mapping['fields']],
            'relation_keys': [{key: relation[key] for key in ('column', 'target_asset_id', 'asset_version')}
                              for relation in mapping['relation_keys']],
            'review': {'reviewed_by': mapping.get('reviewed_by'), 'reviewed_at': mapping.get('updated_at')},
            'locally_current': True}


def validate_receipts(receipts):
    """Recheck every consumed source before returning any multi-step result.

    The source shared lock also serializes source-grant mutations, whose write
    path locks the source first. No remote IO occurs while these locks are held.
    """
    principal = _principal()
    values = [value if isinstance(value, Receipt) else Receipt.model_validate(value, strict=True) for value in receipts]
    indexed = {}
    for value in values:
        if value.tenant_id != principal.tenant_id:
            raise ToolFailure('FORBIDDEN', '来源归属与当前应用机构不一致。')
        if value.mapping_id in indexed and indexed[value.mapping_id] != value:
            raise ToolFailure('STALE_MAPPING', '同次调用中的映射版本不一致。')
        indexed[value.mapping_id] = value
    with closing(connect()) as conn, conn:
        # A previous mapping read must not pin REPEATABLE READ's old snapshot
        # while a later source is still waiting for its shared lock.
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        for value in sorted(indexed.values(), key=lambda entry: (entry.source_id, entry.mapping_id)):
            _auth().require_mapping(principal, value.mapping_id)
            conn.execute('SELECT id FROM source_connections WHERE id=? FOR SHARE', (value.source_id,)).fetchone()
            conn.execute('SELECT id FROM source_mappings WHERE id=? AND source_id=? FOR SHARE', (value.mapping_id, value.source_id)).fetchone()
            source, mapping, _ = (_binding(conn, value.mapping_id, current=False, allow_stale=True)
                                   if value.purpose == 'diagnostic' else _binding(conn, value.mapping_id))
            if (source['id'] != value.source_id or source['version'] != value.source_version or
                    mapping['version'] != value.mapping_version or mapping['schema_hash'] != value.schema_hash):
                raise ToolFailure('STALE_MAPPING', '来源或映射在执行期间已变化，结果未返回。')
        assert_principal_current(principal)


def _discover(body: DiscoverInput):
    principal = _principal()
    conditions, params = [_visible()], []
    if body.kind:
        conditions.append('e.kind=?'); params.append(body.kind)
    if body.query:
        conditions.append('(e.id LIKE ? OR e.title LIKE ? OR e.summary LIKE ?)')
        params.extend(['%' + body.query + '%'] * 3)
    with business_readonly(True), closing(connect()) as conn:
        rows = conn.execute('SELECT e.id,e.kind,e.title,e.summary,e.version FROM entities e WHERE ' + ' AND '.join(conditions) +
                            ' ORDER BY e.id LIMIT ?', [*params, body.limit + 1]).fetchall()
    bindings, receipts = [], []
    # Authorized IDs are capped by the application registry. Do not scan or
    # disclose the entire source catalog to an application with ontology scope.
    allowed = _auth().application_permissions(principal)['mapping_ids']
    with closing(connect()) as conn:
        for mapping_id in sorted(allowed):
            try:
                source, mapping, resource = _binding(conn, mapping_id)
            except HTTPException as exc:
                if exc.status_code not in (403, 404, 409):
                    raise
                continue
            except ToolFailure as exc:
                if exc.code not in ('STALE_MAPPING', 'FORBIDDEN'):
                    raise
                continue
            if body.query and body.query.casefold() not in (mapping['title'] + ' ' + mapping['id']).casefold():
                continue
            bindings.append({'id': mapping['id'], 'title': mapping['title'], 'version': mapping['version'],
                             'source_id': source['id'], 'source_name': source['title'],
                             'resource_name': sources._decode(resource['metadata']).get('name', ''),
                             'owner_tenant_id': principal.tenant_id})
            receipts.append(_receipt(source, mapping))
            if len(bindings) > body.limit:
                break
    return {'entities': [_node(row) for row in rows[:body.limit]], 'mappings': bindings[:body.limit],
            'truncated': len(rows) > body.limit or len(bindings) > body.limit}, receipts[:body.limit], []


def _query(body: SemanticQueryInput):
    with closing(connect()) as conn:
        source, mapping, _ = _binding(conn, body.mapping_id)
        receipt = _receipt(source, mapping)
    result = sources.execute_mapping_query(body.mapping_id, body.query)
    if result['mapping_version'] != receipt.mapping_version or result['schema_hash'] != receipt.schema_hash:
        raise ToolFailure('STALE_MAPPING', '映射在查询期间已变化，结果未返回。')
    warnings = [WarningMessage(code='TRUNCATED', message='源端存在更多记录，本次仅返回指定范围。')] if result['truncated'] else []
    return result, [receipt], warnings


def _traverse(body: TraverseInput):
    with business_readonly(True), closing(connect()) as conn:
        root = _entity(conn, body.entity_id)
        nodes, edges, frontier, truncated, reached = {root['id']: root}, {}, {root['id']}, False, 0
        for level in range(1, body.depth + 1):
            if not frontier:
                break
            ids = sorted(frontier)
            marks = ','.join('?' for _ in ids)
            rows = conn.execute('SELECT r.id,r.source,r.target,r.type,r.label FROM relations r '
                                'JOIN entities a ON a.id=r.source JOIN entities b ON b.id=r.target '
                                "WHERE r.status='confirmed' AND " + _visible('a') + ' AND ' + _visible('b') +
                                f' AND (r.source IN ({marks}) OR r.target IN ({marks})) ORDER BY r.id LIMIT ?',
                                [*ids, *ids, body.max_edges + 1]).fetchall()
            next_frontier = set()
            for row in rows:
                if row['id'] in edges:
                    continue
                if len(edges) >= body.max_edges:
                    truncated = True; break
                unknown = [ident for ident in (row['source'], row['target']) if ident not in nodes]
                if len(nodes) + len(set(unknown)) > body.max_nodes:
                    truncated = True; continue
                for ident in unknown:
                    nodes[ident] = _entity(conn, ident)
                    next_frontier.add(ident)
                edges[row['id']] = {key: row[key] for key in ('id', 'source', 'target', 'type', 'label')}
            reached = level
            if len(rows) > body.max_edges:
                truncated = True
            frontier = next_frontier
            if len(edges) >= body.max_edges:
                # If all seen edges exactly fill the budget, a further level is
                # deliberately uninspected and must not be claimed complete.
                if frontier and level < body.depth:
                    truncated = True
                break
    warnings = [WarningMessage(code='TRUNCATED', message='关系穿透达到节点或关系限额，结果不是全量图谱。')] if truncated else []
    return {'nodes': list(nodes.values()), 'edges': list(edges.values()), 'visited_depth': reached,
            'requested_depth': body.depth, 'truncated': truncated}, [], warnings


def _explanation(conn, mapping_id):
    source, mapping, resource = _binding(conn, mapping_id, current=False, allow_stale=True)
    reason = mapping.get('invalid_reason') or ('mapping_' + mapping['status'])
    current = False
    if mapping['status'] == 'published':
        try:
            resource = sources._validate_mapping_current(conn, source, mapping)
            current = True
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            reason = exc.detail.get('code', 'mapping_not_current') if isinstance(exc.detail, dict) else 'mapping_not_current'
    if current:
        return _safe_binding(source, mapping, resource), _receipt(source, mapping)
    if reason not in {'mapping_draft', 'mapping_in_review', 'mapping_rejected', 'mapping_stale',
                      'schema_drift', 'source_changed', 'target_changed', 'target_unpublished', 'target_inactive'}:
        reason = 'mapping_not_current'
    # A previously published binding may now be a private revision. Diagnostics
    # expose its state and version, never the unreviewed definition or fields.
    value = {'id': mapping['id'], 'version': mapping['version'],
             'status': mapping['status'], 'owner_tenant_id': require_context().tenant_id,
             'locally_current': False, 'invalid_reason': reason,
             'source': {'id': source['id'], 'version': source['version']},
             'resource': {'id': mapping['resource_id'], 'schema_hash': mapping['schema_hash'],
                          'schema_version': resource['schema_version'] if resource else None}}
    return value, _receipt(source, mapping, purpose='diagnostic')


def _explain(body: ExplainInput):
    principal = _principal()
    with closing(connect()) as conn:
        if body.mapping_id:
            value, receipt = _explanation(conn, body.mapping_id)
            return {'mapping': value}, [receipt], []
        entity = _entity(conn, body.entity_id)
        bindings, receipts = [], []
        for mapping_id in sorted(_auth().application_permissions(principal)['mapping_ids']):
            try:
                source, mapping, resource = _binding(conn, mapping_id)
            except HTTPException as exc:
                if exc.status_code not in (403, 404, 409): raise
                continue
            except ToolFailure as exc:
                if exc.code not in ('STALE_MAPPING', 'FORBIDDEN'): raise
                continue
            if (any(field['asset_id'] == body.entity_id for field in mapping['fields']) or
                    any(key['target_asset_id'] == body.entity_id for key in mapping['relation_keys'])):
                bindings.append(_safe_binding(source, mapping, resource))
                receipts.append(_receipt(source, mapping))
        return {'entity': entity, 'mappings': bindings}, receipts, []


def _key(value):
    """Typed JSON equality: never coerce a source string into a number."""
    if value is None:
        return None
    if type(value) not in (str, int, float, bool) or (type(value) is float and not math.isfinite(value)):
        raise ToolFailure('INVALID_ARGUMENT', '关联键须为有限JSON标量。')
    return type(value).__name__, json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _join_keys(left_mapping, right_mapping, left_key, right_key):
    left = next((field for field in left_mapping['fields'] if field['field'] == left_key), None)
    right = next((field for field in right_mapping['fields'] if field['field'] == right_key), None)
    if left is None or right is None:
        raise ToolFailure('INVALID_ARGUMENT', '关联键必须是两侧已映射的语义字段。')
    same_standard = (left['asset_id'] == right['asset_id'] and left['asset_version'] == right['asset_version'] and left['unit'] == right['unit'])
    forward = (right_mapping['object_key'] == [right['column']] and
               any(item['column'] == left['column'] and item['target_asset_id'] == right['asset_id'] and
                   item['asset_version'] == right['asset_version'] for item in left_mapping['relation_keys']))
    reverse = (left_mapping['object_key'] == [left['column']] and
               any(item['column'] == right['column'] and item['target_asset_id'] == left['asset_id'] and
                   item['asset_version'] == left['asset_version'] for item in right_mapping['relation_keys']))
    if not same_standard or not (forward or reverse):
        raise ToolFailure('INVALID_ARGUMENT', '关联键须使用同版同单位数据标准，并指向完整单字段对象标识；本版不支持复合键的一部分。')


def _projection(fields, mapping, key):
    selected = list(fields) if fields is not None else [field['field'] for field in mapping['fields']]
    if key not in selected:
        # A join must not secretly read an unselected key. It is included only
        # after the caller explicitly requests it in the published projection.
        raise ToolFailure('INVALID_ARGUMENT', '查询返回字段必须显式包含关联键。')
    sources._selection(mapping, sources.QueryBody(fields=selected))
    return selected


def _bounded_join(left_rows, right_rows, left_key, right_key, join_type, maximum, *, right_truncated=False):
    index = {}
    for row in right_rows:
        key = _key(row[right_key])
        if key is not None:
            index.setdefault(key, []).append(row)
    output, omitted, unknown_unmatched, output_bytes = [], False, 0, 0

    def append(left, right):
        nonlocal output_bytes
        pair = {'left': left, 'right': right}
        output_bytes += len(json.dumps(pair, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) + 1
        # Reserve metadata/envelope space and stop amplification before building
        # a potentially large serialized output from many duplicate matches.
        if output_bytes > MAX_RESULT_BYTES - 64 * 1024:
            raise ToolFailure('RESULT_LIMIT', '关联结果超过允许大小，请减少字段或返回行数。')
        output.append(pair)

    for left in left_rows:
        key = _key(left[left_key])
        matches = index.get(key, []) if key is not None else []
        if matches:
            for right in matches:
                if len(output) == maximum:
                    omitted = True; break
                append(left, right)
        elif join_type == 'left':
            if right_truncated and key is not None:
                unknown_unmatched += 1
            elif len(output) == maximum:
                omitted = True
            else:
                append(left, None)
    return output, omitted, unknown_unmatched


def _join(body: JoinInput):
    with closing(connect()) as conn:
        left_source, left_mapping, _ = _binding(conn, body.left.mapping_id)
        right_source, right_mapping, _ = _binding(conn, body.right.mapping_id)
        if left_source['id'] == right_source['id']:
            raise ToolFailure('INVALID_ARGUMENT', '跨源关联须选择两个不同数据源。')
        _join_keys(left_mapping, right_mapping, body.left_key, body.right_key)
        left_fields = _projection(body.left.query.fields, left_mapping, body.left_key)
        right_fields = _projection(body.right.fields, right_mapping, body.right_key)
        receipts = [_receipt(left_source, left_mapping), _receipt(right_source, right_mapping)]
    left_query = body.left.query.model_copy(update={'fields': left_fields})
    left_result = sources.execute_mapping_query(body.left.mapping_id, left_query)
    if left_result['mapping_version'] != receipts[0].mapping_version:
        raise ToolFailure('STALE_MAPPING', '左侧映射版本在执行期间变化。')
    keys, seen = [], set()
    for row in left_result['rows']:
        value = row[body.left_key]
        key = _key(value)
        if key is not None and key not in seen:
            seen.add(key); keys.append(value)
    if len(keys) > 50:
        raise ToolFailure('RESULT_LIMIT', '关联键超过50项，请缩小左侧查询。')
    right_result = {'rows': [], 'truncated': False}
    if keys:
        right_query = sources.QueryBody(fields=right_fields, filters=[*body.right.filters,
            sources.QueryFilter(field=body.right_key, op='in', value=keys)], limit=200)
        try:
            right_result = sources.execute_mapping_query(body.right.mapping_id, right_query)
        except HTTPException as exc:
            code = exc.detail.get('code') if isinstance(exc.detail, dict) else None
            if not body.allow_partial or code not in ('SOURCE_TIMEOUT', 'SOURCE_UPSTREAM_UNAVAILABLE'):
                raise
            validate_receipts(receipts)
            warning = WarningMessage(code='RIGHT_SOURCE_UNAVAILABLE', message='右侧源暂未完成；仅返回已授权左侧记录，不推断关联结果。')
            return {'rows': [], 'unjoined_left': left_result['rows'], 'failed_step': 'right', 'complete': False,
                    'join_type': body.join_type, 'left_truncated': left_result['truncated'], 'right_truncated': False,
                    'left_count': len(left_result['rows']), 'right_count': 0}, receipts, [warning]
        if right_result['mapping_version'] != receipts[1].mapping_version:
            raise ToolFailure('STALE_MAPPING', '右侧映射版本在执行期间变化。')
    rows, limited, unknown = _bounded_join(left_result['rows'], right_result['rows'], body.left_key, body.right_key,
                                          body.join_type, body.max_rows, right_truncated=right_result['truncated'])
    complete = not (left_result['truncated'] or right_result['truncated'] or limited)
    warnings = []
    if not complete:
        warnings.append(WarningMessage(code='TRUNCATED', message='关联结果受源端或返回限额影响，不能作为全量关联结果。'))
    if unknown:
        warnings.append(WarningMessage(code='UNMATCHED_UNKNOWN', message='右侧结果截断，部分左侧记录是否匹配尚不能确定。'))
    return {'rows': rows, 'join_type': body.join_type, 'complete': complete, 'truncated': not complete,
            'left_truncated': left_result['truncated'], 'right_truncated': right_result['truncated'],
            'result_truncated': limited, 'unknown_unmatched_count': unknown,
            'left_count': len(left_result['rows']), 'right_count': len(right_result['rows']),
            'join_key_count': len(keys)}, receipts, warnings


_HANDLERS = {
    'ontology_discover': _discover,
    'ontology_query': _query,
    'ontology_traverse': _traverse,
    'ontology_explain': _explain,
    'ontology_join': _join,
}


def dispatch_tool(name: str, arguments: ToolInput | dict, request_id: str) -> ToolEnvelope:
    principal = _principal()
    _auth().require_tool(principal, name)
    # Reparse even an existing model so a caller cannot smuggle a different
    # tool's model or unvalidated model_copy into the dispatch boundary.
    body = parse_tool(name, arguments.model_dump(mode='python') if isinstance(arguments, ToolInput) else arguments)
    data, receipts, warnings = _HANDLERS[name](body)
    validate_receipts(receipts)
    assert_principal_current(principal)
    return bounded_envelope(ToolEnvelope(request_id=request_id, status='partial' if warnings else 'ok',
                                        data=data, provenance=receipts, warnings=warnings))


class _SessionPolicy:
    """Internal adapter: a real session intersects explicit source selections.

    Neither HTTP headers nor MCP arguments can construct or activate this scope.
    It never creates an application identity or grants its caller any authority.
    """
    def __init__(self, principal, mapping_ids):
        self.principal = principal
        self.mapping_ids = frozenset(mapping_ids)

    def _current(self, principal):
        if principal != self.principal or principal.identity_kind != 'session' or principal.must_change_password or principal.readonly:
            raise HTTPException(403, '当前会话无权调用此本体工具。')
        return assert_principal_current(principal)

    def require_tool(self, principal, name):
        self._current(principal)
        if name not in _HANDLERS:
            raise ToolFailure('UNKNOWN_TOOL', '工具不在受控只读目录中。')

    def require_mapping(self, principal, mapping_id):
        self._current(principal)
        if mapping_id not in self.mapping_ids:
            raise ToolFailure('FORBIDDEN', '此映射未被本次运行明确选用。')
        with closing(connect()) as conn:
            row = conn.execute('SELECT source_id FROM source_mappings WHERE id=?', (mapping_id,)).fetchone()
            if not row:
                raise ToolFailure('STALE_MAPPING', '映射不存在。')
            sources._authorize(conn, row['source_id'], 'query')

    def application_permissions(self, principal):
        self._current(principal)
        # Each actual access rechecks current source grants; the selection is
        # an upper bound, never a persisted permission cache.
        return {'mapping_ids': self.mapping_ids}


@contextmanager
def session_tool_scope(principal, mapping_ids):
    from .security.context import use_context
    if principal.identity_kind != 'session' or len(mapping_ids) > 20:
        raise HTTPException(403, '本体工具会话无效。')
    policy = _SessionPolicy(principal, mapping_ids)
    policy._current(principal)
    token = _SESSION_POLICY.set(policy)
    try:
        with use_context(principal):
            yield
    finally:
        _SESSION_POLICY.reset(token)


def dispatch_as_session(principal, mapping_ids, name, arguments, request_id):
    with session_tool_scope(principal, mapping_ids):
        return dispatch_tool(name, arguments, request_id)
