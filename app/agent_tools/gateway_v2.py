"""Agent-facing V2 extension of the original Core Tool Gateway boundary."""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from app.schemas.common import CenterPoint
from app.schemas.diagnosis_v2 import DiagnosisDataV2, DiagnosisRequestV2, FacilityEvidenceV2
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi_v2 import POISearchFailureV2, POISearchRequestV2, POISearchSuccessV2
from app.schemas.routing import RoutingRequest, RoutingResult


@runtime_checkable
class ToolGatewayV2(Protocol):
    """V2 contracts over the same Location, POI, Routing and Diagnosis tools."""

    def resolve_location(
        self, request: LocationRequest, *, deadline_monotonic: float | None = None
    ) -> LocationResult: ...

    def search_pois(
        self,
        request: POISearchRequestV2,
        *,
        deadline_monotonic: float | None = None,
        priority_facility_ids: Sequence[str] = (),
    ) -> POISearchSuccessV2 | POISearchFailureV2: ...

    def explore_pois(
        self,
        term: str,
        center: CenterPoint,
        radius_m: int,
        *,
        deadline_monotonic: float | None = None,
    ) -> POISearchSuccessV2 | POISearchFailureV2: ...

    def calculate_walking_times(
        self, request: RoutingRequest, *, deadline_monotonic: float | None = None
    ) -> RoutingResult: ...

    def diagnose_community(
        self,
        request: DiagnosisRequestV2,
        evidence_by_id: Mapping[str, FacilityEvidenceV2],
    ) -> DiagnosisDataV2: ...
