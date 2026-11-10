# Fixed Python base; only the reviewed bootstrap enters this execution image.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --chmod=0444 ops/skill_runner_bootstrap.py /opt/skill_runner_bootstrap.py
USER 65534:65534
ENTRYPOINT ["python", "-I", "-B", "-u", "/opt/skill_runner_bootstrap.py"]
