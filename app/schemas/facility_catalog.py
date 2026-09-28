"""Public, versioned DTOs for the canonical facility registry."""

from typing import Literal

from pydantic import Field

from app.schemas.common import ContractModel


class FacilityCatalogEntryV2(ContractModel):
    id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)
    aliases: list[str]
    capabilities: list[str]
    metric_profile: str | None = None
    data_notes: str = Field(min_length=1)
    basis: str = Field(min_length=1)


class FacilityCatalogGroupV2(ContractModel):
    id: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)


class FacilityCatalogResultV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    catalog_version: str = Field(min_length=1)
    groups: list[FacilityCatalogGroupV2]
    facilities: list[FacilityCatalogEntryV2]
