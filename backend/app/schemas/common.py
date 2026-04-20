"""Shared API schema types."""

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class MessageResponse(BaseModel):
    message: str = Field(..., examples=["ok"])
