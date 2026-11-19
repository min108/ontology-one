"""Recover authors of an experience revision from its retained business versions and signed requests."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from backend import db
from backend.security import store
from backend.security.context import Principal, require_context

PROPOSAL_REASONS = {"人工采用AI修订建议": "revise", "人工采用AI拆分建议": "split", "人工采用AI归并建议": "merge"}


@dataclass
class Authors:
    ids: set[str] = field(default_factory=set)
    unverified: str = ""

    def merge(self, other: Authors) -> None:
        self.ids.update(other.ids)
        self.unverified = max(self.unverified, other.unverified)

    @classmethod
    def from_draft(cls, item: dict) -> Authors:
        return cls(set(item.get("draft_editor_ids") or []), item.get("unverified_edit_at") or "")

    def save_draft(self, item: dict) -> None:
        item["draft_editor_ids"] = sorted(self.ids)
        item["draft_authorship_tracked"] = not self.unverified
        if self.unverified:
            item["unverified_edit_at"] = self.unverified
        else:
            item.pop("unverified_edit_at", None)

    def save_review(self, meta: dict) -> None:
        meta["editor_ids"] = list(dict.fromkeys([*(meta.get("editor_ids") or []), *sorted(self.ids)]))
        meta["experience_authorship_tracked"] = not self.unverified
        if self.unverified:
            meta["unverified_experience_edit_at"] = self.unverified
        else:
            meta.pop("unverified_experience_edit_at", None)

    def unresolved_for(self, person: Principal) -> bool:
        if not self.unverified:
            return False
        with store.control_connection() as control, control.cursor() as cursor:
            cursor.execute("SELECT created_at FROM sec_users WHERE id=%s", (person.user_id,))
            account = cursor.fetchone()
        return not account or account["created_at"] <= datetime.fromisoformat(self.unverified).timestamp()


@dataclass(frozen=True)
class Edit:
    stamp: str
    method: str
    paths: tuple[str, ...]
    activity: str = ""
    detail: str = ""
    next_stamp: str = ""


def section_facts(sections: list[dict] | None) -> list[tuple]:
    # Section IDs and timestamps can change on a compatibility save without a content change.
    return [
        (s.get("title"), s.get("content"), s.get("origin", "source_synthesis"), s.get("source_refs") or [])
        for s in sections or []
    ]


def draft_changed(before: dict, after: dict) -> bool:
    return any(
        (before.get(key) or "") != (after.get(key) or "")
        for key in ("title", "summary", "content", "questions", "boundary")
    ) or (
        after.get("article_sections") is not None
        and section_facts(before.get("article_sections")) != section_facts(after.get("article_sections"))
    )


def _signed_editors(conn: db.Connection, source_id: str, events: list[Edit]) -> Authors:
    result = Authors()
    result.unverified = max((event.stamp for event in events if not event.paths), default="")
    events = [event for event in events if event.paths]
    if not events:
        return result
    times = defaultdict(list)
    for edit in events:
        for path in edit.paths:
            times[(edit.method, path)].append((edit.stamp, datetime.fromisoformat(edit.stamp).timestamp()))
    paths = sorted({path for _, path in times})
    stamps = [point for group in times.values() for _, point in group]
    params = ",".join(["%s"] * len(paths))
    proven = set()
    with store.control_connection() as control, control.cursor() as cursor:
        cursor.execute(
            "SELECT s.actor_id,s.resource,JSON_UNQUOTE(JSON_EXTRACT(s.details,'$.method')) method,"
            "s.created_at started,f.created_at finished FROM sec_audit s JOIN sec_audit f "
            "ON f.request_id=s.request_id AND f.tenant_id=s.tenant_id "
            "AND f.resource=s.resource AND f.actor_id=s.actor_id "
            f"WHERE s.tenant_id=%s AND s.resource IN ({params}) AND s.action='business_request' AND f.action=s.action "
            "AND s.outcome='started' AND f.outcome='success' AND s.created_at<=%s AND f.created_at>=%s",
            (require_context().tenant_id, *paths, max(stamps), min(stamps)),
        )
        for row in cursor:
            for stamp, point in times.get((row["method"], row["resource"]), ()):
                if row["started"] <= point <= row["finished"]:
                    result.ids.add(row["actor_id"])
                    proven.add(stamp)
        for edit in events:
            if edit.stamp in proven:
                continue
            # The workbench also recorded the authenticated username in the same business transaction.
            actor = _activity_actor(conn, source_id, edit) if edit.activity else None
            if actor:
                cursor.execute(
                    "SELECT id FROM sec_users WHERE username=%s AND created_at<=%s",
                    (actor, datetime.fromisoformat(edit.stamp).timestamp()),
                )
                user = cursor.fetchone()
                if user:
                    result.ids.add(user["id"])
                    continue
            result.unverified = max(result.unverified, edit.stamp)
    return result


def _activity_actor(conn: db.Connection, source_id: str, edit: Edit) -> str | None:
    query = "SELECT actor FROM activities WHERE entity_id=? AND action=? AND detail=? AND created_at>=?"
    args = (source_id, edit.activity, edit.detail, edit.stamp)
    rows = (
        list(conn.execute(query + " AND created_at<?", (*args, edit.next_stamp)))
        if edit.next_stamp
        else list(conn.execute(query, args))
    )
    return rows[0]["actor"] if len(rows) == 1 else None


def _source_versions(
    conn: db.Connection, record: dict, since: str, through: str | None, *, candidates: bool
) -> Iterator[tuple[str, str, str, dict, dict]]:
    fields = ["draft", "knowledge_candidates", "proposals"] if candidates else ["draft"]
    columns = ",".join(f"json_extract(snapshot,'$.payload.{field}') {field}" for field in fields)
    rows = conn.execute(
        f"SELECT reason,created_at,{columns} FROM versions WHERE entity_id=? AND created_at>=? "
        "ORDER BY version DESC,id DESC",
        (record["id"], since),
    )
    after = record["payload"]
    next_stamp = ""
    for row in rows:
        before = {field: json.loads(row[field] or "null") for field in fields}
        if not through or row["created_at"] <= through:
            yield row["reason"], row["created_at"], next_stamp, before, after
        # A version holds the state immediately before this operation.
        after, next_stamp = before, row["created_at"]


def draft_editors(conn: db.Connection, record: dict) -> Authors:
    draft = record["payload"].get("draft") or {}
    if not draft or "draft_editor_ids" in draft:
        return Authors.from_draft(draft)
    linked = next(
        (
            i["adopted_entity_id"]
            for i in record["payload"].get("knowledge_candidates", [])
            if i.get("legacy_draft") and i.get("adopted_entity_id")
        ),
        record["payload"].get("adopted_entity_id"),
    )
    since = _last_publication(conn, linked) if linked else ""
    path = "/api/experience/" + record["id"] + "/draft"
    return _signed_editors(
        conn,
        record["id"],
        [
            Edit(stamp, "PATCH", (path,))
            for reason, stamp, _, before, after in _source_versions(conn, record, since, None, candidates=False)
            if reason == "人工编辑经验草稿" and draft_changed(before.get("draft") or {}, after.get("draft") or {})
        ],
    )


def _last_publication(conn: db.Connection, asset_id: str, through: str | None = None) -> str:
    query = "SELECT MAX(created_at) FROM activities WHERE entity_id=? AND action='发布成果'"
    if through:
        return conn.execute(query + " AND created_at<=?", (asset_id, through)).fetchone()[0] or ""
    return conn.execute(query, (asset_id,)).fetchone()[0] or ""


def _by_id(items: list[dict] | None) -> dict:
    return {item["id"]: item for item in items or [] if isinstance(item, dict) and item.get("id")}


def _applied_proposals(before: dict, after: dict, expected: str | None) -> Iterator[tuple[dict, dict]]:
    old = _by_id(before.get("proposals"))
    inputs = _by_id(before.get("knowledge_candidates"))
    outputs = _by_id(after.get("knowledge_candidates"))
    for proposal in after.get("proposals") or []:
        previous = old.get(proposal["id"], {})
        operation = proposal.get("operation")
        if (
            previous.get("status") != "pending"
            or proposal.get("status") != "applied"
            or operation not in {"revise", "split", "merge"}
            or (expected and operation != expected)
            or previous.get("operation") != operation
            or previous.get("input_ids") != proposal.get("input_ids")
        ):
            continue
        input_ids = proposal.get("input_ids") or []
        result_ids = proposal.get("result_ids") or []
        if (
            not input_ids
            or not result_ids
            or not set(input_ids) <= inputs.keys()
            or not set(result_ids) <= outputs.keys()
        ):
            continue
        primary = result_ids[0]
        if primary not in input_ids or outputs[primary].get("revision", 1) <= inputs[primary].get("revision", 1):
            continue
        if not all(previous.get("input_revisions", {}).get(i) == inputs[i].get("revision", 1) for i in input_ids):
            continue
        if operation == "split" and not all(
            i not in inputs and outputs[i].get("split_from") == primary for i in result_ids[1:]
        ):
            continue
        if operation == "merge" and not all(
            outputs.get(i, {}).get("merged_into") == primary for i in input_ids if i != primary
        ):
            continue
        yield proposal, inputs


def _timeline(
    conn: db.Connection, source: dict, since: str, through: str
) -> list[tuple[str, str, str, dict, dict, list[tuple[dict, dict]]]]:
    changes = []
    seen = set()
    for reason, stamp, next_stamp, before, after in _source_versions(conn, source, since, through, candidates=True):
        operation = PROPOSAL_REASONS.get(reason)
        applied = list(_applied_proposals(before, after, operation))
        seen.update(proposal["id"] for proposal, _ in applied)
        changes.append((reason, stamp, next_stamp, before, after, applied))
    # Some old backups kept the applied proposal and its input/output ids but lost the corresponding version.
    candidates = _by_id(source["payload"].get("knowledge_candidates"))
    for proposal in source["payload"].get("proposals") or []:
        stamp = proposal.get("applied_at") or ""
        outputs = proposal.get("result_ids") or []
        if proposal["id"] in seen or proposal.get("status") != "applied" or not since < stamp <= through or not outputs:
            continue
        next_stamp = min((at for _, at, *_ in changes if at > stamp), default=through)
        changes.append(("applied", stamp, next_stamp, {}, {}, [(proposal, candidates)]))
    return sorted(changes, key=lambda change: change[1], reverse=True)


def _lineage_requests(conn: db.Connection, source: dict, candidate_id: str, since: str, through: str) -> list[Edit]:
    # Walk backward. A split child only inherits its parent's edits up to the split time.
    # A merge follows every input, bounded by each already-adopted input's own release.
    wanted = {candidate_id: since}
    legacy_ids = {i["id"] for i in source["payload"].get("knowledge_candidates", []) if i.get("legacy_draft")}
    base = "/api/experience/" + source["id"]
    events = []
    for reason, stamp, next_stamp, before, after, applied in _timeline(conn, source, since, through):
        active = {ident for ident, cutoff in wanted.items() if stamp > cutoff}
        if not active:
            break
        if applied:
            for proposal, inputs in applied:
                used = active.intersection(proposal["result_ids"])
                if not used:
                    continue
                events.append(
                    Edit(
                        stamp,
                        "POST",
                        (base + "/proposals/" + proposal["id"] + "/decision",),
                        "确认经验调整",
                        proposal["instruction"],
                        next_stamp,
                    )
                )
                floor = min(wanted.pop(i) for i in used)
                input_ids = proposal.get("input_ids") or []
                if not input_ids or proposal["result_ids"][0] not in input_ids or not set(input_ids) <= inputs.keys():
                    # An applied result is real, but an incomplete parent list cannot prove independence.
                    events.append(Edit(stamp, "", ()))
                for ident in input_ids:
                    asset_id = inputs.get(ident, {}).get("adopted_entity_id")
                    bound = max(floor, _last_publication(conn, asset_id, stamp)) if asset_id else floor
                    wanted[ident] = min(wanted.get(ident, bound), bound)
        elif reason in {"人工核对经验点：save", "人工核对经验点：adopt"}:
            old, new = _by_id(before.get("knowledge_candidates")), _by_id(after.get("knowledge_candidates"))
            for ident in active & old.keys() & new.keys():
                a, b = old[ident], new[ident]
                if a.get("revision") == b.get("revision"):
                    continue
                adopted = reason.endswith("adopt") and b.get("adopted_revision") == b.get("revision")
                if adopted or draft_changed(a, b) or a.get("review_note") != b.get("review_note"):
                    paths = (base + "/candidates/" + ident + "/review",)
                    if adopted and ident in legacy_ids:
                        paths += (base + "/adopt",)
                    events.append(Edit(stamp, "POST", paths, "核对经验点", b["title"], next_stamp))
        elif (
            reason == "人工编辑经验草稿"
            and active & legacy_ids
            and draft_changed(before.get("draft") or {}, after.get("draft") or {})
        ):
            events.append(Edit(stamp, "PATCH", (base + "/draft",)))
    return events


def adopted_editors(conn: db.Connection, asset: dict) -> Authors:
    """Recover an already-adopted revision even when it was submitted before authorship tracking."""
    payload = asset["payload"]
    meta = payload.get("r2_review") or {}
    if (
        asset.get("kind") != "knowledge"
        or not payload.get("experience_candidate_id")
        or not payload.get("source_material_id")
        or asset.get("status") == "published"
        or meta.get("cycle_closed")
        or meta.get("experience_authorship_tracked")
    ):
        return Authors()
    fallback = Authors(unverified=meta.get("unverified_experience_edit_at") or "")
    dates = conn.execute(
        "SELECT MAX(CASE WHEN action IN ('经验形成知识草稿','经验稿修订知识') THEN created_at END) adopted, "
        "MAX(CASE WHEN action='发布成果' THEN created_at END) published FROM activities WHERE entity_id=?",
        (asset["id"],),
    ).fetchone()
    since, through = dates["published"] or "", dates["adopted"]
    if not through:
        through = conn.execute(
            "SELECT MAX(created_at) FROM versions WHERE entity_id=? "
            "AND reason IN ('经验记录首次形成知识草稿','从经验萃取记录修订同一知识，重新进入草稿审核')",
            (asset["id"],),
        ).fetchone()[0]
    if not through or since >= through:
        return fallback
    row = conn.execute(
        "SELECT e.* FROM entities e WHERE e.id=? AND e.kind='material' AND EXISTS "
        "(SELECT 1 FROM relations r WHERE r.source=? AND r.target=e.id "
        "AND r.type='derived_from' AND r.status='confirmed')",
        (payload["source_material_id"], asset["id"]),
    ).fetchone()
    if not row:
        return Authors(unverified=max(fallback.unverified, through))
    source = db.entity_json(row)
    candidate_id = payload["experience_candidate_id"]
    candidate = _by_id(source["payload"].get("knowledge_candidates")).get(candidate_id, {})
    if (
        source["payload"].get("record_type") != "experience_session"
        or candidate.get("adopted_entity_id") != asset["id"]
    ):
        return Authors(unverified=max(fallback.unverified, through))
    edits = _lineage_requests(conn, source, candidate_id, since, through)
    return _signed_editors(conn, source["id"], edits) if edits else fallback


def pending_editors(conn: db.Connection, source: dict, item: dict) -> Authors:
    known = Authors.from_draft(item)
    if not item.get("draft_authorship_tracked"):
        asset_id = item.get("adopted_entity_id")
        since = _last_publication(conn, asset_id) if asset_id else ""
        edits = _lineage_requests(conn, source, item["id"], since, db.now())
        if edits:
            history = _signed_editors(conn, source["id"], edits)
            history.ids.update(known.ids)
            return history
    return known
