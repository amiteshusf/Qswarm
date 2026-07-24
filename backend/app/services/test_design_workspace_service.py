"""QSwarm-first Sprint 1 test-design workspace orchestration."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.requirement_analysis_agent import run_requirement_analysis
from app.agents.story_intake_agent import run_intake
from app.agents.test_design_agent import run_test_design
from app.agents.test_design_plan_agent import run_test_design_plan
from app.connectors.jira_client import JiraClient
from app.core.config import Settings, get_settings
from app.core.constants import (
    ActorType,
    ApprovalStatus,
    ArtifactType,
    AuditEventType,
    TestCasePublicationStatus,
    TestDesignWorkspaceStage,
    WorkflowRunStatus,
)
from app.db.models.agent_artifact import AgentArtifact
from app.db.models.approval import Approval
from app.db.models.jira_test_design_review_issue import JiraTestDesignReviewIssue
from app.db.models.test_case_record import TestCaseRecord
from app.db.models.workflow_run import WorkflowRun
from app.publishers.jira_publisher import JiraTestDesignPublisher
from app.schemas.test_design_publish import PublishResult
from app.schemas.test_design_workspace import TestDesignRunCreateBody, WorkspaceApproveBody, WorkspacePlanRevisionBody, WorkspaceRevisionBody
from app.services import approval_service, audit_service
from app.services import test_design_version_service as tdv
from app.services.jira_service import fetch_and_upsert_story, story_to_api_dict
from app.services.test_case_publication_service import publish_test_case_record
from app.services.test_case_registry_service import list_test_cases_for_api, materialize_test_cases_from_approved_workflow, record_to_api_dict
from app.services.test_design_evolution_service import (
    apply_workspace_evolution,
    compute_new_test_design_json,
    list_feedback_for_api,
    list_versions_for_api,
)
from app.services.test_design_publish_builder import build_publish_package, draft_cases_from_test_design_json


_ACTIVE_RUN_STATUSES = frozenset(
    {
        WorkflowRunStatus.PENDING.value,
        WorkflowRunStatus.RUNNING.value,
        WorkflowRunStatus.AWAITING_APPROVAL.value,
        WorkflowRunStatus.APPROVED.value,
    }
)


def _graph_state(run: WorkflowRun) -> dict[str, Any]:
    blob = run.graph_state_json or {}
    return blob if isinstance(blob, dict) else {}


def _product_workspace(run: WorkflowRun) -> dict[str, Any]:
    gs = _graph_state(run)
    pw = gs.get("product_workspace")
    return pw if isinstance(pw, dict) else {}


def _set_product_workspace(run: WorkflowRun, patch: dict[str, Any]) -> None:
    gs = dict(_graph_state(run))
    pw = dict(_product_workspace(run))
    pw.update(patch)
    gs["product_workspace"] = pw
    run.graph_state_json = gs


def _set_stage(run: WorkflowRun, stage: str) -> None:
    _set_product_workspace(run, {"stage": stage})


def is_qswarm_first_run(run: WorkflowRun) -> bool:
    return _product_workspace(run).get("mode") == "qswarm_first"


def map_product_stage(run: WorkflowRun) -> str:
    pw = _product_workspace(run)
    if pw.get("stage"):
        return str(pw["stage"])
    if run.status == WorkflowRunStatus.AWAITING_APPROVAL.value:
        return TestDesignWorkspaceStage.LEGACY_AWAITING_APPROVAL.value
    if run.status == WorkflowRunStatus.COMPLETED.value:
        return TestDesignWorkspaceStage.COMPLETED.value
    if run.status == WorkflowRunStatus.FAILED.value:
        return TestDesignWorkspaceStage.FAILED.value
    if run.status == WorkflowRunStatus.PENDING.value:
        return TestDesignWorkspaceStage.DISCOVERED.value
    if run.status == WorkflowRunStatus.RUNNING.value:
        return TestDesignWorkspaceStage.ANALYZING_REQUIREMENTS.value
    return TestDesignWorkspaceStage.DISCOVERED.value


def derive_next_actions(run: WorkflowRun) -> list[str]:
    stage = map_product_stage(run)
    mapping: dict[str, list[str]] = {
        TestDesignWorkspaceStage.INTAKE_READY.value: ["analyze_requirements"],
        TestDesignWorkspaceStage.ANALYSIS_READY.value: ["prepare_plan"],
        TestDesignWorkspaceStage.AWAITING_PLAN_APPROVAL.value: ["approve_plan", "request_plan_revision"],
        TestDesignWorkspaceStage.PLAN_APPROVED.value: ["generate_test_cases"],
        TestDesignWorkspaceStage.AWAITING_TEST_CASE_REVIEW.value: ["request_revision", "approve_test_design"],
        TestDesignWorkspaceStage.LEGACY_AWAITING_APPROVAL.value: ["request_revision", "approve_test_design"],
        TestDesignWorkspaceStage.APPROVED.value: ["publish_test_cases"],
        TestDesignWorkspaceStage.PUBLISHED.value: ["view_automation_backlog"],
        TestDesignWorkspaceStage.AUTOMATION_READY.value: ["view_automation_backlog"],
        TestDesignWorkspaceStage.COMPLETED.value: ["view_automation_backlog"],
    }
    if stage == TestDesignWorkspaceStage.PLAN_REVISION_REQUESTED.value:
        return ["prepare_plan"]
    return mapping.get(stage, [])


def _artifact_by_type(db: Session, run_id: uuid.UUID, artifact_type: str) -> AgentArtifact | None:
    return db.scalar(
        select(AgentArtifact)
        .where(
            AgentArtifact.workflow_run_id == run_id,
            AgentArtifact.artifact_type == artifact_type,
        )
        .order_by(AgentArtifact.version.desc(), AgentArtifact.created_at.desc())
        .limit(1)
    )


def _story_key(run: WorkflowRun) -> str:
    return str(_graph_state(run).get("jira_issue_key") or "").strip().upper()


def find_active_run_for_story(db: Session, story_key: str) -> WorkflowRun | None:
    key = story_key.strip().upper()
    rows = list(
        db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.status.in_(_ACTIVE_RUN_STATUSES))
            .order_by(WorkflowRun.updated_at.desc())
            .limit(200)
        ).all()
    )
    for r in rows:
        if _story_key(r) == key:
            return r
    return None


def list_stories_for_ui(
    db: Session,
    jira: JiraClient,
    *,
    project_key: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    clauses = ['issuetype in (Story, Task)']
    if project_key:
        clauses.append(f'project = "{project_key.strip().upper()}"')
    if status:
        clauses.append(f'status = "{status.strip()}"')
    if q:
        clauses.append(f'(summary ~ "{q.strip()}" OR key = "{q.strip().upper()}")')
    jql = " AND ".join(clauses) + " ORDER BY updated DESC"
    data = jira.search_issues(jql, max_results=limit)
    items: list[dict[str, Any]] = []
    for issue in data.get("issues") or []:
        key = str(issue.get("issue_key") or "").strip().upper()
        if not key:
            continue
        active = find_active_run_for_story(db, key)
        items.append(
            {
                "story_key": key,
                "title": str(issue.get("summary") or ""),
                "description": str(issue.get("description") or ""),
                "status": issue.get("status"),
                "sprint": issue.get("sprint"),
                "assignee": issue.get("assignee"),
                "readiness": "ready",
                "active_workflow_run_id": str(active.id) if active else None,
            }
        )
    total = int(data.get("total") if data.get("total") is not None else len(items))
    return {"items": items, "total": total}


def get_story_detail_for_ui(db: Session, jira: JiraClient, story_key: str) -> dict[str, Any]:
    key = story_key.strip().upper()
    issue = jira.get_issue(key)
    active = find_active_run_for_story(db, key)
    return {
        "story_key": key,
        "title": issue.get("summary"),
        "description": issue.get("description"),
        "labels": issue.get("labels") or [],
        "status": issue.get("status"),
        "issue_type": issue.get("issue_type"),
        "priority": issue.get("priority"),
        "active_workflow_run_id": str(active.id) if active else None,
        "active_workflow_run_status": active.status if active else None,
        "active_workflow_stage": map_product_stage(active) if active else None,
    }


def create_workspace_run(
    db: Session,
    jira: JiraClient,
    *,
    story_key: str,
    body: TestDesignRunCreateBody,
) -> WorkflowRun:
    key = story_key.strip().upper()
    existing = find_active_run_for_story(db, key)
    if existing is not None:
        raise ValueError("active_run_exists")

    run = WorkflowRun(
        workflow_name="sprint1_qswarm_workspace",
        status=WorkflowRunStatus.PENDING.value,
        current_step="intake_ready",
        graph_state_json={
            "jira_issue_key": key,
            "initiated_by": body.initiated_by,
            "product_workspace": {
                "mode": "qswarm_first",
                "stage": TestDesignWorkspaceStage.INTAKE_READY.value,
            },
        },
        initiated_by=body.initiated_by,
    )
    db.add(run)
    db.flush()

    story = fetch_and_upsert_story(
        db, jira, key, workflow_run_id=run.id, actor_id=body.initiated_by
    )
    run.jira_story_id = story.id
    js = story_to_api_dict(story)
    intake = run_intake(js)
    art = AgentArtifact(
        workflow_run_id=run.id,
        agent_name="story_intake_agent",
        artifact_type=ArtifactType.STORY_INTAKE.value,
        version=1,
        content_json=intake,
    )
    db.add(art)
    db.flush()

    gs = dict(_graph_state(run))
    gs["intake_artifact_id"] = str(art.id)
    run.graph_state_json = gs
    db.flush()
    return run


def analyze_requirements(db: Session, run_id: uuid.UUID, *, actor_id: str) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if not is_qswarm_first_run(run):
        raise ValueError("not_workspace_run")

    intake = _artifact_by_type(db, run.id, ArtifactType.STORY_INTAKE.value)
    if intake is None or not intake.content_json:
        raise ValueError("intake_missing")

    _set_stage(run, TestDesignWorkspaceStage.ANALYZING_REQUIREMENTS.value)
    run.status = WorkflowRunStatus.RUNNING.value
    db.flush()

    analysis_json = run_requirement_analysis(intake.content_json)
    prev = _artifact_by_type(db, run.id, ArtifactType.REQUIREMENT_ANALYSIS.value)
    ver = (prev.version if prev else 0) + 1
    art = AgentArtifact(
        workflow_run_id=run.id,
        agent_name="requirement_analysis_agent",
        artifact_type=ArtifactType.REQUIREMENT_ANALYSIS.value,
        version=ver,
        content_json=analysis_json,
    )
    db.add(art)
    db.flush()

    _set_product_workspace(
        run,
        {
            "stage": TestDesignWorkspaceStage.ANALYSIS_READY.value,
            "requirement_analysis_artifact_id": str(art.id),
            "requirement_analysis_version": ver,
        },
    )
    run.status = WorkflowRunStatus.PENDING.value
    run.current_step = "analysis_ready"
    db.flush()
    return analysis_json


def get_analysis_for_ui(db: Session, run_id: uuid.UUID) -> dict[str, Any] | None:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        return None
    art = _artifact_by_type(db, run.id, ArtifactType.REQUIREMENT_ANALYSIS.value)
    if art is None or not isinstance(art.content_json, dict):
        return None
    return {
        "version": art.version,
        "artifact_id": str(art.id),
        "content": art.content_json,
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }


def prepare_test_design_plan(db: Session, run_id: uuid.UUID, *, actor_id: str) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if not is_qswarm_first_run(run):
        raise ValueError("not_workspace_run")

    analysis_art = _artifact_by_type(db, run.id, ArtifactType.REQUIREMENT_ANALYSIS.value)
    if analysis_art is None or not isinstance(analysis_art.content_json, dict):
        raise ValueError("analysis_missing")

    _set_stage(run, TestDesignWorkspaceStage.PREPARING_TEST_DESIGN_PLAN.value)
    db.flush()

    plan_json = run_test_design_plan(analysis_art.content_json)
    prev = _artifact_by_type(db, run.id, ArtifactType.TEST_DESIGN_PLAN.value)
    ver = (prev.version if prev else 0) + 1
    art = AgentArtifact(
        workflow_run_id=run.id,
        agent_name="test_design_plan_agent",
        artifact_type=ArtifactType.TEST_DESIGN_PLAN.value,
        version=ver,
        content_json=plan_json,
    )
    db.add(art)
    db.flush()

    pw_patch = {
        "stage": TestDesignWorkspaceStage.AWAITING_PLAN_APPROVAL.value,
        "test_design_plan_artifact_id": str(art.id),
        "test_design_plan_version": ver,
        "plan_approved_at": None,
        "plan_approved_by": None,
    }
    if ver > 1:
        pw_patch["stage"] = TestDesignWorkspaceStage.AWAITING_PLAN_APPROVAL.value
    _set_product_workspace(run, pw_patch)
    run.current_step = "awaiting_plan_approval"
    db.flush()
    return plan_json


def get_plan_for_ui(db: Session, run_id: uuid.UUID) -> dict[str, Any] | None:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        return None
    art = _artifact_by_type(db, run.id, ArtifactType.TEST_DESIGN_PLAN.value)
    if art is None or not isinstance(art.content_json, dict):
        return None
    pw = _product_workspace(run)
    return {
        "version": art.version,
        "artifact_id": str(art.id),
        "content": art.content_json,
        "plan_approved": bool(pw.get("plan_approved_at")),
        "plan_approved_at": pw.get("plan_approved_at"),
        "plan_approved_by": pw.get("plan_approved_by"),
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }


def approve_test_design_plan(db: Session, run_id: uuid.UUID, *, actor_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    stage = map_product_stage(run)
    if stage not in (
        TestDesignWorkspaceStage.AWAITING_PLAN_APPROVAL.value,
        TestDesignWorkspaceStage.PLAN_REVISION_REQUESTED.value,
    ):
        raise ValueError("plan_not_ready_for_approval")

    now = datetime.now(timezone.utc).isoformat()
    _set_product_workspace(
        run,
        {
            "stage": TestDesignWorkspaceStage.PLAN_APPROVED.value,
            "plan_approved_at": now,
            "plan_approved_by": actor_id[:256],
        },
    )
    run.current_step = "plan_approved"
    db.flush()
    return run


def request_test_design_plan_revision(
    db: Session, run_id: uuid.UUID, body: WorkspacePlanRevisionBody
) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if map_product_stage(run) != TestDesignWorkspaceStage.AWAITING_PLAN_APPROVAL.value:
        raise ValueError("plan_not_awaiting_approval")

    _set_product_workspace(
        run,
        {
            "stage": TestDesignWorkspaceStage.PLAN_REVISION_REQUESTED.value,
            "plan_revision_instruction": body.instruction[:20000],
            "plan_approved_at": None,
            "plan_approved_by": None,
        },
    )
    run.current_step = "plan_revision_requested"
    db.flush()
    return run


def generate_test_cases(
    db: Session,
    jira: JiraClient,
    settings: Settings,
    run_id: uuid.UUID,
    *,
    actor_id: str,
) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    pw = _product_workspace(run)
    if not pw.get("plan_approved_at"):
        raise ValueError("plan_not_approved")

    intake = _artifact_by_type(db, run.id, ArtifactType.STORY_INTAKE.value)
    if intake is None or not intake.content_json:
        raise ValueError("intake_missing")

    _set_stage(run, TestDesignWorkspaceStage.GENERATING_TEST_CASES.value)
    run.status = WorkflowRunStatus.RUNNING.value
    db.flush()

    design = run_test_design(intake.content_json if isinstance(intake.content_json, dict) else {})
    plan_art = _artifact_by_type(db, run.id, ArtifactType.TEST_DESIGN_PLAN.value)
    if plan_art and isinstance(plan_art.content_json, dict):
        design = copy.deepcopy(design)
        design["plan_context"] = plan_art.content_json

    art = AgentArtifact(
        workflow_run_id=run.id,
        agent_name="test_design_agent",
        artifact_type=ArtifactType.TEST_DESIGN.value,
        version=1,
        content_json=design,
    )
    db.add(art)
    db.flush()

    tdv.record_initial_version(db, workflow_run_id=run.id, artifact_id=art.id, created_by=actor_id)

    gs = dict(_graph_state(run))
    gs["test_design_artifact_id"] = str(art.id)
    run.graph_state_json = gs

    parent_key = _story_key(run)
    package = build_publish_package(
        parent_issue_key=parent_key,
        workflow_run_id=run.id,
        source_artifact_id=art.id,
        test_design_content_json=design,
    )
    publisher = JiraTestDesignPublisher(jira, settings)
    pub: PublishResult = publisher.publish(package, db=db, workflow_run_id=run.id)

    appr = Approval(
        workflow_run_id=run.id,
        artifact_id=art.id,
        status=ApprovalStatus.PENDING.value,
        requested_by=actor_id[:256],
    )
    db.add(appr)
    db.flush()
    gs["approval_id"] = str(appr.id)
    run.graph_state_json = gs

    run.status = WorkflowRunStatus.AWAITING_APPROVAL.value
    run.current_step = "awaiting_test_case_review"
    _set_stage(run, TestDesignWorkspaceStage.AWAITING_TEST_CASE_REVIEW.value)
    db.flush()

    cases = draft_cases_from_test_design_json(design, max_cases=10)
    return {
        "test_case_count": len(cases),
        "approval_id": str(appr.id),
        "publish_warnings": list(pub.warnings),
        "publish_errors": list(pub.errors),
    }


def _project_test_cases(design: dict[str, Any], *, story_key: str, version_number: int) -> list[dict[str, Any]]:
    drafts = draft_cases_from_test_design_json(design, max_cases=10)
    out: list[dict[str, Any]] = []
    for idx, d in enumerate(drafts, start=1):
        rkey = f"{story_key}-TC-{idx:02d}"
        out.append(
            {
                "registry_key": rkey,
                "draft_key": rkey,
                "title": d.title,
                "objective": d.objective,
                "preconditions": d.preconditions,
                "steps": d.steps,
                "expected_results": d.expected_results,
                "test_type": d.case_type,
                "priority": "medium",
                "automation_suitability": "candidate" if d.case_type in ("positive", "validation") else "manual_first",
                "source_story_key": story_key,
                "generated_version": version_number,
                "status": "draft",
            }
        )
    return out


def get_review_data_for_ui(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise KeyError("run_not_found")

    stage = map_product_stage(run)
    versions = list_versions_for_api(db, run.id)
    current = next((v for v in versions if v.get("is_current")), versions[-1] if versions else None)
    current_ver = int(current.get("version_number") or 0) if current else 0

    test_cases: list[dict[str, Any]] = []
    art = None
    if current and current.get("artifact_id"):
        art = db.get(AgentArtifact, uuid.UUID(str(current["artifact_id"])))
    elif _graph_state(run).get("test_design_artifact_id"):
        art = db.get(AgentArtifact, uuid.UUID(str(_graph_state(run)["test_design_artifact_id"])))
    if art and isinstance(art.content_json, dict):
        test_cases = _project_test_cases(art.content_json, story_key=_story_key(run), version_number=current_ver)

    records = list(
        db.scalars(select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == run.id)).all()
    )
    published_ids = {r.registry_key: r.external_id for r in records if r.external_id}

    conversation: list[dict[str, Any]] = []
    for fb in list_feedback_for_api(db, run.id):
        conversation.append(
            {
                "id": fb["id"],
                "type": fb["action_type"],
                "actor": fb["actor_id"],
                "text": fb["feedback_text"],
                "scope": fb.get("target_scope"),
                "created_at": fb["created_at"],
                "status": "recorded",
            }
        )
    pw = _product_workspace(run)
    if pw.get("plan_revision_instruction"):
        conversation.append(
            {
                "id": f"plan-rev-{run.id}",
                "type": "plan_revision_requested",
                "actor": pw.get("plan_approved_by") or "reviewer",
                "text": str(pw.get("plan_revision_instruction")),
                "created_at": pw.get("plan_approved_at") or "",
                "status": "open",
            }
        )
    if pw.get("plan_approved_at"):
        conversation.append(
            {
                "id": f"plan-appr-{run.id}",
                "type": "plan_approved",
                "actor": str(pw.get("plan_approved_by") or "reviewer"),
                "text": "Test-design plan approved.",
                "created_at": str(pw["plan_approved_at"]),
                "status": "addressed",
            }
        )

    gaps = 0
    analysis = get_analysis_for_ui(db, run.id)
    if analysis and isinstance(analysis.get("content"), dict):
        gaps = len(analysis["content"].get("missing_information") or [])

    automation_candidates = sum(1 for tc in test_cases if tc.get("automation_suitability") == "candidate")

    review_summary = {
        "status": stage,
        "current_version": current_ver,
        "test_case_count": len(test_cases),
        "gaps_count": gaps,
        "automation_candidate_count": automation_candidates,
        "next_actions": derive_next_actions(run),
        "workflow_status": run.status,
    }

    for tc in test_cases:
        ext = published_ids.get(tc["registry_key"])
        if ext:
            tc["status"] = "published"
            tc["external_id"] = ext

    return {
        "workflow_run_id": str(run.id),
        "review_summary": review_summary,
        "test_cases": test_cases,
        "conversation": conversation,
        "versions": versions,
        "publication": {
            "published_count": sum(1 for r in records if r.publication_status == TestCasePublicationStatus.PUBLISHED.value),
            "records": [record_to_api_dict(r) for r in records],
        },
    }


def request_test_case_revision(
    db: Session,
    run_id: uuid.UUID,
    body: WorkspaceRevisionBody,
) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != WorkflowRunStatus.AWAITING_APPROVAL.value:
        raise ValueError("invalid_run_state")

    action: Literal["refine", "regenerate"] = body.action if body.action in ("refine", "regenerate") else "refine"
    appr = db.scalar(
        select(Approval).where(
            Approval.workflow_run_id == run.id,
            Approval.status == ApprovalStatus.PENDING.value,
        )
    )
    if appr is None:
        raise ValueError("no_pending_approval")

    current = tdv.get_current_version(db, run.id)
    if current is None:
        raise ValueError("no_test_design_version")
    current_art = db.get(AgentArtifact, current.artifact_id)
    if current_art is None:
        raise ValueError("current_artifact_missing")

    new_json = compute_new_test_design_json(
        db, run, current_art, action=action, feedback_text=body.instruction
    )
    fb_row, next_ver_num = apply_workspace_evolution(
        db,
        run,
        appr=appr,
        action=action,
        feedback_text=body.instruction,
        target_scope=body.scope,
        actor_id=body.actor_id,
        new_json=new_json,
    )
    _set_stage(run, TestDesignWorkspaceStage.AWAITING_TEST_CASE_REVIEW.value)
    db.flush()
    return {
        "ok": True,
        "new_version_number": next_ver_num,
        "feedback_id": str(fb_row.id),
        "action": action,
    }


def approve_test_design(db: Session, run_id: uuid.UUID, body: WorkspaceApproveBody) -> Approval:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    gs = _graph_state(run)
    aid = gs.get("approval_id")
    if not aid:
        appr = db.scalar(
            select(Approval).where(
                Approval.workflow_run_id == run.id,
                Approval.status == ApprovalStatus.PENDING.value,
            )
        )
        if appr is None:
            raise ValueError("no_pending_approval")
        aid = str(appr.id)

    current = tdv.get_current_version(db, run.id)
    appr_row = db.get(Approval, uuid.UUID(str(aid)))
    if appr_row and current and appr_row.artifact_id != current.artifact_id:
        raise ValueError("stale_version_not_approvable")

    row = approval_service.approve(db, uuid.UUID(str(aid)), actor_id=body.actor_id, notes=body.notes)
    _set_stage(run, TestDesignWorkspaceStage.APPROVED.value)
    run.current_step = "approved"
    db.flush()
    return row


def publish_test_design(db: Session, run_id: uuid.UUID, *, actor_id: str) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != WorkflowRunStatus.COMPLETED.value:
        raise ValueError("run_not_approved")

    appr = db.scalar(
        select(Approval).where(
            Approval.workflow_run_id == run.id,
            Approval.status == ApprovalStatus.APPROVED.value,
        )
    )
    if appr is None:
        raise ValueError("approval_missing")

    _set_stage(run, TestDesignWorkspaceStage.PUBLISHING.value)
    db.flush()

    records = materialize_test_cases_from_approved_workflow(
        db, appr, actor_id=actor_id, auto_publish=False
    )
    published = 0
    for rec in records:
        if rec.publication_status != TestCasePublicationStatus.PUBLISHED.value:
            try:
                publish_test_case_record(db, rec.id, actor_id=actor_id)
                published += 1
            except Exception:
                pass

    _set_stage(run, TestDesignWorkspaceStage.AUTOMATION_READY.value)
    run.current_step = "automation_ready"
    db.flush()

    backlog = list_test_cases_for_api(db, status="automation_ready", workflow_run_id=run.id)
    return {"published_count": published, "automation_ready_count": len(backlog)}


def get_run_detail_for_ui(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise KeyError("run_not_found")

    gs = _graph_state(run)
    stage = map_product_stage(run)
    review_issue = db.scalar(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == run.id)
    )

    records = list_test_cases_for_api(db, workflow_run_id=run.id, limit=50)
    automation_ready = list_test_cases_for_api(db, status="automation_ready", workflow_run_id=run.id)

    return {
        "id": str(run.id),
        "story_key": _story_key(run),
        "workflow_name": run.workflow_name,
        "status": run.status,
        "current_step": run.current_step,
        "current_stage": stage,
        "next_actions": derive_next_actions(run),
        "blocked_reason": run.error_message,
        "initiated_by": run.initiated_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "source_story": {
            "story_key": _story_key(run),
            "intake_artifact_id": gs.get("intake_artifact_id"),
        },
        "requirement_analysis": get_analysis_for_ui(db, run.id),
        "test_design_plan": get_plan_for_ui(db, run.id),
        "versions": list_versions_for_api(db, run.id),
        "review_issue": {
            "review_jira_issue_key": review_issue.review_jira_issue_key if review_issue else None,
            "publish_status": review_issue.publish_status if review_issue else None,
        }
        if review_issue
        else None,
        "test_case_records": records,
        "automation_ready_test_cases": automation_ready,
        "approval_id": gs.get("approval_id"),
        "product_workspace": _product_workspace(run),
    }
