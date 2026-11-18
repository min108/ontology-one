"""In-memory synthetic Messages provider; the real pinned SDK performs the work."""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise


class Provider:
    def __init__(self, responder: Callable[[dict], dict] | None = None) -> None:
        self.responder = responder or reply
        self.key = "synthetic-r2-" + secrets.token_hex(16)
        self.requests = 0

    def __enter__(self) -> Provider:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                pass

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4 * 1024 * 1024 or not hmac.compare_digest(
                    self.headers.get("x-api-key", ""), owner.key
                ):
                    self.send_error(403)
                    return
                body = json.loads(self.rfile.read(size))
                owner.requests += 1
                content = owner.responder(body)
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "synthetic-r2",
                            "type": "message",
                            "role": "assistant",
                            "model": "synthetic-r2",
                            "content": [],
                            "usage": {"input_tokens": 8, "output_tokens": 0},
                        },
                    },
                    {"type": "content_block_start", "index": 0, "content_block": content},
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use" if content["type"] == "tool_use" else "end_turn"},
                        "usage": {"output_tokens": 8},
                    },
                    {"type": "message_stop"},
                ]
                raw = "".join(
                    "event: " + e["type"] + "\ndata: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def reply(body: dict) -> dict:
    text = "\n".join(strings(body.get("messages", [])))
    marker = "Conversation messages (the role labels describe application history):\n"
    messages = []
    for value in strings(body):
        if marker in value:
            messages = json.JSONDecoder().raw_decode(value.split(marker, 1)[1])[0]
            break
    names = {tool["name"] for tool in body.get("tools", [])}
    used = {
        part.get("name")
        for message in body.get("messages", [])
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "tool_use"
    }
    if "submit_task_result" in names and "submit_task_result" not in used:
        source = json.loads(next(message["content"] for message in reversed(messages) if message["role"] == "user"))
        # Synthetic-only failure injection: the uploaded source stays byte-for-byte
        # unchanged; only an explicit test filename selects unusable model output.
        if str(source.get("material", {}).get("title", "")).startswith("R4A1-unusable-"):
            chunk = source["chunks"][0]
            invalid = {
                "candidates": [
                    {
                        "kind": "rule",
                        "title": "Synthetic rejected candidate",
                        "content": chunk["content"],
                        "source_chunk_ids": [chunk["id"]],
                        "source_quotes": [{"chunk_id": chunk["id"], "quote": "short"}],
                        "conditions": [],
                        "questions": [],
                    }
                ],
                "coverage": [
                    {"chunk_id": item["id"], "reason": "Synthetic invalid quote case"} for item in source["chunks"]
                ],
            }
            return {
                "type": "tool_use",
                "id": "r4a1-unusable",
                "name": "submit_task_result",
                "input": {"text": json.dumps(invalid)},
            }
        candidates, coverage = [], []
        for chunk in source["chunks"]:
            article_starts = [
                match.start() for match in re.finditer(r"第[一二三四五六七八九十百零〇\d]+条", chunk["content"])
            ]
            boundaries = [*article_starts, len(chunk["content"])]
            quotes = [chunk["content"][left:right].strip() for left, right in pairwise(boundaries)]
            if not article_starts:
                quotes = [chunk["content"].strip()]
            for quote in quotes:
                if len(quote) < 8 or re.fullmatch(r"(?:第[^条]+章.*|.*(?:办法|规定)(?:（试行）)?)", quote):
                    continue
                if re.search(r"本(?:办法|规定)自(?:\d{4}年\d+月\d+日|发布之日|印发之日)起?施行", quote):
                    continue
                data_field = any(
                    text in quote for text in ("合同付款台账逐份合同记录付款金额", "单笔付款金额超过100万元的")
                )
                skill_mistake = any(
                    text in quote for text in ("经办人员应保存验收记录", "审计部门每年至少开展一次合同付款专项检查")
                )
                candidates.append(
                    {
                        # Deliberately misclassify one normative clause so the UI
                        # acceptance path must review it before adopting a draft.
                        "kind": "data" if data_field else "skill" if skill_mistake else "rule",
                        # Different clauses keep distinct titles under the real QG2 check.
                        "title": "付款申请时限" if "30日" in quote else quote[:24],
                        "summary": quote,
                        "content": quote,
                        "definition": quote,
                        "source_chunk_ids": [chunk["id"]],
                        "source_quotes": [{"chunk_id": chunk["id"], "quote": quote}],
                        "conditions": [
                            condition
                            for condition in (
                                "验收通过后",
                                "有异议的款项",
                                "对存在争议的付款",
                                "收到完整付款资料后",
                                "单笔付款金额超过100万元的",
                                "超过500万元的",
                                "质保期满且无质量问题后",
                                "对未经审批擅自付款、超合同约定付款的",
                                "返回后",
                                "收到报销申请后",
                            )
                            if condition in quote
                        ],
                        "exceptions": "；".join(
                            exception
                            for exception in (
                                "有异议的款项应记录原因并暂停支付",
                                "资料不齐全的，应一次性告知经办部门补正",
                                "确需超过的，应报财务负责人批准",
                                "经部门负责人批准的会议统一安排住宿除外",
                            )
                            if exception in quote
                        ),
                        # The ordinary original supports these missing extraction facts;
                        # the browser must supply and persist the data-field repair.
                        "unit": "",
                        "grain": "",
                        "questions": [],
                    }
                )
            coverage.append({"chunk_id": chunk["id"], "reason": "按各条原文提炼；标题和施行说明保留为文件事实"})
        return {
            "type": "tool_use",
            "id": "r2-extract",
            "name": "submit_task_result",
            "input": {"text": json.dumps({"candidates": candidates, "coverage": coverage}, ensure_ascii=False)},
        }
    if "ontology_read" in names and "ontology_read" not in used:
        ident = re.search(r'"entity_ids"\s*:\s*\[\s*"([^"\]]+)"', "\n".join(strings(messages)))
        if not ident:
            ident = re.search(r'"entity_ids"\s*:\s*\[\s*"([^"\]]+)"', text)
        if ident:
            return {
                "type": "tool_use",
                "id": "r2-read",
                "name": "ontology_read",
                "input": {"entity_id": ident.group(1)},
            }
    selected = re.search(r'"entity_ids"\s*:\s*\[\s*"([^"\]]+)"', "\n".join(strings(messages))) or re.search(
        r'"entity_ids"\s*:\s*\[\s*"([^"\]]+)"', text
    )
    citation = f"[付款制度](/assets/{selected.group(1)})" if selected else ""
    return {
        "type": "text",
        "text": "根据所选已发布制度，验收通过后30日内完成付款申请，付款前应核对合同约定与验收资料。"
        + citation
        + "\n\n|事项|要求|依据|\n|---|---|---|\n|申请时限|30日|"
        + citation
        + "|\n|付款资料|合同约定与验收资料|"
        + citation
        + "|"
        + "\n\n以上是模拟模型演练结果。",
    }
