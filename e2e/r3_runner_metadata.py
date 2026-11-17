"""Allowlisted, secret-free provider metadata for browser acceptance receipts."""

import re
from urllib.parse import urlsplit, urlunsplit


def model_metadata(environment: dict[str, str]) -> dict[str, str | None]:
    model = environment.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
    endpoint = environment.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic").strip()
    secret = environment.get("DEEPSEEK_API_KEY", "")
    safe_model = model if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", model) else None
    if secret and secret in model:
        safe_model = None
    safe_url = None
    try:
        parsed = urlsplit(endpoint)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            if parsed.port is not None:
                host += f":{parsed.port}"
            candidate = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            if not secret or secret not in candidate:
                safe_url = candidate
    except ValueError:
        pass
    return {"model_name": safe_model, "base_url": safe_url}
