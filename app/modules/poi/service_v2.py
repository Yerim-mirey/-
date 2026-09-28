"""Registry-backed generalized POI search without changing the V1 service."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
import inspect
import json
import re
import time

from app.modules.poi.facility_catalog import FacilityCatalog, FacilityDefinition
from app.providers.baidu.poi import BaiduPOIProvider, POIProviderError, ProviderPOI
from app.schemas.common import CenterPoint
from app.schemas.poi_v2 import (
    FacilityCountV2,
    FacilityQueryFailureV2,
    POISearchDataV2,
    POISearchFailureV2,
    POISearchRequestV2,
    POISearchSuccessV2,
    POIResultV2,
)


def _key(item: ProviderPOI) -> tuple[object, ...]:
    if item.uid and item.uid.strip():
        return ("uid", item.uid.strip().removeprefix("baidu:"))
    return (
        "value",
        _normalize(item.name),
        round(item.lng, 5),
        round(item.lat, 5),
        _normalize(item.address or ""),
    )


def _poi_id(item: ProviderPOI) -> str:
    if item.uid and item.uid.strip():
        return f"baidu:{item.uid.strip().removeprefix('baidu:')}"
    raw = json.dumps(
        [item.name, item.lng, item.lat, item.address or ""],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"baidu:{sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _term_poi(
    item: ProviderPOI, facility_id: str, term: str, confidence: str
) -> POIResultV2:
    return POIResultV2(
        poi_id=_poi_id(item),
        facility_id=facility_id,
        name=item.name,
        location={"lng": item.lng, "lat": item.lat, "crs": "BD09LL"},
        address=item.address or None,
        source="baidu",
        exploratory=facility_id == "custom_query",
        query_term=term,
        classification_confidence=confidence,
    )


def _normalize(term: str) -> str:
    return re.sub(r"[\s\u3000·•,，。；;、()（）_\-/]+", "", term).casefold()


class POIServiceV2:
    # A broad request covers all 45 primary terms, then may spend at most 15
    # more keyword slots on categories where another name is most useful.
    MAX_SEARCH_TERMS_PER_REQUEST = 60
    DEFAULT_SEARCH_DEADLINE_SECONDS = 180.0
    def __init__(
        self,
        provider: BaiduPOIProvider | None = None,
        catalog: FacilityCatalog | None = None,
    ) -> None:
        self.provider = provider if provider is not None else BaiduPOIProvider()
        if catalog is None:
            from app.modules.poi.facility_catalog import load_facility_catalog

            catalog = load_facility_catalog()
        self.catalog = catalog

    def search(
        self,
        request: POISearchRequestV2,
        *,
        deadline_monotonic: float | None = None,
        priority_facility_ids: Sequence[str] = (),
    ) -> POISearchSuccessV2 | POISearchFailureV2:
        service_deadline = time.monotonic() + self.DEFAULT_SEARCH_DEADLINE_SECONDS
        deadline_monotonic = min(deadline_monotonic, service_deadline) if deadline_monotonic is not None else service_deadline
        requested = list(dict.fromkeys(request.facility_ids))
        definitions = [self.catalog.get(facility_id) for facility_id in requested]
        for definition in definitions:
            if "poi_search" not in definition.capabilities:
                raise ValueError(f"Facility {definition.id!r} does not support POI search")
        priority = set(priority_facility_ids) & set(requested)
        next_term = {definition.id: 0 for definition in definitions}
        succeeded = {definition.id: 0 for definition in definitions}
        matches_by_id: dict[str, dict[str, POIResultV2]] = {
            definition.id: {} for definition in definitions
        }
        failures_by_id: dict[str, list[FacilityQueryFailureV2]] = {
            definition.id: [] for definition in definitions
        }
        term_slots_used = 0
        first_error: Exception | None = None
        quota_exhausted = False
        deadline_exhausted = False

        def attempt(definition: FacilityDefinition) -> None:
            nonlocal term_slots_used, first_error, quota_exhausted, deadline_exhausted
            facility_id = definition.id
            term = definition.search_terms[next_term[facility_id]]
            next_term[facility_id] += 1
            term_slots_used += 1
            found, success_count, failures, error = self._search_terms(
                definition, request.center, request.search_radius_m, [term],
                deadline_monotonic=deadline_monotonic,
            )
            succeeded[facility_id] += success_count
            failures_by_id[facility_id].extend(failures)
            for poi in found:
                matches_by_id[facility_id].setdefault(poi.poi_id, poi)
            first_error = first_error or error
            quota_exhausted = quota_exhausted or any(item.error_code == "RATE_LIMITED" for item in failures)
            deadline_exhausted = deadline_exhausted or any(
                item.error_code == "RUN_DEADLINE_EXCEEDED" for item in failures
            )

        # Always give every requested facility a primary-term attempt before
        # spending the remaining budget on synonyms.
        for definition in definitions:
            if term_slots_used >= self.MAX_SEARCH_TERMS_PER_REQUEST or quota_exhausted:
                break
            if time.monotonic() >= deadline_monotonic:
                deadline_exhausted = True
                break
            attempt(definition)

        order = {definition.id: index for index, definition in enumerate(definitions)}
        while term_slots_used < self.MAX_SEARCH_TERMS_PER_REQUEST and not (quota_exhausted or deadline_exhausted):
            candidates = [
                definition for definition in definitions
                if next_term[definition.id] < len(definition.search_terms)
            ]
            if not candidates:
                break
            if time.monotonic() >= deadline_monotonic:
                deadline_exhausted = True
                break
            candidates.sort(key=lambda definition: (
                0 if definition.id in priority else 1,
                next_term[definition.id] - 1,
                0 if succeeded[definition.id] and not matches_by_id[definition.id]
                else 1 if succeeded[definition.id] else 2,
                len(matches_by_id[definition.id]),
                order[definition.id],
            ))
            attempt(candidates[0])

        counts: list[FacilityCountV2] = []
        pois: list[POIResultV2] = []
        failed_queries: list[FacilityQueryFailureV2] = []
        any_success = any(succeeded.values())
        for definition in definitions:
            facility_id = definition.id
            pois.extend(matches_by_id[facility_id].values())
            failed_queries.extend(failures_by_id[facility_id])
            remaining = definition.search_terms[next_term[facility_id]:]
            if deadline_exhausted:
                skipped_code, skipped_retryable = "RUN_DEADLINE_EXCEEDED", False
            elif quota_exhausted:
                skipped_code, skipped_retryable = "RATE_LIMITED", True
            else:
                skipped_code, skipped_retryable = "QUERY_BUDGET_LIMIT", False
            failed_queries.extend(
                FacilityQueryFailureV2(
                    facility_id=facility_id, query_term=term,
                    error_code=skipped_code, retryable=skipped_retryable,
                ) for term in remaining
            )
            status = "unavailable" if not succeeded[facility_id] else (
                "partial" if failures_by_id[facility_id] or remaining else "complete"
            )
            counts.append(
                FacilityCountV2(
                    facility_id=facility_id, count=None if status == "unavailable"
                    else len(matches_by_id[facility_id]), status=status,
                )
            )

        if not any_success:
            return self._failure(first_error, failed_queries)
        return POISearchSuccessV2(
            ok=True,
            data=POISearchDataV2(
                center=request.center, pois=pois, counts=counts, failed_queries=failed_queries
            ),
        )

    def search_exploratory(
        self,
        term: str,
        center: CenterPoint,
        radius_m: int,
        *,
        deadline_monotonic: float | None = None,
    ) -> POISearchSuccessV2 | POISearchFailureV2:
        if deadline_monotonic is None:
            deadline_monotonic = time.monotonic() + self.DEFAULT_SEARCH_DEADLINE_SECONDS
        definition = self.catalog.exploratory_definition(term)
        matches, succeeded_terms, failures, error = self._search_terms(
            definition, center, radius_m,
            definition.search_terms[: self.MAX_SEARCH_TERMS_PER_REQUEST],
            deadline_monotonic=deadline_monotonic,
        )
        if succeeded_terms == 0:
            return self._failure(error, failures)
        return POISearchSuccessV2(
            ok=True,
            data=POISearchDataV2(
                center=center,
                pois=matches,
                counts=[],
                exploratory_query=term.strip(),
                failed_queries=failures,
            ),
        )

    def _search_terms(
        self,
        definition: FacilityDefinition,
        center: CenterPoint,
        radius_m: int,
        terms: tuple[str, ...] | list[str],
        *,
        deadline_monotonic: float,
    ) -> tuple[list[POIResultV2], int, list[FacilityQueryFailureV2], Exception | None]:
        by_key: dict[tuple[object, ...], POIResultV2] = {}
        successes = 0
        failures: list[FacilityQueryFailureV2] = []
        first_error: Exception | None = None
        for index, term in enumerate(terms):
            if time.monotonic() >= deadline_monotonic:
                failures.extend(
                    FacilityQueryFailureV2(
                        facility_id=definition.id,
                        query_term=remaining_term,
                        error_code="RUN_DEADLINE_EXCEEDED",
                        retryable=False,
                    )
                    for remaining_term in terms[index:]
                )
                break
            try:
                search = self.provider.search
                if "deadline_monotonic" in inspect.signature(search).parameters:
                    records = search(
                        term, center, radius_m,
                        deadline_monotonic=deadline_monotonic,
                    )
                else:
                    records = search(term, center, radius_m)
                if not isinstance(records, list):
                    raise POIProviderError("INVALID_RESPONSE", "invalid provider response", False)
                successes += 1
                for record in records:
                    if not isinstance(record, ProviderPOI):
                        raise POIProviderError("INVALID_RESPONSE", "invalid provider record", False)
                    # A keyword hit is useful category evidence, but Baidu
                    # does not return a verified facility taxonomy here.
                    confidence = "medium"
                    by_key.setdefault(
                        _key(record), _term_poi(record, definition.id, term, confidence)
                    )
            except Exception as exc:
                if not isinstance(exc, POIProviderError):
                    raise
                if exc.partial_results:
                    successes += 1
                    for record in exc.partial_results:
                        if not isinstance(record, ProviderPOI):
                            raise POIProviderError(
                                "INVALID_RESPONSE", "invalid partial provider record", False
                            )
                        by_key.setdefault(
                            _key(record), _term_poi(record, definition.id, term, "medium")
                        )
                failures.append(
                    FacilityQueryFailureV2(
                        facility_id=definition.id,
                        query_term=term,
                        error_code=str(exc.code),
                        retryable=bool(exc.retryable),
                    )
                )
                first_error = first_error or exc
                if str(exc.code) == "RATE_LIMITED":
                    failures.extend(
                        FacilityQueryFailureV2(
                            facility_id=definition.id,
                            query_term=remaining_term,
                            error_code="RATE_LIMITED",
                            retryable=True,
                        )
                        for remaining_term in terms[index + 1 :]
                    )
                    break
        return list(by_key.values()), successes, failures, first_error

    @staticmethod
    def _failure(
        error: Exception | None,
        failed_queries: list[FacilityQueryFailureV2] | None = None,
    ) -> POISearchFailureV2:
        failures = failed_queries or []
        code = getattr(error, "code", None)
        if code is None and failures:
            code = failures[0].error_code
        code = code or "PROVIDER_ERROR"
        message = (
            "本次检索达到时间预算，已保留可用的部分结果。"
            if str(code) == "RUN_DEADLINE_EXCEEDED"
            else "百度地图 POI 服务请求失败。"
        )
        retryable = bool(getattr(error, "retryable", code != "RUN_DEADLINE_EXCEEDED"))
        return POISearchFailureV2(
            ok=False,
            error_code=str(code),
            message=message,
            retryable=retryable,
            failed_queries=failures,
        )
