"""Controlled DEMO experience regression tests; no model or production writes.
Run with MYSQL_* test-server settings: python scripts/check_experience.py
Creates a unique temporary MySQL database and drops it after the run.
All language-model responses are explicit fixtures, not online AI validation.
"""
import asyncio,json,os,sys,tempfile,shutil,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK_CONTEXT=tempfile.TemporaryDirectory(prefix='ontology-experience-')
WORK=Path(WORK_CONTEXT.name)
shutil.copytree(ROOT/'backend',WORK/'backend',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
sys.path.insert(0,str(WORK))
test_database = 'ontology_exp_qa_' + uuid.uuid4().hex[:16]
from fastapi import FastAPI
from backend import experience as ex,materials as materials,db
from backend.provenance import get_source_version
import httpx,pymysql
settings=db.mysql_config()
os.environ.pop('ONTOLOGY_MYSQL_URL', None)
for key in ('host', 'port', 'user', 'password'):
 os.environ['MYSQL_' + key.upper()] = str(settings[key])
os.environ['MYSQL_DATABASE'] = test_database
settings['database'] = test_database
assert db.ROOT == WORK and materials.ROOT == WORK, 'Test files must remain isolated'
app=FastAPI();app.include_router(ex.router,prefix='/api');app.include_router(materials.router,prefix='/api')
ex.config=lambda:{'api_key':'qa-controlled','model':'controlled-fixture'}
SOURCE=['专家甲谈项目复盘沟通：对方认为报告措辞过重，双方对记录所载事实本身并无争议。','处理这类分歧时，我们先列出共同确认的事实和各自解释，再讨论报告口径，避免把措辞争议混作事实争议。','会议于周一召开，参会人员按照会议通知到场。','专家乙补充：如原始事实本身尚有分歧，不能跳过补证环节直接商议措辞。']
state={'mode':'normal','calls':0,'bad_once':False}
def candidate(blocks,title='沟通分歧先辨事实再议口径',indices=(0,1),key='沟通分歧/先确认事实后讨论口径'):
 refs=[{'chunk_id':blocks[i]['id'],'quote':blocks[i]['content'],'role':'context' if i==0 else 'support'} for i in indices]
 return {'title':title,'summary':'先确认事实分歧和表述分歧属于不同层面，再确定沟通与补证的次序。','experience_key':key,'source_refs':refs,'article_sections':[{'id':'clarify','title':'先把分歧说清楚','content':'讨论报告表述前，先分别列明已经确认的事实与不同解释。事实本身有争议时仍需补证。','source_refs':refs,'origin':'source_synthesis'}],'boundary':'','questions':''}
async def mock(messages,json_mode=False):
 body=json.loads(messages[-1]['content']);state['calls']+=1
 if 'conversation' in body:return json.dumps({'question':'你提到事实已无分歧，后来是怎样区分报告措辞与不同解释的？'},ensure_ascii=False)
 if 'repair_candidates' in body:
  return json.dumps({'candidates':[]})
 if 'operation' in body:
  blocks=body['source_blocks'];out=candidate(blocks,indices=tuple(range(min(2,len(blocks)))))
  if body['operation']=='split':return json.dumps({'candidates':[out,{**out,'title':'事实尚有争议时先补证','experience_key':'事实分歧/补证优先'}],'rationale':'区分报告沟通与事实补证两项独立认识。'},ensure_ascii=False)
  return json.dumps({'candidates':[{**out,'title':'先辨清事实与表述分歧'}],'rationale':'收束标题，保留原话能够支持的认识。'},ensure_ascii=False)
 blocks=body['source_blocks'];primary=body['primary_chunk_ids'];mode=state['mode']
 if mode=='none':return json.dumps({'candidates':[],'content_roles':[{'chunk_id':x['id'],'roles':['general_information']} for x in blocks],'outline':[],'no_experience_reason':'仅为会议时间和参会安排，未记载可独立复用的工作认识。','has_more':False},ensure_ascii=False)
 if mode=='partial' and any(x['content']==SOURCE[2] and x['id'] in primary for x in blocks):raise RuntimeError('controlled temporary connection failure')
 selected=[x for x in blocks if x['content'] in SOURCE[:2]]
 if len(selected)<2:selected=blocks[:2]
 out=candidate(selected,indices=tuple(range(len(selected))))
 if mode=='suggestion':out['article_sections'][0]['origin']='ai_suggestion'
 if mode=='cross':
  primary_block=next(x for x in blocks if x['id'] in primary)
  out=candidate([primary_block],title='报告沟通先对齐事实认识' if state['calls']%2 else '表述分歧与事实分歧分开处理',indices=(0,))
  if body['prior_experience_points']:out['continuation_of']=body['prior_experience_points'][0]['id']
 if mode=='invalid':
  wrong={**out,'title':'缺少引文的另一个建议','source_refs':[{'chunk_id':blocks[0]['id'],'quote':'根本不存在的原始记录原话','role':'support'}]}
  outputs=[out,wrong]
 else:outputs=[out]
 return json.dumps({'candidates':outputs,'content_roles':[{'chunk_id':x['id'],'roles':['context' if x['content']==SOURCE[0] else 'experience_support']} for x in blocks],'outline':['区分事实、解释和表述，提高复盘沟通质量。'],'has_more':False},ensure_ascii=False)
ex.complete=mock
async def main():
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
  async def imported(lines=SOURCE,**context):
   r=await client.post('/api/experience/import',files={'file':('非核查类沟通经验.txt','\n'.join(lines).encode(),'text/plain')},data={'title':'复盘沟通经验原稿',**context});assert r.status_code==200,r.text;return r.json()['entity']['id']
  async def get(sid):return (await client.get('/api/experience/'+sid)).json()
  async def analyze(sid,retry=False):
   r=await client.post('/api/experience/'+sid+'/analyze',json={'retry_failed':retry});assert r.status_code==200,r.text;return await get(sid)
  sid=await imported();r=await analyze(sid);p=r['entity']['payload'];assert p['analysis']['status']=='completed',p['analysis'];assert len(p['knowledge_candidates'])==1
  c=p['knowledge_candidates'][0];assert c['source_refs'][0]['role']=='context';assert c['source_refs'][0]['anchor_id'];assert len(c['article_sections'])==1;assert '原文未说明' not in c['content'];assert p['analysis']['reading_manifest'][0]['status']=='completed';assert len(p['analysis']['citation_index'])==2
  # Background/context is legitimately quoted; no equality with semantic roles.
  assert p['analysis']['content_roles'][0]['roles']==['context']
  request={**c,'action':'adopt','expected_revision':c['revision'],'reason':'人工核对，作为工作沟通认识'}
  adopted=await client.post(f'/api/experience/{sid}/candidates/{c["id"]}/review',json=request);assert adopted.status_code==200,adopted.text;kid=adopted.json()['entity_id']
  repeated=await client.post(f'/api/experience/{sid}/candidates/{c["id"]}/review',json=request);assert repeated.status_code==200 and repeated.json()['already_adopted'],repeated.text
  conn=db.connect();asset=db.entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(kid,)).fetchone());oldref=asset['payload']['source_refs'][0];assert asset['payload']['knowledge_form']=='experience' and asset['payload']['active_asset'];conn.close()
  # Updating an adopted idea changes the SAME asset ID and creates a draft revision.
  r=await get(sid);current=r['entity']['payload']['knowledge_candidates'][0];current['article_sections'][0]['content']+=' 人工补充：还应记录双方对术语含义的理解。'
  revised=await client.post(f'/api/experience/{sid}/candidates/{c["id"]}/review',json={**current,'action':'adopt','expected_revision':current['revision'],'expected_asset_version':current['asset_version']});assert revised.status_code==200,revised.text;assert revised.json()['entity_id']==kid
  conn=db.connect();asset=db.entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(kid,)).fetchone());assert asset['version']==2;assert asset['payload']['article_sections'][0]['origin']=='human_input';assert conn.execute('SELECT COUNT(*) FROM entities WHERE id=?',(kid,)).fetchone()[0]==1;conn.close()
  # AI splitting is a proposal; original remains unchanged before explicit apply.
  r=await get(sid);cur=r['entity']['payload']['knowledge_candidates'][0]
  prop=await client.post(f'/api/experience/{sid}/proposals',json={'candidate_ids':[cur['id']],'operation':'split','instruction':'将报告沟通方法与原始事实补证原则分开，各自保留出处。','expected_revisions':{cur['id']:cur['revision']}});assert prop.status_code==200,prop.text
  assert len((await get(sid))['entity']['payload']['knowledge_candidates'])==1
  pid=prop.json()['proposal']['id'];accepted=await client.post(f'/api/experience/{sid}/proposals/{pid}/decision',json={'action':'apply'});assert accepted.status_code==200,accepted.text
  r=await get(sid);assert len(r['entity']['payload']['knowledge_candidates'])==2;assert r['entity']['payload']['knowledge_candidates'][0]['adopted_entity_id']==kid
  # Merge is also previewed, preserves the adopted ID, and old proposals cannot overwrite manual edits.
  items=r['entity']['payload']['knowledge_candidates']
  prop=await client.post(f'/api/experience/{sid}/proposals',json={'candidate_ids':[x['id'] for x in items],'operation':'merge','instruction':'合成同一条沟通经验，保持不同情形的适用条件。'});assert prop.status_code==200,prop.text
  before=await get(sid);assert len([x for x in before['entity']['payload']['knowledge_candidates'] if x['status']!='merged'])==2
  merged=await client.post(f"/api/experience/{sid}/proposals/{prop.json()['proposal']['id']}/decision",json={'action':'apply'});assert merged.status_code==200,merged.text
  r=await get(sid);live=[x for x in r['entity']['payload']['knowledge_candidates'] if x['status']!='merged'];assert len(live)==1 and live[0]['adopted_entity_id']==kid
  old=await client.post(f'/api/experience/{sid}/proposals',json={'candidate_ids':[live[0]['id']],'operation':'revise','instruction':'保留原始条件，收束标题表达。'});assert old.status_code==200,old.text
  manual={**live[0],'title':'人工修订后的沟通认识','action':'save','expected_revision':live[0]['revision']}
  saved=await client.post(f"/api/experience/{sid}/candidates/{live[0]['id']}/review",json=manual);assert saved.status_code==200,saved.text
  blocked=await client.post(f"/api/experience/{sid}/proposals/{old.json()['proposal']['id']}/decision",json={'action':'apply'});assert blocked.status_code==409,blocked.text
  r=await get(sid);assert r['entity']['payload']['knowledge_candidates'][0]['title']=='人工修订后的沟通认识'
  # Appending raw words creates a source revision while old evidence resolves unchanged.
  turn=await client.post(f'/api/experience/{sid}/turns',json={'content':'提供者继续补充：这些认识适用于沟通分歧，不能代替尚缺原始材料时的核查。','expected_version':r['entity']['version']});assert turn.status_code==200,turn.text
  conn=db.connect();old=get_source_version(conn,sid,oldref['source_version']);assert old['text'][oldref['span']['start']:oldref['span']['end']]==oldref['quote'];conn.close()
  # Non-experiential material returns a successful zero result without template filler.
  state['mode']='none';none=await imported([SOURCE[2]]);r=await analyze(none);assert r['entity']['payload']['analysis']['status']=='completed';assert not r['entity']['payload']['knowledge_candidates']
  # Extra AI suggestions cannot be adopted as source facts before explicit human confirmation.
  state['mode']='suggestion';suggestion=await imported(SOURCE[:2]);r=await analyze(suggestion);idea=r['entity']['payload']['knowledge_candidates'][0]
  rejected=await client.post(f"/api/experience/{suggestion}/candidates/{idea['id']}/review",json={**idea,'action':'adopt','expected_revision':idea['revision']});assert rejected.status_code==422,rejected.text
  idea['article_sections'][0]['confirmed_suggestion']=True
  confirmed=await client.post(f"/api/experience/{suggestion}/candidates/{idea['id']}/review",json={**idea,'action':'adopt','expected_revision':idea['revision']});assert confirmed.status_code==200,confirmed.text
  conn=db.connect();asset=db.entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(confirmed.json()['entity_id'],)).fetchone());assert asset['payload']['article_sections'][0]['origin']=='human_input';conn.close()
  # Across batches, different titles and different quotes still combine one underlying insight.
  original_batches=ex.analysis_batches;ex.analysis_batches=lambda chunks:[[ch] for ch in chunks]
  state['mode']='cross';cross=await imported(SOURCE[:2]);r=await analyze(cross);p=r['entity']['payload'];assert p['analysis']['progress']==2;assert len(p['knowledge_candidates'])==1,p['knowledge_candidates'];assert len(p['knowledge_candidates'][0]['source_refs'])==2
  # One failed batch preserves valid preceding/following discoveries and resumes only failures.
  state['mode']='partial';partial=await imported(SOURCE[:3]);r=await analyze(partial);p=r['entity']['payload'];assert p['analysis']['status']=='partial';assert p['knowledge_candidates'];attempts=[b['attempts'] for b in p['analysis']['reading_manifest']];assert attempts==[1,1,1]
  state['mode']='cross';r=await analyze(partial,True);assert r['entity']['payload']['analysis']['status']=='completed';assert [b['attempts'] for b in r['entity']['payload']['analysis']['reading_manifest']]==[1,1,2]
  # A fabricated quote is quarantined while valid experience remains reviewable.
  ex.analysis_batches=original_batches;state['mode']='invalid';invalid=await imported(SOURCE[:2]);r=await analyze(invalid);p=r['entity']['payload'];assert p['analysis']['status']=='partial';assert len(p['knowledge_candidates'])==1;assert p['analysis']['reading_manifest'][0]['issue_count']==1
  # Late source changes cannot inject old discoveries into the current session.
  state['mode']='normal';stale=await imported(SOURCE[:2]);normal=ex.complete
  async def changing(messages,json_mode=False):
   result=await normal(messages,json_mode)
   with db.connect() as conn:
    item=ex.session(conn,stale);ex.append_narrative(conn,item,'free','处理时提供者新增说明，补充了原来记录缺少的条件。');ex.save_session(conn,item,'隔离测试：分析期间新增原话')
   return result
  ex.complete=changing;r=await analyze(stale);assert r['entity']['payload']['analysis']['status']=='stale';assert not r['entity']['payload']['knowledge_candidates'];ex.complete=normal
  # True paging, not LIMIT-based fake total.
  listing=(await client.get('/api/experience/sessions?page=1&page_size=2&q=复盘沟通')).json();assert listing['total']>=6 and len(listing['items'])==2
  page2=(await client.get('/api/experience/sessions?page=2&page_size=2&q=复盘沟通')).json();assert not ({x['id'] for x in listing['items']} & {x['id'] for x in page2['items']})
  # Source extraction keeps TXT indentation and line breaks; appends never normalize the original prefix.
  exact='  专家记录开头保留两个空格。\r\n\r\n\t对齐事实之后，再沟通报告措辞。  \r\n'
  uploaded=await client.post('/api/experience/import',files={'file':('空白位置原稿.txt',exact.encode(),'text/plain')});assert uploaded.status_code==200,uploaded.text
  exact_id=uploaded.json()['entity']['id']
  with db.connect() as conn:
   first=get_source_version(conn,exact_id);assert first['text']==exact
  more=await client.post(f'/api/experience/{exact_id}/turns',json={'content':'追加说明应保留原稿原有缩进和全部换行。','mode':'structured'});assert more.status_code==200,more.text
  with db.connect() as conn:
   latest=get_source_version(conn,exact_id);assert latest['text'].startswith(exact) and latest['id']!=first['id']
   assert get_source_version(conn,exact_id,first['id'])['text']==exact
  # Scoped experience material and its knowledge share project/task/matter IDs, without copied knowledge.
  pid,tid,mid='QA-EXP-PROJECT','QA-EXP-TASK','QA-EXP-MATTER'
  with db.connect() as conn:
   for eid,kind,payload in [(pid,'project',{}),(tid,'task',{'project_id':pid}),(mid,'matter',{'project_id':pid,'review_state':'confirmed'})]:
    conn.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(eid,kind,'隔离经验关联测试','仅用于受控回归','知识管理','','active','demo',json.dumps(payload,ensure_ascii=False),db.now(),db.now(),1))
  state['mode']='normal';scoped=await imported(SOURCE[:2],task_id=tid,matter_id=mid)
  with db.connect() as conn:
   entity=ex.session(conn,scoped);assert entity['payload']['project_id']==pid
   assert conn.execute('SELECT COUNT(*) FROM relations WHERE source=? AND target=?',(mid,scoped)).fetchone()[0]==1
   matter=db.entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(mid,)).fetchone());matter['payload']['review_state']='confirmed';conn.execute('UPDATE entities SET payload=? WHERE id=?',(json.dumps(matter['payload']),mid));version=matter['version']
  more=await client.post(f'/api/experience/{scoped}/turns',json={'content':'专家补充：应先形成共同事实清单，然后沟通报告文字。','mode':'structured'});assert more.status_code==200,more.text
  with db.connect() as conn:
   matter=db.entity_json(conn.execute('SELECT * FROM entities WHERE id=?',(mid,)).fetchone());assert matter['payload']['review_state']=='pending' and matter['version']==version+1
   assert conn.execute('SELECT COUNT(*) FROM relations WHERE source=? AND target=?',(mid,scoped)).fetchone()[0]==1
  r=await analyze(scoped);idea=r['entity']['payload']['knowledge_candidates'][0]
  adopted=await client.post(f"/api/experience/{scoped}/candidates/{idea['id']}/review",json={**idea,'action':'adopt','expected_revision':idea['revision']});assert adopted.status_code==200,adopted.text
  with db.connect() as conn:
   produced=adopted.json()['entity_id']
   assert {r['source'] for r in conn.execute('SELECT source FROM relations WHERE target=? AND type=?',(produced,'produces'))}=={pid,tid,mid}
   conn.execute('UPDATE entities SET status=? WHERE id=?',('review',tid))
  locked=await client.post(f'/api/experience/{scoped}/turns',json={'content':'这段原話在任务已经提交后应当被拒绝新增。','mode':'structured'});assert locked.status_code==409,locked.text

  (WORK/'results.json').write_text(json.dumps({'session_id':sid,'knowledge_id':kid,'cross':cross,'partial':partial,'invalid':invalid,'none':none,'stale':stale,'mock':True},ensure_ascii=False))
  print('DEMO API PASS: background citation, optional free sections, non-inspection insight, immutable exact source and appended-prefix preservation, same-ID knowledge revision, split/merge preview and stale proposal rejection, supplementary suggestion confirmation, repeat adoption idempotence, no experience, cross-batch consolidation, persistent partial batches and selective retry, fabricated-quote quarantine, late-source guard, true paging, matter re-review and locked task context. Controlled model fixtures only.')
created=False
try:
 with pymysql.connect(host=settings['host'],port=settings['port'],user=settings['user'],password=settings['password'],charset='utf8mb4') as server:
  with server.cursor() as cursor:cursor.execute(f"CREATE DATABASE `{settings['database']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
  created=True
 db.initialize()
 asyncio.run(main())
finally:
 try:
  if created:
   with pymysql.connect(host=settings['host'],port=settings['port'],user=settings['user'],password=settings['password'],charset='utf8mb4') as server:
    with server.cursor() as cursor:cursor.execute(f"DROP DATABASE `{settings['database']}`")
 finally:
  WORK_CONTEXT.cleanup()
