"""Live projections from confirmed dictionary mappings to unit-owned original cells.

No instance is copied into entities: every count/value comes from the current
mapping and immutable import batch. Candidate or unavailable standards are excluded.
"""
from contextlib import closing
import hashlib
import json
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from .db import connect, entity_json
from .datasets import json_path, transform_value, mapping_confirmation_version

router = APIRouter()
NOTE = "按各独立导入批次的当前已确认字段对应统计；同一原行对应多个字段时，记录数去重，字段取值数分别保留。候选对应、未发布或已下架的标准不计入。"


def _active(payload):
    return payload.get("active_asset", True) not in (False, 0) and payload.get("active", True) not in (False, 0)


def _standard(c, ident):
    row = c.execute("SELECT * FROM entities WHERE id=? AND kind='data'", (ident,)).fetchone()
    if not row:
        raise HTTPException(404, "未找到这项标准数据资产")
    return entity_json(row)


def _bindings(c, ids=None):
    args = []
    where = "m.status='confirmed' AND std.kind='data' AND std.status='published' AND d.kind='dataset'"
    if ids is not None:
        if not ids:
            return []
        where += " AND m.standard_id IN (" + ",".join("?" for _ in ids) + ")"
        args.extend(ids)
    rows = c.execute("SELECT m.*,std.title standard_title,std.payload standard_payload,std.version standard_version,std.updated_at standard_updated_at,d.title dataset_title,d.org_id,d.source_kind,d.version dataset_version,d.payload dataset_payload,o.title org_title FROM mappings m JOIN entities std ON std.id=m.standard_id JOIN entities d ON d.id=m.dataset_id LEFT JOIN entities o ON o.id=d.org_id AND o.kind='organization' WHERE " + where + " ORDER BY d.org_id,d.title,m.column_name", args).fetchall()
    result = []
    sheet_cache = {}
    for row in rows:
        row = dict(row)
        payload = json.loads(row["payload"] or "{}")
        confirmed_version, confirmation_basis = mapping_confirmation_version(c, row, {"id": row["standard_id"], "version": row["standard_version"], "updated_at": row["standard_updated_at"], "status": "published"})
        if confirmed_version != row["standard_version"]:
            continue
        dp = json.loads(row["dataset_payload"] or "{}")
        if not _active(dp) or not _active(json.loads(row["standard_payload"] or "{}")):
            continue
        dataset_id = row["dataset_id"]
        if dataset_id not in sheet_cache:
            sheet_cache[dataset_id] = [(s["sheet_name"], json.loads(s["payload"] or "{}")) for s in c.execute("SELECT sheet_name,payload FROM dataset_sheets WHERE dataset_id=? ORDER BY position", (dataset_id,))]
        column = payload.get("source_column") or row["column_name"]
        for sheet, sp in sheet_cache[dataset_id]:
            if payload.get("sheet_names") and sheet not in payload["sheet_names"]:
                continue
            names = [col.get("name") if isinstance(col, dict) else col for col in sp.get("columns", [])]
            if column not in names:
                continue
            source_id = "instance:field:" + hashlib.sha256(json.dumps([dataset_id, sheet, row["column_name"]], ensure_ascii=False).encode()).hexdigest()[:24]
            result.append({"source_id": source_id, "standard_id": row["standard_id"], "standard_title": row["standard_title"], "org_id": row["org_id"], "org_title": row["org_title"] or row["org_id"] or "未登记单位", "dataset_id": dataset_id, "dataset_title": row["dataset_title"], "source_kind": row["source_kind"], "is_demo": row["source_kind"] == "demo", "source_label": "演示数据" if row["source_kind"] == "demo" else "用户导入数据", "dataset_version": row["dataset_version"], "mapping_version": dp.get("mapping_version", 0), "confirmed_standard_version": confirmed_version, "confirmation_basis": confirmation_basis, "batch_number": dp.get("batch_number", 1), "previous_id": dp.get("previous_id"), "original_name": dp.get("original_name", ""), "sheet": sheet, "source_column": column, "mapping_key": row["column_name"], "transform": row["transform"], "grain": sp.get("grain") or "行记录含义待确认", "grain_confirmed": bool(sp.get("grain_confirmed")), "published_for_analysis": bool(dp.get("published_for_analysis")), "json_path": json_path(column), "href": "/datasets/" + dataset_id + "?" + urlencode({"tab": "rows", "sheet": sheet}), "mapping_href": "/datasets/" + dataset_id + "?" + urlencode({"tab": "mappings", "sheet": sheet})})
    return result


# CTE scopes every record to the unit AND the physical worksheet, including
# multi-sheet same-name fields and two fields aligned to one dictionary item.
_CELLS = """WITH b AS (
 SELECT j.source_id,j.standard_id,j.dataset_id,j.org_id,j.sheet,j.path,j.source_kind
 FROM JSON_TABLE(?, '$[*]' COLUMNS(
   source_id VARCHAR(191) PATH '$.source_id',standard_id VARCHAR(191) PATH '$.standard_id',
   dataset_id VARCHAR(191) PATH '$.dataset_id',org_id VARCHAR(191) PATH '$.org_id',
   sheet VARCHAR(255) PATH '$.sheet',path VARCHAR(1000) PATH '$.json_path',
   source_kind VARCHAR(64) PATH '$.source_kind')) j
), cells AS (
 SELECT b.*,r.row_no,s.source_row,json_extract(r.values_json,b.path) raw_value
 FROM b JOIN dataset_rows r ON r.dataset_id=b.dataset_id AND r.org_id <=> b.org_id
 JOIN dataset_row_sources s ON s.dataset_id=r.dataset_id AND s.row_no=r.row_no AND s.sheet_name=b.sheet
) """
_NONEMPTY = "raw_value IS NOT NULL AND TRIM(CAST(raw_value AS TEXT))!=''"


def _table_counts(c, bindings):
    ids = sorted({b["dataset_id"] for b in bindings})
    if not ids:
        return {}
    rows = c.execute("SELECT r.dataset_id,r.org_id,s.sheet_name,COUNT(*) records FROM dataset_rows r JOIN dataset_row_sources s ON s.dataset_id=r.dataset_id AND s.row_no=r.row_no WHERE r.dataset_id IN (" + ",".join("?" for _ in ids) + ") GROUP BY r.dataset_id,r.org_id,s.sheet_name", ids)
    return {(r["dataset_id"], r["org_id"], r["sheet_name"]): r["records"] for r in rows}


def _stats(c, bindings, count_values=True, table_counts=None):
    counts = _table_counts(c, bindings) if table_counts is None else table_counts
    tables = {(b["dataset_id"], b["org_id"], b["sheet"]): b["source_kind"] for b in bindings}
    records = sum(counts.get(key, 0) for key in tables)
    cells = sum(counts.get((b["dataset_id"], b["org_id"], b["sheet"]), 0) for b in bindings)
    nonempty = sum(r["nonempty"] for r in _source_counts(c, bindings).values()) if count_values else None
    return {"standards": len({b["standard_id"] for b in bindings}), "units": len({b["org_id"] for b in bindings}), "datasets": len({b["dataset_id"] for b in bindings}), "sheets": len(tables), "fields": len(bindings), "records": records, "cells": cells, "nonempty": nonempty, "empty": cells - nonempty if count_values else None, "demo_records": sum(counts.get(key, 0) for key, kind in tables.items() if kind == "demo"), "user_records": sum(counts.get(key, 0) for key, kind in tables.items() if kind != "demo"), "value_counts_available": count_values}


def _source_counts(c, bindings):
    return {r["source_id"]: dict(r) for r in c.execute(_CELLS + "SELECT source_id,COUNT(*) records,COALESCE(SUM(" + _NONEMPTY + "),0) nonempty FROM cells GROUP BY source_id", (json.dumps(bindings),))}


def _row_data(binding, row):
    values = json.loads(row["values_json"])
    value = values.get(binding["source_column"])
    normalized = transform_value(value, binding["transform"])
    missing = value is None or (isinstance(value, str) and not value.strip())
    return {"row_no": row["row_no"], "source_row": row["source_row"], "sheet": binding["sheet"], "dataset_id": binding["dataset_id"], "org_id": binding["org_id"], "raw_value": value, "standard_value": normalized, "empty": missing, "conversion_failed": not missing and normalized is None, "values": values, "href": binding["href"] + "&row=" + str(row["row_no"])}


def _rows(c, binding, page=1, page_size=10, samples=False):
    order = "CASE WHEN json_extract(r.values_json,?) IS NULL OR TRIM(CAST(json_extract(r.values_json,?) AS TEXT))='' THEN 1 ELSE 0 END," if samples else ""
    params = [binding["dataset_id"], binding["org_id"], binding["sheet"]]
    if samples:
        params += [binding["json_path"], binding["json_path"]]
    params += [page_size, (page - 1) * page_size]
    rows = c.execute("SELECT r.*,s.source_row FROM dataset_rows r JOIN dataset_row_sources s ON s.dataset_id=r.dataset_id AND s.row_no=r.row_no WHERE r.dataset_id=? AND r.org_id <=> ? AND s.sheet_name=? ORDER BY " + order + "r.row_no LIMIT ? OFFSET ?", params)
    return [_row_data(binding, row) for row in rows]


def _filtered(bindings, org_id="", dataset_id="", sheet="", source_kind=""):
    return [b for b in bindings if (not org_id or b["org_id"] == org_id) and (not dataset_id or b["dataset_id"] == dataset_id) and (not sheet or b["sheet"] == sheet) and (not source_kind or b["source_kind"] == source_kind)]


def _clean(binding):
    return {k: v for k, v in binding.items() if k != "json_path"}


@router.get("/data-instances/summary")
def summary(ids: str = ""):
    wanted = list(dict.fromkeys(s for s in ids.split(",") if s)) if ids else None
    if wanted and len(wanted) > 100:
        raise HTTPException(422, "一次最多查询100项标准数据")
    with closing(connect()) as c:
        bindings = _bindings(c, wanted)
        table_counts = _table_counts(c, bindings)
        stats = _stats(c, bindings, False, table_counts)
        by_standard = {ident: _stats(c, [b for b in bindings if b["standard_id"] == ident], False, table_counts) for ident in (wanted if wanted is not None else sorted({b["standard_id"] for b in bindings}))}
        return {"summary": stats, "by_standard": by_standard, "scope_note": NOTE}


@router.get("/data-instances/{standard_id}")
def instances(standard_id: str, org_id: str = "", dataset_id: str = "", sheet: str = "", source_kind: str = "", page: int = Query(1, ge=1), page_size: int = Query(6, ge=1, le=50)):
    with closing(connect()) as c:
        standard = _standard(c, standard_id)
        all_bindings = _bindings(c, [standard_id])
        bindings = _filtered(all_bindings, org_id, dataset_id, sheet, source_kind)
        total = len(bindings)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        selected = bindings[(page - 1) * page_size:page * page_size]
        counts = _source_counts(c, selected)
        sources = []
        for b in selected:
            count = counts.get(b["source_id"], {"records": 0, "nonempty": 0})
            sources.append({**_clean(b), **count, "empty": count["records"] - count["nonempty"], "samples": _rows(c, b, page_size=3, samples=True)})
        return {"standard": {k: standard[k] for k in ("id", "title", "status", "version")}, "available": standard["status"] == "published" and _active(standard["payload"]), "summary": _stats(c, bindings), "sources": sources, "total": total, "page": page, "page_size": page_size, "filters": {"organizations": [{"id": i, "title": t} for i, t in dict((b["org_id"], b["org_title"]) for b in all_bindings).items()], "datasets": [{"id": i, "title": t} for i, t in dict((b["dataset_id"], b["dataset_title"]) for b in all_bindings if not org_id or b["org_id"] == org_id).items()]}, "scope_note": NOTE}


@router.get("/data-instances/{standard_id}/rows")
def instance_rows(standard_id: str, source_id: str, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100)):
    with closing(connect()) as c:
        _standard(c, standard_id)
        binding = next((b for b in _bindings(c, [standard_id]) if b["source_id"] == source_id), None)
        if not binding:
            raise HTTPException(404, "该原字段已不在此标准的当前确认范围内，请刷新对应关系")
        count = _source_counts(c, [binding]).get(source_id, {"records": 0, "nonempty": 0})
        page = min(page, max(1, (count["records"] + page_size - 1) // page_size))
        return {"source": _clean(binding), "items": _rows(c, binding, page, page_size), "total": count["records"], "page": page, "page_size": page_size}


@router.get("/data-instances/{standard_id}/graph")
def instance_graph(standard_id: str, org_id: str = "", dataset_id: str = "", page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=10)):
    with closing(connect()) as c:
        standard = _standard(c, standard_id)
        all_bindings = _filtered(_bindings(c, [standard_id]), org_id, dataset_id)
        total = len(all_bindings)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        bindings = all_bindings[(page - 1) * page_size:page * page_size]
        stats = _stats(c, all_bindings)
        counts = _source_counts(c, bindings)
        nodes = {standard_id: {"id": standard_id, "kind": "data", "level": "standard", "title": standard["title"], "label": standard["title"], "href": "/assets/" + standard_id, "version": standard["version"], "count": stats["records"], "aggregate": False}}
        edges = {}
        shown = set()
        sources = []
        def edge(source, target, label, kind):
            eid = "instance:edge:" + hashlib.sha256((source + "|" + target + "|" + kind).encode()).hexdigest()[:24]
            edges[eid] = {"id": eid, "source": source, "target": target, "label": label, "type": kind, "count": 1, "status": "confirmed", "aggregate": False}
        for b in bindings:
            oid = "instance:org:" + (b["org_id"] or "unknown")
            did = "instance:dataset:" + b["dataset_id"]
            sid = "instance:sheet:" + hashlib.sha256((b["dataset_id"] + "|" + b["sheet"]).encode()).hexdigest()[:24]
            count = counts.get(b["source_id"], {"records": 0, "nonempty": 0})
            common = {"source_kind": b["source_kind"], "is_demo": b["is_demo"], "org_id": b["org_id"], "aggregate": False}
            for ident, level, title, href in [(oid, "organization", b["org_title"], "/assets/" + b["org_id"] if b["org_id"] else "/datasets"), (did, "dataset", b["dataset_title"], "/datasets/" + b["dataset_id"]), (sid, "sheet", b["sheet"], b["href"]), (b["source_id"], "field", b["source_column"], b["mapping_href"])]:
                node = {"id": ident, "kind": "instance_" + level, "level": level, "title": title, "label": title, "href": href, "count": count["records"] if level == "field" else None, "version": b["dataset_version"], "summary": ("演示数据" if b["is_demo"] else "用户导入数据") + " · 第" + str(b["batch_number"]) + "批", **common}
                if level == "organization":
                    node.update(summary="原始数据的报送主体，按批次分别核对", version=None, source_kind=None, is_demo=None)
                nodes.setdefault(ident, node)
            edge(oid, did, "报送", "instance_owns")
            edge(did, sid, "保留原表", "instance_sheet")
            edge(sid, b["source_id"], "包含原字段", "instance_field")
            edge(b["source_id"], standard_id, "已确认对应", "maps_to")
            samples = _rows(c, b, page_size=2, samples=True)
            sources.append({**_clean(b), **count, "samples": samples})
            for row in samples:
                rid = "instance:row:" + b["dataset_id"] + ":" + str(row["row_no"])
                shown.add((b["dataset_id"], row["row_no"]))
                value = "空值" if row["empty"] else str(row["raw_value"])
                nodes.setdefault(rid, {"id": rid, "kind": "instance_row", "level": "row", "title": "原表第" + str(row["source_row"]) + "行", "label": "原表第" + str(row["source_row"]) + "行", "summary": b["sheet"] + " · 完整原行可穿透核对", "href": row["href"], "source_row": row["source_row"], "row_no": row["row_no"], **common})
                edge(b["source_id"], rid, "原值：" + value[:35], "instance_value")
        return {"standard_id": standard_id, "title": standard["title"], "nodes": list(nodes.values()), "edges": list(edges.values()), "sources": sources, "summary": stats, "total_rows": stats["records"], "total_mappings": total, "shown_rows": len(shown), "omitted_rows": max(0, stats["records"] - len(shown)), "source_counts": {"demo": stats["demo_records"], "user": stats["user_records"]}, "page": page, "page_size": page_size, "total": total, "scope_note": NOTE + "图中每个字段最多展示2条原行样例；其余记录可从原表继续查看。"}
