"""The HTTP application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from medvault.config import get_settings
from medvault.db import get_engine, get_session_factory
from medvault.models import Base, ProjectionState
from medvault.routers import analytics, documents, tenants
from medvault.schemas import HealthOut
from medvault.vault.store import Vault

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    vault = Vault(settings.vault_path)
    vault.initialise()
    app.state.vault = vault
    # create_all is safe to run repeatedly and creates nothing that holds data
    # of its own — every table here is rebuildable from the vault.
    Base.metadata.create_all(get_engine())
    log.info("medical-vault ready; vault at %s", settings.vault_path)
    yield


app = FastAPI(
    title="Medical Vault",
    version="0.1.0",
    summary="A durable, portable record of medical results",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def db_session_middleware(request: Request, call_next) -> Response:
    """One transaction per request, committed only if the handler succeeded."""
    session = get_session_factory()()
    request.state.session = session
    try:
        response = await call_next(request)
        if response.status_code < 400:
            session.commit()
        else:
            session.rollback()
        return response
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


app.include_router(tenants.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")


@app.get("/api/health", response_model=HealthOut, tags=["ops"])
def health(request: Request) -> HealthOut:
    """Liveness plus the numbers an operator actually wants to see."""
    session = request.state.session
    state = session.scalars(select(ProjectionState)).first()
    return HealthOut(
        status="ok",
        vault_path=str(get_settings().vault_path),
        documents=state.document_count if state else 0,
        observations=state.observation_count if state else 0,
        unmapped=state.unmapped_count if state else 0,
        catalog_version=state.catalog_version if state else None,
        last_reindex_at=state.last_reindex_at if state else None,
    )


def _mount_web_app(application: FastAPI) -> None:
    """Serve the built single-page app, if this image contains one.

    Registered after every API route so it can claim everything else without
    shadowing them. Unknown paths return index.html rather than 404, because the
    client owns its own routing — but only for paths outside /api, so a mistyped
    endpoint still fails loudly instead of returning HTML to a JSON caller.
    """
    root = get_settings().web_root
    if root is None or not Path(root).is_dir():
        return

    root = Path(root)
    index = root / "index.html"

    application.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = (root / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(root.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(index)


_mount_web_app(app)
