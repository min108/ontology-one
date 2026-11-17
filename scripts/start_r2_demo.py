"""Start an isolated Compose demo and load the original synthetic demo package."""

from __future__ import annotations

import getpass
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if environment.get("ONTOLOGY_MODEL_FROM_ENV") != "1":
        environment = {key: value for key, value in environment.items() if not key.upper().startswith("DEEPSEEK_")}
    return environment


def main() -> None:
    password = getpass.getpass("演示账号密码（至少12位，含英文字母和数字；三个演示身份共用）：")
    if len(password) < 12 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise SystemExit("演示密码不符合要求，未启动服务。")
    folder = ROOT / "work/r2-demo"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = folder / "database.env"
    if not settings.exists():
        # Only generated database credentials persist. Model credentials are
        # inherited by Compose from the caller's process environment.
        with open(settings, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write("MYSQL_ROOT_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
            stream.write("MYSQL_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
    command = [
        "docker",
        "compose",
        "--project-name",
        "ontology-r2-demo",
        "--env-file",
        str(settings),
        "-f",
        str(ROOT / "docker-compose.yml"),
    ]
    environment = compose_environment()
    if environment.get("ONTOLOGY_MODEL_FROM_ENV") == "1":
        command += ["-f", str(ROOT / "docker-compose.model-env.yml")]
    ca = os.getenv("SSL_CERT_FILE")
    if ca and Path(ca).is_file():
        # Reuse the Dockerfile's trust injection and mount the same approved
        # CA bundle for runtime model connections; never disable TLS.
        override = folder / "build-ca.json"
        override.write_text(
            json.dumps(
                {
                    "services": {
                        name: {
                            "build": {"secrets": ["proxy_ca"]},
                            "volumes": [str(Path(ca).resolve()) + ":/run/ontology-ca.pem:ro"],
                            "environment": {
                                "SSL_CERT_FILE": "/run/ontology-ca.pem",
                                "REQUESTS_CA_BUNDLE": "/run/ontology-ca.pem",
                                "NODE_EXTRA_CA_CERTS": "/run/ontology-ca.pem",
                            },
                        }
                        for name in ("api", "agent", "worker", "migrate")
                    },
                    "secrets": {"proxy_ca": {"file": str(Path(ca).resolve())}},
                }
            )
        )
        command += ["-f", str(override)]
    subprocess.run([*command, "up", "-d", "--build", "--wait"], cwd=ROOT, check=True, env=environment)
    subprocess.run(
        [*command, "exec", "-T", "api", "python", "scripts/bootstrap_r2_demo.py"],
        cwd=ROOT,
        input=json.dumps({"password": password}),
        text=True,
        check=True,
        env=environment,
    )
    print("演示环境就绪：http://localhost:" + os.getenv("ONTOLOGY_PORT", "8765"))
    print("账号：demo.admin / demo.builder / demo.reviewer；密码为刚输入的演示密码。")
    print("后续流程：docs/blueprint/rounds/R4-DEMO.md（含发布前五项检查）")


if __name__ == "__main__":
    main()
