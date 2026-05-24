"""Project management API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from kubeforge.db import projects as db
from kubeforge.models import ProjectCreate, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate):
    project = await db.create_project(body.name, body.description)
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(status: str | None = None):
    return await db.list_projects(status)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    project = await db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str):
    project = await db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    await db.delete_project(project_id)
