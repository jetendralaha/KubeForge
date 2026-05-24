"""KubeForge FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kubeforge.api import api_router
from kubeforge.config import settings
from kubeforge.db import close_db, get_db
from kubeforge.version import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("kubeforge")
    logger.info(f"KubeForge v{__version__} starting")

    # Initialise database (runs migrations)
    await get_db()
    logger.info("Database ready")

    yield

    # Shutdown
    await close_db()
    logger.info("KubeForge stopped")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="KubeForge",
        description="AI-Powered K3s Deployment Platform",
        version=__version__,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health endpoint
    @app.get("/health")
    async def health():
        try:
            from kubeforge.ai.ollama import health_check as ai_health
            ollama_ok = await ai_health()
        except Exception:
            ollama_ok = False
        return {
            "status": "ok",
            "version": __version__,
            "ollama": "connected" if ollama_ok else "unavailable (optional)",
        }

    # Version
    @app.get("/version")
    async def version():
        from kubeforge.version import version_info
        return version_info()

    # API routes
    app.include_router(api_router)

    return app


# Module-level app instance for `uvicorn kubeforge.app:app`
app = create_app()
