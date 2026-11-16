from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager, closing
from pathlib import Path
import json, uuid, os, hashlib, asyncio
import pymysql
from .db import connect, initialize, entity_json, log, now, ROOT, current_org_id
from .mcp_server import build_mcp_app
from .skill_harness import EXECUTOR as harness_executor
from .asset_kinds import canonical_entity, canonical_kind
from .version import APP_VERSION
from .core.brand import BRAND

mcp_app = build_mcp_app()


@asynccontextmanager
async def lifespan(app):
    from .infra.migrations import validate_runtime
    from .jobs import worker_loop
    from .maintenance import runtime_guard

    with runtime_guard(shared=True):
        await asyncio.to_thread(validate_runtime)
        worker = asyncio.create_task(worker_loop()) if os.getenv("ONTOLOGY_WORKER_ENABLED", "1") == "1" else None
        try:
            async with mcp_app.lifespan():
                yield
        finally:
            await harness_executor.close()
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title=f"{BRAND['product_name']} · {BRAND['product_descriptor']}", version=APP_VERSION, lifespan=lifespan)
from .security.middleware import SecurityMiddleware

app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8765", "http://127.0.0.1:8765"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from .api.v1.core import (
    db_error,
    health,
    catalog,
    entities,
    search,
    entity_facets,
    create,
    _create_entity,
    versions,
    entity_route,
    detail,
    edit,
    action,
    _entity_action,
    restore_entity,
    _restore_entity,
    activity,
    relations,
    relation_work_entry,
    confirm_relation,
    _confirm_relation,
    overview,
    spa,
    router as core_router,
    spa_router,
)

app.add_exception_handler(pymysql.err.MySQLError, db_error)
app.include_router(core_router)
from .api.v2.routes import router as v2_router
from .api.v2.errors import install_errors

app.include_router(v2_router)
install_errors(app)

from .materials import router as material_router
from .datasets import router as dataset_router
from .graph import router as graph_router
from .ai import router as ai_router
from .applications import router as applications_router
from .system import router as system_router
from .experience import router as experience_router
from .impact import router as impact_router
from .projects import router as projects_router
from .placement import router as placement_router
from .asset_catalog import router as asset_catalog_router
from .special_projects import router as special_projects_router
from .data_instances import router as data_instances_router
from .authority import router as authority_router
from .asset_import import router as asset_import_router

for router in [
    material_router,
    dataset_router,
    graph_router,
    ai_router,
    applications_router,
    system_router,
    experience_router,
    impact_router,
    projects_router,
    placement_router,
    asset_catalog_router,
    special_projects_router,
    data_instances_router,
    authority_router,
    asset_import_router,
]:
    app.include_router(router, prefix="/api")
from .identity import router as identity_router
from .federation import router as federation_router
from .jobs import router as jobs_router

app.include_router(identity_router, prefix="/api")
app.include_router(federation_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
from .integrations import router as integration_router

app.include_router(integration_router, prefix="/api")
from .sources import router as source_router

app.include_router(source_router, prefix="/api")
from .skill_harness import router as harness_router

app.include_router(harness_router, prefix="/api")
from .skills import router as skill_router

app.include_router(skill_router, prefix="/api")
from .security.applications import router as integration_applications_router

app.include_router(integration_applications_router, prefix="/api")
app.mount("/mcp", mcp_app)

from .agent_proxy import router as agent_proxy_router

app.include_router(agent_proxy_router, prefix="/api")
from .harness_runs import router as harness_runs_router

app.include_router(harness_runs_router, prefix="/api")
from .demo import router as demo_router

app.include_router(demo_router, prefix="/api")

dist = ROOT / "frontend/dist"
if dist.exists():
    app.mount("/static", StaticFiles(directory=dist / "static"), name="static")

    app.include_router(spa_router)
