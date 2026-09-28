"""Real V2 Gateway adapter over existing Core Tool services."""

from collections.abc import Mapping, Sequence

from app.modules.diagnosis.service_v2 import DiagnosisServiceV2
from app.modules.location.service import Geocode, resolve_location
from app.modules.poi.facility_catalog import FacilityCatalog, load_facility_catalog
from app.modules.poi.service_v2 import POIServiceV2
from app.modules.routing.service import RoutingService
from app.providers.baidu.live import build_live_providers_v2
from app.schemas.common import CenterPoint
from app.schemas.diagnosis_v2 import DiagnosisDataV2, DiagnosisRequestV2, FacilityEvidenceV2
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi_v2 import POISearchFailureV2, POISearchRequestV2, POISearchSuccessV2
from app.schemas.routing import RoutingRequest, RoutingResult


class MVPToolGatewayV2:
    """Keep V2 workflow decisions in Runtime and deterministic facts in Core."""

    def __init__(
        self,
        *,
        catalog: FacilityCatalog,
        poi_service: POIServiceV2,
        routing_service: RoutingService,
        geocode: Geocode | None = None,
        diagnosis_service: DiagnosisServiceV2 | None = None,
    ) -> None:
        self._catalog = catalog
        self._poi = poi_service
        self._routing = routing_service
        self._geocode = geocode
        self._diagnosis = diagnosis_service or DiagnosisServiceV2(catalog)

    @classmethod
    def from_baidu_ak(
        cls, ak: str, catalog: FacilityCatalog | None = None
    ) -> "MVPToolGatewayV2":
        """Build a shared-quota adapter without issuing external requests."""
        catalog = catalog or load_facility_catalog()
        providers = build_live_providers_v2(ak)
        return cls(
            catalog=catalog,
            poi_service=POIServiceV2(providers.poi, catalog),
            routing_service=RoutingService(providers.routing),
            geocode=providers.geocoding.geocode if providers.geocoding else None,
        )

    def resolve_location(
        self, request: LocationRequest, *, deadline_monotonic: float | None = None
    ) -> LocationResult:
        return resolve_location(
            request, geocode=self._geocode, deadline_monotonic=deadline_monotonic
        )

    def search_pois(
        self,
        request: POISearchRequestV2,
        *,
        deadline_monotonic: float | None = None,
        priority_facility_ids: Sequence[str] = (),
    ) -> POISearchSuccessV2 | POISearchFailureV2:
        return self._poi.search(
            request,
            deadline_monotonic=deadline_monotonic,
            priority_facility_ids=priority_facility_ids,
        )

    def explore_pois(
        self,
        term: str,
        center: CenterPoint,
        radius_m: int,
        *,
        deadline_monotonic: float | None = None,
    ) -> POISearchSuccessV2 | POISearchFailureV2:
        return self._poi.search_exploratory(
            term, center, radius_m, deadline_monotonic=deadline_monotonic
        )

    def calculate_walking_times(
        self, request: RoutingRequest, *, deadline_monotonic: float | None = None
    ) -> RoutingResult:
        return self._routing.route(request, deadline_monotonic=deadline_monotonic)

    def diagnose_community(
        self,
        request: DiagnosisRequestV2,
        evidence_by_id: Mapping[str, FacilityEvidenceV2],
    ) -> DiagnosisDataV2:
        return self._diagnosis.diagnose(
            request,
            evidence_provider=lambda definition: evidence_by_id.get(
                definition.id, FacilityEvidenceV2(status="unavailable")
            ),
            budget_units=len(request.facility_ids),
        )
