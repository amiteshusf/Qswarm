"""
Sprint 1 LangGraph: linear shell through approval creation (no autonomous loop).

Human approve/reject is handled via FastAPI, not inside the graph.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.story_intake_agent import run_intake
from app.agents.test_design_agent import run_test_design
from app.connectors.jira_client import JiraClient
from app.core.constants import (
    ActorType,
    ArtifactType,
    AuditEventType,
    ApprovalStatus,
    WorkflowRunStatus,
)
from app.db.models.agent_artifact import AgentArtifact
from app.db.models.approval import Approval
from app.db.models.workflow_run import WorkflowRun
from app.services import audit_service
from app.services.jira_service import fetch_and_upsert_story, story_to_api_dict
from app.workflows.state import Sprint1State


class Sprint1Runner:
    """Node implementations with injected DB session and Jira client."""

    def __init__(self, db: Any, jira_client: JiraClient):
        self.db = db
        self.jira_client = jira_client

    def fetch_story(self, state: Sprint1State) -> dict[str, Any]:
        run_id = uuid.UUID(state["run_id"])
        run = self.db.get(WorkflowRun, run_id)
        if run is None:
            return {"errors": [f"workflow_run not found: {run_id}"]}
        key = state.get("jira_issue_key") or ""
        story = fetch_and_upsert_story(
            self.db,
            self.jira_client,
            key,
            workflow_run_id=run.id,
            actor_id=state.get("initiated_by") or "system",
        )
        run.jira_story_id = story.id
        run.current_step = "fetch_story"
        self.db.flush()
        js = story_to_api_dict(story)
        return {"jira_story": js, "normalized_story": js}

    def create_story_intake(self, state: Sprint1State) -> dict[str, Any]:
        run_id = uuid.UUID(state["run_id"])
        run = self.db.get(WorkflowRun, run_id)
        if run is None:
            return {"errors": ["workflow_run missing in create_story_intake"]}
        intake = run_intake(state.get("jira_story") or {})
        art = AgentArtifact(
            workflow_run_id=run.id,
            agent_name="story_intake_agent",
            artifact_type=ArtifactType.STORY_INTAKE.value,
            version=1,
            content_json=intake,
            content_text=None,
        )
        self.db.add(art)
        self.db.flush()
        run.current_step = "create_story_intake"
        audit_service.write_audit(
            self.db,
            event_type=AuditEventType.STORY_INTAKE_CREATED.value,
            actor_type=ActorType.AGENT.value,
            actor_id="story_intake_agent",
            workflow_run_id=run.id,
            step_name="create_story_intake",
            entity_type="agent_artifact",
            entity_id=str(art.id),
            payload={"artifact_type": ArtifactType.STORY_INTAKE.value},
        )
        return {"intake_artifact_id": str(art.id)}

    def create_test_design(self, state: Sprint1State) -> dict[str, Any]:
        run_id = uuid.UUID(state["run_id"])
        run = self.db.get(WorkflowRun, run_id)
        if run is None:
            return {"errors": ["workflow_run missing in create_test_design"]}
        iid = state.get("intake_artifact_id")
        if not iid:
            return {"errors": ["intake_artifact_id missing"]}
        intake_row = self.db.get(AgentArtifact, uuid.UUID(iid))
        if intake_row is None or not intake_row.content_json:
            return {"errors": ["intake artifact not found"]}
        design = run_test_design(intake_row.content_json)
        art = AgentArtifact(
            workflow_run_id=run.id,
            agent_name="test_design_agent",
            artifact_type=ArtifactType.TEST_DESIGN.value,
            version=1,
            content_json=design,
            content_text=None,
        )
        self.db.add(art)
        self.db.flush()
        run.current_step = "create_test_design"
        audit_service.write_audit(
            self.db,
            event_type=AuditEventType.TEST_DESIGN_CREATED.value,
            actor_type=ActorType.AGENT.value,
            actor_id="test_design_agent",
            workflow_run_id=run.id,
            step_name="create_test_design",
            entity_type="agent_artifact",
            entity_id=str(art.id),
            payload={"artifact_type": ArtifactType.TEST_DESIGN.value},
        )
        return {"test_design_artifact_id": str(art.id)}

    def create_approval_request(self, state: Sprint1State) -> dict[str, Any]:
        run_id = uuid.UUID(state["run_id"])
        run = self.db.get(WorkflowRun, run_id)
        if run is None:
            return {"errors": ["workflow_run missing in create_approval_request"]}
        tid = state.get("test_design_artifact_id")
        if not tid:
            return {"errors": ["test_design_artifact_id missing"]}
        art = self.db.get(AgentArtifact, uuid.UUID(tid))
        if art is None:
            return {"errors": ["test design artifact not found"]}

        appr = Approval(
            workflow_run_id=run.id,
            artifact_id=art.id,
            status=ApprovalStatus.PENDING.value,
            requested_by=state.get("initiated_by") or run.initiated_by,
        )
        self.db.add(appr)
        self.db.flush()

        run.status = WorkflowRunStatus.AWAITING_APPROVAL.value
        run.current_step = "awaiting_approval"
        self.db.flush()

        audit_service.write_audit(
            self.db,
            event_type=AuditEventType.APPROVAL_REQUESTED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=state.get("initiated_by") or run.initiated_by,
            workflow_run_id=run.id,
            step_name="create_approval_request",
            entity_type="approval",
            entity_id=str(appr.id),
            payload={"artifact_id": str(art.id)},
        )
        return {
            "approval_id": str(appr.id),
            "approval_status": ApprovalStatus.PENDING.value,
        }


def build_sprint1_graph(runner: Sprint1Runner):
    g = StateGraph(Sprint1State)
    g.add_node("fetch_story", runner.fetch_story)
    g.add_node("create_story_intake", runner.create_story_intake)
    g.add_node("create_test_design", runner.create_test_design)
    g.add_node("create_approval_request", runner.create_approval_request)
    g.set_entry_point("fetch_story")
    g.add_edge("fetch_story", "create_story_intake")
    g.add_edge("create_story_intake", "create_test_design")
    g.add_edge("create_test_design", "create_approval_request")
    g.add_edge("create_approval_request", END)
    return g.compile()
