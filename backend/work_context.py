"""Shared boundaries for content arriving from a project or an assigned task.

Call with the caller's connection inside its write transaction so that the
content, relationship and parent audit history either all commit or all roll back.
"""
import json
import uuid
from fastapi import HTTPException
from .db import entity_json, now, log

EDITABLE_TASK_STATES = {'draft', 'active', 'pending', 'returned'}
CLOSED_PROJECT_STATES = {'completed', 'published', 'archived'}
ROLES = {'source_material': ('contains', '归集材料'), 'dataset': ('contains', '接入台账'), 'produced_asset': ('produces', '形成成果'), 'work_object': ('contains', '办理对象')}


def _entity(conn, eid, kind):
    e=entity_json(conn.execute('SELECT * FROM entities WHERE id=? AND kind=?',(eid,kind)).fetchone())
    if not e:raise HTTPException(422,'指定的'+{'project':'专题','task':'任务','matter':'业务事项'}.get(kind,'工作对象')+'不存在，请重新选择。')
    return e


def validate_context(conn, project_id='', task_id=''):
    project_id=str(project_id or '').strip();task_id=str(task_id or '').strip()
    if task_id:
        task=_entity(conn,task_id,'task')
        linked_project=str(task['payload'].get('project_id') or '')
        if project_id and project_id!=linked_project:
            raise HTTPException(422,'所选任务不属于当前专题，请核对工作归属。')
        project_id=linked_project
        if task['status'] not in EDITABLE_TASK_STATES:
            raise HTTPException(409,'本任务已提交复核或办结，工作清单已经锁定。请先在任务详情退回补充或重新办理，再归集材料和成果。')
    if project_id:
        project=_entity(conn,project_id,'project')
        if project['status'] in CLOSED_PROJECT_STATES:
            raise HTTPException(409,'本专题已归档，请先在专题档案重新打开，再归集材料或成果。')
    return project_id,task_id


def attach_context(conn, target_id, project_id='', task_id='', role='source_material', label='', actor='本地业务人员'):
    project_id,task_id=validate_context(conn,project_id,task_id)
    if role not in ROLES:raise HTTPException(422,'工作清单用途不正确。')
    target=entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(target_id,)).fetchone())
    if not target:raise HTTPException(422,'待关联的材料或成果不存在。')
    relation_type,default_label=ROLES[role];label=label or default_label
    stamp=now()
    for parent_id,kind in ((project_id,'project'),(task_id,'task')):
        if not parent_id:continue
        existing=conn.execute("SELECT id FROM relations WHERE source=? AND target=? AND status!='rejected' AND json_extract(payload,'$.role')=?",(parent_id,target_id,role)).fetchone()
        if existing:continue
        parent=_entity(conn,parent_id,kind)
        event={'target_id':target_id,'target_title':target['title'],'target_version':target['version'],'role':role,'label':label,'at':stamp}
        original=dict(parent);original['workspace_relation_change']=event
        conn.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(parent_id,parent['version'],json.dumps(original,ensure_ascii=False),stamp,label+'：'+target['title']))
        conn.execute('UPDATE entities SET version=version+1,updated_at=? WHERE id=?',(stamp,parent_id))
        conn.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',('rel-work-'+uuid.uuid4().hex,parent_id,target_id,relation_type,label,'confirmed',json.dumps({'role':role,'managed_by':'content_ingress','confirmed_by':actor,'target_version':target['version'],'created_at':stamp},ensure_ascii=False)))
        log(conn,parent_id,label,target['title']+'（'+target_id+'）',actor)
    return project_id,task_id


def validate_matter_context(conn, project_id='', task_id='', matter_id=''):
    """Extend existing ingress with a scoped ontology matter, without copying sources."""
    matter_id=str(matter_id or '').strip()
    if matter_id:
        matter=_entity(conn,matter_id,'matter')
        owner=str(matter['payload'].get('project_id') or '')
        if not owner:raise HTTPException(422,'该业务事项尚未归入有效专项。')
        if project_id and str(project_id)!=owner:raise HTTPException(422,'所选业务事项不属于当前专项。')
        project_id=owner
    project_id,task_id=validate_context(conn,project_id,task_id)
    return project_id,task_id,matter_id


def attach_matter_context(conn,target_id,project_id='',task_id='',matter_id='',role='source_material',label='',actor='本地业务人员',*,source_changed=False):
    project_id,task_id,matter_id=validate_matter_context(conn,project_id,task_id,matter_id)
    attach_context(conn,target_id,project_id,task_id,role,label,actor)
    if matter_id:
        target=entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(target_id,)).fetchone())
        if not target:raise HTTPException(422,'待关联工作对象不存在。')
        rel_type='produces' if role=='produced_asset' else 'references_material' if target['kind'] in ('material','dataset') else 'contains'
        rel_label=label or ('形成专项成果' if role=='produced_asset' else '关联事项材料')
        exists=conn.execute("SELECT 1 FROM relations WHERE source=? AND target=? AND type=? AND status!='rejected'",(matter_id,target_id,rel_type)).fetchone()
        if not exists or (source_changed and role in ('source_material','dataset')):
            if source_changed:rel_label='原始资料已补充，重新核对事项'
            matter=_entity(conn,matter_id,'matter');stamp=now();event={'target_id':target_id,'target_version':target['version'],'role':role,'project_id':project_id}
            original=dict(matter);original['workspace_relation_change']=event
            conn.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(matter_id,matter['version'],json.dumps(original,ensure_ascii=False),stamp,rel_label))
            payload=matter['payload']
            if role in ('source_material','dataset'):payload['review_state']='pending';payload['review_note']='新关联材料，需结合新增内容复核。'
            conn.execute('UPDATE entities SET payload=?,status=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),'draft' if role in ('source_material','dataset') else matter['status'],stamp,matter_id))
            if not exists:conn.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',('rel-matter-'+uuid.uuid4().hex,matter_id,target_id,rel_type,rel_label,'confirmed',json.dumps({'role':role,'target_version':target['version'],'managed_by':'content_ingress','project_id':project_id},ensure_ascii=False)))
            log(conn,matter_id,rel_label,target['title'],actor)
    return project_id,task_id,matter_id
