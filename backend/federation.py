"""Authorized, read-only federation over isolated institution databases.

No source entities are copied. Every request rebuilds its authorization scope,
queries only provisioned tenants and filters both ends of every relationship.
"""
from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import copy_context
from urllib.parse import quote

import pymysql
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal

from . import db
from .security.context import require_context
from .security import store

router = APIRouter(prefix='/federation', tags=['federation'])
ASSETS = ('data', 'rule', 'algorithm', 'knowledge')
MAX_WORKERS = 8
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='federation')


def initialize_federation():
    """Run once during application startup, after the control store exists."""
    with store.control_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''CREATE TABLE IF NOT EXISTS federation_links (
                id VARCHAR(64) PRIMARY KEY,
                source_tenant_id VARCHAR(100) NOT NULL, source_entity_id VARCHAR(191) NOT NULL,
                target_tenant_id VARCHAR(100) NOT NULL, target_entity_id VARCHAR(191) NOT NULL,
                relation_type VARCHAR(100) NOT NULL, label VARCHAR(200) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending', reason TEXT NOT NULL,
                created_by VARCHAR(100) NOT NULL, reviewed_by VARCHAR(100),
                created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL,
                UNIQUE KEY uq_federation_edge(source_tenant_id,source_entity_id,target_tenant_id,target_entity_id,relation_type),
                KEY ix_federation_source(source_tenant_id,source_entity_id),
                KEY ix_federation_target(target_tenant_id,target_entity_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')


def _audience_scope(principal, tenants):
    if principal.home_tenant_id == 'hq':
        return tenants
    home = next((tenant for tenant in tenants if tenant['id'] == principal.home_tenant_id), None)
    if home is None:
        raise HTTPException(401, '所属机构已不可用，请重新登录。')
    return [tenant for tenant in tenants if tenant['id'] == principal.home_tenant_id or
            (home['category'] == 'headquarters_department' and
             tenant['category'] in ('headquarters', 'headquarters_department') and
             tenant.get('visibility') == 'published')]


def _assert_scope_current(principal, tenants, connection=None):
    # A grant may be revoked/downgraded while another tenant is being queried.
    # Revalidate before returning data; never return a result from a broader
    # authorization snapshot than the one currently persisted.
    live_tenants = (store.authorized_read_tenants_in_transaction(principal, connection)
                    if connection is not None else store.authorized_read_tenants(principal))
    current = {tenant['id']: tenant for tenant in _audience_scope(principal, live_tenants)}
    for tenant in tenants:
        live = current.get(tenant['id'])
        if live is None or (tenant.get('visibility') == 'private' and live.get('visibility') != 'private'):
            raise HTTPException(403, '机构访问授权已变化，请刷新后重试。')


def _scope():
    principal = store.assert_principal_current(require_context())
    tenants = _audience_scope(principal, store.authorized_read_tenants(principal))
    tenants = [dict(tenant) for tenant in tenants]
    databases = [tenant['db_name'] for tenant in tenants if tenant.get('status') == 'active' and tenant.get('db_name')]
    provisioned = set()
    if databases:
        with store.control_connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT table_schema FROM information_schema.tables WHERE table_name=%s AND table_schema IN (' +
                           ','.join('%s' for _ in databases) + ')', ['entity_access', *databases])
            provisioned = {row['TABLE_SCHEMA'] if 'TABLE_SCHEMA' in row else row['table_schema'] for row in cursor.fetchall()}
    for tenant in tenants:
        tenant['provisioned'] = tenant.get('db_name') in provisioned
    return principal, sorted(tenants, key=lambda item: (item['name'], item['id']))


def _ready(tenant):
    return tenant.get('status') == 'active' and bool(tenant.get('provisioned'))


def _tenant_view(tenant, principal, counts=None):
    access = 'own' if tenant['id'] == principal.tenant_id else 'all' if tenant.get('visibility') == 'private' else 'headquarters'
    result = {'tenant_id': tenant['id'], 'tenant_name': tenant['name'],
              'org_id': tenant.get('org_id'), 'tenant_type': tenant['category'],
              'access_mode': access, 'provisioned': _ready(tenant)}
    if counts is not None:
        result['counts'] = counts
    return result


def _scope_view(principal, tenants):
    own = next((tenant for tenant in tenants if tenant['id'] == principal.tenant_id), None)
    can_global = principal.home_tenant_id == 'hq' and any(
        tenant['id'] != principal.tenant_id and tenant.get('visibility') == 'private' for tenant in tenants)
    return {'tenant_id': principal.tenant_id, 'tenant_name': own['name'] if own else '',
            'can_global_read': can_global,
            'can_create_links': principal.home_tenant_id == 'hq' and principal.role in ('admin', 'modeler') and not principal.readonly,
            'can_review_links': principal.home_tenant_id == 'hq' and principal.role in ('admin', 'reviewer') and not principal.readonly}


def _predicate(tenant, alias='e', access_alias='a', *, q='', kind='', status='', entity_id=None):
    terms = [f"COALESCE(json_extract({alias}.payload,'$.active_asset'),1)!=0"]
    args = []
    if tenant.get('visibility') != 'private':
        terms.extend([f"{alias}.kind IN ('data','rule','algorithm','knowledge')", f"{alias}.status='published'",
                      f"{access_alias}.visibility='headquarters'"])
    if entity_id is not None:
        terms.append(f'{alias}.id=?')
        args.append(entity_id)
    for column, value in [('kind', kind), ('status', status)]:
        if value:
            terms.append(f'{alias}.{column}=?')
            args.append(value)
    if q:
        # Search only the metadata that can be returned, never hidden payload.
        escaped = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        terms.append(f'({alias}.id LIKE ? OR {alias}.title LIKE ? OR {alias}.summary LIKE ? OR {alias}.domain LIKE ?)')
        args.extend([escaped] * 4)
    return ' AND '.join(terms), args


def _from(alias='e', access_alias='a'):
    return f'entities {alias} LEFT JOIN entity_access {access_alias} ON {access_alias}.entity_id={alias}.id'


def _parallel(tenants, function):
    """Propagate request identity separately to each bounded worker."""
    results, unavailable = {}, []
    ready = [tenant for tenant in tenants if _ready(tenant)]
    if not ready:
        return results, unavailable
    pending = [(tenant, _EXECUTOR.submit(copy_context().run, function, tenant)) for tenant in ready]
    for tenant, future in pending:
        try:
            results[tenant['id']] = future.result()
        except HTTPException:
            # An expired or revoked principal must fail the entire request.
            for _, pending_future in pending:
                pending_future.cancel()
            raise
        except (pymysql.MySQLError, OSError, TimeoutError):
            unavailable.append(tenant['id'])
    return results, unavailable


def _node_id(tenant_id, entity_id):
    return 'entity:' + quote(tenant_id, safe='') + ':' + quote(entity_id, safe='')


def _root_id(tenant_id):
    return 'tenant:' + quote(tenant_id, safe='')


def _metadata(row, tenant):
    return {**{key: row.get(key) for key in ('id', 'kind', 'title', 'summary', 'domain', 'status', 'version', 'org_id', 'updated_at')},
            'tenant_id': tenant['id'], 'tenant_name': tenant['name'], 'tenant_org_id': tenant.get('org_id'),
            'node_id': _node_id(tenant['id'], row['id']), 'visibility': row.get('visibility') or 'institution',
            'detail_url': '/api/federation/entities/' + quote(tenant['id'], safe='') + '/' + quote(row['id'], safe='')}


def _counts(tenant, **filters):
    where, args = _predicate(tenant, **filters)
    with db.tenant_connection(tenant['id'], readonly=True) as connection:
        rows = connection.execute(f'SELECT e.kind,count(*) count FROM {_from()} WHERE {where} GROUP BY e.kind', args).fetchall()
        kinds = {row['kind']: row['count'] for row in rows}
        left, left_args = _predicate(tenant, 's', 'sa')
        right, right_args = _predicate(tenant, 't', 'ta')
        relation_count = connection.execute(f'''SELECT count(*) FROM relations r
            JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target
            LEFT JOIN entity_access sa ON sa.entity_id=s.id LEFT JOIN entity_access ta ON ta.entity_id=t.id
            WHERE r.status!='rejected' AND {left} AND {right}''', left_args + right_args).fetchone()[0]
    return {'entities': sum(kinds.values()), 'assets': sum(kinds.get(kind, 0) for kind in ASSETS),
            'relations': relation_count, 'kinds': kinds}


@router.get('/overview')
def overview():
    principal, tenants = _scope()
    results, unavailable = _parallel(tenants, _counts)
    totals = {'entities': 0, 'assets': 0, 'relations': 0, 'tenants': len(results)}
    for counts in results.values():
        for key in ('entities', 'assets', 'relations'):
            totals[key] += counts[key]
    _assert_scope_current(principal, tenants)
    return {'scope': _scope_view(principal, tenants), 'totals': totals,
            'tenants': [_tenant_view(tenant, principal, results.get(tenant['id'], {'entities': 0, 'assets': 0, 'relations': 0, 'kinds': {}})) for tenant in tenants],
            'partial': bool(unavailable), 'unavailable_tenants': unavailable}


def _filter_tenants(tenants, tenant_id):
    if not tenant_id:
        return tenants
    selected = [tenant for tenant in tenants if tenant['id'] == tenant_id]
    if not selected:
        raise HTTPException(404, '未找到可访问的机构。')
    return selected


def _search_count(tenant, q, kind, status):
    where, args = _predicate(tenant, q=q, kind=kind, status=status)
    with db.tenant_connection(tenant['id'], readonly=True) as connection:
        return connection.execute(f'SELECT count(*) FROM {_from()} WHERE {where}', args).fetchone()[0]


def _search_rows(tenant, q, kind, status, offset, size, exclude_root=False):
    where, args = _predicate(tenant, q=q, kind=kind, status=status)
    if exclude_root:
        where += " AND NOT (e.kind='organization' AND e.id=?)"
        args.append(tenant.get('org_id') or tenant['id'])
    with db.tenant_connection(tenant['id'], readonly=True) as connection:
        rows = connection.execute(f'''SELECT e.id,e.kind,e.title,e.summary,e.domain,e.org_id,e.status,e.version,e.updated_at,
            COALESCE(a.visibility,'institution') visibility FROM {_from()} WHERE {where} ORDER BY e.id LIMIT ? OFFSET ?''', args + [size, offset]).fetchall()
        return [_metadata(row, tenant) for row in rows]


@router.get('/search')
def search(q: str = Query('', max_length=300), kind: str = Query('', max_length=64),
           status: str = Query('', max_length=64), tenant_id: str = Query('', max_length=100),
           page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
    principal, tenants = _scope()
    tenants = _filter_tenants(tenants, tenant_id)
    counts, unavailable = _parallel(tenants, lambda tenant: _search_count(tenant, q, kind, status))
    offset, remaining, plan = (page - 1) * page_size, page_size, {}
    for tenant in tenants:
        count = counts.get(tenant['id'], 0)
        if offset >= count:
            offset -= count
            continue
        size = min(remaining, count - offset)
        plan[tenant['id']] = (offset, size)
        remaining -= size
        offset = 0
        if not remaining:
            break
    results, failed = _parallel([tenant for tenant in tenants if tenant['id'] in plan],
                                lambda tenant: _search_rows(tenant, q, kind, status, *plan[tenant['id']]))
    unavailable = sorted(set(unavailable + failed))
    _assert_scope_current(principal, tenants)
    return {'items': [item for tenant in tenants for item in results.get(tenant['id'], [])],
            'total': sum(counts.values()), 'page': page, 'page_size': page_size, 'order': 'tenant_name,id',
            'scope': _scope_view(principal, tenants), 'partial': bool(unavailable), 'unavailable_tenants': unavailable}


def _tenant_map(tenants):
    return {tenant['id']: tenant for tenant in tenants}


def _read_entity(tenant, entity_id):
    if not _ready(tenant):
        raise HTTPException(404, '机构工作区尚未就绪。')
    where, args = _predicate(tenant, entity_id=entity_id)
    with db.tenant_connection(tenant['id'], readonly=True) as connection:
        row = connection.execute(f"SELECT e.*,COALESCE(a.visibility,'institution') visibility FROM {_from()} WHERE {where}", args).fetchone()
        if not row:
            raise HTTPException(404, '未找到可访问的内容。')
        return dict(row)


def _visible_entities(tenant, ids):
    ids = list(set(ids))
    if not ids:
        return {}
    where, args = _predicate(tenant)
    found = {}
    with db.tenant_connection(tenant['id'], readonly=True) as connection:
        for start in range(0, len(ids), 100):
            batch = ids[start:start + 100]
            rows = connection.execute(f'''SELECT e.id,e.kind,e.title,e.summary,e.domain,e.org_id,e.status,e.version,e.updated_at,
                COALESCE(a.visibility,'institution') visibility FROM {_from()} WHERE {where} AND e.id IN ({','.join('?' for _ in batch)})''', args + batch).fetchall()
            found.update({row['id']: _metadata(row, tenant) for row in rows})
    return found


# Explicit display schema: raw file paths, credentials, upload metadata, chats,
# job results, original snapshots and arbitrary embedded dictionaries never leave
# the owning workspace through the federation API.
DISPLAY_KEYS = frozenset('''id title name content original_text body description summary definition format data_type data_form unit constraints
standard_basis positive_example negative_example sensitivity category subcategory ownership rule_type asset_source
rule_text obligation scope conditions exceptions basis clause authority effective_from effective_to review_note
algorithm_type scenario inputs method steps outputs boundary maturity follow_up knowledge_form knowledge_type origin_type
topic_label article_sections sections source_refs purpose notes type subtype nodes edges responsibility control evidence
role time_limit x y source target label decision parameters intro topic is_demo demo_notice'''.split())
REF_FIELDS = frozenset(('material_id', 'source_id', 'id', 'title', 'quote', 'excerpt', 'locator', 'chunk_id', 'source_version', 'anchor_id'))


def _reference_ids(value):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'source_refs' and isinstance(child, list):
                for ref in child:
                    if isinstance(ref, dict):
                        ident = ref.get('material_id') or ref.get('source_id') or ref.get('id')
                        if isinstance(ident, str):
                            result.add(ident)
            elif key in DISPLAY_KEYS:
                result.update(_reference_ids(child))
    elif isinstance(value, list):
        for item in value:
            result.update(_reference_ids(item))
    return result


def _safe_payload(payload, tenant, visible):
    hidden = 0

    def clean(value):
        nonlocal hidden
        if isinstance(value, dict):
            output = {}
            for key, child in value.items():
                if key not in DISPLAY_KEYS:
                    continue
                if key == 'source_refs':
                    refs = []
                    for ref in child if isinstance(child, list) else []:
                        ident = (ref.get('material_id') or ref.get('source_id') or ref.get('id')) if isinstance(ref, dict) else None
                        if ident not in visible or ref.get('tenant_id', tenant['id']) != tenant['id']:
                            hidden += 1
                            continue
                        safe = {key: val for key, val in ref.items() if key in REF_FIELDS and isinstance(val, (str, int, float, bool))}
                        safe.update(tenant_id=tenant['id'], tenant_name=tenant['name'], org_id=visible[ident]['org_id'],
                                    detail_url=visible[ident]['detail_url'], title=visible[ident]['title'])
                        refs.append(safe)
                    output[key] = refs
                else:
                    output[key] = clean(child)
            return output
        if isinstance(value, list):
            return [clean(item) for item in value if isinstance(item, (str, int, float, bool, dict, list)) or item is None]
        return value if isinstance(value, (str, int, float, bool)) or value is None else None

    return clean(payload), hidden


def _local_relations(tenant, entity_id, visible_ids=None):
    if visible_ids is not None:
        visible_ids = sorted(set(visible_ids))
        if not visible_ids:
            return []
    left, left_args = _predicate(tenant, 's', 'sa')
    right, right_args = _predicate(tenant, 't', 'ta')
    where, args = f"r.status!='rejected' AND {left} AND {right}", left_args + right_args
    if entity_id:
        where += ' AND (r.source=? OR r.target=?)'
        args.extend([entity_id, entity_id])
    if visible_ids is not None:
        placeholders = ','.join('?' for _ in visible_ids)
        where += f' AND r.source IN ({placeholders}) AND r.target IN ({placeholders})'
        args.extend(visible_ids)
        args.extend(visible_ids)
    with db.tenant_connection(tenant['id'], readonly=True) as connection:
        rows = connection.execute(f'''SELECT r.id,r.source,r.target,r.type,r.label,r.status,
            s.title source_title,t.title target_title FROM relations r
            JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target
            LEFT JOIN entity_access sa ON sa.entity_id=s.id LEFT JOIN entity_access ta ON ta.entity_id=t.id
            WHERE {where} ORDER BY r.id LIMIT 1000''', args).fetchall()
    return [{'id': 'relation:' + tenant['id'] + ':' + row['id'], 'source': _node_id(tenant['id'], row['source']),
             'target': _node_id(tenant['id'], row['target']), 'source_entity_id': row['source'], 'target_entity_id': row['target'],
             'source_tenant_id': tenant['id'], 'target_tenant_id': tenant['id'], 'tenant_id': tenant['id'],
             'tenant_name': tenant['name'], 'org_id': tenant.get('org_id'), 'source_title': row['source_title'],
             'target_title': row['target_title'], 'label': row['label'], 'type': row['type'], 'status': row['status'],
             'cross_tenant': False} for row in rows]


def _central_links(tenant_ids, entity=None, include_pending=False):
    if not tenant_ids:
        return []
    placeholders = ','.join('%s' for _ in tenant_ids)
    sql = f'SELECT * FROM federation_links WHERE source_tenant_id IN ({placeholders}) AND target_tenant_id IN ({placeholders})'
    args = list(tenant_ids) * 2
    sql += " AND status IN ('pending','confirmed')" if include_pending else " AND status='confirmed'"
    if entity:
        sql += ' AND ((source_tenant_id=%s AND source_entity_id=%s) OR (target_tenant_id=%s AND target_entity_id=%s))'
        args.extend([*entity, *entity])
    sql += ' ORDER BY id LIMIT 1000'
    with store.control_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, args)
            return list(cursor.fetchall())


def _cross_relations(tenants, entity=None, visible_nodes=None, include_pending=False, node_metadata=None):
    by_tenant = _tenant_map(tenants)
    links = _central_links(list(by_tenant), entity, include_pending)
    if not links:
        return []
    by_node = dict(node_metadata or {})
    if visible_nodes is None:
        ids = {tid: set() for tid in by_tenant}
        for link in links:
            ids[link['source_tenant_id']].add(link['source_entity_id'])
            ids[link['target_tenant_id']].add(link['target_entity_id'])
        visible, _ = _parallel([tenant for tenant in tenants if ids[tenant['id']]],
                               lambda tenant: _visible_entities(tenant, ids[tenant['id']]))
        by_node = {_node_id(tid, ident): item for tid, records in visible.items() for ident, item in records.items()}
        visible_nodes = set(by_node)
    results = []
    for link in links:
        source = _node_id(link['source_tenant_id'], link['source_entity_id'])
        target = _node_id(link['target_tenant_id'], link['target_entity_id'])
        if source not in visible_nodes or target not in visible_nodes:
            continue
        results.append({'id': 'cross:' + link['id'], 'link_id': link['id'], 'source': source, 'target': target,
                        'source_entity_id': link['source_entity_id'], 'target_entity_id': link['target_entity_id'],
                        'source_tenant_id': link['source_tenant_id'], 'target_tenant_id': link['target_tenant_id'],
                        'source_tenant_name': by_tenant[link['source_tenant_id']]['name'],
                        'source_title': by_node.get(source, {}).get('title', ''), 'target_title': by_node.get(target, {}).get('title', ''),
                        'source_org_id': by_node.get(source, {}).get('org_id'), 'target_org_id': by_node.get(target, {}).get('org_id'),
                        'target_tenant_name': by_tenant[link['target_tenant_id']]['name'],
                        'type': link['relation_type'], 'label': link['label'], 'status': link['status'], 'cross_tenant': True})
    return results


@router.get('/entities/{tenant_id}/{entity_id}')
def entity_detail(tenant_id: str, entity_id: str):
    principal, tenants = _scope()
    tenant = _filter_tenants(tenants, tenant_id)[0]
    row = _read_entity(tenant, entity_id)
    payload = json.loads(row['payload'] or '{}')
    visible = _visible_entities(tenant, _reference_ids(payload))
    safe, hidden = _safe_payload(payload, tenant, visible)
    relations = _local_relations(tenant, entity_id) + _cross_relations(tenants, (tenant_id, entity_id), include_pending=_scope_view(principal, tenants)['can_review_links'])
    node = _node_id(tenant_id, entity_id)
    can_set = tenant_id == principal.tenant_id and tenant['category'] == 'headquarters_department' and principal.role in ('admin', 'modeler') and not principal.readonly and row['kind'] in ASSETS
    _assert_scope_current(principal, tenants)
    return {'entity': {**_metadata(row, tenant), 'payload': safe},
            'relations': {'incoming': [relation for relation in relations if relation['target'] == node],
                          'outgoing': [relation for relation in relations if relation['source'] == node]},
            'can_set_visibility': can_set, 'visibility_options': ['institution', 'headquarters'] if can_set else [],
            'hidden_reference_count': hidden, 'readonly': True}


def _graph_rows(tenant, q, kind, limit):
    items = _search_rows(tenant, q, kind, '', 0, limit + 1, exclude_root=True)
    return {'items': items[:limit], 'truncated': len(items) > limit}


@router.get('/graph')
def graph(tenant_id: str = Query('', max_length=100), kind: str = Query('', max_length=64),
          q: str = Query('', max_length=300), limit: int = Query(100, ge=1, le=300)):
    principal, tenants = _scope()
    tenants = _filter_tenants(tenants, tenant_id)
    results, unavailable = _parallel(tenants, lambda tenant: _graph_rows(tenant, q, kind, limit))
    roots, nodes, relations, truncated = [], [], [], False
    # Round-robin allocation retains institution diversity within the global cap.
    root_ids = set()
    for tenant in tenants:
        if tenant['id'] in results and tenant['category'] != 'headquarters':
            root = _root_id(tenant['id'])
            roots.append({'id': root, 'label': tenant['name'], 'kind': 'tenant', 'tenant_id': tenant['id'],
                          'tenant_name': tenant['name'], 'org_id': tenant.get('org_id')})
            root_ids.add(root)
    queues = [(tenant, results[tenant['id']]['items']) for tenant in tenants if tenant['id'] in results]
    position = 0
    while len(nodes) < limit and any(len(items) > position for _, items in queues):
        for tenant, items in queues:
            if len(nodes) >= limit:
                break
            if position >= len(items):
                continue
            item = items[position]
            parent = None if tenant['category'] == 'headquarters' else _root_id(tenant['id'])
            if parent and parent not in root_ids:
                roots.append({'id': parent, 'label': tenant['name'], 'kind': 'tenant', 'tenant_id': tenant['id'],
                              'tenant_name': tenant['name'], 'org_id': tenant.get('org_id')})
                root_ids.add(parent)
            nodes.append({**item, 'id': item['node_id'], 'entity_id': item['id'], 'label': item['title'], 'parent_id': parent})
        position += 1
    visible_nodes = {node['id'] for node in nodes}
    # Fetch edges only after the global node budget is allocated. Each query
    # applies endpoint IDs in SQL, so rows discarded by round-robin never cross
    # the database boundary and cannot consume the per-query edge limit.
    selected_ids = {}
    for node in nodes:
        selected_ids.setdefault(node['tenant_id'], set()).add(node['entity_id'])
    local, failed = _parallel([tenant for tenant in tenants if tenant['id'] in selected_ids],
                             lambda tenant: _local_relations(tenant, None, selected_ids[tenant['id']]))
    unavailable = sorted(set(unavailable + failed))
    for tenant in tenants:
        relations.extend(local.get(tenant['id'], []))
    truncated = any(result['truncated'] for result in results.values()) or sum(len(result['items']) for result in results.values()) > len(nodes)
    relations.extend(_cross_relations(tenants, visible_nodes=visible_nodes, node_metadata={node['id']: node for node in nodes}))
    _assert_scope_current(principal, tenants)
    return {'scope': _scope_view(principal, tenants), 'roots': roots, 'nodes': nodes, 'edges': relations,
            'truncated': truncated, 'partial': bool(unavailable), 'unavailable_tenants': unavailable}


class VisibilityRequest(BaseModel):
    visibility: Literal['institution', 'headquarters']


@router.post('/entities/{tenant_id}/{entity_id}/visibility')
def set_visibility(tenant_id: str, entity_id: str, body: VisibilityRequest):
    principal, tenants = _scope()
    store.assert_principal_current(principal, require_write=True)
    tenant = _filter_tenants(tenants, tenant_id)[0]
    if tenant_id != principal.tenant_id or tenant['category'] != 'headquarters_department' or principal.role not in ('admin', 'modeler') or principal.readonly:
        store.append_audit(principal, 'entity_visibility', tenant_id + '/' + entity_id, 'denied')
        raise HTTPException(403, '仅本机构总部部门的建模员或管理员可以设置成果共享范围。')
    with closing(db.connect()) as connection, connection:
        entity = connection.execute('SELECT id,kind FROM entities WHERE id=? FOR UPDATE', (entity_id,)).fetchone()
        if not entity or entity['kind'] not in ASSETS:
            raise HTTPException(404, '仅业务资产可以设置共享范围。')
        connection.execute('''INSERT INTO entity_access(entity_id,visibility,updated_by,updated_at) VALUES(?,?,?,?)
            ON DUPLICATE KEY UPDATE visibility=VALUES(visibility),updated_by=VALUES(updated_by),updated_at=VALUES(updated_at)''',
                           (entity_id, body.visibility, principal.user_id, db.now()))
    store.append_audit(principal, 'entity_visibility', tenant_id + '/' + entity_id, 'success', {'visibility': body.visibility})
    return {'ok': True, 'tenant_id': tenant_id, 'entity_id': entity_id, 'visibility': body.visibility}


class LinkRequest(BaseModel):
    source_tenant_id: str = Field(min_length=1, max_length=100)
    source_entity_id: str = Field(min_length=1, max_length=191)
    target_tenant_id: str = Field(min_length=1, max_length=100)
    target_entity_id: str = Field(min_length=1, max_length=191)
    relation_type: Literal['references', 'related_to', 'uses_data', 'implements_rule', 'derived_from', 'explains'] = 'references'
    label: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=3000)


class LinkReview(BaseModel):
    status: Literal['confirmed', 'rejected']
    reason: str = Field(min_length=1, max_length=3000)


def _authorize_link_write(principal, review=False):
    store.assert_principal_current(principal, require_write=True)
    roles = ('admin', 'reviewer') if review else ('admin', 'modeler')
    if principal.home_tenant_id != 'hq' or principal.role not in roles or principal.readonly:
        store.append_audit(principal, 'federation_link_review' if review else 'federation_link_create', 'federation', 'denied')
        raise HTTPException(403, '需要总部授权的' + ('审核或管理员' if review else '建模或管理员') + '权限。')


def _link_endpoints(tenants, source_tenant, source_entity, target_tenant, target_entity):
    if source_tenant == target_tenant:
        raise HTTPException(422, '机构内部关系请在本机构工作区维护。')
    source = _read_entity(_filter_tenants(tenants, source_tenant)[0], source_entity)
    target = _read_entity(_filter_tenants(tenants, target_tenant)[0], target_entity)
    return source, target


@router.post('/links')
def create_link(body: LinkRequest):
    principal, tenants = _scope()
    _authorize_link_write(principal)
    if not body.label.strip() or not body.reason.strip():
        raise HTTPException(422, '请填写关系名称和建立依据。')
    source, target = _link_endpoints(tenants, body.source_tenant_id, body.source_entity_id, body.target_tenant_id, body.target_entity_id)
    constraints = {'uses_data': (ASSETS, ('data',)), 'implements_rule': (('algorithm',), ('rule',)),
                   'derived_from': (ASSETS, ('material', 'case', 'dataset')), 'explains': (('knowledge',), ('rule',))}
    if body.relation_type in constraints:
        sources, targets = constraints[body.relation_type]
        if source['kind'] not in sources or target['kind'] not in targets:
            raise HTTPException(422, '关系类型与两端业务对象不匹配。')
    _assert_scope_current(principal, tenants)
    ident, timestamp = 'FL-' + uuid.uuid4().hex, db.now()
    try:
        with store.authorized_control_connection(principal) as connection:
            with connection.cursor() as cursor:
                cursor.execute('''INSERT INTO federation_links(id,source_tenant_id,source_entity_id,target_tenant_id,target_entity_id,
                    relation_type,label,status,reason,created_by,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)''',
                               (ident, body.source_tenant_id, body.source_entity_id, body.target_tenant_id, body.target_entity_id,
                                body.relation_type, body.label.strip(), body.reason.strip(), principal.user_id, timestamp, timestamp))
            _assert_scope_current(principal, tenants, connection)
    except pymysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            raise HTTPException(409, '相同跨机构关系已登记，请查看现有记录。')
        raise
    store.append_audit(principal, 'federation_link_create', ident, 'success', {'status': 'pending', 'source_tenant_id': body.source_tenant_id, 'target_tenant_id': body.target_tenant_id})
    return {'id': ident, 'status': 'pending', 'source_tenant_id': body.source_tenant_id, 'target_tenant_id': body.target_tenant_id}


@router.patch('/links/{link_id}')
def review_link(link_id: str, body: LinkReview):
    principal, tenants = _scope()
    _authorize_link_write(principal, review=True)
    if not body.reason.strip():
        raise HTTPException(422, '请填写审核意见。')
    with store.control_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM federation_links WHERE id=%s', (link_id,))
            link = cursor.fetchone()
    if not link:
        raise HTTPException(404, '未找到关联记录。')
    _link_endpoints(tenants, link['source_tenant_id'], link['source_entity_id'], link['target_tenant_id'], link['target_entity_id'])
    _assert_scope_current(principal, tenants)
    if link['created_by'] == principal.user_id and not principal.is_superadmin:
        raise HTTPException(403, '请由其他审核人员确认本人创建的关系。')
    with store.authorized_control_connection(principal) as connection:
        with connection.cursor() as cursor:
            cursor.execute('''UPDATE federation_links SET status=%s,reason=%s,reviewed_by=%s,updated_at=%s
                WHERE id=%s AND status=%s''', (body.status, body.reason.strip(), principal.user_id, db.now(), link_id, link['status']))
            if cursor.rowcount != 1:
                raise HTTPException(409, '关联已变化，请刷新后审核。')
        _assert_scope_current(principal, tenants, connection)
    store.append_audit(principal, 'federation_link_review', link_id, 'success', {'status': body.status})
    return {'id': link_id, 'status': body.status}


@router.get('/links')
def list_links():
    principal, tenants = _scope()
    flags = _scope_view(principal, tenants)
    items = _cross_relations(tenants, include_pending=flags['can_review_links'] or flags['can_create_links'])
    _assert_scope_current(principal, tenants)
    return {'items': items, 'can_create': flags['can_create_links'], 'can_review': flags['can_review_links']}
