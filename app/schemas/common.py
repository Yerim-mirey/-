"""Shared contract models used by Core MVP tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SchemaVersion = Literal["1.0"]
CoordinateReferenceSystem = Literal["BD09LL"]


class ContractModel(BaseModel):
    """Strict base model for versioned public contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class CenterPoint(ContractModel):
    """Canonical point shared between Location and downstream tools."""

    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    crs: CoordinateReferenceSystem


class WarningItem(ContractModel):
    """Non-fatal condition that may affect data completeness or quality."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ErrorDetail(ContractModel):
    """Machine-readable and human-readable details for a failed tool call."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class Meta(ContractModel):
    """Metadata common to tool responses."""

    schema_version: SchemaVersion
    provider: str | None = Field(default=None, min_length=1)
