"""AI analysis, risk detection, and recommendation routes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from kubeforge.ai import detect_risks, detect_type, get_recommendations
from kubeforge.db import analyses as analysis_db
from kubeforge.db import artifacts as artifact_db
from kubeforge.events import ANALYSIS_COMPLETED, Event, bus
from kubeforge.models import AnalysisStatus

logger = logging.getLogger("kubeforge.api.analysis")

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/{artifact_id}/detect")
async def detect_artifact_type(artifact_id: str):
    """Detect the type of a deployment artifact using AI."""
    artifact = await artifact_db.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")

    result = await detect_type(artifact.content, artifact.filename)
    return result.model_dump()


@router.post("/{artifact_id}/risks")
async def analyze_risks(artifact_id: str):
    """Run risk analysis on an artifact."""
    artifact = await artifact_db.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")

    # Create analysis record
    analysis = await analysis_db.create_analysis(artifact_id, artifact.project_id, "risk")
    await analysis_db.update_analysis(analysis.id, AnalysisStatus.RUNNING)

    try:
        result = await detect_risks(
            artifact.content,
            artifact.artifact_type,
            project_id=artifact.project_id,
            analysis_id=analysis.id,
        )

        # Store individual risks
        for risk in result.risks:
            await analysis_db.create_risk(
                analysis.id, artifact.project_id,
                risk.title, risk.description, risk.severity, risk.category, risk.remediation,
            )

        await analysis_db.update_analysis(
            analysis.id, AnalysisStatus.COMPLETED,
            result_json=json.dumps(result.model_dump()),
        )

        await bus.publish(Event(ANALYSIS_COMPLETED, {
            "analysis_id": analysis.id,
            "artifact_id": artifact_id,
            "project_id": artifact.project_id,
            "risk_count": len(result.risks),
        }))

        return {
            "analysis_id": analysis.id,
            "status": "completed",
            "risk_count": len(result.risks),
            "risks": [r.model_dump() for r in result.risks],
            "summary": result.summary,
        }

    except Exception as e:
        logger.exception("Risk analysis failed")
        await analysis_db.update_analysis(analysis.id, AnalysisStatus.FAILED, result_json=str(e))
        raise HTTPException(500, f"Analysis failed: {e}")


@router.post("/{artifact_id}/recommend")
async def recommend(artifact_id: str):
    """Get K3s best-practice recommendations for an artifact."""
    artifact = await artifact_db.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")

    result = await get_recommendations(artifact.content, artifact.artifact_type)
    return {
        "recommendations": [r.model_dump() for r in result.recommendations],
        "summary": result.summary,
    }


@router.get("/risks/{project_id}")
async def list_project_risks(project_id: str):
    """List all risks for a project."""
    risks = await analysis_db.list_risks(project_id)
    return [r.model_dump() for r in risks]


@router.get("/{analysis_id}")
async def get_analysis(analysis_id: str):
    """Get an analysis result."""
    analysis = await analysis_db.get_analysis(analysis_id)
    if not analysis:
        raise HTTPException(404, "Analysis not found")
    return {
        "id": analysis.id,
        "artifact_id": analysis.artifact_id,
        "analysis_type": analysis.analysis_type,
        "status": analysis.status.value,
        "result": json.loads(analysis.result_json) if analysis.result_json else None,
        "created_at": analysis.created_at.isoformat(),
    }
