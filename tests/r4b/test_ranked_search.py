"""Visibility, precision and pagination of the shared tenant search."""

import json
import os
import unittest
from contextlib import closing
from dataclasses import replace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import db, provenance
from backend.core.errors import ApiError
from backend.infra.search_index import rebuild_index
from backend.kernel import search
from backend.security import store, visibility
from backend.security.context import Principal, use_context
from tests.r2 import test_build as construction


def _login(name: str) -> Principal:
    token = store.login(name, "SyntheticFixture9!", "127.0.0.1", "ranked-search")
    return store.authenticate_session(token, "ranked-search")[0]


def _entity(
    conn: db.Connection,
    ident: str,
    kind: str,
    title: str,
    *,
    status: str = "published",
    summary: str = "",
    source: str = "user",
    payload: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,"
        "created_at,updated_at,version) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
        (
            ident,
            kind,
            title,
            summary,
            "搜索测试",
            "检索归属",
            status,
            source,
            json.dumps(payload or {}, ensure_ascii=False),
            db.now(),
            db.now(),
        ),
    )


def _material(conn: db.Connection, ident: str, status: str, lines: list[str]) -> list[dict]:
    # These are genuine immutable anchors, backed by one current reading and
    # the full original. Each test can query the evidence independently.
    _entity(conn, ident, "material", "分段原件" + ident[-1], status=status)
    source = provenance.ensure_source_version(conn, ident, "".join(lines))
    refs = []
    start = 0
    for position, line in enumerate(lines, 1):
        # Exercise the 0.7 VARCHAR(255) compatibility path as well.
        chunk_id = ident + ("长" * 195 if position == 1 else "") + "-" + str(position)
        end = start + len(line)
        conn.execute(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
            (chunk_id, ident, position, "原句", line, "第" + str(position) + "段", "confirmed"),
        )
        refs.append(provenance.register_chunk_anchor(conn, ident, chunk_id, source["id"], start, end))
        start = end
    conn.execute(
        "INSERT INTO reading_versions VALUES(?,?,?,?,?,?,?)",
        ("READ-" + ident, ident, source["id"], 1, json.dumps(refs, ensure_ascii=False), "已确认", db.now()),
    )
    return refs


def _examples(conn: db.Connection) -> None:
    hidden = _material(
        conn,
        "M-RANK-A",
        "draft",
        ["私有原文编号云杉，北岸检索段甲。", "北岸检索段乙。", "北岸检索段丙。", "北岸检索段丁。"],
    )
    public = _material(conn, "M-RANK-B", "published", ["河流索引公开引文，北岸检索段戊。"])
    _entity(
        conn,
        "R-RANK",
        "rule",
        "松柏现金核验办法",
        summary="业务规则摘要。",
        payload={
            "aliases": ["北境同义登记", "冬季说明"],
            "source_refs": [*hidden[:1], *public],
            "judgement": {"requirement": "收款账户和付款人应当分别登记。"},
            "r2_review": {"private": "never expose"},
        },
    )
    _entity(conn, "R-TITLE", "rule", "北境同义登记示例", summary="松柏现金核验办法在正文中提及。")
    _entity(conn, "D-RANK", "data", "蓝图排名字段", summary="词序测试资料", status="draft")
    _entity(conn, "A-DRAFT", "rule", "雪松等级", status="draft")
    _entity(conn, "B-PUB", "rule", "雪松等级")
    _entity(conn, "C-LAW", "rule", "雪松等级", source="regulation")
    _entity(conn, "X-ARCH", "rule", "遗存公开索引", status="archived")
    _entity(conn, "X-OFF", "rule", "遗存停用索引", payload={"active_asset": False})
    _entity(conn, "ORG-HIDDEN", "organization", "隐藏根节点纹理", payload={"hide_in_graph": True})
    _entity(conn, "REPORT-X", "report", "某人私有的排名")
    _entity(conn, "IGNORED", "rule", "付款账户与收款专户", summary="本项仅确定书面发布程序。")
    _entity(
        conn,
        "P-REFS",
        "knowledge",
        "可见记录间接引用",
        payload={
            "content": "保留的自有内容",
            "steps": [
                {
                    "name": "业务步骤",
                    "source_refs": [
                        public[0],
                        {"kind": "report", "material_id": "REPORT-X", "quote": "私有报告引文"},
                        {"entity_id": "X-OFF", "quote": "停用要素缓存泄漏"},
                        {"material_id": "M-RANK-B", "tenant_id": "hq", "quote": "外部机构伪造引文"},
                    ],
                }
            ],
            "evidence": [{"artifact_id": "ART-0123456789abcdef", "title": "私有报告标题"}],
            "source_chunk_ids": [hidden[0]["chunk_id"], "不存在的片段"],
        },
    )
    _entity(conn, "S-STOP-PRE", "knowledge", "Tornado Harbor")
    _entity(conn, "S-STOP-IN", "knowledge", "Untold Harbor")
    rebuild_index(conn)


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in ranked MySQL search")
class RankedSearchMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        construction.BuildMySQL.setUpClass.__func__(cls)
        cls.addClassCleanup(construction.BuildMySQL.tearDownClass.__func__, cls)
        cls.person = _login("fixture.builder")
        owner = _login("fixture.superadmin")
        ident = store.create_user(
            owner,
            username="search.viewer",
            display_name="检索读者",
            tenant_id="dept-01",
            role="modeler",
            password="SyntheticFixture9!",
            must_change_password=False,
        )
        # The retained administration API provisions three roles. Load a
        # pre-existing viewer identity before its first real session (BA6).
        with store.control_connection() as conn, conn.cursor() as cursor:
            cursor.execute("UPDATE sec_users SET role=%s WHERE id=%s", ("viewer", ident))
        cls.viewer = _login("search.viewer")
        with use_context(cls.person), closing(db.connect()) as conn, conn:
            _examples(conn)
        with use_context(owner), closing(db.connect()) as conn, conn:
            _entity(
                conn,
                "FED-SHARED",
                "rule",
                "协作机构线索",
                payload={"aliases": ["不可外发别名"], "rule_text": "后台未公开文字"},
            )
            _entity(conn, "FED-PRIVATE", "rule", "协作机构线索")
            _entity(conn, "FED-DRAFT", "rule", "协作机构线索", status="draft")
            conn.executemany(
                "INSERT INTO entity_access(entity_id,visibility) VALUES(?,?)",
                [("FED-SHARED", "headquarters"), ("FED-DRAFT", "headquarters")],
            )
            rebuild_index(conn)

    def setUp(self) -> None:
        self.context = use_context(self.person)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.conn = db.connect()
        self.addCleanup(self.conn.close)

    def test_unbound_database_never_derives_visibility_from_a_user_parameter(self) -> None:
        with closing(db._raw_connection(self.names["hq"])) as raw, self.assertRaises(ApiError):
            visibility.scope_for(db.Connection(raw, self.person))

    def test_scoped_session_may_not_supply_a_different_identity(self) -> None:
        with use_context(replace(self.person, user_id="other")), self.assertRaises(ApiError):
            visibility.scope_for(self.conn)

    def test_id_exact_match_returns_current_entity_and_canonical_link(self) -> None:
        ident = "DEMO07-FIELD-CONTRACT_ID"
        first = search.search(self.conn, ident)["items"][0]
        self.assertEqual(
            (first["id"], first["entity_id"], first["doc_type"], first["match"]), (ident, ident, "element", "id")
        )
        self.assertEqual(first["href"], "/ontology/elements/" + ident)

    def test_kind_filter_can_narrow_but_cannot_widen_visibility(self) -> None:
        ident = "DEMO07-RULE-PAYMENT"
        self.assertEqual(search.search(self.conn, ident, kind="data")["total"], 0)
        self.assertEqual(search.search(self.conn, ident, published_only=True)["total"], 0)
        self.assertEqual(search.search(self.conn, ident, kind="rule")["items"][0]["status"], "draft")

    def test_one_character_matches_only_beginning_of_title(self) -> None:
        hits = search.search(self.conn, "松")["items"]
        self.assertTrue(hits)
        self.assertTrue(all(item["title"].startswith("松") for item in hits))
        self.assertEqual(search.search(self.conn, "≠")["total"], 0)

    def test_irrelevant_overlap_is_not_an_answer(self) -> None:
        self.assertEqual(search.search(self.conn, "星际合同时差投影")["items"], [])

    def test_sql_wildcards_are_literal(self) -> None:
        for query in ("%", "A%_9", "%合同%"):
            with self.subTest(query=query):
                self.assertEqual(search.search(self.conn, query)["total"], 0)

    def test_legacy_empty_query_orders_entities_and_paginates_even_past_end(self) -> None:
        first = search.search(self.conn, "", page=1, page_size=3)
        second = search.search(self.conn, "", page=2, page_size=3)
        beyond = search.search(self.conn, "", page=300, page_size=3)
        self.assertEqual(first["total"], second["total"])
        self.assertEqual(beyond["total"], first["total"])
        self.assertEqual(beyond["items"], [])
        self.assertFalse({hit["doc_id"] for hit in first["items"]} & {hit["doc_id"] for hit in second["items"]})
        self.assertTrue(all(hit["doc_type"] == "element" for hit in first["items"]))

    def test_legacy_payload_requires_both_explicit_request_and_privileged_scope(self) -> None:
        ident = "DEMO07-FIELD-CONTRACT_ID"
        self.assertNotIn("payload", search.search(self.conn, ident)["items"][0])
        self.assertIn("definition", search.search(self.conn, ident, legacy_payload=True)["items"][0]["payload"])
        self.assertFalse(
            search.search(self.conn, ident, published_only=True, legacy_payload=True)["items"][0].get("payload")
        )

    def test_private_reports_are_not_returned_by_any_scope(self) -> None:
        self.assertEqual(search.search(self.conn, "", kind="artifact", history="all")["items"], [])

    def test_alias_and_registered_title_beat_a_substring_or_body_mention(self) -> None:
        for q, match in (
            ("北境同义登记", "alias"),
            ("松柏现金核验办法", "title"),
            ("现金核验", "partial"),
            ("松柏现金", "prefix"),
        ):
            with self.subTest(q=q):
                hits = search.search(self.conn, q)["items"]
                self.assertEqual((hits[0]["id"], hits[0]["match"]), ("R-RANK", match))
                self.assertIn(q, hits[0]["highlight"])

    def test_publication_and_actual_regulation_break_equal_name_ties(self) -> None:
        hits = search.search(self.conn, "雪松等级")["items"]
        self.assertEqual([h["id"] for h in hits], ["C-LAW", "B-PUB", "A-DRAFT"])
        self.assertGreater(hits[0]["score"], hits[1]["score"])

    def test_multiple_business_facets_require_content_beyond_header(self) -> None:
        ids = [h["id"] for h in search.search(self.conn, "付款账户和收款专户是否分别登记？")["items"]]
        self.assertIn("R-RANK", ids)
        self.assertNotIn("IGNORED", ids)

    def test_real_anchors_give_both_rule_and_at_most_two_chunks_before_pagination(self) -> None:
        result = search.search(self.conn, "北岸检索段", page_size=1)
        self.assertEqual(result["total"], 4)
        pages = [
            search.search(self.conn, "北岸检索段", page=n, page_size=1, legacy_payload=True)["items"][0]
            for n in range(1, 5)
        ]
        self.assertEqual(len({h["doc_id"] for h in pages}), 4)
        self.assertEqual([h["entity_id"] for h in pages].count("M-RANK-A"), 2)
        rule = next(h for h in pages if h["id"] == "R-RANK")
        self.assertEqual(rule["doc_type"], "element")
        for h in pages:
            if h["doc_type"] == "chunk":
                self.assertIn("北岸检索段", h["highlight"])
                self.assertIn(
                    h["chunk_id"],
                    [
                        r["id"]
                        for r in self.conn.execute("SELECT id FROM chunks WHERE material_id=?", (h["entity_id"],))
                    ],
                )
                self.assertEqual(h["payload"], {"chunk_id": h["chunk_id"], "material_id": h["entity_id"]})
        self.assertEqual(search.search(self.conn, "北岸检索段", include_chunks=False)["total"], 1)

    def test_long_legacy_chunk_id_remains_a_navigable_actual_chunk(self) -> None:
        hits = search.search(self.conn, "河流索引公开引文")["items"]
        chunk = next(h for h in hits if h["doc_type"] == "chunk")
        self.assertTrue(chunk["doc_id"].startswith("chunk-sha256:"))
        self.assertGreater(len(chunk["id"]), 194)
        self.assertEqual(chunk["chunk_id"], chunk["id"])
        from urllib.parse import parse_qs, urlsplit

        url = urlsplit(chunk["href"])
        self.assertEqual(url.path, "/materials/M-RANK-B")
        self.assertEqual(parse_qs(url.query)["chunk"], [chunk["id"]])

    def test_viewer_search_never_matches_quotes_from_hidden_sources_but_keeps_published_evidence(self) -> None:
        self.assertEqual(search.search(self.conn, "私有原文编号云杉", kind="rule")["items"][0]["id"], "R-RANK")
        with use_context(self.viewer), closing(db.connect()) as conn:
            for q in ("私有原文编号云杉", "蓝图排名字段"):
                self.assertEqual(search.search(conn, q)["total"], 0)
            public = search.search(conn, "河流索引公开引文", kind="rule")["items"][0]
            self.assertEqual(public["entity_id"], "R-RANK")
            self.assertIn("公开引文", public["highlight"])
            self.assertNotIn("私有原文", str(public))
            view = visibility.visible_entity(conn, "R-RANK")
            self.assertEqual(view["payload"], {})
            self.assertEqual(visibility.visible_entities(conn, ["D-RANK", "REPORT-X"]), {})

    def test_history_scope_only_widens_for_draft_readers(self) -> None:
        self.assertEqual(search.search(self.conn, "遗存")["total"], 0)
        self.assertEqual(search.search(self.conn, "遗存", history="all")["total"], 2)
        with use_context(self.viewer), closing(db.connect()) as conn:
            self.assertEqual(search.search(conn, "遗存", history="all")["total"], 0)

    def test_fixed_external_metadata_neither_matches_aliases_nor_hidden_graph_root(self) -> None:
        for q in ("北境同义登记", "冬季说明", "隐藏根节点纹理", "私有原文编号云杉"):
            with self.subTest(q=q):
                ids = [h["id"] for h in search.search(self.conn, q, metadata_only=True)["items"]]
                self.assertNotIn("R-RANK", ids)
                self.assertNotIn("ORG-HIDDEN", ids)
        hits = search.search(self.conn, "松柏现金核验办法", metadata_only=True, legacy_payload=True)["items"]
        self.assertEqual(hits[0]["summary"], "业务规则摘要。")
        self.assertEqual(hits[0]["payload"], {})

    def test_cross_institution_discovery_uses_live_grant_and_actual_database(self) -> None:
        spoof = {"id": "hq", "visibility": "private"}
        with db.tenant_connection("hq", readonly=True) as cross:
            result = search.search_federated(cross, spoof, q="协作机构线索")
            self.assertEqual((result["total"], result["items"][0]["id"]), (1, "FED-SHARED"))
            self.assertEqual(result["items"][0]["visibility"], "headquarters")
            self.assertEqual(search.search_federated(cross, spoof, q="后台未公开文字")["total"], 0)
            self.assertEqual(search.search_federated(cross, spoof, q="不可外发别名")["total"], 0)
            self.assertEqual(
                search.search_federated(cross, spoof, q="协作机构线索", limit=0), {"items": [], "total": 1}
            )
            with self.assertRaises(ApiError):
                search.search_federated(cross, {"id": "dept-01", "visibility": "private"}, q="检索")
        self.assertEqual(search.search(self.conn, "协作机构线索")["total"], 0)

    def test_each_facet_ignores_only_its_own_filter(self) -> None:
        result = search.facet_counts(
            self.conn, "雪松等级", kind="rule", status="published", source_kind="user", domain="搜索测试"
        )
        self.assertEqual(
            result,
            {
                "total": 1,
                "kind": {"rule": 1},
                "status": {"published": 1, "draft": 1},
                "source_kind": {"regulation": 1, "user": 1},
                "domain": {"搜索测试": 1},
            },
        )
        self.assertEqual(search.facet_counts(self.conn, "雪松等级", org_id="不存在")["total"], 0)
        self.assertEqual(
            search.search(
                self.conn,
                "雪松等级",
                pillar="asset",
                module_id="MOD-DEFAULT",
                kind="data,rule",
                org_id="检索归属",
                source_kind="regulation",
            )["total"],
            1,
        )

    def test_privileged_legacy_detail_and_search_hide_indirect_private_references(self) -> None:
        detail = visibility.visible_entity(self.conn, "P-REFS")["payload"]
        hit = search.search(self.conn, "P-REFS", legacy_payload=True)["items"][0]
        self.assertEqual(hit["payload"], detail)
        self.assertEqual(detail["content"], "保留的自有内容")
        self.assertEqual(len(detail["steps"][0]["source_refs"]), 1)
        self.assertEqual(detail["steps"][0]["source_refs"][0]["material_id"], "M-RANK-B")
        self.assertEqual(detail["evidence"], [])
        self.assertEqual(len(detail["source_chunk_ids"]), 1)
        for hidden in ("REPORT-X", "私有报告", "ART-0123456789abcdef", "X-OFF", "外部机构伪造引文", "不存在的片段"):
            self.assertNotIn(hidden, str(hit))

    def test_forged_role_on_matching_raw_connection_is_revalidated(self) -> None:
        forged = replace(self.person, role="admin")
        with (
            use_context(forged),
            closing(db._raw_connection(self.names["dept-01"])) as raw,
            self.assertRaises(HTTPException) as caught,
        ):
            visibility.scope_for(db.Connection(raw, forged))
        self.assertEqual(caught.exception.status_code, 401)

    def test_native_stopword_only_falls_back_to_real_title_prefix(self) -> None:
        # "to" is an InnoDB default English stopword, also for ngram indexing.
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM search_index WHERE MATCH(title,aliases) AGAINST (? IN NATURAL LANGUAGE MODE) "
                "OR MATCH(body) AGAINST (? IN NATURAL LANGUAGE MODE)",
                ("to", "to"),
            ).fetchone()[0],
            0,
        )
        hits = search.search(self.conn, "to")["items"]
        self.assertIn("S-STOP-PRE", [h["id"] for h in hits])
        self.assertNotIn("S-STOP-IN", [h["id"] for h in hits])
        self.assertTrue(all(h["title"].casefold().startswith("to") for h in hits))

    def test_material_title_alone_does_not_fill_results_with_unrelated_chunks(self) -> None:
        hits = search.search(self.conn, "分段原件")["items"]
        self.assertEqual({h["id"] for h in hits}, {"M-RANK-A", "M-RANK-B"})
        chunk = next(h for h in search.search(self.conn, "河流索引公开引文")["items"] if h["doc_type"] == "chunk")
        self.assertEqual(search.search(self.conn, chunk["id"])["items"][0]["match"], "id")
        self.assertEqual(search.search(self.conn, chunk["doc_id"])["items"][0]["id"], chunk["chunk_id"])

    def _invoke(self, client: TestClient, capability: str, body: dict) -> dict:
        response = client.post(f"/api/v2/capabilities/{capability}:invoke", json=body)
        self.assertEqual(response.status_code, 200, response.text[:400])
        return response.json()

    def test_large_uploaded_original_is_readable_from_detail_and_not_copied_into_semantic_search(self) -> None:
        client = construction.BuildMySQL.login(self, "fixture.builder")
        original = "这是一段需要按页核对的正式原始材料。" * 5200 + "末页验证标识。"
        self.assertGreater(len(original.encode()), 262144)
        uploaded = client.post(
            "/api/materials/upload", files={"file": ("联册容量核对.txt", original.encode(), "text/plain")}
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text[:300])
        ident = uploaded.json()["id"]
        result = self._invoke(client, "semantic.search", {"q": ident})
        hit = result["items"][0]
        self.assertEqual((result["total"], hit["entity_id"], hit["doc_type"]), (1, ident, "element"))
        self.assertNotIn("original_text", hit["payload"])
        self.assertNotIn("source_blocks", hit["payload"])
        self.assertNotIn("末页验证标识", str(hit))
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 12000)
        old = client.get("/api/search", params={"q": ident}).json()["items"][0]
        self.assertEqual((old["id"], old["kind"]), (ident, "material"))
        self.assertIsInstance(old["payload"], dict)
        self.assertNotIn("doc_id", old)
        source = client.get("/api/materials/" + ident + "/source")
        self.assertEqual(source.status_code, 200, source.text[:200])
        self.assertEqual(source.json()["text"], original)

    def test_full_semantic_page_keeps_every_hit_with_long_multibyte_titles_and_summaries(self) -> None:
        client = construction.BuildMySQL.login(self, "fixture.builder")
        for i in range(104):
            _entity(
                self.conn,
                "WIDE-" + str(i).zfill(3),
                "data",
                "百行检索回执" + str(i) + "广" * 235,
                summary="超长摘要条目。" * 420,
                payload={"unit": "份", "definition": "单据份数", "profile": {"bulk": "超大业务资料" * 900}},
            )
        self.conn.commit()
        first = self._invoke(client, "semantic.search", {"q": "百行检索回执", "page_size": 100})
        second = self._invoke(client, "semantic.search", {"q": "百行检索回执", "page_size": 100, "page": 2})
        self.assertEqual(
            (first["total"], first["page_size"], len(first["items"]), len(second["items"])), (104, 100, 100, 4)
        )
        all_hits = first["items"] + second["items"]
        self.assertEqual({h["id"] for h in all_hits}, {"WIDE-" + str(i).zfill(3) for i in range(104)})
        self.assertTrue(
            all(h["title"].startswith("百行检索回执") and h["highlight"] and h["summary"] for h in all_hits)
        )
        self.assertTrue(all(h["payload"].get("unit") == "份" and "profile" not in h["payload"] for h in all_hits))
        from backend.capabilities.read import SearchOutput

        self.assertLessEqual(len(SearchOutput.model_validate(first).model_dump_json().encode()), 262144)

    def test_question_facets_require_body_even_for_an_exact_title_and_resolve_still_finds_its_name(self) -> None:
        client = construction.BuildMySQL.login(self, "fixture.builder")
        query = "付款账户与收款专户是否为同一账户？"
        _entity(self.conn, "Q-NAME", "rule", query, summary="本条只要求保留纸质票据。")
        _entity(
            self.conn,
            "Q-ANSWER",
            "rule",
            "支付核对要求",
            summary="核对付款账号与收款账户是否属于同一单位或者个人的账户。",
        )
        self.conn.commit()
        hits = self._invoke(client, "semantic.search", {"q": query})["items"]
        self.assertIn("Q-ANSWER", [h["id"] for h in hits])
        self.assertNotIn("Q-NAME", [h["id"] for h in hits])
        by_id = self._invoke(client, "semantic.search", {"q": "Q-NAME"})["items"][0]
        self.assertEqual((by_id["id"], by_id["match"]), ("Q-NAME", "id"))
        names = self._invoke(client, "semantic.resolve", {"text": query})["items"]
        self.assertEqual((names[0]["id"], names[0]["match"]), ("Q-NAME", "title"))

    def test_highlights_use_actual_late_body_facets_and_alias_prefix(self) -> None:
        client = construction.BuildMySQL.login(self, "fixture.builder")
        _entity(
            self.conn,
            "LATE-PASSAGE",
            "rule",
            "财务处理说明",
            summary="按单核对凭证，保留收据副本。" * 23 + "办理费用报销时核对单据。",
            payload={"aliases": ["质量保证金"]},
        )
        self.conn.commit()
        for query, terms in (("费用如何报销？", ("费用", "报销")), ("质量保证", ("质量保证",))):
            with self.subTest(query=query):
                hits = self._invoke(client, "semantic.search", {"q": query})["items"]
                hit = next(h for h in hits if h["id"] == "LATE-PASSAGE")
                for term in terms:
                    self.assertIn(term, hit["highlight"])
                    self.assertIn(term, hit["match_terms"])
                self.assertLessEqual(len(hit["highlight"]), 160)

    def test_large_opaque_ids_use_a_stable_page_size_and_remain_navigable(self) -> None:
        client = construction.BuildMySQL.login(self, "fixture.builder")
        ids = {"编号" + "汉" * 183 + str(i).zfill(4) for i in range(101)}
        for ident in ids:
            _entity(self.conn, ident, "term", "厚册编号核对-" + ident[-4:], summary="保留完整标识以供打开。")
        self.conn.commit()
        for q in ("厚册编号核对", ""):
            with self.subTest(q=q):

                def page(number: int, query: str = q) -> dict:
                    return self._invoke(
                        client, "semantic.search", {"q": query, "page": number, "page_size": 100, "kind": "term"}
                    )

                first = page(1)
                width = first["page_size"]
                self.assertLess(width, 100)
                found = set()
                for i in range((first["total"] + width - 1) // width):
                    part = first if i == 0 else page(i + 1)
                    self.assertEqual(part["page_size"], width)
                    self.assertEqual(len(part["items"]), min(width, first["total"] - i * width))
                    for item in part["items"]:
                        self.assertNotIn(item["id"], found)
                        self.assertEqual(item["entity_id"], item["id"])
                        self.assertTrue(item["href"])
                        self.assertTrue(item["title"] and item["summary"])
                        found.add(item["id"])
                self.assertEqual(ids, found)
