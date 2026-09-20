"""Shared Agent v1 contracts built on deterministic Core MVP results."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.blindspot import BlindspotResult
from app.schemas.common import ContractModel, ErrorDetail, WarningItem
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.isochrone import IsochroneResult
from app.schemas.location import LocationInput, LocationResult
from app.schemas.poi import FacilityType, POISearchResult
from app.schemas.routing import RoutingResult


AgentSchemaVersion = Literal["1.0"]
NonEmptyText = Annotated[str, Field(min_length=1)]
Facility = Annotated[FacilityType, Field(strict=False)]


class AgentIntent(str, Enum):
    FACILITY_QUERY = "facility_query"
    ACCESSIBILITY_QUERY = "accessibility_query"
    BLINDSPOT_QUERY = "blindspot_query"
    COMMUNITY_DIAGNOSIS = "community_diagnosis"
    PLANNING_ANALYSIS = "planning_analysis"


class BriefMissingField(str, Enum):
    LOCATION = "location"
    FACILITY_TYPES = "facility_types"


class EvidenceKind(str, Enum):
    LOCATION = "location"
    POI = "poi"
    ROUTING = "routing"
    ISOCHRONE = "isochrone"
    BLINDSPOT = "blindspot"
    DIAGNOSIS = "diagnosis"


class MissingInformation(ContractModel):
    field: BriefMissingField = Field(strict=False)
    message: NonEmptyText


class LifeCircleBrief(ContractModel):
    schema_version: AgentSchemaVersion
    intent: AgentIntent = Field(strict=False)
    user_goal: NonEmptyText
    location: LocationInput | None = None
    facility_types: list[Facility] = Field(default_factory=list)
    needs_full_diagnosis: bool
    needs_planning: bool
    needs_review: bool
    missing_information: list[MissingInformation] = Field(default_factory=list)

    @model_validator(mode="after")
    def intent_is_actionable(self) -> "LifeCircleBrief":
        if len(set(self.facility_types)) != len(self.facility_types):
            raise ValueError("facility_types 不能重复。")
        missing = [item.field for item in self.missing_information]
        if len(set(missing)) != len(missing):
            raise ValueError("missing_information.field 不能重复。")
        if (self.location is None) != (BriefMissingField.LOCATION in missing):
            raise ValueError("location 与对应 missing_information 必须一致。")
        if (not self.facility_types) != (BriefMissingField.FACILITY_TYPES in missing):
            raise ValueError("facility_types 与对应 missing_information 必须一致。")
        expected = {
            AgentIntent.FACILITY_QUERY: (False, False, False),
            AgentIntent.ACCESSIBILITY_QUERY: (False, False, False),
            AgentIntent.BLINDSPOT_QUERY: (False, False, False),
            AgentIntent.COMMUNITY_DIAGNOSIS: (True, False, False),
            AgentIntent.PLANNING_ANALYSIS: (True, True, True),
        }[self.intent]
        if (self.needs_full_diagnosis, self.needs_planning, self.needs_review) != expected:
            raise ValueError("Intent 与 diagnosis/planning/review 标志不一致。")
        return self


class EvidenceRef(ContractModel):
    evidence_id: str = Field(pattern=r"^(location|poi|routing|isochrone|blindspot|diagnosis):[a-z0-9][a-z0-9._-]*$")
    kind: EvidenceKind = Field(strict=False)
    json_pointer: str = Field(
        min_length=2,
        pattern=r"^/(location|poi|routing|isochrone|blindspot|diagnosis)(?:/(?:[^/~]|~[01])*)*$",
    )
    summary: NonEmptyText

    @model_validator(mode="after")
    def identity_matches_kind(self) -> "EvidenceRef":
        if self.evidence_id.split(":", 1)[0] != self.kind.value:
            raise ValueError("evidence_id 前缀必须与 kind 一致。")
        tokens = [
            token.replace("~1", "/").replace("~0", "~")
            for token in self.json_pointer.split("/")[1:]
        ]
        if tokens[0] != self.kind.value:
            raise ValueError("json_pointer 根必须与 kind 一致。")
        if "root" in tokens:
            raise ValueError("json_pointer 不得暴露 RootModel.root。")
        return self


class EvidenceBundle(ContractModel):
    schema_version: AgentSchemaVersion
    bundle_id: str = Field(pattern=r"^evidence:[a-z0-9][a-z0-9._-]*$")
    refs: list[EvidenceRef] = Field(min_length=1)
    location: LocationResult | None = None
    poi: POISearchResult | None = None
    routing: RoutingResult | None = None
    isochrone: IsochroneResult | None = None
    blindspot: BlindspotResult | None = None
    diagnosis: DiagnosisResult | None = None
    warnings: list[WarningItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_is_traceable(self) -> "EvidenceBundle":
        lower_level = (self.location, self.poi, self.routing, self.isochrone, self.blindspot)
        if self.diagnosis is not None and any(item is not None for item in lower_level):
            raise ValueError("Diagnosis 证据不能与其低层 Tool 结果重复出现。")
        if self.diagnosis is None and not any(item is not None for item in lower_level):
            raise ValueError("EvidenceBundle 至少包含一个 Tool Result。")
        if len({item.evidence_id for item in self.refs}) != len(self.refs):
            raise ValueError("evidence_id 不能重复。")
        if any(getattr(self, item.kind.value) is None for item in self.refs):
            raise ValueError("EvidenceRef 必须指向 Bundle 中存在的 Tool Result。")
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("EvidenceBundle warnings 必须去重。")
        return self
