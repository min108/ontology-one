"""Ontology workbench: aggregate the whole scope, expand connected neighborhoods.

The graph uses a cached, compact read model. Hidden cards remain represented by
an expandable group; no classification page silently discards cross-type edges.
"""
from __future__ import annotations
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict, namedtuple
from contextlib import closing
from threading import RLock
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from .db import connect
from .relation_semantics import semantic_relation, classify_relation_role, ROLE_LABELS, ROLE_LANES, RELATION_SEMANTICS

router = APIRouter(tags=['本体图谱'])
KINDS = {'data':'数据资产','rule':'规则资产','algorithm':'算法资产','knowledge':'知识资产','material':'原始材料','dataset':'单位数据台账','organization':'组织单位','person':'任职人员','project':'本体专题','task':'工作任务','case':'案例档案','matter':'业务事项','evidence':'证据引用','issue':'核对事项','collection':'知识专栏','analysis':'分析记录'}
ASSETS = {'data','rule','algorithm','knowledge'}
EDGE = namedtuple('GraphRelation', 'id source target type label status')
_CACHE = {'signature':None,'at':0,'value':None};_LOCK=RLock()
ORDER={'material':0,'dataset':1,'evidence':2,'data':3,'rule':4,'algorithm':5,'knowledge':6,'collection':7,'project':8,'task':9,'matter':10,'case':11,'issue':12,'organization':13,'person':14}


def _snapshot():
    with _LOCK, closing(connect()) as db:
        sig=tuple(db.execute("SELECT count(*),coalesce(sum(version),0),coalesce(max(updated_at),'') FROM entities").fetchone())+tuple(db.execute("SELECT count(*),coalesce(max(id),'') FROM relations").fetchone())+(db.execute('SELECT coalesce(max(id),0) FROM activities').fetchone()[0],)
        if _CACHE['signature']==sig and time.monotonic()-_CACHE['at']<20:return _CACHE['value']
        nodes={}
        for row in db.execute("""SELECT id,kind,title,summary,domain,org_id,status,source_kind,version,updated_at,
          json_extract(payload,'$.category') category,json_extract(payload,'$.subcategory') subcategory,
          json_extract(payload,'$.topic_label') topic_label,json_extract(payload,'$.topic_id') topic_id,
          json_extract(payload,'$.active_asset') active_asset,json_extract(payload,'$.record_role') record_role,
          json_extract(payload,'$.merged_into') merged_into,json_extract(payload,'$.type') org_type,
          json_extract(payload,'$.group') org_group,json_extract(payload,'$.shared') shared,
          json_extract(payload,'$.is_demo') is_demo,json_extract(payload,'$.collection_scope') collection_scope
          FROM entities"""):
            d=dict(row);d['label']=d.pop('title');d['active']=d.get('active_asset')!=0 and d.get('record_role') not in ('historical_alias','procedure_reference')
            d['is_demo']=d.get('source_kind')=='demo' or bool(d.get('is_demo'));d['aggregate']=False
            for key in ('category','subcategory','topic_label','domain','org_id'):d[key]=str(d.get(key) or '')
            nodes[d['id']]=d
        # Use the same evidence-based confirmation rule as data import and
        # instance projection. Cache history reads per dataset/standard so a
        # large set of fields never repeats the same version query.
        from .datasets import mapping_confirmation_version, mapping_source
        class EvidenceCache:
            def __init__(self,conn):self.conn=conn;self.rows={}
            def execute(self,sql,params=()):
                key=(sql,tuple(params))
                if key not in self.rows:self.rows[key]=self.conn.execute(sql,params).fetchall()
                return iter(self.rows[key])
        evidence=EvidenceCache(db)
        current_mappings={};mapping_candidates=defaultdict(list)
        for row in db.execute('SELECT * FROM mappings'):
            value=dict(row)
            try:value['payload']=json.loads(value.get('payload') or '{}')
            except (TypeError,ValueError):value['payload']={}
            rid='REL-DS-MAP-'+hashlib.sha256((value['dataset_id']+'|'+value['column_name']).encode()).hexdigest()[:24]
            current_mappings[rid]=value
            mapping_candidates[(value['dataset_id'],value['standard_id'],mapping_source(value))].append(value)
        mapping_review={};mapping_confirmation={}
        for row in db.execute("SELECT id,source,target,payload,status FROM relations WHERE type='maps_to'"):
            if row['status']!='confirmed':continue
            target=nodes.get(row['target'],{})
            try:payload=json.loads(row['payload'] or '{}')
            except (TypeError,ValueError):payload={}
            mapping=current_mappings.get(row['id'])
            if mapping is None:
                matches=mapping_candidates.get((row['source'],row['target'],payload.get('source_column')),[])
                if payload.get('sheet_names'):
                    matches=[m for m in matches if sorted(m['payload'].get('sheet_names',[]))==sorted(payload['sheet_names'])]
                if len(matches)==1:mapping=matches[0]
            if not mapping or mapping['status']!='confirmed' or mapping['standard_id']!=row['target']:
                mapping_review[row['id']]='原字段已改配或尚未确认，此历史对应须重新核对'
                continue
            confirmed,basis=mapping_confirmation_version(evidence,mapping,target)
            mapping_confirmation[row['id']]={'resolved_confirmation_version':confirmed,'confirmation_basis':basis,'mapping_key':mapping['column_name']}
            if target.get('status')!='published' or not target.get('active') or confirmed is None or confirmed!=target.get('version'):
                mapping_review[row['id']]='字段对应须重新核对：'+(basis if confirmed is None else '标准版本或状态已变化')
        edges=[];adj=defaultdict(list)
        for row in db.execute('SELECT id,source,target,type,label,status FROM relations'):
            if row['source'] not in nodes or row['target'] not in nodes:continue
            edge=EDGE(row['id'],row['source'],row['target'],sys.intern(row['type'] or ''),sys.intern('字段对应待复核' if row['id'] in mapping_review else row['label'] or ''),sys.intern('candidate' if row['id'] in mapping_review else row['status'] or 'candidate'))
            edges.append(edge);adj[edge.source].append(edge)
            if edge.target!=edge.source:adj[edge.target].append(edge)
        value={'nodes':nodes,'edges':edges,'adj':adj,'signature':str(sig),'mapping_review':mapping_review,'mapping_confirmation':mapping_confirmation}
        _CACHE.update(signature=sig,value=value,at=time.monotonic());return value


def _active(node,history=False):return history or node['active']
def _matches(n,kind='',domain='',org_id='',category='',subcategory='',q='',topic=''):
    return (not kind or n['kind'] in kind.split(',')) and (not domain or n['domain']==('' if domain=='__unclassified__' else domain)) and (not org_id or n['org_id']==org_id) and (not category or n['category']==('' if category=='__unclassified__' else category)) and (not subcategory or n['subcategory']==('' if subcategory=='__unclassified__' else subcategory)) and (not topic or n.get('topic_id')==topic) and (not q or q.lower() in '\n'.join([n['id'],n['label'],n.get('summary') or '',n.get('topic_label') or '']).lower())
def _valid_edge(e,snap,history,status,typ):return (status=='all' and e.status!='rejected' or e.status==status) and (not typ or e.type==typ) and _active(snap['nodes'][e.source],history) and _active(snap['nodes'][e.target],history)
def _ownership_matches(n,scope,snap):
    if not scope:return True
    if scope=='shared':return _public(n)
    return n['kind']!='organization' and not _public(n) and (not n['org_id'] or n['org_id'] not in snap['nodes'])

def _public(n):return not n['org_id'] and (n['source_kind'] in ('imported','regulation') or n.get('shared')) and n['kind']!='organization'
def _digest(value):return hashlib.sha1(value.encode()).hexdigest()[:16]
def _public_node(n,snap=None,status='all',typ=''):
    result={k:v for k,v in n.items() if k not in ('shared','org_type','org_group','active_asset')}
    if snap:
        edges=[e for e in snap['adj'].get(n['id'],[]) if (status=='all' and e.status!='rejected' or e.status==status) and (not typ or e.type==typ)]
        result['incoming']=sum(e.target==n['id'] for e in edges);result['outgoing']=sum(e.source==n['id'] for e in edges)
    return result

def _group(eid,label,count,kind='group',**extra):return {'id':eid,'label':label,'count':count,'kind':kind,'aggregate':True,'summary':'','internal_edges':0,**extra}
def _base(snap,history=False):
    live=[n for n in snap['nodes'].values() if _active(n,history)]
    kinds=Counter(n['kind'] for n in live);domains=Counter(n['domain'] for n in live if n['domain'])
    return {'total_nodes':len(snap['nodes']),'total_edges':len(snap['edges']),'active_nodes':len(live),'business_objects':len(live)-kinds['organization'],'active_assets':sum(kinds[k] for k in ASSETS),'historical_nodes':len(snap['nodes'])-len(live),'facets':{'kinds':[{'value':k,'label':KINDS.get(k,k),'count':v} for k,v in sorted(kinds.items(),key=lambda x:ORDER.get(x[0],99))],'domains':[{'value':k,'count':v} for k,v in sorted(domains.items())],'relation_types':[{'value':k,'label':v[0]} for k,v in RELATION_SEMANTICS.items()]},'truncated':False,'edge_truncated':False}

def _bundle_edges(raw,mapping,snap,reading='business'):
    bundles={};internals=Counter();represented=0
    for e in raw:
        a=mapping.get(e.source);b=mapping.get(e.target)
        if not a or not b:continue
        represented+=1
        if a==b:internals[a]+=1;continue
        spec=RELATION_SEMANTICS.get(e.type,(e.label,'',False,'reference'));reverse=reading=='business' and spec[2] and bool(spec[1]);label=spec[1] if reverse else e.label or spec[0];family=spec[3]
        source,target=(b,a) if reverse else (a,b)
        key=(source,target)
        if key not in bundles:bundles[key]={'id':'bundle:'+_digest(source+'|'+target),'source':source,'target':target,'count':0,'aggregate':True,'status_counts':Counter(),'types':Counter(),'demo_count':0,'samples':[],'family_counts':Counter()}
        v=bundles[key];v['count']+=1;v['status_counts'][e.status]+=1;v['types'][(e.type,label,e.label or spec[0],reverse)]+=1;v['family_counts'][family]+=1
        v['demo_count']+=int(snap['nodes'][e.source]['is_demo'] or snap['nodes'][e.target]['is_demo'] or e.status=='demo')
        if len(v['samples'])<5:v['samples'].append(e.id)
    edges=[]
    for v in bundles.values():
        dominant=v['status_counts'].most_common(1)[0][0];v['status']=dominant if len(v['status_counts'])==1 else 'mixed'
        dominant_type=v['types'].most_common(1)[0][0]
        v['label']=dominant_type[1] if len(v['types'])==1 else '多类业务关联';v['family']=v['family_counts'].most_common(1)[0][0]
        v['breakdown']=[{'type':k[0],'label':k[1],'original_label':k[2],'display_reversed':k[3],'count':n} for k,n in v.pop('types').most_common()]
        v['status_counts']=dict(v['status_counts']);v['family_counts']=dict(v['family_counts']);edges.append(v)
    return edges,internals,represented

def _raw_edge(e,snap,reading='business'):
    a,b=snap['nodes'][e.source],snap['nodes'][e.target]
    return {**semantic_relation(e,a['kind'],b['kind'],reading),'aggregate':False,'count':1,'is_demo':a['is_demo'] or b['is_demo'] or e.status=='demo'}

def _finish(base,nodes,edges,scope_count,represented,view,**extra):
    return {**base,'view':view,'mode':'focus' if view=='focus' else 'overview','nodes':nodes,'edges':edges,'visible_nodes':len(nodes),'visible_edges':len(edges),'filtered_nodes':scope_count,'represented_nodes':scope_count,'represented_edges':represented,'filtered_edges':represented,**extra}

def _scope(snap,history,**filters):return [n for n in snap['nodes'].values() if _active(n,history) and _matches(n,**filters)]

def _classification(snap,base,scope,raw,view,reading,domain,kind,category,subcategory,detail,budget):
    scope_ids={n['id'] for n in scope};mapping={};nodes=[];context_members=defaultdict(set)
    if view=='panorama':dimension='kind'
    elif not domain:dimension='domain'
    elif not kind:dimension='kind'
    elif not category and any(n['category'] for n in scope):dimension='category'
    elif not subcategory and any(n['subcategory'] for n in scope):dimension='subcategory'
    else:dimension='objects'
    if detail:dimension='objects'
    if dimension!='objects':
        buckets=defaultdict(list)
        for n in scope:buckets[n.get(dimension) or '__unclassified__'].append(n)
        for value,items in sorted(buckets.items(),key=lambda pair:(ORDER.get(pair[0],99),pair[0])):
            nid='scope:'+dimension+':'+_digest(value)
            op={'view':'classification','domain':domain,'asset_kind':kind,'category':category,'subcategory':subcategory}
            if dimension=='kind':op['asset_kind']=value
            if dimension=='kind' and value=='organization':op={'view':'organization'}
            elif dimension=='domain':op['domain']=value
            elif dimension=='category':op['category']=value
            elif dimension=='subcategory':op['subcategory']=value
            label=KINDS.get(value,value) if dimension=='kind' else '未分类' if value=='__unclassified__' else value
            group_kind=value if dimension=='kind' else kind or 'group'
            node=_group(nid,label,len(items),group_kind,group_dimension=dimension,group_value=value,open_params=op,lane='assets',domain=domain)
            if view=='panorama':node['lane']='sources' if value in ('material','dataset','evidence') else 'assets' if value in ASSETS else 'organization' if value in ('organization','collection','person') else 'work'
            nodes.append(node)
            for n in items:mapping[n['id']]=nid
    else:
        # Keep actual cards and a truthful continuation node; off-page relations
        # attach to that node instead of vanishing.
        ordered=sorted(scope,key=lambda n:(n['label'],n['id']));chosen=ordered[:budget]
        for n in chosen:mapping[n['id']]=n['id'];nodes.append({**_public_node(n,snap),'lane':'assets'})
        if len(ordered)>len(chosen):
            nid='fold:scope';nodes.append(_group(nid,'其余资产',len(ordered)-len(chosen),kind or 'group',lane='assets',list_query={'scope':'selection'}))
            for n in ordered[len(chosen):]:mapping[n['id']]=nid
    touching=[]
    for e in raw:
        if e.source not in scope_ids and e.target not in scope_ids:continue
        touching.append(e)
        for eid in (e.source,e.target):
            if eid not in scope_ids:
                other=snap['nodes'][eid];nid='context:'+other['kind'];mapping[eid]=nid;context_members[other['kind']].add(eid)
    for k,members in sorted(context_members.items(),key=lambda pair:ORDER.get(pair[0],99)):
        lane='sources' if k in ('material','dataset','evidence') else 'work' if k in ('project','task','matter','case','issue') else 'organization' if k in ('organization','collection','person') else 'context'
        nodes.append(_group('context:'+k,'关联'+KINDS.get(k,k),len(members),k,is_context=True,lane=lane,list_query={'scope':'context','context_kind':k}))
    edges,internal,represented=_bundle_edges(touching,mapping,snap,reading)
    for n in nodes:n['internal_edges']=internal[n['id']]
    label='全局全景' if view=='panorama' else ' / '.join(filter(None,['未分类' if v=='__unclassified__' else v for v in [domain,KINDS.get(kind,kind),category,subcategory]])) or '业务分类全景'
    return _finish(base,nodes,edges,len(scope),represented,view,dimension=dimension,title=label,scope_note='每个分组代表全部符合条件的对象；周边分组保留跨类型联系。目录容器不是新的业务资产。',context_nodes=sum(len(v) for v in context_members.values()),folded_nodes=max(0,len(scope)-budget) if dimension=='objects' else len(scope),scope_ids_count=len(scope),list_available=True)

def _org_family(n):return 'stationed' if n.get('org_type')=='区域机构' else 'subsidiaries' if n.get('org_group')=='一级子公司' else 'headquarters'
ORG_NAMES={'headquarters':'总部职能机构','stationed':'区域机构','subsidiaries':'一级子公司','shared':'公共规范与共享资产','unassigned':'归属待完善'}

def _organization(snap,base,live,raw,reading,org_group,org_id):
    organizations={n['id']:n for n in live if n['kind']=='organization'};business=[n for n in live if n['kind']!='organization'];by_org=defaultdict(list)
    for n in business:by_org[n['org_id']].append(n)
    if org_id:
        org=organizations.get(org_id)
        if not org:raise HTTPException(404,'未找到该单位。')
        sets={'owned':{n['id'] for n in by_org[org_id]},'provided':set(),'collaboration':set()}
        for e in raw:
            if org_id not in (e.source,e.target):continue
            other=e.target if e.source==org_id else e.source
            if e.type in ('collaborates_with','coordinated_by'):sets['collaboration'].add(other)
            if e.type=='owned_by':sets['provided'].add(other)
        nodes=[{**_public_node(org,snap),'lane':'center','root':True}];edges=[]
        for key,ids in sets.items():
            nid='org-role:'+key;label={'owned':'归口业务对象','provided':'提供的单位台账','collaboration':'专题归口与协作'}[key]
            nodes.append(_group(nid,label,len(ids),'group',lane='right',list_query={'scope':'org_role','org_id':org_id,'org_role':key}))
            if ids:edges.append({'id':'directory:'+key,'source':org_id,'target':nid,'label':label,'type':'directory','status':'directory','count':len(ids),'aggregate':True,'directory':True})
        return _finish(base,nodes,edges,len(set.union(*sets.values())),sum(len(v) for v in sets.values()),'organization',title=org['label']+' · 共建范围',scope_note='归口、台账提供、专题协作分别统计，三者可交叉，不直接相加。组织记录本身不计业务成果。',org_roles={k:len(v) for k,v in sets.items()},list_available=True)
    mapping={};nodes=[];groups=defaultdict(list)
    for n in organizations.values():groups[_org_family(n)].append(n)
    if org_group in ('headquarters','stationed','subsidiaries'):
        for org in sorted(groups[org_group],key=lambda n:n['id']):
            members=by_org[org['id']];nodes.append({**_public_node(org,snap),'aggregate':True,'count':len(members),'directory_organization':True,'lane':org_group,'open_params':{'view':'organization','org_id':org['id']},'summary':'已登记单位，尚无归属业务对象' if not members else f'{len(members):,} 项归口业务对象'})
            mapping[org['id']]=org['id']
            for n in members:mapping[n['id']]=org['id']
        included=set(mapping)
        for e in raw:
            if e.source in included or e.target in included:
                for eid in (e.source,e.target):
                    if eid not in mapping:
                        other=snap['nodes'][eid];org=organizations.get(other['org_id']);bucket='shared' if _public(other) else _org_family(org) if org else 'unassigned';mapping[eid]='org-family:'+bucket
        for bucket in sorted({v for v in mapping.values() if v.startswith('org-family:')}):
            key=bucket.split(':',1)[1];nodes.append(_group(bucket,ORG_NAMES[key],len({eid for eid,value in mapping.items() if value==bucket and snap['nodes'][eid]['kind']!='organization'}),lane='context',open_params={'view':'organization','org_group':key}))
    else:
        for family in ('headquarters','stationed','subsidiaries'):
            orgs=groups[family];count=sum(len(by_org[o['id']]) for o in orgs)
            nodes.append(_group('org-family:'+family,ORG_NAMES[family],count,'organization',directory_count=len(orgs),lane=family,open_params={'view':'organization','org_group':family},summary=f'{len(orgs)} 家（个）名录机构 · {count:,} 项归口对象'))
            for o in orgs:
                mapping[o['id']]='org-family:'+family
                for n in by_org[o['id']]:mapping[n['id']]='org-family:'+family
        for key,test in [('shared',lambda n:_public(n)),('unassigned',lambda n:not n['org_id'] and not _public(n) or n['org_id'] not in organizations and bool(n['org_id']))]:
            items=[n for n in business if test(n)];nodes.append(_group('org-family:'+key,ORG_NAMES[key],len(items),'group',lane=key,open_params={'view':'organization','org_group':key},list_query={'scope':'org_family','org_group':key}))
            for n in items:mapping[n['id']]='org-family:'+key
    if org_group in ('shared','unassigned'):
        ids={eid for eid,b in mapping.items() if b=='org-family:'+org_group};scope=[snap['nodes'][eid] for eid in ids if snap['nodes'][eid]['kind']!='organization']
        result=_classification(snap,base,scope,raw,'panorama',reading,'','','','',False,80);result.update(view='organization',title=ORG_NAMES[org_group],dimension='kind')
        for n in result['nodes']:
            if n.get('open_params'):n['open_params']['ownership_scope']=org_group
            if n.get('list_query'):n['list_query']['ownership_scope']=org_group
        return result
    edges,internal,represented=_bundle_edges(raw,mapping,snap,reading)
    for n in nodes:n['internal_edges']=internal[n['id']]
    return _finish(base,nodes,edges,sum(len(by_org[o['id']]) for o in groups.get(org_group,[])) if org_group else len(business),represented,'organization',title=ORG_NAMES.get(org_group,'集团组织与共建范围'),scope_note='按提供的组织名录展示：总部职能机构、区域机构与一级子公司分区；不推断法律隶属或股权关系。目录容器不是业务关系。',org_totals={k:len(v) for k,v in groups.items()},business_count=len(business),list_available=True)

def _neighbors(snap,node,raw,role_filter=''):
    grouped=defaultdict(dict);edge_groups=defaultdict(list);assignments={};incident=[]
    rank={'origins':0,'inputs':1,'evidence':2,'outputs':3,'usage':4,'interpretation':5,'reference':6,'organization':7,'curation':8,'history':9}
    for e in raw:
        if node not in (e.source,e.target):continue
        other=e.target if e.source==node else e.source
        role=classify_relation_role(e,node,snap['nodes'][e.source]['kind'],snap['nodes'][e.target]['kind'])
        if role_filter and role!=role_filter:continue
        key=role+':'+snap['nodes'][other]['kind'];incident.append((other,e))
        if other not in assignments or rank.get(role,99)<rank.get(assignments[other].split(':')[0],99):assignments[other]=key
    for other,key in assignments.items():grouped[key][other]=snap['nodes'][other]
    for other,e in incident:edge_groups[assignments[other]].append(e)
    return grouped,edge_groups

def _focus(snap,base,node,raw,reading,budget,expand,role,depth):
    root=snap['nodes'].get(node)
    if not root:raise HTTPException(404,'未找到该对象。')
    grouped,edge_groups=_neighbors(snap,node,raw,role);nodes=[{**_public_node(root,snap),'root':True,'lane':'center'}];mapping={node:node};groups=[];remaining=budget-1;expanded=set(filter(None,expand.split(',')));hidden=0
    # Small connected neighborhoods open completely; large ones keep readable
    # representative cards per semantic role and an exact continuation count.
    total_neighbors=len(set(eid for members in grouped.values() for eid in members));loaded={node}
    for key,members in sorted(grouped.items(),key=lambda item:({'origins':0,'inputs':1,'evidence':2,'outputs':3,'usage':4,'interpretation':5,'reference':6,'organization':7,'curation':8,'history':9}.get(item[0].split(':')[0],10),item[0])):
        r,k=key.split(':',1);items=sorted(members.values(),key=lambda n:(n['label'],n['id']));preview=len(items) if key in expanded else 0 if len(items)>2 or r=='curation' and len(items)>1 else len(items) if total_neighbors<=18 else 1;allow=min(remaining,preview)
        chosen=items[:allow];remaining-=allow
        for n in chosen:
            if n['id'] not in loaded:nodes.append({**_public_node(n,snap),'lane':ROLE_LANES[r],'role':r,'depth':1});loaded.add(n['id'])
            mapping[n['id']]=n['id']
        unshown=[n for n in items if n['id'] not in loaded]
        if unshown:
            gid='neighbors:'+key;nodes.append(_group(gid,ROLE_LABELS[r]+' · '+KINDS.get(k,k),len(unshown),k,lane=ROLE_LANES[r],role=r,expand_key=key,list_query={'scope':'neighbors','node':node,'bucket':key},total_count=len(items),expanded=key in expanded))
            for n in unshown:mapping.setdefault(n['id'],gid)
            hidden+=len(unshown)
        groups.append({'key':key,'role':r,'label':ROLE_LABELS[r]+' · '+KINDS.get(k,k),'count':len(items),'shown':len(chosen),'relation_count':len(edge_groups[key])})
    # Depth is optional and bounded. Extra-hop cards remain tagged as indirect.
    if depth>1 and remaining>0:
        frontier=set(loaded)-{node}
        for level in range(2,depth+1):
            more=set()
            for e in raw:
                if e.source in frontier and e.target not in mapping:more.add(e.target)
                if e.target in frontier and e.source not in mapping:more.add(e.source)
            selected=sorted(more,key=lambda eid:(ORDER.get(snap['nodes'][eid]['kind'],99),eid))[:remaining]
            for eid in selected:nodes.append({**_public_node(snap['nodes'][eid],snap),'lane':'lower','role':'reference','depth':level});mapping[eid]=eid;loaded.add(eid)
            remaining-=len(selected);frontier=set(selected)
            if not frontier or remaining<=0:break
    touching=[e for e in raw if e.source in mapping and e.target in mapping]
    edges=[];folded=[]
    for e in touching:
        if mapping[e.source]==e.source and mapping[e.target]==e.target:edges.append(_raw_edge(e,snap,reading))
        else:folded.append(e)
    bundled,internal,represented=_bundle_edges(folded,mapping,snap,reading);edges+=bundled
    return _finish(base,nodes,edges,len(mapping),len(touching),'focus',root=node,title=root['label'],neighbor_groups=groups,folded_nodes=hidden,loaded_objects=len(loaded),depth=depth,scope_note='按来源、依据、成果、运用与归属分区阅读。业务读法可使用合法反向谓词，原始端点及关系保留在详情中。未展开对象仍由分组计数代表。',list_available=True,canonical_target=root.get('merged_into'),historical_root=not root['active'])

@router.get('/graph/search')
def search_graph(q:str=Query('',max_length=200),kind:str='',domain:str='',org_id:str='',include_history:bool=False,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    snap=_snapshot();matches=_scope(snap,include_history,kind=kind,domain=domain,org_id=org_id,q=q)
    matches.sort(key=lambda n:(n['id']!=q,n['label'],n['id']));start=(page-1)*page_size
    return {'items':[_public_node(n,snap) for n in matches[start:start+page_size]],'total':len(matches),'page':page,'page_size':page_size}

@router.get('/graph/edge/{relation_id}')
def relation_detail(relation_id:str,reading:Literal['business','original']='business'):
    snap=_snapshot()
    with closing(connect()) as c:row=c.execute('SELECT * FROM relations WHERE id=?',(relation_id,)).fetchone()
    if not row:raise HTTPException(404,'未找到关系记录。')
    d=dict(row)
    try:p=json.loads(d.pop('payload') or '{}')
    except (TypeError,ValueError):p={}
    a=snap['nodes'].get(d['source'],{});b=snap['nodes'].get(d['target'],{})
    p={**p,**snap.get('mapping_confirmation',{}).get(relation_id,{})}
    if relation_id in snap.get('mapping_review',{}):
        p={**p,'stored_status':d['status'],'review_reason':snap['mapping_review'][relation_id]};d={**d,'status':'candidate','label':'字段对应待复核'}
    return {**semantic_relation(d,a.get('kind',''),b.get('kind',''),reading),'source_entity':_public_node(a) if a else None,'target_entity':_public_node(b) if b else None,'payload':p,'is_demo':a.get('is_demo') or b.get('is_demo') or d['status']=='demo'}

@router.get('/graph')
def graph(view:str='',mode:str='overview',group:str='kind',group_value:str='',node:str='',domain:str='',kind:str='',asset_kind:str='',category:str='',subcategory:str='',org_id:str='',org_group:str='',ownership_scope:str='',topic:str='',q:str='',relation_status:str='all',relation_type:str='',reading:Literal['business','original']='business',include_history:bool=False,budget:int=Query(80,ge=24,le=160),limit:int=Query(80,ge=10,le=200),expand:str='',role:str='',detail:bool=False,depth:int=Query(1,ge=1,le=3),direction:str='both',page:int=1):
    snap=_snapshot();view=view or ('focus' if node or mode=='focus' else 'organization' if group=='org_id' else 'classification' if group=='domain' or mode=='group' else 'panorama')
    if view not in ('panorama','classification','organization','focus'):raise HTTPException(400,'请选择有效的图谱视角。')
    k=asset_kind or kind
    if mode=='group' and group_value:
        if group=='kind':k=group_value
        elif group=='domain':domain=group_value
        elif group=='org_id':org_id=group_value
    history=include_history or bool(node and node in snap['nodes'] and not snap['nodes'][node]['active']);base=_base(snap,include_history)
    raw=[e for e in snap['edges'] if _valid_edge(e,snap,history,relation_status,relation_type)]
    if view=='focus':
        if direction!='both':raw=[e for e in raw if (direction=='down' and e.source==node) or (direction=='up' and e.target==node)]
        result=_focus(snap,base,node,raw,reading,budget,expand,role,depth)
    elif view=='organization':result=_organization(snap,base,_scope(snap,include_history),raw,reading,org_group,org_id)
    else:result=_classification(snap,base,[n for n in _scope(snap,include_history,kind=k,domain=domain,category=category,subcategory=subcategory,topic=topic,q=q) if _ownership_matches(n,ownership_scope,snap)],raw,view,reading,domain,k,category,subcategory,detail,budget)
    if ownership_scope:
        for n in result['nodes']:
            if n.get('open_params'):n['open_params']['ownership_scope']=ownership_scope
            if n.get('list_query'):n['list_query']['ownership_scope']=ownership_scope
    result.update(reading=reading,budget=budget,relation_status=relation_status,include_history=include_history)
    return result

@router.get('/graph/objects')
def graph_objects(scope:str='selection',node:str='',bucket:str='',context_kind:str='',org_group:str='',org_id:str='',org_role:str='',ownership_scope:str='',domain:str='',asset_kind:str='',kind:str='',category:str='',subcategory:str='',topic:str='',q:str='',relation_status:str='all',relation_type:str='',include_history:bool=False,page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100)):
    snap=_snapshot();include_history=include_history or bool(scope=='neighbors' and node in snap['nodes'] and not snap['nodes'][node]['active']);live=_scope(snap,include_history);raw=[e for e in snap['edges'] if _valid_edge(e,snap,include_history,relation_status,relation_type)]
    selected=_scope(snap,include_history,kind=asset_kind or kind,domain=domain,category=category,subcategory=subcategory,topic=topic,org_id=org_id if scope=='selection' else '')
    selected=[n for n in selected if _ownership_matches(n,ownership_scope,snap)]
    if scope=='selection' and org_id:selected=[n for n in selected if n['kind']!='organization']
    if scope=='organizations':selected=[n for n in live if n['kind']=='organization' and (not org_group or _org_family(n)==org_group)]
    if scope=='org_all_business':selected=[n for n in live if n['kind']!='organization']
    if scope=='neighbors':
        grouped,_=_neighbors(snap,node,raw);selected=list(grouped.get(bucket,{}).values()) if bucket else list({n['id']:n for v in grouped.values() for n in v.values()}.values())
    elif scope=='context':
        ids={n['id'] for n in selected};others=set()
        for e in raw:
            if e.source in ids and e.target not in ids:others.add(e.target)
            if e.target in ids and e.source not in ids:others.add(e.source)
        selected=[snap['nodes'][eid] for eid in others if not context_kind or snap['nodes'][eid]['kind']==context_kind]
    elif scope=='org_role':
        if org_role=='owned':selected=[n for n in live if n['org_id']==org_id and n['kind']!='organization']
        else:
            types={'owned_by'} if org_role=='provided' else {'collaborates_with','coordinated_by'};ids={e.source if e.target==org_id else e.target for e in raw if org_id in (e.source,e.target) and e.type in types};selected=[snap['nodes'][eid] for eid in ids]
    elif scope=='org_family':selected=[n for n in live if n['kind']!='organization' and (_public(n) if org_group=='shared' else not _public(n) and (not n['org_id'] or n['org_id'] not in snap['nodes']))]
    if q:selected=[n for n in selected if _matches(n,q=q)]
    selected.sort(key=lambda n:(ORDER.get(n['kind'],99),n['label'],n['id']));start=(page-1)*page_size
    return {'items':[_public_node(n,snap,relation_status,relation_type) for n in selected[start:start+page_size]],'total':len(selected),'page':page,'page_size':page_size,'pages':max(1,(len(selected)+page_size-1)//page_size)}
