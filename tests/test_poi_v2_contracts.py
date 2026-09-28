from importlib.util import find_spec

import pytest

from app.schemas.common import CenterPoint
from app.modules.poi.facility_catalog import load_facility_catalog


def _poi_v2_types():
    if find_spec("app.schemas.poi_v2") is None:
        pytest.fail("generic V2 POI contracts are not implemented")
    from app.schemas.poi_v2 import (
        FacilityCountV2,
        POISearchRequestV2,
        POIResultV2,
    )

    return FacilityCountV2, POISearchRequestV2, POIResultV2


def test_v2_search_accepts_catalog_ids_beyond_legacy_three():
    _, request_type, _ = _poi_v2_types()

    request = request_type(
        schema_version="2.0",
        center=CenterPoint(lng=114.3, lat=30.5, crs="BD09LL"),
        facility_ids=["kindergarten", "community_health_center", "library", "pet_hospital"],
        search_radius_m=1500,
    )

    assert request.facility_ids == [
        "kindergarten", "community_health_center", "library", "pet_hospital"
    ]


def test_v2_search_accepts_one_bounded_full_catalog_request():
    _, request_type, _ = _poi_v2_types()
    ids = [item.id for item in load_facility_catalog().facilities]
    request = request_type(
        schema_version="2.0",
        center=CenterPoint(lng=114.3, lat=30.5, crs="BD09LL"),
        facility_ids=ids,
        search_radius_m=1500,
    )
    assert len(request.facility_ids) == 45


def test_v2_count_preserves_unknown_instead_of_turning_it_into_zero():
    count_type, _, _ = _poi_v2_types()

    unknown = count_type(facility_id="kindergarten", count=None, status="unavailable")
    confirmed_zero = count_type(facility_id="library", count=0, status="complete")

    assert unknown.count is None
    assert confirmed_zero.count == 0


def test_v2_count_status_and_value_must_be_consistent():
    count_type, _, _ = _poi_v2_types()
    with pytest.raises(ValueError):
        count_type(facility_id="library", count=0, status="unavailable")
    with pytest.raises(ValueError):
        count_type(facility_id="library", count=None, status="complete")


def test_v2_exploratory_poi_is_marked_and_carries_query_term():
    _, _, poi_type = _poi_v2_types()

    poi = poi_type(
        poi_id="baidu:custom-1",
        facility_id="custom_query",
        name="示例宠物医院",
        location={"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        address=None,
        source="baidu",
        exploratory=True,
        query_term="宠物医院",
        classification_confidence="low",
    )

    assert poi.exploratory is True
    assert poi.query_term == "宠物医院"
    assert poi.classification_confidence == "low"
