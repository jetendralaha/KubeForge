"""API package — aggregates all routers."""

from fastapi import APIRouter

from kubeforge.api.projects import router as projects_router
from kubeforge.api.artifacts import router as artifacts_router
from kubeforge.api.analysis import router as analysis_router
from kubeforge.api.manifests import router as manifests_router
from kubeforge.api.packages import router as packages_router
from kubeforge.api.deploy import router as deploy_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(projects_router)
api_router.include_router(artifacts_router)
api_router.include_router(analysis_router)
api_router.include_router(manifests_router)
api_router.include_router(packages_router)
api_router.include_router(deploy_router)

__all__ = ["api_router"]
