"""Agent-facing adapter over the existing Core Tool 1–6 services."""

from app.modules.blindspot.service import analyze_blindspots
from app.modules.diagnosis.service import diagnose
from app.modules.isochrone.service import IsochroneService
from app.modules.location.service import Geocode, resolve_location
from app.modules.poi.service import POIService
from app.modules.routing.service import RoutingService
from app.providers.baidu.geocoding import BaiduGeocodingProvider
from app.providers.baidu.live import build_live_providers
from app.providers.baidu.poi import BaiduPOIProvider
from app.providers.baidu.quota import BaiduQuotaGuard
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.blindspot import BlindspotRequest, BlindspotResult
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult


class MVPToolGateway:
    """Reuse one set of Core services; leave workflow decisions to Runtime."""

    def __init__(
        self,
        *,
        poi_provider: BaiduPOIProvider,
        routing_provider: BaiduRoutingProvider,
        geocode: Geocode | None = None,
    ) -> None:
        self._geocode = geocode
        self._poi = POIService(poi_provider)
        self._routing = RoutingService(routing_provider)
        self._isochrone = IsochroneService(self.calculate_walking_times)

    @classmethod
    def from_baidu_ak(cls, ak: str) -> "MVPToolGateway":
        """Build live providers without issuing a network request.

        All three providers share one per-AK quota guard, so concurrent runs on
        the same key queue behind the same console quota instead of each
        getting a private budget.
        """
        providers = build_live_providers(ak)
        return cls(
            geocode=providers.geocoding.geocode,
            poi_provider=providers.poi,
            routing_provider=providers.routing,
        )

    @property
    def routing_provider(self) -> BaiduRoutingProvider:
        return self._routing.provider

    @property
    def quota_guard(self) -> BaiduQuotaGuard | None:
        """The shared guard, when this gateway was built from a Baidu AK."""
        return self.routing_provider.quota_guard

    @property
    def routing_batch_size(self) -> int:
        """Route targets per request, bounded by the shared quota window."""
        return self._routing.batch_size

    def resolve_location(self, request: LocationRequest) -> LocationResult:
        return resolve_location(request, geocode=self._geocode)

    def search_pois(self, request: POISearchRequest) -> POISearchResult:
        return self._poi.search(request)

    def calculate_walking_times(self, request: RoutingRequest) -> RoutingResult:
        return self._routing.route(request)

    def generate_isochrone(self, request: IsochroneRequest) -> IsochroneResult:
        return self._isochrone.generate(request)

    def detect_blindspots(self, request: BlindspotRequest) -> BlindspotResult:
        return analyze_blindspots(request, self.calculate_walking_times)

    def diagnose_community(self, request: DiagnosisRequest) -> DiagnosisResult:
        return diagnose(
            request,
            resolve_location=self.resolve_location,
            search_pois=self.search_pois,
            route=self.calculate_walking_times,
            generate_isochrone=self.generate_isochrone,
            analyze_blindspots=analyze_blindspots,
        )
