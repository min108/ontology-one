"""变化影响复核：按明确依赖方向查找建议范围，持久化版本绑定的任务。"""
from __future__ import annotations
import hashlib
import json
import uuid
from contextlib import closing
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from .db import connect, entity_json, log, now

router = APIRouter(tags=["变化影响复核"])
STATUSES = {"confirmed": "已确认", "candidate": "待核实", "source_verified": "来源文本一致", "demo": "演示关联"}
ASSETS = ("rule", "data", "skill", "knowledge")
REVIEWABLE = ASSETS + ("case", "dataset", "analysis", "issue")
# These edges point from a consumer to its dependency. A change in the target
# therefore reaches the source; changing the consumer must not affect its inputs.
REVERSE = ("uses_data", "requires_data", "implements_rule", "based_on", "explains", "uses_method", "summarizes", "inspects", "maps_to", "refines", "references_data")
FORWARD = ("applies_in_demo", "supports")
SOURCE_DEPENDENCIES = ("derived_from", "supported_by", "references_source")
SCAN_CAP = 300
PATH_CAP = 1600


def brief(row):
    d = dict(row)
    return {k: d.get(k) for k in ("id", "kind", "title", "summary", "domain", "org_id", "status", "source_kind", "version", "updated_at")}


def get_source(db, source_id):
    row = db.execute("SELECT * FROM entities WHERE id=?", (source_id,)).fetchone()
    if not row or (row["kind"] not in ASSETS and not (row["kind"] == "material" and row["source_kind"] == "regulation")):
        raise HTTPException(404, "请选择规则、数据、技能、知识资产或已归集的法规原文")
    return row


def edge_path(row, previous, affected):
    try:
        p = json.loads(row["payload"] or "{}")
    except (ValueError, TypeError):
        p = {}
    return {"id": row["id"], "source": row["source"], "target": row["target"], "type": row["type"],
            "label": row["label"], "status": row["status"], "from_id": previous, "to_id": affected,
            "from_title": row['stitle'] if previous == row['source'] else row['ttitle'],
            "to_title": row['ttitle'] if affected == row['target'] else row['stitle'],
            "from_kind": row['sk'] if previous == row['source'] else row['tk'],
            "to_kind": row['tk'] if affected == row['target'] else row['sk'],
            "source_title":row['stitle'], "target_title":row['ttitle'],
            "basis": p.get("basis") or p.get("note") or p.get("locator") or "关系记录所载业务关联"}


def scope(db, source, depth=2):
    root = source["id"]
    current_only=json.loads(source['payload'] or '{}').get('active_asset',True) is not False
    found, frontier, paths = {}, [root], {root: []}
    truncated, scanned = False, 0
    for level in range(1, depth+1):
        if not frontier:
            break
        slots = ",".join("?" for _ in frontier)
        rev = ",".join("?" for _ in REVERSE)
        fwd = ",".join("?" for _ in FORWARD)
        source_types = ",".join("?" for _ in SOURCE_DEPENDENCIES)
        clauses = [f"(r.target IN ({slots}) AND (r.type IN ({rev}) OR (r.type='references_skill' AND s.kind='case')))",
                   f"(r.source IN ({slots}) AND (r.type IN ({fwd}) OR (r.type='references_skill' AND s.kind='rule')))"]
        params = [*frontier, *REVERSE, *frontier, *FORWARD]
        if level == 1 and source["kind"] == "material":
            clauses.append(f"(r.target=? AND r.type IN ({source_types}))")
            params.extend([root, *SOURCE_DEPENDENCIES])
        sql = "SELECT r.*,s.kind AS sk,t.kind AS tk,s.title AS stitle,t.title AS ttitle FROM relations r JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target WHERE r.status IN ('confirmed','candidate','source_verified','demo') AND (" + " OR ".join(clauses) + ") ORDER BY CASE r.status WHEN 'confirmed' THEN 0 WHEN 'source_verified' THEN 1 WHEN 'demo' THEN 2 ELSE 3 END,r.id LIMIT ?"
        if current_only:sql=sql.replace("WHERE r.status IN","WHERE COALESCE(json_extract(s.payload,'$.active_asset'),1)!=0 AND COALESCE(json_extract(t.payload,'$.active_asset'),1)!=0 AND r.status IN")
        rows = db.execute(sql, [*params, PATH_CAP+1]).fetchall()
        if len(rows)>PATH_CAP:
            truncated=True
        next_frontier = []
        for r in rows[:PATH_CAP]:
            scanned += 1
            if r["source"] in frontier and (r["type"] in FORWARD or (r["type"] == "references_skill" and r["sk"] == "rule")):
                previous, affected = r["source"], r["target"]
                target_kind = r["tk"]
            else:
                previous, affected = r["target"], r["source"]
                target_kind = r["sk"]
            if affected == root or target_kind not in REVIEWABLE:
                continue
            path = paths.get(previous, []) + [edge_path(r, previous, affected)]
            statuses = list(dict.fromkeys(x["status"] for x in path))
            chain_status = "candidate" if "candidate" in statuses else "demo" if "demo" in statuses else "source_verified" if "source_verified" in statuses else "confirmed"
            if affected not in found:
                if len(found)>=SCAN_CAP:
                    truncated=True
                    continue
                entity = db.execute("SELECT * FROM entities WHERE id=?", (affected,)).fetchone()
                found[affected] = {**brief(entity), "level": level, "relation_status": chain_status, "relation_statuses": statuses,
                                   "paths": [path], "path_count": 1,
                                   "review_hint": "检查历史分析采用的版本与口径，必要时重新分析；原结果保留" if target_kind == "analysis" else "核对引用内容、适用条件与口径是否需要同步调整"}
                paths[affected] = path
                # 粗分类候选只列为复核线索，不继续扩散。
                if chain_status != "candidate":
                    next_frontier.append(affected)
            else:
                item = found[affected]
                if all(p[-1]["id"] != r["id"] for p in item["paths"]):
                    item["path_count"] += 1
                    if len(item["paths"])<3:
                        item["paths"].append(path)
                item["relation_statuses"] = list(dict.fromkeys(item["relation_statuses"]+statuses))
        frontier=next_frontier
    ordered = sorted(found.values(), key=lambda x: (x["level"], {"confirmed":0,"source_verified":1,"demo":2,"candidate":3}.get(x["relation_status"],4), x["kind"], x["title"], x["id"]))
    return ordered, {"truncated":truncated,"exploration_limit":SCAN_CAP,"path_scan_limit_per_level":PATH_CAP,"scanned_paths":scanned,
                     "note":"只沿明确的依赖或使用关系寻找复核范围；候选关联不继续向外扩散。组织归属、专题包含和来源清单关系不用于传播。"}


@router.get("/impact/sources")
def sources(q: str = Query("",max_length=200), kind: str="", page: int=Query(1,ge=1), page_size:int=Query(12,ge=1,le=30)):
    where="(e.kind IN ('rule','data','skill','knowledge') OR (e.kind='material' AND e.source_kind='regulation')) AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0"
    args=[]
    if kind:
        where+=" AND e.kind=?";args.append(kind)
    if q.strip():
        term="%"+q.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        where+=" AND (e.title LIKE ? OR e.id LIKE ?)";args += [term,term]
    with closing(connect()) as db:
        total=db.execute(f"SELECT COUNT(*) FROM entities e WHERE {where}",args).fetchone()[0]
        rows=db.execute(f"SELECT e.* FROM entities e WHERE {where} ORDER BY e.updated_at DESC,e.title,e.id LIMIT ? OFFSET ?",[*args,page_size,(page-1)*page_size]).fetchall()
        return {"items":[brief(r) for r in rows],"total":total,"page":page,"page_size":page_size}


@router.get("/impact/preview")
def preview(source_id:str, depth:int=Query(2,ge=1,le=2), page:int=Query(1,ge=1),page_size:int=Query(12,ge=1,le=30), level:Literal['all','direct','indirect']='all', relation_status:Literal['all','confirmed','candidate','source_verified','demo']='all'):
    with closing(connect()) as db:
        source=get_source(db,source_id)
        items,coverage=scope(db,source,depth)
        counts={"direct":sum(x['level']==1 for x in items),"indirect":sum(x['level']>1 for x in items),"candidate":sum('candidate' in x['relation_statuses'] for x in items)}
        filtered=[x for x in items if (level=='all' or x['level']==(1 if level=='direct' else 2)) and (relation_status=='all' or relation_status in x['relation_statuses'])]
        latest=db.execute("SELECT version,reason,created_at FROM versions WHERE entity_id=? ORDER BY id DESC LIMIT 1",(source_id,)).fetchone()
        return {"source":brief(source),"last_change":dict(latest) if latest else None,"items":filtered[(page-1)*page_size:page*page_size],"total":len(filtered),"identified_total":len(items),"counts":counts,"coverage":coverage,"page":page,"page_size":page_size,"depth":depth,
                "disclaimer":"关联仅用于建议复核范围，不表示相关内容已经失效，也不代替适用判断。"}


class SelectedTarget(BaseModel):
    id:str=Field(min_length=1,max_length=150)
    version:int=Field(ge=1)


class ReviewBatch(BaseModel):
    source_id:str=Field(min_length=1,max_length=150)
    source_version:int=Field(ge=1)
    targets:list[SelectedTarget]=Field(min_length=1,max_length=30)
    change_reason:str=Field(min_length=2,max_length=1500)
    instruction:str=Field(min_length=4,max_length=3000)
    owner:str=Field(min_length=1,max_length=100)
    due_date:date
    depth:int=Field(default=2,ge=1,le=2)


@router.post("/impact/review-tasks")
def create_review_tasks(body:ReviewBatch):
    if not body.change_reason.strip() or not body.instruction.strip() or not body.owner.strip():
        raise HTTPException(422,"请填写变化原因、复核要求与负责人")
    if body.due_date < datetime.now(ZoneInfo('Asia/Shanghai')).date():
        raise HTTPException(422,"复核期限不能早于今天")
    if len({t.id for t in body.targets})!=len(body.targets):
        raise HTTPException(422,"同一批次不能重复选择成果")
    with closing(connect()) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            source=get_source(db,body.source_id)
            if source['version']!=body.source_version:
                raise HTTPException(409,"变化来源已有新版本，请刷新范围后重新确认")
            available,coverage=scope(db,source,body.depth)
            candidates={x['id']:x for x in available}
            for t in body.targets:
                if t.id not in candidates:
                    raise HTTPException(409,"所选成果已不在当前依赖复核范围，请刷新后选择")
                if candidates[t.id]['version']!=t.version:
                    raise HTTPException(409,f"《{candidates[t.id]['title']}》已有新版本，请刷新后重新确认")
            batch='IMPACT-'+uuid.uuid4().hex[:12]
            created,existing=[],[]
            for t in body.targets:
                item=candidates[t.id]
                fingerprint=hashlib.sha256(f"{body.source_id}|{body.source_version}|{t.id}|{t.version}".encode()).hexdigest()
                task_id='TASK-IMPACT-'+fingerprint[:22]
                old=db.execute("SELECT * FROM entities WHERE id=?",(task_id,)).fetchone()
                if old:
                    existing.append(brief(old));continue
                payload={"task_type":"impact_review","batch_id":batch,"fingerprint":fingerprint,"source_id":body.source_id,"source_version":body.source_version,
                         "source_title":source['title'],"source_kind":source['source_kind'],"target_id":t.id,"target_version":t.version,"target_title":item['title'],
                         "source_snapshot":entity_json(source),"target_snapshot":entity_json(db.execute('SELECT * FROM entities WHERE id=?',(t.id,)).fetchone()),
                         "change_reason":body.change_reason.strip(),"instruction":body.instruction.strip(),"owner":body.owner.strip(),"responsible":body.owner.strip(),
                         "due_date":body.due_date.isoformat(),"priority":"high","priority_label":"重要","impact_level":item['level'],"relation_statuses":item['relation_statuses'],
                         "relation_paths":item['paths'],"scope_note":"这是建议复核范围，不代表所选成果已失效；请记录核对结论与是否需要修订。",
                         "checklist":[{"item":"核对本次变化及引用版本","done":False},{"item":"检查适用条件、口径和例外是否受到影响","done":False},{"item":"记录复核结论及需修订的具体内容","done":False}]}
                timestamp=now()
                title='变化复核：'+item['title'][:90]
                summary=f"因《{source['title']}》V{body.source_version}的变化说明，对该成果V{t.version}开展复核。{body.instruction.strip()}"
                db.execute("INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(task_id,'task',title,summary,item['domain'],item['org_id'],'draft','user',json.dumps(payload,ensure_ascii=False),timestamp,timestamp,1))
                for endpoint,rel_type,label in [(body.source_id,'reviews_change_source','复核变化来源'),(t.id,'reviews_changed_asset','复核关联成果')]:
                    rid='REL-IMPACT-'+hashlib.sha256((task_id+rel_type).encode()).hexdigest()[:24]
                    db.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?)",(rid,task_id,endpoint,rel_type,label,'confirmed',json.dumps({'batch_id':batch,'source_version':body.source_version,'target_version':t.version},ensure_ascii=False)))
                log(db,task_id,'建立变化复核任务',body.change_reason.strip(),body.owner.strip())
                log(db,t.id,'纳入变化复核',f"来源 {body.source_id} V{body.source_version}，任务 {task_id}",body.owner.strip())
                created.append({'id':task_id,'title':title,'status':'draft','target_id':t.id})
            if created:
                log(db,body.source_id,'发起变化影响复核',f"针对V{body.source_version}建立{len(created)}项复核任务，批次{batch}",body.owner.strip())
            db.commit()
            return {'batch_id':batch,'created':created,'existing':existing,'created_count':len(created),'existing_count':len(existing),'message':'相同来源版本与成果版本的任务自动复用，避免重复办理。'}
        except Exception:
            db.rollback();raise


@router.get('/impact/history')
def history(source_id:str='',page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=30)):
    where="e.kind='task' AND json_extract(e.payload,'$.task_type')='impact_review'"
    args=[]
    if source_id:
        where+=" AND json_extract(e.payload,'$.source_id')=?";args.append(source_id)
    with closing(connect()) as db:
        total=db.execute(f"SELECT COUNT(*) FROM entities e WHERE {where}",args).fetchone()[0]
        rows=db.execute(f"SELECT e.* FROM entities e WHERE {where} ORDER BY e.created_at DESC,e.id LIMIT ? OFFSET ?",[*args,page_size,(page-1)*page_size]).fetchall()
        return {'items':[entity_json(r) for r in rows],'total':total,'page':page,'page_size':page_size}
