"""Fixed business-operation adapters for the single-owner assistance service.

HTTP input selects a reviewed operation, never an import, URL, SQL or callable.
The original business handlers retain their validation and adoption workflow.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import inspect
import json
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request

from .db import connect
from .security.context import use_context
from .security.policy import authorize

_registered = False


class AskBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=1000)
    mode: str = 'online'


class PlanBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instruction: str = Field(default='', max_length=5000)


def _request(body):
    raw = json.dumps(body, ensure_ascii=False).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {'type': 'http.disconnect'}
        sent = True
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    return Request({'type': 'http', 'method': 'POST', 'path': '/',
                    'headers': [(b'content-type', b'application/json')]}, receive)


def _ids(value, result=None, depth=0):
    """Collect bounded business references, never arbitrary data-cell values."""
    result = set() if result is None else result
    if depth > 12:
        return result
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'id', 'entity_id', 'material_id', 'source_id', 'target_id',
                       'standard_id', 'project_id', 'task_id', 'mapping_id'}:
                if isinstance(item, str) and 0 < len(item) <= 191:
                    result.add(item)
            elif key in {'material_ids', 'entity_ids', 'org_ids', 'person_ids'}:
                if isinstance(item, list):
                    result.update(x for x in item if isinstance(x, str) and 0 < len(x) <= 191)
            elif key not in {'rows', 'samples', 'examples', 'input', 'output', 'files'}:
                _ids(item, result, depth + 1)
            if len(result) > 512:
                raise HTTPException(413, '本轮引用较多，请缩小任务范围。')
    elif isinstance(value, list):
        for item in value:
            _ids(item, result, depth + 1)
    return result


def _snapshot(identifiers):
    if not identifiers:
        return {}
    ids = sorted(identifiers)
    with closing(connect()) as connection:
        rows = connection.execute('SELECT id,version,status,updated_at FROM entities WHERE id IN (' +
                                  ','.join('?' for _ in ids) + ')', ids).fetchall()
    return {row['id']: (row['version'], row['status'], row['updated_at']) for row in rows}


def _handler(name, title, path_template, page, model, function, *, request_body=False, parameter=None, optional_target=False):
    async def prepare(principal, entity_id, body):
        from .agent_tasks import current
        from . import skill_harness
        await asyncio.to_thread(current, principal)
        if ('{id}' in path_template or optional_target and entity_id) and (not isinstance(entity_id, str)
                                       or not re.fullmatch(r'[A-Za-z0-9_-]{1,191}', entity_id)):
            raise HTTPException(422, '请选择有效的业务对象。')
        if '{id}' not in path_template and entity_id and not optional_target:
            raise HTTPException(422, '当前任务不接受额外业务对象。')
        if not isinstance(body, dict) or len(skill_harness._json(body)) > 64 * 1024:
            raise HTTPException(413, '本轮业务说明过长，请分次处理。')
        try:
            if set(body) - set(model.model_fields):
                raise ValueError()
            parsed = model.model_validate(body, strict=True)
        except (ValidationError, ValueError, TypeError):
            raise HTTPException(422, '请核对本轮业务要求和选用范围。') from None
        path = path_template.replace('{id}', entity_id)
        decision = authorize(principal, 'POST', path)
        if not decision.allowed:
            raise HTTPException(decision.code, decision.reason)
        normalized = parsed.model_dump()
        if normalized.get('mode') not in (None, 'online', 'auto'):
            raise HTTPException(422, '此入口仅处理在线智能任务。')
        references = _ids(normalized)
        if entity_id:
            references.add(entity_id)
        if entity_id and entity_id not in await asyncio.to_thread(_snapshot, {entity_id}):
            raise HTTPException(404, '未找到当前机构的业务对象。')
        if optional_target and entity_id:
            def check_target():
                with closing(connect()) as connection:
                    row = connection.execute('SELECT kind,payload FROM entities WHERE id=?', (entity_id,)).fetchone()
                if not row or row['kind'] != 'rule' or not isinstance(json.loads(row['payload']).get('authority_spec'), dict):
                    raise HTTPException(422, '请选择当前机构已有的权责规范。')
            await asyncio.to_thread(check_target)
        state = {}

        async def operation():
            argument = _request(normalized) if request_body else parsed
            value = function(**({parameter: entity_id} if parameter else {}),
                             **({'request': argument} if request_body else {'body': argument}))
            result = await value if inspect.isawaitable(value) else value
            # Existing handlers check generation-time versions. Freeze their
            # accepted business references for every later result read as well.
            references.update(_ids(result))
            state['snapshot'] = await asyncio.to_thread(_snapshot, references)
            state['configuration_revision'] = (await asyncio.to_thread(skill_harness._configuration))['revision']
            return result

        def guard():
            if state.get('snapshot') is not None and _snapshot(references) != state['snapshot']:
                raise HTTPException(409, '相关内容已有更新，请回到业务页面重新核对。')
            if (state.get('configuration_revision') is not None and
                    skill_harness._configuration()['revision'] != state['configuration_revision']):
                raise skill_harness._error('HARNESS_CONFIG_CHANGED')

        instruction = next((normalized[k] for k in ('question', 'instruction', 'text', 'content', 'context')
                            if isinstance(normalized.get(k), str) and normalized[k].strip()), title)
        return {'operation': operation, 'title': title, 'entity_id': entity_id,
                'href': page.replace('{id}', entity_id).rstrip('/'), 'guard': guard,
                'instruction': instruction[:20000]}
    return prepare


def register_operations():
    global _registered
    if _registered:
        return
    from .harness_runs import register_operation
    from . import ai, authority, datasets, experience, materials, placement, projects

    entries = [
        ('ai_ask', '依据本机构资料回答', '/api/ai/ask', '/briefing', AskBody, ai.ask, True, None),
        ('authority_assist', '整理权责与办理流程', '/api/authority/assist', '/authority/{id}', authority.AssistRequest, authority.assist, False, None),
        ('project_plan', '完善专题工作安排', '/api/projects/{id}/plan', '/projects/{id}', PlanBody, projects.suggest_plan, True, 'pid'),
        ('placement_suggest', '核对业务资产关联', '/api/placement/{id}/suggest', '/assets/{id}', placement.SuggestRequest, placement.suggest, False, 'asset_id'),
        ('dataset_mapping', '识别台账字段口径', '/api/datasets/{id}/mapping-recognize', '/datasets/{id}', datasets.RecognitionBody, datasets.recognize_mappings, False, 'dataset_id'),
        ('material_reading', '整理材料阅读结构', '/api/materials/{id}/reading/ai', '/materials/{id}', materials.ReadingAI, materials.reading_ai, False, 'material_id'),
        ('material_segments', '按业务含义整理原文', '/api/materials/{id}/segment-preview', '/materials/{id}', materials.SegmentRequest, materials.segment_preview, False, 'material_id'),
        ('experience_start', '开始经验访谈', '/api/experience/sessions', '/experience', experience.CreateSession, experience.create_session, False, None),
        ('experience_turn', '根据经验叙述继续追问', '/api/experience/{id}/turns', '/experience?session={id}', experience.TurnRequest, experience.add_turn, False, 'session_id'),
        ('experience_question', '生成经验访谈问题', '/api/experience/{id}/question', '/experience?session={id}', experience.QuestionRequest, experience.retry_question, False, 'session_id'),
        ('experience_proposal', '按业务要求完善经验成果', '/api/experience/{id}/proposals', '/experience?session={id}', experience.ProposalRequest, experience.propose_revision, False, 'session_id'),
    ]
    for name, title, path, page, model, function, raw, parameter in entries:
        handler = _handler(name, title, path, page, model, function, request_body=raw, parameter=parameter,
                           optional_target=name == 'authority_assist')
        # segment_preview's model parameter is named request, not body.
        if name == 'material_segments':
            async def segment_adapter(material_id, body):
                return await materials.segment_preview(material_id, body)
            handler = _handler(name, title, path, page, model, segment_adapter, parameter=parameter)
        register_operation(name, handler)
    _registered = True
