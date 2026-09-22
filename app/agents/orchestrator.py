"""Interpret one user message as a validated Agent v1 task brief."""

from typing import Annotated

from pydantic import Field, ValidationError, model_validator

from app.agents.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from app.providers.llm import StructuredLLM
from app.schemas.agent import AgentIntent, BriefMissingField, LifeCircleBrief, MissingInformation
from app.schemas.common import ContractModel
from app.schemas.location import LocationInput
from app.schemas.poi import FacilityType


class OrchestratorOutputError(ValueError):
    """The model returned an invalid semantic extraction."""


class _SemanticExtraction(ContractModel):
    """Model-owned fields; workflow decisions are deliberately absent."""

    intent: AgentIntent = Field(strict=False)
    user_goal: str = Field(min_length=1)
    location: LocationInput | None
    facility_types: list[Annotated[FacilityType, Field(strict=False)]]
    use_standard_facilities: bool

    @model_validator(mode="after")
    def standard_scope_is_unambiguous(self) -> "_SemanticExtraction":
        if self.use_standard_facilities and (
            self.facility_types
            or self.intent
            not in {
                AgentIntent.COMMUNITY_DIAGNOSIS,
                AgentIntent.PLANNING_ANALYSIS,
                AgentIntent.BLINDSPOT_QUERY,
            }
        ):
            raise ValueError("Standard facility scope requires a broad request without named facility types")
        return self


class OrchestratorAgent:
    """Use an injected structured model and derive the public brief in Python."""

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def create_brief(self, user_message: str) -> LifeCircleBrief:
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must contain non-whitespace text")
        raw = self._llm.generate_object(
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            user_message=user_message.strip(),
            response_schema=_SemanticExtraction.model_json_schema(),
        )
        try:
            extraction = _SemanticExtraction.model_validate(raw)
            facilities = list(dict.fromkeys(extraction.facility_types))
            if extraction.use_standard_facilities:
                facilities = list(FacilityType)
            missing = []
            if extraction.location is None:
                missing.append(MissingInformation(
                    field=BriefMissingField.LOCATION,
                    message="请提供社区地址、街道或明确的 BD09LL 中心点。",
                ))
            if not facilities:
                missing.append(MissingInformation(
                    field=BriefMissingField.FACILITY_TYPES,
                    message="请指定要查询的设施类别：菜场、药店或小学。",
                ))
            needs_planning = extraction.intent is AgentIntent.PLANNING_ANALYSIS
            return LifeCircleBrief(
                schema_version="1.0",
                intent=extraction.intent,
                user_goal=extraction.user_goal,
                location=extraction.location,
                facility_types=facilities,
                needs_full_diagnosis=extraction.intent in {
                    AgentIntent.COMMUNITY_DIAGNOSIS,
                    AgentIntent.PLANNING_ANALYSIS,
                    # 盲区必须由 Diagnosis 的等时圈与盲区阶段支撑，POI 数量不足以回答。
                    AgentIntent.BLINDSPOT_QUERY,
                },
                needs_planning=needs_planning,
                needs_review=needs_planning,
                missing_information=missing,
            )
        except ValidationError:
            raise OrchestratorOutputError("Model output does not satisfy the LifeCircleBrief contract") from None
