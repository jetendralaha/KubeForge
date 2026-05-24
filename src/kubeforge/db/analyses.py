"""AI analysis and risk CRUD operations."""

from __future__ import annotations

from datetime import datetime, timezone

from kubeforge.db.engine import get_db
from kubeforge.models import AIAnalysis, AnalysisStatus, RiskItem


async def create_analysis(artifact_id: str, project_id: str, analysis_type: str = "risk") -> AIAnalysis:
    analysis = AIAnalysis(artifact_id=artifact_id, project_id=project_id, analysis_type=analysis_type)
    db = await get_db()
    await db.execute(
        """INSERT INTO ai_analyses (id, artifact_id, project_id, analysis_type, status, result_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (analysis.id, analysis.artifact_id, analysis.project_id, analysis.analysis_type,
         analysis.status.value, analysis.result_json, analysis.created_at.isoformat()),
    )
    await db.commit()
    return analysis


async def update_analysis(analysis_id: str, status: AnalysisStatus, result_json: str = "") -> None:
    db = await get_db()
    completed = datetime.now(timezone.utc).isoformat() if status in (AnalysisStatus.COMPLETED, AnalysisStatus.FAILED) else None
    await db.execute(
        "UPDATE ai_analyses SET status = ?, result_json = ?, completed_at = ? WHERE id = ?",
        (status.value, result_json, completed, analysis_id),
    )
    await db.commit()


async def get_analysis(analysis_id: str) -> AIAnalysis | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM ai_analyses WHERE id = ?", (analysis_id,))
    row = await cursor.fetchone()
    return _row_to_analysis(row) if row else None


async def list_analyses(project_id: str) -> list[AIAnalysis]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM ai_analyses WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
    )
    return [_row_to_analysis(r) for r in await cursor.fetchall()]


async def create_risk(analysis_id: str, project_id: str, title: str, description: str = "",
                      severity: str = "medium", category: str = "", remediation: str = "") -> RiskItem:
    risk = RiskItem(
        analysis_id=analysis_id, project_id=project_id, title=title,
        description=description, severity=severity, category=category, remediation=remediation,
    )
    db = await get_db()
    await db.execute(
        """INSERT INTO ai_risks (id, analysis_id, project_id, title, description, severity, category, remediation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (risk.id, risk.analysis_id, risk.project_id, risk.title,
         risk.description, risk.severity, risk.category, risk.remediation),
    )
    await db.commit()
    return risk


async def list_risks(project_id: str) -> list[RiskItem]:
    db = await get_db()
    sev_order = "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
    cursor = await db.execute(
        f"SELECT * FROM ai_risks WHERE project_id = ? ORDER BY {sev_order}", (project_id,)
    )
    return [_row_to_risk(r) for r in await cursor.fetchall()]


def _row_to_analysis(row) -> AIAnalysis:
    return AIAnalysis(
        id=row["id"], artifact_id=row["artifact_id"], project_id=row["project_id"],
        analysis_type=row["analysis_type"] or "", status=AnalysisStatus(row["status"]),
        result_json=row["result_json"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
    )


def _row_to_risk(row) -> RiskItem:
    return RiskItem(
        id=row["id"], analysis_id=row["analysis_id"], project_id=row["project_id"],
        title=row["title"], description=row["description"] or "",
        severity=row["severity"], category=row["category"] or "",
        remediation=row["remediation"] or "",
    )
