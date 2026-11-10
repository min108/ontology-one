"""Immutable source versions and precise anchors, independent of reading layout.

Call helpers in the caller's transaction. Existing chunk IDs remain resolvable;
new reading revisions never delete them or redirect historical references.
"""
from __future__ import annotations
import hashlib,json,uuid
from .db import now
from fastapi import HTTPException


def _json(value): return json.dumps(value,ensure_ascii=False)
def _id(prefix): return prefix+'_'+uuid.uuid4().hex[:20]


def initialize_provenance(conn):
    statements=[
        "CREATE TABLE IF NOT EXISTS source_versions(id VARCHAR(191) PRIMARY KEY,material_id VARCHAR(191) NOT NULL,version INTEGER NOT NULL,text LONGTEXT NOT NULL,sha256 VARCHAR(64) NOT NULL,metadata LONGTEXT NOT NULL,created_at VARCHAR(40) NOT NULL,UNIQUE(material_id,version),UNIQUE(material_id,sha256)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS source_anchors(id VARCHAR(191) PRIMARY KEY,material_id VARCHAR(191) NOT NULL,source_version VARCHAR(191) NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,quote_sha256 VARCHAR(64) NOT NULL,locator TEXT NOT NULL,created_at VARCHAR(40) NOT NULL,UNIQUE(source_version,start,end)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS chunk_anchors(chunk_id VARCHAR(255) PRIMARY KEY,anchor_id VARCHAR(191) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS reading_versions(id VARCHAR(191) PRIMARY KEY,material_id VARCHAR(191) NOT NULL,source_version VARCHAR(191) NOT NULL,version INTEGER NOT NULL,segments LONGTEXT NOT NULL,reason TEXT NOT NULL,created_at VARCHAR(40) NOT NULL,UNIQUE(material_id,version)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS reading_drafts(material_id VARCHAR(191) PRIMARY KEY,id VARCHAR(191) NOT NULL,source_version VARCHAR(191) NOT NULL,base_version VARCHAR(191) NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,segments LONGTEXT NOT NULL,history LONGTEXT NOT NULL,future LONGTEXT NOT NULL,pending_ai LONGTEXT NOT NULL,chat LONGTEXT NOT NULL,updated_at VARCHAR(40) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS archived_reading_drafts(id VARCHAR(191) PRIMARY KEY,material_id VARCHAR(191) NOT NULL,source_version VARCHAR(191) NOT NULL,snapshot LONGTEXT NOT NULL,created_at VARCHAR(40) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE INDEX IF NOT EXISTS idx_source_versions_material ON source_versions(material_id,version)",
        "CREATE INDEX IF NOT EXISTS idx_source_anchors_material ON source_anchors(material_id,source_version)",
        "CREATE INDEX IF NOT EXISTS idx_reading_versions_material ON reading_versions(material_id,version)",
    ]
    for sql in statements: conn.execute(sql)


def _schema(conn):
    # Schema is provisioned before a workspace can be selected.
    return None


def get_active_chunks(conn,material_id):
    _schema(conn)
    source=conn.execute('SELECT id,text FROM source_versions WHERE material_id=? ORDER BY version DESC LIMIT 1',(material_id,)).fetchone()
    version=conn.execute('SELECT segments FROM reading_versions WHERE material_id=?'+(' AND source_version=?' if source else '')+' ORDER BY version DESC LIMIT 1',(material_id,source['id']) if source else (material_id,)).fetchone()
    rows=[dict(r) for r in conn.execute('SELECT * FROM chunks WHERE material_id=? ORDER BY position,id',(material_id,))]
    if version:
        ids=[s['chunk_id'] for s in json.loads(version['segments']) if s.get('chunk_id')]
        if not ids:return []
        by_id={r['id']:r for r in rows}
        return [by_id[cid] for cid in ids if cid in by_id]
    # A new original revision must never be hidden by a reading layout for an
    # older source. Return physical narrative blocks until the new layout is
    # confirmed, excluding every historical reading chunk to avoid duplicates.
    reading_ids={s['chunk_id'] for row in conn.execute('SELECT segments FROM reading_versions WHERE material_id=?',(material_id,)) for s in json.loads(row['segments']) if s.get('chunk_id')}
    row=conn.execute('SELECT payload FROM entities WHERE id=?',(material_id,)).fetchone()
    payload=json.loads(row['payload'] or '{}') if row else {}
    turns={t['chunk_id'] for t in payload.get('turns',[]) if t.get('role')=='user' and t.get('chunk_id')}
    candidates=[r for r in rows if r['id'] not in reading_ids and (not turns or r['id'] in turns or '::narrative-' in r['id'])]
    return [r for r in candidates if not source or r['content'] in source['text']]


def ensure_source_version(conn,material_id,text=None,*,metadata=None):
    _schema(conn)
    entity=conn.execute("SELECT payload FROM entities WHERE id=? AND kind='material'",(material_id,)).fetchone()
    if not entity:raise HTTPException(404,'未找到原始材料')
    if text is None:
        existing=conn.execute('SELECT * FROM source_versions WHERE material_id=? ORDER BY version DESC LIMIT 1',(material_id,)).fetchone()
        if existing:
            value=dict(existing);value['metadata']=json.loads(value['metadata']);return value
        payload=json.loads(entity['payload'] or '{}')
        text=payload.get('original_text')
        if text is None:text='\n\n'.join(r['content'] for r in get_active_chunks(conn,material_id))
        metadata=metadata or {'filename':payload.get('filename') or payload.get('source_file'),'format':payload.get('format'),'source_file_sha256':payload.get('sha256'),'parse_warnings':payload.get('parse_warnings',[])}
        previous=payload.get('original_text_before_v3') or payload.get('original_body_before_v3')
        if isinstance(previous,str) and previous and previous!=text:
            ensure_source_version(conn,material_id,previous,metadata={'material_version':payload.get('previous_material_version'),'source_path':payload.get('previous_source_path'),'historical':True})
    if not isinstance(text,str):raise HTTPException(422,'原文版本必须为文字内容')
    sha=hashlib.sha256(text.encode()).hexdigest()
    row=conn.execute('SELECT * FROM source_versions WHERE material_id=? AND sha256=?',(material_id,sha)).fetchone()
    if not row:
        if getattr(conn, 'readonly', False):raise HTTPException(409,'原文版本索引尚未初始化，请先保存材料。')
        version=conn.execute('SELECT coalesce(max(version),0)+1 FROM source_versions WHERE material_id=?',(material_id,)).fetchone()[0]
        sid=_id('source');conn.execute('INSERT INTO source_versions VALUES(?,?,?,?,?,?,?)',(sid,material_id,version,text,sha,_json(metadata or {}),now()))
        row=conn.execute('SELECT * FROM source_versions WHERE id=?',(sid,)).fetchone()
    value=dict(row);value['metadata']=json.loads(value['metadata']);return value


def get_source_version(conn,material_id,source_version=None):
    _schema(conn)
    if not source_version:return ensure_source_version(conn,material_id)
    row=conn.execute('SELECT * FROM source_versions WHERE id=? AND material_id=?',(source_version,material_id)).fetchone()
    if not row:raise HTTPException(422,'所引用的原文版本不存在或不属于这份材料')
    value=dict(row);value['metadata']=json.loads(value['metadata']);return value


def _anchor(conn,material_id,source_version,start,end,locator=''):
    source=get_source_version(conn,material_id,source_version)
    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(source['text']):raise HTTPException(422,'出处范围超出原文或为空')
    quote=source['text'][start:end]
    row=conn.execute('SELECT * FROM source_anchors WHERE source_version=? AND start=? AND end=?',(source_version,start,end)).fetchone()
    if not row:
        if getattr(conn, 'readonly', False):raise HTTPException(409,'该出处尚未保存，请先在材料工作面确认引用。')
        aid=_id('anchor');loc=locator or f'原文第 {start+1}–{end} 字'
        conn.execute('INSERT INTO source_anchors VALUES(?,?,?,?,?,?,?,?)',(aid,material_id,source_version,start,end,hashlib.sha256(quote.encode()).hexdigest(),loc,now()))
        row=conn.execute('SELECT * FROM source_anchors WHERE id=?',(aid,)).fetchone()
    return {**dict(row),'quote':quote}


def _ref(anchor,chunk_id=None,extra=None):
    value={**(extra or {}),'material_id':anchor['material_id'],'source_version':anchor['source_version'],'revision_id':anchor['source_version'],'anchor_id':anchor['id'],'span_id':anchor['id'],'span':{'start':anchor['start'],'end':anchor['end']},'start':anchor['start'],'end':anchor['end'],'quote':anchor['quote'],'locator':anchor['locator']}
    if chunk_id:value['chunk_id']=chunk_id
    return value


def register_chunk_anchor(conn,material_id,chunk_id,source_version,start,end,locator=''):
    _schema(conn)
    row=conn.execute('SELECT * FROM chunks WHERE id=? AND material_id=?',(chunk_id,material_id)).fetchone()
    if not row:raise HTTPException(422,'所引用的原文片段不存在')
    existing=conn.execute('SELECT a.* FROM chunk_anchors c JOIN source_anchors a ON a.id=c.anchor_id WHERE c.chunk_id=?',(chunk_id,)).fetchone()
    if existing:
        source=get_source_version(conn,material_id,existing['source_version']);return _ref({**dict(existing),'quote':source['text'][existing['start']:existing['end']]},chunk_id)
    anchor=_anchor(conn,material_id,source_version,start,end,locator or row['locator'])
    if anchor['quote']!=row['content']:raise HTTPException(422,'片段正文与原文范围不一致，未建立出处锚点')
    if getattr(conn, 'readonly', False):raise HTTPException(409,'片段出处尚未确认，请先保存材料分段。')
    conn.execute('INSERT INTO chunk_anchors VALUES(?,?)',(chunk_id,anchor['id']))
    return _ref(anchor,chunk_id)


def ensure_chunk_anchor(conn,material_id,chunk_id):
    _schema(conn)
    row=conn.execute('SELECT * FROM chunks WHERE id=? AND material_id=?',(chunk_id,material_id)).fetchone()
    if not row:raise HTTPException(422,'所引用片段不存在或不属于该材料')
    mapped=conn.execute('SELECT a.* FROM chunk_anchors c JOIN source_anchors a ON a.id=c.anchor_id WHERE c.chunk_id=?',(chunk_id,)).fetchone()
    if mapped:
        source=get_source_version(conn,material_id,mapped['source_version']);return _ref({**dict(mapped),'quote':source['text'][mapped['start']:mapped['end']]},chunk_id)
    ensure_source_version(conn,material_id)
    sources=[dict(s) for s in conn.execute('SELECT * FROM source_versions WHERE material_id=? ORDER BY version',(material_id,))]
    cursors={s['id']:0 for s in sources}
    for ch in conn.execute('SELECT * FROM chunks WHERE material_id=? ORDER BY position,id',(material_id,)):
        previous=conn.execute('SELECT a.source_version,a.start,a.end FROM chunk_anchors c JOIN source_anchors a ON a.id=c.anchor_id WHERE c.chunk_id=?',(ch['id'],)).fetchone()
        if previous:
            sid=previous['source_version'];cursors[sid]=max(cursors.get(sid,0),previous['end'])
            continue
        match=None
        for source in sources:
            start=source['text'].find(ch['content'],cursors[source['id']])
            if start<0:start=source['text'].find(ch['content'])
            if start>=0:
                match=(source,start);break
        if match is None:
            if ch['id']==chunk_id:raise HTTPException(422,'历史片段无法与当前原文逐字对齐，请保留原件并核对来源版本')
            continue
        source,start=match
        ref=register_chunk_anchor(conn,material_id,ch['id'],source['id'],start,start+len(ch['content']),ch['locator'])
        cursors[source['id']]=start+len(ch['content'])
        if ch['id']==chunk_id:return ref
    raise HTTPException(422,'未能建立原文定位')


def normalize_source_ref(conn,material_id,ref,min_quote_length=1):
    _schema(conn)
    if not isinstance(ref,dict):raise HTTPException(422,'出处须为原文引用对象')
    if ref.get('material_id') and ref['material_id']!=material_id:raise HTTPException(422,'出处材料归属不一致')
    chunk_id=ref.get('chunk_id')
    aid=ref.get('anchor_id') or ref.get('span_id')
    source_id=ref.get('source_version') or ref.get('revision_id')
    base=None
    if aid:
        anchor=conn.execute('SELECT * FROM source_anchors WHERE id=? AND material_id=?',(aid,material_id)).fetchone()
        if not anchor:raise HTTPException(422,'不可变出处锚点不存在')
        source=get_source_version(conn,material_id,anchor['source_version']);base=_ref({**dict(anchor),'quote':source['text'][anchor['start']:anchor['end']]},chunk_id)
        if source_id and source_id!=base['source_version']:raise HTTPException(422,'出处锚点与原文版本不一致')
    elif chunk_id:base=ensure_chunk_anchor(conn,material_id,chunk_id)
    span=ref.get('span') or ({'start':ref['start'],'end':ref['end']} if 'start' in ref and 'end' in ref else None)
    if base:
        if source_id and source_id!=base['source_version']:raise HTTPException(422,'历史片段与指定原文版本不一致')
        source_id=base['source_version']
    source=get_source_version(conn,material_id,source_id)
    quote=ref.get('quote')
    if span:
        start,end=span.get('start'),span.get('end')
        if base and (type(start) is not int or type(end) is not int or start<base['start'] or end>base['end']):raise HTTPException(422,'引文超出指定出处范围')
    elif base:
        if quote is None:quote=base['quote']
        if not isinstance(quote,str):raise HTTPException(422,'引文必须为原文文字')
        relative=base['quote'].find(quote)
        if relative<0:raise HTTPException(422,'引文未逐字出现在指定原文片段中')
        start,end=base['start']+relative,base['start']+relative+len(quote)
    else:raise HTTPException(422,'引用必须提供片段、锚点或原文版本与准确范围')
    anchor=_anchor(conn,material_id,source['id'],start,end,ref.get('locator') or (base or {}).get('locator',''))
    if quote is None:quote=anchor['quote']
    if not isinstance(quote,str) or len(quote.strip())<min_quote_length or quote!=anchor['quote']:raise HTTPException(422,'引文与原文范围不一致或长度不足')
    if chunk_id:
        original=ensure_chunk_anchor(conn,material_id,chunk_id)
        if original['source_version']!=source['id'] or not original['start']<=start<end<=original['end']:raise HTTPException(422,'出处锚点不在声明的原文片段内')
    return _ref(anchor,chunk_id,ref)
