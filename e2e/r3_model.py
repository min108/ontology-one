"""R3 synthetic provider: real SDK/tool calls, one unsupported number then correction."""

import re

from r2_model import Provider as MessagesProvider
from r2_model import reply, strings


def r3_reply(body: dict) -> dict:
    content = reply(body)
    # The first answer deliberately fails unchanged numeric verification. Only a
    # subsequent correction prompt produces the supported number; UI must trigger it.
    history = "\n".join(strings(body.get("messages", [])))
    if content["type"] == "text" and "本条规定的办理时限" in history:
        read_ids = {
            part.get("id")
            for message in body.get("messages", [])
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part.get("type") == "tool_use" and part.get("name") == "ontology_read"
        }
        evidence = "\n".join(
            text
            for message in body.get("messages", [])
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part.get("type") == "tool_result" and part.get("tool_use_id") in read_ids
            for text in strings(part.get("content"))
        )
        deadline = re.search(r"(?<!\d)(10|15)个工作日", evidence)
        citation = re.search(r"\[[^\]]+\]\(/assets/[^)]+\)", content["text"])
        if not deadline or not citation:
            return {**content, "text": "当前所选条款未提供可确认的办理时限，请核对原文。"}
        days, reference = deadline.group(0), citation.group(0)
        return {
            **content,
            "text": f"所选条款规定的办理时限为{days}，起算条件和材料要求请结合原文核对。{reference}"
            f"\n\n|事项|时限|依据|\n|---|---|---|\n|本条规定的办理时限|{days}|{reference}|"
            "\n\n以上是模拟模型演练结果。",
        }
    if content["type"] == "text" and "核对结果：" not in history:
        return {**content, "text": content["text"].replace("30日", "31日")}
    return content


class Provider(MessagesProvider):
    def __init__(self) -> None:
        super().__init__(responder=r3_reply)
