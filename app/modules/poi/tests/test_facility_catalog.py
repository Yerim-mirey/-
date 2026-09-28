from importlib.util import find_spec

import pytest


def _catalog_api():
    if find_spec("app.modules.poi.facility_catalog") is None:
        pytest.fail("facility catalog module is not implemented")
    from app.modules.poi.facility_catalog import load_facility_catalog

    return load_facility_catalog()


def test_catalog_resolves_synonyms_to_canonical_life_circle_facilities():
    catalog = _catalog_api()

    assert catalog.resolve_mentions(["幼儿园", "社区医院", "快递点"]) == [
        "kindergarten",
        "community_health_center",
        "postal_express",
    ]


def test_catalog_includes_all_seven_groups_and_exploratory_categories():
    catalog = _catalog_api()

    assert {group.id for group in catalog.groups} == {
        "education_children",
        "health_medical",
        "elderly_disability",
        "daily_commerce",
        "culture_sports_green",
        "transport_municipal",
        "community_safety",
    }
    assert catalog.resolve_mentions(["宠物医院"]) == []
    assert catalog.unresolved_mentions(["宠物医院"]) == ["宠物医院"]


def test_catalog_distinguishes_searchable_and_diagnosable_capabilities():
    catalog = _catalog_api()

    kindergarten = catalog.get("kindergarten")
    eldercare = catalog.get("eldercare_facility")
    custom = catalog.exploratory_definition("宠物医院")

    assert "poi_search" in kindergarten.capabilities
    assert "routing" in kindergarten.capabilities
    assert "diagnosis" not in kindergarten.capabilities
    assert "coverage" not in catalog.get("market").capabilities
    assert "poi_search" in eldercare.capabilities
    assert "diagnosis" not in eldercare.capabilities
    assert custom.exploratory is True
    assert "diagnosis" not in custom.capabilities


def test_catalog_search_terms_do_not_claim_multiple_categories():
    catalog = _catalog_api()
    owners = {}
    for facility in catalog.facilities:
        for term in facility.search_terms:
            owners.setdefault(term, []).append(facility.id)

    assert {term: ids for term, ids in owners.items() if len(ids) > 1} == {}


def test_catalog_rejects_duplicate_aliases_and_unsupported_metric_capabilities():
    from app.modules.poi.facility_catalog import (
        FacilityCatalog,
        FacilityDefinition,
        FacilityGroup,
    )

    groups = (FacilityGroup(id="daily", name_zh="日常服务"),)
    first = FacilityDefinition(
        id="first", group_id="daily", name_zh="设施一", aliases=("共同名称",),
        search_terms=("设施一",), capabilities=frozenset({"poi_search"}),
        metric_profile=None, data_notes="test", basis="test",
    )
    second = FacilityDefinition(
        id="second", group_id="daily", name_zh="设施二", aliases=("共同名称",),
        search_terms=("设施二",), capabilities=frozenset({"poi_search"}),
        metric_profile=None, data_notes="test", basis="test",
    )
    with pytest.raises(ValueError, match="maps to multiple IDs"):
        FacilityCatalog(version="test", groups=groups, facilities=(first, second))

    invalid_profile = FacilityDefinition(
        id="profile_without_diagnosis", group_id="daily", name_zh="配置错误", aliases=("配置错误",),
        search_terms=("配置错误",), capabilities=frozenset({"poi_search", "planning"}),
        metric_profile="legacy_community_15m_1km", data_notes="test", basis="test",
    )
    with pytest.raises(ValueError, match="must support diagnosis"):
        FacilityCatalog(version="test", groups=groups, facilities=(invalid_profile,))
