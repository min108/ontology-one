"""R3 synthetic provider: real SDK/tool calls, one unsupported number then correction."""

from r2_model import Provider as MessagesProvider
from r2_model import reply, strings


def r3_reply(body: dict) -> dict:
    content = reply(body)
    # The first answer deliberately fails unchanged numeric verification. Only a
    # subsequent correction prompt produces the supported number; UI must trigger it.
    history = "\n".join(strings(body.get("messages", [])))
    if content["type"] == "text" and "核对结果：" not in history:
        return {**content, "text": content["text"].replace("30日", "31日")}
    return content


class Provider(MessagesProvider):
    def __init__(self) -> None:
        super().__init__(responder=r3_reply)
