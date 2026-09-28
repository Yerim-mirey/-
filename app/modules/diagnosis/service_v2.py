"""Capability-gated, provider-independent V2 diagnosis core."""

from collections.abc import Callable

from app.modules.poi.facility_catalog import FacilityCatalog, FacilityDefinition, load_facility_catalog
from app.schemas.diagnosis_v2 import (
    DiagnosisDataV2,
    DiagnosisRequestV2,
    FacilityEvidenceV2,
    FacilityMetricV2,
)


# Thresholds are attached to named catalog profiles; there is deliberately no
# default/fallback policy for categories that do not declare a known profile.
_METRIC_PROFILES = {
    "legacy_community_15m_1km": {"time_limit_s": 900, "service_distance_m": 1000},
}


class DiagnosisServiceV2:
    def __init__(self, catalog: FacilityCatalog | None = None) -> None:
        self.catalog = catalog if catalog is not None else load_facility_catalog()

    def diagnose(
        self,
        request: DiagnosisRequestV2,
        *,
        evidence_provider: Callable[[FacilityDefinition], FacilityEvidenceV2],
        budget_units: int | None = None,
    ) -> DiagnosisDataV2:
        """Evaluate already-collected evidence without making provider calls.

        One evidence evaluation consumes one budget unit. Facilities lacking
        both catalog authorization and a recognized metric profile are emitted
        as unavailable and never passed to ``evidence_provider``.
        """
        if budget_units is not None and budget_units < 0:
            raise ValueError("budget_units must be non-negative")

        remaining = budget_units
        metrics: list[FacilityMetricV2] = []
        for facility_id in dict.fromkeys(request.facility_ids):
            try:
                definition = self.catalog.get(facility_id)
            except KeyError:
                metrics.append(self._unavailable(facility_id, "UNKNOWN_FACILITY", ["facility_catalog"]))
                continue

            unsupported = self._unsupported_dimensions(definition)
            profile = _METRIC_PROFILES.get(definition.metric_profile or "")
            if "diagnosis" not in definition.capabilities:
                metrics.append(self._unavailable(definition.id, "DIAGNOSIS_NOT_SUPPORTED", [*unsupported, "standard_diagnosis"]))
                continue
            if definition.metric_profile is None:
                metrics.append(self._unavailable(definition.id, "METRIC_PROFILE_REQUIRED", [*unsupported, "metric_policy"]))
                continue
            if profile is None:
                metrics.append(self._unavailable(definition.id, "METRIC_PROFILE_UNSUPPORTED", [*unsupported, "metric_policy"]))
                continue
            if remaining is not None and remaining == 0:
                metrics.append(self._unavailable(definition.id, "BUDGET_EXHAUSTED", unsupported, definition.metric_profile))
                continue
            if remaining is not None:
                remaining -= 1

            evidence = evidence_provider(definition)
            if not isinstance(evidence, FacilityEvidenceV2):
                raise TypeError("evidence_provider must return FacilityEvidenceV2")
            metrics.append(self._from_evidence(definition, evidence, profile, unsupported))

        return DiagnosisDataV2(metrics=metrics, quality=self._quality(metrics))

    @staticmethod
    def _unsupported_dimensions(definition: FacilityDefinition) -> list[str]:
        result = []
        if "coverage" not in definition.capabilities:
            result.append("spatial_coverage")
        if "planning" not in definition.capabilities:
            result.append("planning_recommendation")
        return result

    @staticmethod
    def _unavailable(
        facility_id: str,
        reason_code: str,
        unsupported: list[str],
        metric_profile: str | None = None,
    ) -> FacilityMetricV2:
        return FacilityMetricV2(
            facility_id=facility_id,
            metric_profile=metric_profile,
            status="unavailable",
            unsupported_dimensions=list(dict.fromkeys(unsupported)),
            reason_code=reason_code,
        )

    @staticmethod
    def _from_evidence(definition, evidence, profile, unsupported) -> FacilityMetricV2:
        if evidence.status == "unavailable":
            return DiagnosisServiceV2._unavailable(
                definition.id, "EVIDENCE_UNAVAILABLE", unsupported, definition.metric_profile
            )
        can_route = "routing" in definition.capabilities
        can_cover = "coverage" in definition.capabilities
        poi_count = evidence.poi_count
        reachable_count = (
            sum(duration <= profile["time_limit_s"] for duration in evidence.route_durations_s)
            if can_route and evidence.route_durations_s is not None
            else None
        )
        covered_ratio = evidence.covered_ratio if can_cover else None
        output_unsupported = list(unsupported)
        if not can_route:
            output_unsupported.append("walking_accessibility")
        if can_cover and evidence.covered_ratio is None:
            output_unsupported.append("spatial_coverage_evidence")

        complete = (
            evidence.status == "complete"
            and poi_count is not None
            and (not can_route or evidence.route_durations_s is not None)
            and (not can_cover or evidence.covered_ratio is not None)
        )
        return FacilityMetricV2(
            facility_id=definition.id,
            metric_profile=definition.metric_profile,
            status="complete" if complete else "partial",
            poi_count=poi_count,
            reachable_count=reachable_count,
            covered_ratio=covered_ratio,
            time_limit_s=profile["time_limit_s"] if can_route else None,
            service_distance_m=profile["service_distance_m"] if can_cover else None,
            unsupported_dimensions=list(dict.fromkeys(output_unsupported)),
            reason_code=None if complete else "EVIDENCE_PARTIAL",
        )

    @staticmethod
    def _quality(metrics: list[FacilityMetricV2]) -> str:
        statuses = {item.status for item in metrics}
        if statuses == {"complete"}:
            return "complete"
        if statuses == {"unavailable"}:
            return "unavailable"
        if "complete" in statuses and "unavailable" in statuses:
            return "mixed"
        return "partial"
