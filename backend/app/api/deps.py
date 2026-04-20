"""FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient
from app.core.config import Settings, get_settings
from app.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_jira_client(settings: SettingsDep) -> JiraClient:
    return JiraClient(settings)


JiraClientDep = Annotated[JiraClient, Depends(get_jira_client)]
