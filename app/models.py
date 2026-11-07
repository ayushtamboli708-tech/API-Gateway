from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])


class ReadyResponse(BaseModel):
    status: str = Field(examples=["ready"])


class ErrorResponse(BaseModel):
    detail: str
    request_id: str
