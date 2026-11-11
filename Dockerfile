# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci --strict-ssl=true
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ONTOLOGY_STORAGE_ROOT=/app/data/tenants ONTOLOGY_WORKER_ENABLED=0
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 ontology && useradd --uid 10001 --gid ontology --no-create-home ontology \
    && mkdir -p /app/data && chown ontology:ontology /app/data
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY ops/ ./ops/
COPY tests/ ./tests/
# Keep the reviewed source and deployment recipe available for the HQ release export.
# .dockerignore excludes local secrets, tenant files, caches and dependency directories.
COPY frontend/ ./frontend/
COPY VERSION README.md docker-compose.yml Dockerfile .dockerignore .gitignore .env.example start.sh 启动平台.bat ./
COPY --from=frontend /build/dist ./frontend/dist/
# Uploaded/check-out sources may have owner-only modes; the unprivileged runtime
# must be able to import them. Runtime tenant data is created separately as 0700/0600.
RUN chmod -R a+rX /app
USER 10001:10001
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "4"]
