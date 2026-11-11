from fastapi import FastAPI,HTTPException,Query,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager,closing
from pathlib import Path
import json,uuid,os,hashlib,asyncio
import pymysql
from .db import connect,initialize,entity_json,log,now,ROOT,current_org_id
from .mcp_server import build_mcp_app
from .skill_harness import EXECUTOR as harness_executor
from .asset_kinds import canonical_entity, canonical_kind
from .version import APP_VERSION

mcp_app = build_mcp_app()

@asynccontextmanager
async def lifespan(app):
    from .security.store import initialize_control
    from .federation import initialize_federation
    from .jobs import ensure_queue, worker_loop
    from .maintenance import runtime_guard
    with runtime_guard(shared=True):
        await asyncio.to_thread(initialize_control)
        await asyncio.to_thread(initialize_federation)
        await asyncio.to_thread(ensure_queue)
        worker = asyncio.create_task(worker_loop()) if os.getenv('ONTOLOGY_WORKER_ENABLED', '1') == '1' else None
        try:
            async with mcp_app.lifespan():
                yield
        finally:
            await harness_executor.close()
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
app=FastAPI(title='Ontology One · 集团本体平台',version=APP_VERSION,lifespan=lifespan)
from .security.middleware import SecurityMiddleware
app.add_middleware(SecurityMiddleware)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173','http://localhost:8765','http://127.0.0.1:8765'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
@app.exception_handler(pymysql.err.MySQLError)
async def db_error(request,exc):return JSONResponse(status_code=503,content={'detail':'MySQL 数据服务暂时不可用或繁忙，请检查连接后重试。'})

@app.get('/api/health')
def health():return {'ok':True,'service':'集团本体平台','version':APP_VERSION,'database':'MySQL','mode':'enterprise-preview','time':now()}
@app.get('/api/catalog')
def catalog():
    with closing(connect()) as c, c:
        return {'domains':[r[0] for r in c.execute("SELECT DISTINCT domain FROM entities WHERE domain<>'' AND domain IS NOT NULL ORDER BY domain")], 'statuses':[r[0] for r in c.execute('SELECT DISTINCT status FROM entities')], 'kinds':[dict(r) for r in c.execute("SELECT kind,count(*) count FROM entities WHERE COALESCE(json_extract(payload,'$.active_asset'),1)!=0 GROUP BY kind")], 'organizations':[dict(r) for r in c.execute("SELECT id,title FROM entities WHERE kind='organization' ORDER BY id")]}
@app.get('/api/entities')
def entities(kind:str='',q:str='',domain:str='',org_id:str='',status:str='',source_kind:str='',page:int=1,page_size:int=30,history:str='current',asset_only:bool=False):
    page=max(1,page);page_size=min(max(1,page_size),200);where=[];args=[]
    if history!='all':where.append("COALESCE(json_extract(payload,'$.active_asset'),1)!=0")
    if asset_only:where.append("kind IN ('data','rule','skill','knowledge')")
    for k,v in [('kind',kind),('domain',domain),('org_id',org_id),('status',status),('source_kind',source_kind)]:
        if v:
            vals=v.split(',');where.append(f"{k} IN ({','.join('?' for _ in vals)})");args+=vals
    if q:where.append('(title LIKE ? OR summary LIKE ? OR id LIKE ? OR CAST(payload AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci LIKE ?)');args += ['%'+q+'%']*4
    filt=' WHERE '+' AND '.join(where) if where else ''
    with closing(connect()) as c, c:
        total=c.execute('SELECT COUNT(*) FROM entities'+filt,args).fetchone()[0]
        rows=c.execute('SELECT * FROM entities'+filt+' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size]).fetchall()
        return {'items':[entity_json(r) for r in rows],'total':total,'page':page,'page_size':page_size}
@app.get('/api/search')
def search(q:str=''):
    return entities(q=q,page_size=25)
@app.get('/api/entities/facets')
def entity_facets(kind:str='',q:str='',domain:str='',org_id:str='',status:str='',source_kind:str='',history:str='current'):
    filters={'kind':kind,'domain':domain,'org_id':org_id,'status':status,'source_kind':source_kind}
    def clause(omit=''):
        terms=[] if history=='all' else ["COALESCE(json_extract(payload,'$.active_asset'),1)!=0"];args=[]
        for field,value in filters.items():
            if value and field!=omit:
                values=value.split(',');terms.append(f"{field} IN ({','.join('?' for _ in values)})");args.extend(values)
        if q:terms.append('(title LIKE ? OR summary LIKE ? OR id LIKE ? OR CAST(payload AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci LIKE ?)');args.extend(['%'+q+'%']*4)
        return (' WHERE '+' AND '.join(terms) if terms else ''),args
    with closing(connect()) as c:
        where,args=clause();result={'total':c.execute('SELECT count(*) FROM entities'+where,args).fetchone()[0]}
        for field in ('kind','status','source_kind','domain'):
            where,args=clause(field)
            result[field]={row[0]:row[1] for row in c.execute(f'SELECT {field},count(*) FROM entities'+where+f' GROUP BY {field}',args) if row[0]}
        return result
@app.post('/api/entities')
async def create(request:Request):
    body = await request.json()
    return await asyncio.to_thread(_create_entity, body)

def _create_entity(b):
    b = {**b, 'kind': canonical_kind(b.get('kind', 'knowledge'))}
    title=str(b.get('title','')).strip()
    if not title:raise HTTPException(400,'请填写名称。')
    if b.get('kind')=='organization':raise HTTPException(403,'机构目录由集团账户管理统一维护。')
    if b.get('org_id') and b['org_id']!=current_org_id():raise HTTPException(403,'不能伪造其他机构的数据归属。')
    if (b.get('payload') or {}).get('authority_spec') or b.get('kind')=='person':raise HTTPException(400,'请从权责规范工作面创建，确保归属、流程和审核状态一致。')
    if b.get('kind') in ['matter','evidence']:raise HTTPException(400,'请从专项事项与依据工作面创建，确保归属、原文引用和办理状态一致。')
    if b.get('kind')=='project' and (b.get('payload') or {}).get('project_mode')=='special_review':raise HTTPException(400,'请从专题工作室的新建专项成果梳理入口创建。')
    if b.get('kind')=='task':
        if b.get('status','draft')!='draft':raise HTTPException(400,'新建任务请先保存为待办理，再从任务详情推进。')
        if (b.get('payload') or {}).get('task_type'):raise HTTPException(400,'请从专题工作档案按明确的任务类型分解工作。')
    if b.get('kind') in ['data','rule','skill','knowledge'] and b.get('status','draft')!='draft':raise HTTPException(400,'新建资产请先保存为草稿，再提交审核。')
    eid=b.get('id') or f"{b.get('kind','ASSET').upper()}-USER-{uuid.uuid4().hex[:10]}";t=now()
    with closing(connect()) as c, c:
        from .skills import guard_generic_entity
        guard_generic_entity(c, body=b)
        if c.execute('SELECT id FROM entities WHERE id=?',(eid,)).fetchone():raise HTTPException(409,'该编号已存在。')
        c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(eid,b.get('kind','knowledge'),title,b.get('summary',''),b.get('domain','综合管理'),current_org_id(),b.get('status','draft'),b.get('source_kind','user'),json.dumps(b.get('payload',{}),ensure_ascii=False),t,t,1))
        log(c,eid,'创建',title,b.get('actor','本地体验用户'))
        for parent_key in ['project_id','case_id','related_asset_id']:
            parent=b.get('payload',{}).get(parent_key)
            if parent and c.execute('SELECT 1 FROM entities WHERE id=?',(parent,)).fetchone():
                c.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',('rel-'+uuid.uuid4().hex,parent,eid,'contains','组织开展','confirmed','{}'))
        c.commit()
    return detail(eid)
@app.get('/api/entities/{eid}/versions')
def versions(eid:str):
    with closing(connect()) as c, c:
        rows=[dict(r) for r in c.execute('SELECT * FROM versions WHERE entity_id=? ORDER BY version DESC,id DESC',(eid,))]
        for r in rows:
            try:r['snapshot']=canonical_entity(json.loads(r['snapshot']))
            except:pass
        return {'items':rows}
@app.get('/api/entities/{eid}/route')
def entity_route(eid:str):
    with closing(connect()) as c:
        row=c.execute('SELECT id,kind,title,status,payload FROM entities WHERE id=?',(eid,)).fetchone()
        if not row:raise HTTPException(404,'未找到这项内容。')
        result=dict(row);payload=json.loads(result.pop('payload') or '{}');result['project_id']=payload.get('project_id','');result['matter_id']=payload.get('matter_id','')
        return result
@app.get('/api/entities/{eid}')
def detail(eid:str,version:int=0):
    with closing(connect()) as c, c:
        d=entity_json(c.execute('SELECT * FROM entities WHERE id=?',(eid,)).fetchone())
        if not d:raise HTTPException(404,'未找到这项内容。')
        if version and version!=d['version']:
            previous=c.execute('SELECT snapshot FROM versions WHERE entity_id=? AND version=? ORDER BY id DESC LIMIT 1',(eid,version)).fetchone()
            if not previous:raise HTTPException(404,'所指定的历史版本不存在。')
            current_version=d['version'];d=canonical_entity(json.loads(previous['snapshot']))
            d['payload']={**d.get('payload',{}),'snapshot_readonly':True,'current_version':current_version}
        base='SELECT r.*,s.title source_title,t.title target_title,s.kind source_kind,t.kind target_kind FROM relations r LEFT JOIN entities s ON s.id=r.source LEFT JOIN entities t ON t.id=r.target WHERE '
        d['relations']={'outgoing':[dict(r) for r in c.execute(base+'r.source=? LIMIT 120',(eid,))],'incoming':[dict(r) for r in c.execute(base+'r.target=? LIMIT 120',(eid,))]}
        d['relation_counts']={'outgoing':c.execute('SELECT count(*) FROM relations WHERE source=?',(eid,)).fetchone()[0],'incoming':c.execute('SELECT count(*) FROM relations WHERE target=?',(eid,)).fetchone()[0]}
        d['activities']=[dict(r) for r in c.execute('SELECT * FROM activities WHERE entity_id=? ORDER BY id DESC LIMIT 30',(eid,))]
        return d
@app.patch('/api/entities/{eid}')
async def edit(eid:str,request:Request):
    b=await request.json()
    with closing(connect()) as c, c:
        old=entity_json(c.execute('SELECT * FROM entities WHERE id=? FOR UPDATE',(eid,)).fetchone())
        if not old:raise HTTPException(404,'未找到内容。')
        from .skills import guard_generic_entity
        guard_generic_entity(c, old, b)
        if old['kind']=='organization':raise HTTPException(403,'机构根节点不能通过业务编辑修改。')
        if b.get('org_id') and b['org_id']!=current_org_id():raise HTTPException(403,'不能变更数据的机构归属。')
        if old['payload'].get('authority_spec') or (b.get('payload') or {}).get('authority_spec') or old['kind']=='person':raise HTTPException(400,'请从权责规范工作面编辑，保留版本、组织人员归属和流程校验。')
        if old['kind'] in ['matter','evidence']:raise HTTPException(400,'请从专项事项与依据工作面修改，确保归属、原文引用和办理状态一致。')
        if old['payload'].get('active_asset') is False:raise HTTPException(409,'这是保留追溯的历史条目。请打开整合后的成果开展修订。')
        if old['kind']=='task':
            from .projects import edit_task
            return await edit_task(eid,request)
        if old['kind']=='project':
            from .projects import edit_project
            return await edit_project(eid,request)
        if old['kind'] in ['data','rule','skill','knowledge'] and 'status' in b and b['status'] not in [old['status'],'draft']:
            raise HTTPException(400,'请通过提交、审核和发布操作变更资产状态。')
        if old['kind'] in ['data','rule','skill','knowledge'] and old['status'] in ['published','approved','review'] and any(k in b for k in ['title','summary','domain','org_id','payload']):
            b['status']='draft'
        c.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(eid,old['version'],json.dumps(old,ensure_ascii=False),now(),b.get('reason','内容修订')))
        allowed=['title','summary','domain','org_id','status','payload']
        for k in allowed:
            if k in b:
                val=json.dumps(b[k],ensure_ascii=False) if k=='payload' else b[k]
                c.execute(f'UPDATE entities SET {k}=? WHERE id=?',(val,eid))
        c.execute('UPDATE entities SET version=version+1,updated_at=? WHERE id=?',(now(),eid));log(c,eid,'修订',b.get('reason','修订内容并保留原版本'));c.commit()
    return detail(eid)
@app.post('/api/entities/{eid}/action')
async def action(eid:str,request:Request):
    body = await request.json()
    return await asyncio.to_thread(_entity_action, eid, body)

def _entity_action(eid, b):
    act=b.get('action');targets={'submit':'review','approve':'approved','return':'returned','publish':'published','revise':'draft','archive':'archived','complete':'completed'}
    with closing(connect()) as c:
        existing=entity_json(c.execute('SELECT * FROM entities WHERE id=?',(eid,)).fetchone())
    if existing and existing['kind']=='organization':raise HTTPException(403,'机构根节点不能通过业务操作修改状态。')
    if existing and existing['kind']=='person':raise HTTPException(400,'任职人员属于组织归属对象，不通过资产审核流程变更状态。')
    if existing and existing['kind'] in ['matter','evidence']:raise HTTPException(400,'请从专项事项与依据工作面办理，不可通过资产审核改变专项工作状态。')
    if existing and existing['kind']=='task':
        from .projects import task_action
        return task_action(eid,b)
    if existing and existing['kind']=='project':
        from .projects import project_action
        return project_action(eid,b)
    if act not in targets:raise HTTPException(400,'不支持此操作。')
    with closing(connect()) as c, c:
        if existing and existing['payload'].get('authority_spec'):c.execute('BEGIN IMMEDIATE')
        old=entity_json(c.execute('SELECT * FROM entities WHERE id=? FOR UPDATE',(eid,)).fetchone())
        if not old:raise HTTPException(404,'未找到内容。')
        from .skills import guard_generic_entity
        guard_generic_entity(c, old)
        if old['payload'].get('active_asset') is False:raise HTTPException(409,'历史条目保留原审定记录，请在当前成果中办理。')
        if old['payload'].get('authority_spec'):
            from .authority import guard_action
            guard_action(c,old,b)
        if act=='approve' and old['status']!='review':raise HTTPException(400,'请先提交审核，再确认审核结果。')
        if act=='complete' and old['kind']!='task':raise HTTPException(400,'该内容不属于可办结的任务。')
        if act=='publish' and old['status'] not in ['approved','published']:raise HTTPException(400,'请先审核通过，再发布成果。')
        if act=='return' and not str(b.get('reason','')).strip():raise HTTPException(400,'请填写需要补充或修改的内容。')
        c.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(eid,old['version'],json.dumps(old,ensure_ascii=False),now(),b.get('reason',act)))
        c.execute('UPDATE entities SET status=?,version=version+1,updated_at=? WHERE id=?',(targets[act],now(),eid))
        if old['payload'].get('authority_spec'):
            from .authority import sync_relations
            sync_relations(c,entity_json(c.execute('SELECT * FROM entities WHERE id=?',(eid,)).fetchone()))
        labels={'submit':'提交审核','approve':'审核通过','return':'退回补充','publish':'发布成果','revise':'发起修订','archive':'归档停用','complete':'完成任务'}
        log(c,eid,labels[act],b.get('reason',''),b.get('actor','本地体验用户'))
        if act=='complete' and old['payload'].get('project_id'):
            project_id=old['payload']['project_id']
            project=entity_json(c.execute('SELECT * FROM entities WHERE id=? AND kind=?',(project_id,'project')).fetchone())
            if project:
                tasks=c.execute("SELECT status FROM entities WHERE kind='task' AND json_extract(payload,'$.project_id')=?",(project_id,)).fetchall()
                finished=sum(t['status']=='completed' for t in tasks)
                c.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(project_id,project['version'],json.dumps(project,ensure_ascii=False),now(),'任务办理后更新专题进度'))
                project['payload']['progress']=round(finished/max(1,len(tasks))*100)
                project['payload']['completed_tasks']=finished
                c.execute('UPDATE entities SET payload=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(project['payload'],ensure_ascii=False),now(),project_id))
                log(c,project_id,'更新专题进度',f'已完成 {finished} / {len(tasks)} 项工作任务')
        if act=='return':
            tid='TASK-USER-'+uuid.uuid4().hex[:8];p={'related_id':eid,'reason':b['reason'],'responsible':'资产整理人员'}
            c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(tid,'task','补充完善：'+old['title'][:45],b['reason'],old['domain'],old['org_id'],'draft','user',json.dumps(p,ensure_ascii=False),now(),now(),1))
            c.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',('rel-'+uuid.uuid4().hex,eid,tid,'requires','需要补充','confirmed','{}'))
        c.commit()
    return detail(eid)
@app.post('/api/entities/{eid}/restore')
async def restore_entity(eid: str, request: Request):
    body = await request.json()
    return await asyncio.to_thread(_restore_entity, eid, body)

def _restore_entity(eid, body):
    from .security.context import require_context
    principal = require_context()
    if principal.role != 'admin':
        raise HTTPException(403, '仅管理员可还原历史版本。')
    reason = str(body.get('reason', '')).strip()
    if not reason:
        raise HTTPException(422, '请填写还原原因。')
    try:
        version = int(body.get('version', 0))
    except (TypeError, ValueError):
        raise HTTPException(422, '请选择有效历史版本。')
    with closing(connect()) as conn, conn:
        old = entity_json(conn.execute('SELECT * FROM entities WHERE id=? FOR UPDATE', (eid,)).fetchone())
        previous = conn.execute('SELECT snapshot FROM versions WHERE entity_id=? AND version=? ORDER BY id DESC LIMIT 1', (eid, version)).fetchone()
        if not old or not previous:
            raise HTTPException(404, '历史版本不存在。')
        from .skills import guard_generic_entity
        guard_generic_entity(conn, old)
        if old['kind'] in ('organization', 'person'):
            raise HTTPException(403, '组织与人员请通过专用管理入口维护。')
        snapshot = canonical_entity(json.loads(previous['snapshot']))
        payload = snapshot.get('payload', {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        conn.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)', (eid, old['version'], json.dumps(old, ensure_ascii=False), now(), '还原前快照：' + reason))
        conn.execute('UPDATE entities SET title=?,summary=?,domain=?,payload=?,status=?,version=version+1,updated_at=? WHERE id=?',
                     (snapshot.get('title', old['title']), snapshot.get('summary', ''), snapshot.get('domain', ''), json.dumps(payload, ensure_ascii=False), 'draft', now(), eid))
        log(conn, eid, '还原历史版本', f'还原版本{version}为草稿：{reason}')
    return detail(eid)

@app.get('/api/activity')
def activity(entity_id:str='',limit:int=50):
    with closing(connect()) as c, c:
        w=' WHERE entity_id=?' if entity_id else '';a=[entity_id] if entity_id else []
        return {'items':[dict(r) for r in c.execute('SELECT a.*,e.title entity_title FROM activities a LEFT JOIN entities e ON e.id=a.entity_id'+w+' ORDER BY a.id DESC LIMIT ?',a+[min(limit,200)])]}
@app.get('/api/relations')
def relations(node:str='',status:str='',q:str='',page:int=1,page_size:int=30):
    w=[];args=[]
    if node:w.append('(r.source=? OR r.target=?)');args += [node,node]
    if status:w.append('r.status=?');args.append(status)
    if q:w.append('(r.label LIKE ? OR r.source LIKE ? OR r.target LIKE ? OR EXISTS (SELECT 1 FROM entities e WHERE e.id IN (r.source,r.target) AND e.title LIKE ?))');args += ['%'+q+'%']*4
    filt=' WHERE '+' AND '.join(w) if w else ''
    with closing(connect()) as c, c:
        total=c.execute('SELECT count(*) FROM relations r'+filt,args).fetchone()[0]
        items=c.execute('SELECT r.*,s.title source_title,t.title target_title,s.kind source_kind,t.kind target_kind FROM relations r LEFT JOIN entities s ON s.id=r.source LEFT JOIN entities t ON t.id=r.target'+filt+' LIMIT ? OFFSET ?',args+[min(page_size,100),(max(1,page)-1)*min(page_size,100)]).fetchall()
        return {'items':[{**dict(r),'work_entry':relation_work_entry(c,r)} for r in items],'total':total}

def relation_work_entry(c,row):
    """Operational membership is reviewed in its own versioned workflow."""
    endpoints=[entity_json(c.execute('SELECT * FROM entities WHERE id=?',(eid,)).fetchone()) for eid in (row['source'],row['target'])]
    if (json.loads(row['payload'] or '{}').get('skill_managed') or
            (endpoints[0] and endpoints[0]['payload'].get('managed_skill'))):
        return {'href':'/skill-development/'+row['source'],'label':'核对技能依赖','reason':'技能依赖随包版本校验、测试与发布，请从技能开发办理。'}
    for entity in endpoints:
        if entity and entity['payload'].get('active_asset') is False:
            return {'href':'/assets/'+entity['id'],'label':'查看历史依据','reason':'历史关联保留原审定记录，请从当前成果开展修订。'}
    if any(entity and entity['kind']=='person' for entity in endpoints) and row['type']=='member_of':
        person=next(entity for entity in endpoints if entity and entity['kind']=='person')
        return {'href':'/authority?person='+person['id'],'label':'查看任职归属','reason':'任职归属由人员登记维护，不可独立变更组织关联状态。'}
    if json.loads(row['payload'] or '{}').get('authority_managed'):
        return {'href':'/authority/'+row['source'],'label':'核对权责规范','reason':'请在权责规范中核对适用组织、人员和来源，再提交审核；关系与规范版本一并维护。'}
    if row['type']=='includes_knowledge':
        return {'href':'/knowledge/collections/'+row['source']+'?state=all','label':'核对专栏收录','reason':'请在知识专栏核对收录，保留采用版本与处理意见。'}
    for entity in endpoints:
        if entity and entity['kind'] in ('project','task','matter','evidence'):
            return {'href':'/assets/'+entity['id'],'label':'进入工作档案','reason':'这项关系属于工作清单或专项依据，请在对应工作档案办理，确保审校与归档状态一致。'}
    return None

@app.patch('/api/relations/{rid}')
async def confirm_relation(rid:str,request:Request):
    body = await request.json()
    return await asyncio.to_thread(_confirm_relation, rid, body)

def _confirm_relation(rid, b):
    if b.get('status') not in ['candidate','confirmed','rejected']:raise HTTPException(400,'无效状态')
    if b.get('status') in ['confirmed','rejected'] and not str(b.get('reason','')).strip():raise HTTPException(400,'请说明本次核对的依据或不采纳原因。')
    with closing(connect()) as c, c:
        row=c.execute('SELECT * FROM relations WHERE id=? FOR UPDATE',(rid,)).fetchone()
        if not row:raise HTTPException(404,'未找到关系')
        entry=relation_work_entry(c,row)
        if entry:raise HTTPException(400,entry['reason'])
        p=json.loads(row['payload'] or '{}')
        if p.get('proposal_job_id') and b['status']!='rejected':
            raise HTTPException(400,'这条关联已经过版本绑定审定。若需重新建立，请从资产档案的关联定位重新核对两端内容。')
        reviewed_at=now()
        p.setdefault('review_history',[]).append({'from_status':row['status'],'to_status':b['status'],'previous_reason':p.get('review_reason',''),'reason':b.get('reason',''),'reviewed_at':reviewed_at})
        if p.get('proposal_job_id'):p['subsequent_review_reason']=b.get('reason','')
        else:p['review_reason']=b.get('reason','')
        p['reviewed_at']=reviewed_at
        source=entity_json(c.execute('SELECT * FROM entities WHERE id=? FOR UPDATE',(row['source'],)).fetchone())
        if source:
            from .skills import guard_generic_entity
            guard_generic_entity(c, source)
            c.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(source['id'],source['version'],json.dumps(source,ensure_ascii=False),reviewed_at,'关联复核：'+b.get('reason','')))
            c.execute('UPDATE entities SET version=version+1,updated_at=? WHERE id=?',(reviewed_at,source['id']))
        c.execute('UPDATE relations SET status=?,payload=? WHERE id=?',(b['status'],json.dumps(p,ensure_ascii=False),rid));log(c,row['source'],'关联核对',b.get('reason','关联状态已更新'));c.commit()
    return {'ok':True}
@app.get('/api/overview')
def overview():
    current="COALESCE(json_extract(payload,'$.active_asset'),1)!=0"
    asset_where="kind IN ('data','rule','skill','knowledge') AND "+current
    with closing(connect()) as c,c:
        counts={r['kind']:r['n'] for r in c.execute('SELECT kind,count(*) n FROM entities WHERE '+current+' GROUP BY kind')}
        assets=sum(counts.get(k,0) for k in ('data','rule','skill','knowledge'))
        published=c.execute("SELECT count(*) FROM entities WHERE "+asset_where+" AND status='published'").fetchone()[0]
        historical=c.execute("SELECT count(*) FROM entities WHERE kind IN ('data','rule','skill','knowledge') AND NOT ("+current+")").fetchone()[0]
        domains=[dict(r) for r in c.execute("SELECT domain,count(*) count,sum(CASE WHEN status='published' THEN 1 ELSE 0 END) published FROM entities WHERE "+asset_where+" GROUP BY domain ORDER BY count DESC")]
        status=[dict(r) for r in c.execute("SELECT status,count(*) count FROM entities WHERE "+asset_where+" GROUP BY status")]
        projects=[entity_json(r) for r in c.execute("SELECT * FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY coalesce(nullif(json_extract(payload,'$.topic_label'),''),domain) ORDER BY CASE WHEN status IN ('active','returned','draft') THEN 0 ELSE 1 END,id DESC) topic_rank FROM entities WHERE kind='project') ranked_projects WHERE topic_rank=1 ORDER BY updated_at DESC,id LIMIT 6")]
        recent=[entity_json(r) for r in c.execute("SELECT * FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY coalesce(nullif(json_extract(payload,'$.topic_label'),''),domain) ORDER BY updated_at DESC,id) topic_rank FROM entities WHERE kind IN ('knowledge','rule') AND status='published' AND "+current+") ranked_recent WHERE topic_rank=1 ORDER BY updated_at DESC,CASE kind WHEN 'knowledge' THEN 0 ELSE 1 END,id LIMIT 5")]
        relation_where="r.source NOT IN (SELECT id FROM entities WHERE json_extract(payload,'$.active_asset')=0) AND r.target NOT IN (SELECT id FROM entities WHERE json_extract(payload,'$.active_asset')=0)"
        relation_statuses={r['status']:r['n'] for r in c.execute('SELECT r.status,count(*) n FROM relations r WHERE '+relation_where+' GROUP BY r.status')}
        return {'counts':counts,'assets':assets,'published':published,'historical_assets':historical,'relations':sum(relation_statuses.values()),'relation_statuses':relation_statuses,'confirmed_relations':relation_statuses.get('confirmed',0),'data_rows':c.execute('SELECT count(*) FROM dataset_rows').fetchone()[0],'domains':domains,'statuses':status,'projects':projects,'recent':recent,'activities':activity(limit=7)['items'],'local_mode':True}

from .materials import router as material_router
from .datasets import router as dataset_router
from .graph import router as graph_router
from .ai import router as ai_router
from .applications import router as applications_router
from .system import router as system_router
from .experience import router as experience_router
from .impact import router as impact_router
from .projects import router as projects_router
from .placement import router as placement_router
from .asset_catalog import router as asset_catalog_router
from .special_projects import router as special_projects_router
from .data_instances import router as data_instances_router
from .authority import router as authority_router
from .asset_import import router as asset_import_router
for router in [material_router,dataset_router,graph_router,ai_router,applications_router,system_router,experience_router,impact_router,projects_router,placement_router,asset_catalog_router,special_projects_router,data_instances_router,authority_router,asset_import_router]:app.include_router(router,prefix='/api')
from .identity import router as identity_router
from .federation import router as federation_router
from .jobs import router as jobs_router
app.include_router(identity_router, prefix='/api')
app.include_router(federation_router, prefix='/api')
app.include_router(jobs_router, prefix='/api')
from .integrations import router as integration_router
app.include_router(integration_router, prefix='/api')
from .sources import router as source_router
app.include_router(source_router, prefix='/api')
from .skill_harness import router as harness_router
app.include_router(harness_router, prefix='/api')
from .skills import router as skill_router
app.include_router(skill_router, prefix='/api')
from .security.applications import router as integration_applications_router
app.include_router(integration_applications_router, prefix='/api')
app.mount('/mcp', mcp_app)

from .agent_proxy import router as agent_proxy_router
app.include_router(agent_proxy_router, prefix='/api')

dist=ROOT/'frontend/dist'
if dist.exists():
    app.mount('/static',StaticFiles(directory=dist/'static'),name='static')
    @app.get('/{path:path}')
    def spa(path:str):
        if path.startswith('api/'):raise HTTPException(404,'接口不存在')
        resolved=(dist/path).resolve()
        if resolved.is_relative_to(dist.resolve()) and resolved.is_file():return FileResponse(resolved)
        return FileResponse(dist/'index.html')
