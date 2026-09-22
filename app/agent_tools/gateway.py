"""Stable Agent-facing boundary for Core Tool 1–6."""

from typing import Protocol, runtime_checkable

from app.schemas.blindspot import BlindspotRequest, BlindspotResult
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult


@runtime_checkable
class ToolGateway(Protocol):
    def resolve_location(self, request: LocationRequest) -> LocationResult: ...

    def search_pois(self, request: POISearchRequest) -> POISearchResult: ...

    def calculate_walking_times(self, request: RoutingRequest) -> RoutingResult: ...

    def generate_isochrone(self, request: IsochroneRequest) -> IsochroneResult: ...

    def detect_blindspots(self, request: BlindspotRequest) -> BlindspotResult: ...

    def diagnose_community(self, request: DiagnosisRequest) -> DiagnosisResult: ...
