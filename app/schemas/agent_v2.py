"""V2 Agent brief contracts with generic catalog-backed facility IDs."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.common import ContractModel
from app.schemas.location import LocationInput


AgentSchemaVersionV2 = Literal["2.0"]
NonEmptyText = Annotated[str, Field(min_length=1)]


class AgentBriefRequestV2(ContractModel):
    schema_version: AgentSchemaVersionV2
    user_message: NonEmptyText = Field(max_length=4000)


class AgentIntentV2(str, Enum):
    FACILITY_QUERY = "facility_query"
    ACCESSIBILITY_QUERY = "accessibility_query"
    BLINDSPOT_QUERY = "blindspot_query"
    COMMUNITY_DIAGNOSIS = "community_diagnosis"
    PLANNING_ANALYSIS = "planning_analysis"


class BriefMissingFieldV2(str, Enum):
    LOCATION = "location"
    FACILITY_IDS = "facility_ids"


class MissingInformationV2(ContractModel):
    field: BriefMissingFieldV2 = Field(strict=False)
    message: NonEmptyText


class CapabilityLimitationV2(ContractModel):
    """A catalog facility capability withheld from standard analysis."""

    facility_id: NonEmptyText
    unsupported_capabilities: list[Literal["diagnosis", "planning"]] = Field(min_length=1)
    message: NonEmptyText


class AgentBriefV2(ContractModel):
    """Validated intent plus facilities canonicalized by the server catalog."""

    schema_version: AgentSchemaVersionV2
    intent: AgentIntentV2 = Field(strict=False)
    user_goal: NonEmptyText
    location: LocationInput | None = None
    facility_ids: list[NonEmptyText] = Field(default_factory=list)
    unresolved_mentions: list[NonEmptyText] = Field(default_factory=list)
    exploratory_terms: list[NonEmptyText] = Field(default_factory=list)
    exploratory_only: bool
    capability_limitations: list[CapabilityLimitationV2] = Field(default_factory=list)
    needs_diagnosis: bool
    needs_planning: bool
    needs_review: bool
    missing_information: list[MissingInformationV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def brief_is_consistent(self) -> "AgentBriefV2":
        if len(set(self.facility_ids)) != len(self.facility_ids):
            raise ValueError("facility_ids cannot contain duplicates")
        if len(set(self.exploratory_terms)) != len(self.exploratory_terms):
            raise ValueError("exploratory_terms cannot contain duplicates")
        if len(set(self.unresolved_mentions)) != len(self.unresolved_mentions):
            raise ValueError("unresolved_mentions cannot contain duplicates")
        if len({item.facility_id for item in self.capability_limitations}) != len(self.capability_limitations):
            raise ValueError("capability_limitations cannot repeat facility IDs")
        if any(item.facility_id not in self.facility_ids for item in self.capability_limitations):
            raise ValueError("capability limitations must refer to a selected facility")
        fields = [item.field for item in self.missing_information]
        if len(set(fields)) != len(fields):
            raise ValueError("missing_information fields cannot contain duplicates")
        if (self.location is None) != (BriefMissingFieldV2.LOCATION in fields):
            raise ValueError("location and its missing-information marker must agree")
        requires_facility = bool(self.unresolved_mentions) or (
            not self.facility_ids and not self.exploratory_terms
        )
        if requires_facility != (BriefMissingFieldV2.FACILITY_IDS in fields):
            raise ValueError("facility IDs and their missing-information marker must agree")
        if self.exploratory_only != (bool(self.exploratory_terms) and not self.facility_ids):
            raise ValueError("exploratory_only must describe the facility selection")
        if self.needs_planning and not self.needs_diagnosis:
            raise ValueError("planning requires an eligible diagnosis")
        if self.needs_review != self.needs_planning:
            raise ValueError("review is required exactly when planning is requested")
        return self
