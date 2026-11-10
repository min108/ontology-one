"""Canonical skill identifiers with a narrow, non-destructive legacy upgrade.

Entity IDs, quoted business text, stored version snapshots and audit records are
never rewritten. Legacy algorithm descriptions are not executable skill packs.
"""
from __future__ import annotations

import json

LEGACY_KIND = 'algorithm'
CANONICAL_KIND = 'skill'
LEGACY_RELATION = 'references_algorithm'
CANONICAL_RELATION = 'references_skill'
PAYLOAD_KEYS = {'algorithm_type': 'skill_type', 'related_algorithms': 'related_skills',
                '算法类型': '技能类型', '算法编码': '技能编码', '算法逻辑名称': '技能逻辑名称', '关联算法': '关联技能'}


def canonical_kind(value):
    return CANONICAL_KIND if value == LEGACY_KIND else value


def canonical_entity(value):
    """Return a canonical view; do not mutate the supplied snapshot or its text."""
    result = dict(value)
    original_kind = result.get('kind')
    result['kind'] = canonical_kind(original_kind)
    payload = result.get('payload') or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError('资产内容必须为JSON对象；技能标识迁移已停止。')
    payload = dict(payload)
    for old, new in PAYLOAD_KEYS.items():
        if old in payload:
            if new in payload and payload[new] != payload[old]:
                raise ValueError('技能资产的新旧字段存在冲突，迁移已停止，请先核对内容。')
            payload[new] = payload.pop(old)
    # Pending material candidates remain operable after the terminology change.
    # Only this governed field is traversed; arbitrary nested business JSON is not.
    if isinstance(payload.get('candidates'), list):
        payload['candidates'] = [
            {**candidate, 'kind': CANONICAL_KIND} if isinstance(candidate, dict)
            and candidate.get('kind') == LEGACY_KIND else candidate
            for candidate in payload['candidates']
        ]
    if original_kind == LEGACY_KIND:
        payload['legacy_skill'] = True
        payload['executable'] = False
    result['payload'] = payload
    return result


def migrate_skill_assets(connection):
    """Idempotent tenant migration, called under schema/maintenance locking.

No version is fabricated: this changes storage identifiers, not business content.
Historic snapshots remain byte-for-byte unchanged and are canonicalized on read.
The caller owns the surrounding transaction and commit/rollback.
"""
    changed = 0
    rows = connection.execute(
        "SELECT id,kind,payload FROM entities WHERE kind=? OR CAST(payload AS CHAR) LIKE ? OR CAST(payload AS CHAR) LIKE ?",
        (LEGACY_KIND, '%algorithm%', '%算法%'),
    ).fetchall()
    # Validate all candidates before the first mutation, including field conflicts.
    pending = []
    for row in rows:
        original = dict(row)
        upgraded = canonical_entity(original)
        before = json.loads(original['payload']) if isinstance(original['payload'], str) else original['payload']
        if upgraded['kind'] != original['kind'] or upgraded['payload'] != before:
            pending.append((upgraded['kind'], json.dumps(upgraded['payload'], ensure_ascii=False), original['id']))
    for values in pending:
        connection.execute('UPDATE entities SET kind=?,payload=? WHERE id=?', values)
        changed += 1
    connection.execute('UPDATE relations SET type=? WHERE type=?', (CANONICAL_RELATION, LEGACY_RELATION))
    return changed
