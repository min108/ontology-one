FROM docker:29-cli@sha256:2357639ad1ea7d0194134bf3292622fd4e562483966ed674e5d7c945b5f3b28d AS docker_cli
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY --chmod=0444 ops/skill_runner.py /opt/skill_runner.py
ENTRYPOINT ["python", "-I", "-B", "-u", "/opt/skill_runner.py"]
CMD ["--socket", "/run/ontology-skills/runner.sock", "--socket-gid", "10001"]
