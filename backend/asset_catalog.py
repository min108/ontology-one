"""Business classification and knowledge collections over the same asset records."""
from contextlib import closing
from collections import Counter
import hashlib
import json
import uuid
from fastapi import APIRouter, HTTPException, Request
from .db import connect, entity_json, now, log

router=APIRouter()
ASSET_KINDS=('data','rule','algorithm','knowledge')
CURRENT="COALESCE(json_extract(payload,'$.active_asset'),1) != 0"
FIELDS={'domain':'domain','status':'status','source_kind':'source_kind','category':"COALESCE(json_extract(payload,'$.category'),'')",'subcategory':"COALESCE(json_extract(payload,'$.subcategory'),'')",'authority':"COALESCE(json_extract(payload,'$.authority'),'')",'basis':"COALESCE(json_extract(payload,'$.basis'),'')",'version_key':"COALESCE(NULLIF(json_extract(payload,'$.effective_from'),''),'未标注来源版本')",'rule_type':"COALESCE(json_extract(payload,'$.rule_type'),'')",'algorithm_type':"COALESCE(json_extract(payload,'$.algorithm_type'),'')",'ownership':"COALESCE(json_extract(payload,'$.ownership'),'')",'knowledge_form':"COALESCE(json_extract(payload,'$.knowledge_form'),'experience')",'origin_type':"COALESCE(json_extract(payload,'$.origin_type'),'personal_note')",'asset_source':"COALESCE(json_extract(payload,'$.asset_source'),'')"}

def collected_article(c,row):
    article=entity_json(row);membership=json.loads(article.pop('membership_payload','{}') or '{}')
    adopted=membership.get('adopted_version',article['version']);membership.update(adopted_version=adopted,current_version=article['version'],current_status=article['status'],needs_review=adopted!=article['version'])
    if membership['needs_review']:
        previous=c.execute('SELECT snapshot FROM versions WHERE entity_id=? AND version=? ORDER BY id DESC LIMIT 1',(article['id'],adopted)).fetchone()
        if previous:
            snapshot=json.loads(previous['snapshot']);snapshot['payload']=json.loads(snapshot['payload']) if isinstance(snapshot.get('payload'),str) else snapshot.get('payload',{});article={**article,**snapshot}
        else:membership['snapshot_missing']=True
    article['membership']=membership;return article

def predicate(kind,params,omit=''):
    if kind not in ASSET_KINDS:raise HTTPException(422,'请选择数据、规则、算法或知识资产。')
    terms=['kind=?'];args=[kind]
    if params.get('history')!='all':terms.append(CURRENT)
    for field,expression in FIELDS.items():
        value=params.get(field,'')
        if value and field!=omit:
            terms.append(expression+'=?');args.append('' if value=='__empty__' else value)
    q=str(params.get('q','')).strip()
    if q:
        escaped='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        terms.append("(title LIKE ? OR id LIKE ? OR summary LIKE ? OR payload LIKE ?)");args.extend([escaped]*4)
    return ' AND '.join(terms),args

@router.get('/asset-catalog/tree')
def classification_tree(request:Request,kind:str='data',view:str='business'):
    params=dict(request.query_params)
    # Tree counts retain search and facet filters, not the selected tree branch.
    for key in ('domain','category','subcategory','authority','basis','version_key'):params.pop(key,None)
    where,args=predicate(kind,params)
    axes=['authority','basis','version_key'] if kind=='rule' and view=='source' else ['domain','category','subcategory'] if kind=='data' else ['domain','category']
    columns=','.join(FIELDS[f]+' AS '+f for f in axes)
    with closing(connect()) as c:
        rows=c.execute(f'SELECT {columns},count(*) n FROM entities WHERE {where} GROUP BY '+','.join(FIELDS[f] for f in axes)+' ORDER BY '+','.join(FIELDS[f] for f in axes),args).fetchall()
    roots=[];index={};total=0
    for row in rows:
        total+=row['n'];path={};parent=roots
        for depth,axis in enumerate(axes):
            raw=row[axis] or '';label=raw or ('待归类' if axis not in ('basis','authority') else '待补充依据')
            path={**path,axis:raw or '__empty__'};ident=hashlib.sha256(json.dumps([kind,view,path],ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]
            if ident not in index:
                index[ident]={'id':ident,'label':label,'axis':axis,'path':dict(path),'count':0,'children':[],'depth':depth};parent.append(index[ident])
            node=index[ident];node['count']+=row['n'];parent=node['children']
    return {'kind':kind,'view':view,'axes':axes,'nodes':roots,'total':total,'node_count':len(index)}

@router.get('/asset-catalog/items')
def catalog_items(request:Request,kind:str='data',page:int=1,page_size:int=20):
    params=dict(request.query_params);where,args=predicate(kind,params);page=max(page,1);page_size=min(max(page_size,1),60)
    with closing(connect()) as c:
        total=c.execute('SELECT count(*) FROM entities WHERE '+where,args).fetchone()[0]
        items=[entity_json(r) for r in c.execute('SELECT * FROM entities WHERE '+where+' ORDER BY CASE status WHEN \'published\' THEN 0 ELSE 1 END,title,id LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])]
        facets={}
        for field in ('status','source_kind','asset_source','rule_type','algorithm_type','ownership','knowledge_form','origin_type'):
            fw,fa=predicate(kind,params,field)
            facets[field]={r['value'] or '__empty__':r['n'] for r in c.execute(f'SELECT {FIELDS[field]} value,count(*) n FROM entities WHERE {fw} GROUP BY {FIELDS[field]}',fa)}
        current=c.execute("SELECT count(*) FROM entities WHERE kind=? AND "+CURRENT,(kind,)).fetchone()[0]
        historical=c.execute("SELECT count(*) FROM entities WHERE kind=? AND NOT ("+CURRENT+')',(kind,)).fetchone()[0]
    return {'items':items,'total':total,'page':page,'page_size':page_size,'facets':facets,'current_total':current,'historical_total':historical}

@router.get('/knowledge/collections')
def knowledge_collections(scope:str='topic',q:str='',page:int=1,page_size:int=24):
    terms=["kind='collection'","json_extract(payload,'$.collection_type')='knowledge'"];args=[]
    if scope:terms.append("json_extract(payload,'$.collection_scope')=?");args.append(scope)
    if q:terms.append('(title LIKE ? OR summary LIKE ?)');args.extend(['%'+q+'%']*2)
    where=' AND '.join(terms);page=max(page,1);page_size=max(1,min(page_size,60))
    with closing(connect()) as c:
        total=c.execute('SELECT count(*) FROM entities WHERE '+where,args).fetchone()[0]
        items=[entity_json(r) for r in c.execute('SELECT * FROM entities WHERE '+where+' ORDER BY title,id LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])]
        for item in items:
            children=[collected_article(c,r) for r in c.execute("SELECT e.*,r.payload membership_payload FROM relations r JOIN entities e ON e.id=r.target WHERE r.source=? AND r.type='includes_knowledge' AND r.status!='rejected' AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0 ORDER BY e.title",(item['id'],))]
            item['article_count']=len(children);item['published_count']=sum(x['status']=='published' for x in children);item['preview']=[{k:x[k] for k in ('id','title','status','summary','membership')} for x in children[:3]]
    return {'items':items,'total':total,'page':page,'page_size':page_size}

@router.get('/knowledge/collections/{cid}')
def collection_detail(cid:str,state:str='published'):
    with closing(connect()) as c:
        item=entity_json(c.execute("SELECT * FROM entities WHERE id=? AND kind='collection'",(cid,)).fetchone())
        if not item:raise HTTPException(404,'未找到知识专栏。')
        rows=c.execute("SELECT e.*,r.id membership_id,r.payload membership_payload FROM relations r JOIN entities e ON e.id=r.target WHERE r.source=? AND r.type='includes_knowledge' AND r.status!='rejected' AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0 ORDER BY e.title",(cid,)).fetchall()
        all_items=[collected_article(c,row) for row in rows]
        item['counts']={'all':len(all_items),'published':sum(x['status']=='published' for x in all_items),'working':sum(x['status']!='published' for x in all_items)}
        item['items']=[x for x in all_items if state=='all' or (state=='published' and x['status']=='published') or (state=='working' and x['status']!='published')]
        item['source_projects']=[dict(r) for r in c.execute("SELECT DISTINCT e.id,e.title FROM entities e JOIN relations r ON (r.source=e.id AND r.target=?) OR (r.target=e.id AND r.source=?) WHERE e.kind='project'",(cid,cid))]
        if item['payload'].get('project_id'):
            p=c.execute("SELECT id,title FROM entities WHERE id=? AND kind='project'",(item['payload']['project_id'],)).fetchone()
            if p and not any(x['id']==p['id'] for x in item['source_projects']):item['source_projects'].append(dict(p))
    return item

@router.get('/knowledge/memberships/{aid}')
def knowledge_memberships(aid:str):
    with closing(connect()) as c:
        return {'items':[dict(r) for r in c.execute("SELECT e.id,e.title,e.version,e.status FROM entities e JOIN relations r ON r.source=e.id WHERE r.target=? AND r.type='includes_knowledge' AND r.status!='rejected' AND e.kind='collection' ORDER BY e.title",(aid,))]}

@router.post('/knowledge/collections')
async def create_collection(request:Request):
    b=await request.json();title=str(b.get('title','')).strip();scope=b.get('scope','topic')
    if not title or len(title)>80:raise HTTPException(422,'请填写80字以内的专栏名称。')
    if scope not in ('topic','project'):raise HTTPException(422,'请选择议题专栏或专题成果。')
    cid='COL-K-USER-'+uuid.uuid4().hex[:10];stamp=now();payload={'collection_type':'knowledge','collection_scope':scope,'intro':str(b.get('intro',''))[:5000],'project_id':b.get('project_id','')}
    with closing(connect()) as c,c:
        if scope=='project' and not c.execute("SELECT 1 FROM entities WHERE id=? AND kind='project'",(payload['project_id'],)).fetchone():raise HTTPException(422,'请指定已存在的专题。')
        c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(cid,'collection',title,payload['intro'],b.get('domain','综合管理'),None,'published','user',json.dumps(payload,ensure_ascii=False),stamp,stamp,1));log(c,cid,'创建知识专栏',title)
    return collection_detail(cid)

@router.post('/knowledge/collections/{cid}/members')
async def manage_member(cid:str,request:Request):
    b=await request.json();aid=str(b.get('asset_id',''));action=b.get('action','add');reason=str(b.get('reason','')).strip()
    if action not in ('add','remove') or not reason:raise HTTPException(422,'请填写收录或移除的理由。')
    with closing(connect()) as c,c:
        collection=entity_json(c.execute("SELECT * FROM entities WHERE id=? AND kind='collection'",(cid,)).fetchone())
        asset=entity_json(c.execute("SELECT * FROM entities WHERE id=? AND kind='knowledge' AND "+CURRENT,(aid,)).fetchone())
        if not collection or not asset:raise HTTPException(404,'专栏或当前知识条目不存在。')
        old=c.execute("SELECT * FROM relations WHERE source=? AND target=? AND type='includes_knowledge'",(cid,aid)).fetchone()
        if action=='remove' and not old:raise HTTPException(404,'这篇知识尚未收录。')
        new_status='confirmed' if action=='add' else 'rejected';stamp=now()
        if old and old['status']==new_status and json.loads(old['payload'] or '{}').get('adopted_version',asset['version'])==asset['version']:return {'ok':True,'unchanged':True}
        c.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',(cid,collection['version'],json.dumps(collection,ensure_ascii=False),stamp,reason))
        relation_payload={'reason':reason,'adopted_version':asset['version'],'at':stamp}
        if old:
            prev=json.loads(old['payload'] or '{}');relation_payload['history']=[*prev.get('history',[]),{'status':old['status'],'reason':prev.get('reason',''),'version':prev.get('adopted_version')}]
            c.execute('UPDATE relations SET status=?,payload=? WHERE id=?',(new_status,json.dumps(relation_payload,ensure_ascii=False),old['id']))
        else:c.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',('rel-col-'+uuid.uuid4().hex,cid,aid,'includes_knowledge','收录知识',new_status,json.dumps(relation_payload,ensure_ascii=False)))
        c.execute('UPDATE entities SET version=version+1,updated_at=? WHERE id=?',(stamp,cid));log(c,cid,'收录知识' if action=='add' else '移除专栏收录',asset['title']+'；'+reason)
    return {'ok':True}

@router.get('/navigation/context')
def navigation_context(entity_id:str):
    with closing(connect()) as c:
        e=entity_json(c.execute('SELECT * FROM entities WHERE id=?',(entity_id,)).fetchone())
        if not e:return {'parents':[]}
        found={}
        for field,kind,label in [('task_id','task','所属任务'),('matter_id','matter','所属事项'),('project_id','project','所属专题'),('case_id','case','关联案例')]:
            ident=e['payload'].get(field)
            if ident:
                row=c.execute('SELECT id,title,kind FROM entities WHERE id=?',(ident,)).fetchone()
                if row:found[ident]={**dict(row),'role_label':label}
        rows=c.execute("SELECT DISTINCT e.id,e.title,e.kind FROM relations r JOIN entities e ON e.id=r.source WHERE r.target=? AND r.status!='rejected' AND e.kind IN ('project','task','matter','collection') LIMIT 20",(entity_id,)).fetchall()
        for row in rows:
            found.setdefault(row['id'],{**dict(row),'role_label':{'project':'所属专题','task':'关联任务','matter':'关联事项','collection':'收录专栏'}[row['kind']]})
        rows=c.execute("SELECT DISTINCT e.id,e.title,e.kind FROM relations r JOIN entities e ON e.id=r.target WHERE r.source=? AND r.type='belongs_to' AND r.status!='rejected' AND e.kind IN ('project','task','matter','collection') LIMIT 20",(entity_id,)).fetchall()
        for row in rows:found.setdefault(row['id'],{**dict(row),'role_label':{'project':'所属专题','task':'关联任务','matter':'关联事项','collection':'收录专栏'}[row['kind']]})
    return {'entity_id':entity_id,'parents':list(found.values())}
