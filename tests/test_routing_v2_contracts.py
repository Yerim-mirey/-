import pytest

from app.schemas.common import CenterPoint
from app.schemas.routing_v2 import RoutingResponseV2


def _types():
    try:
        from app.schemas.routing_v2 import RoutingRequestV2
    except ImportError:
        pytest.fail("V2 routing contract is not implemented")
    return RoutingRequestV2


def test_v2_routing_request_is_bounded_and_keeps_generic_target_ids():
    request_type = _types()
    request = request_type(
        schema_version="2.0",
        origin=CenterPoint(lng=114.3, lat=30.5, crs="BD09LL"),
        targets=[{
            "target_id": "library-1",
            "facility_id": "library",
            "location": {"lng": 114.31, "lat": 30.5, "crs": "BD09LL"},
        }],
        travel_mode="walking",
    )
    assert request.targets[0].facility_id == "library"


def test_v2_routing_caps_batch_size():
    request_type = _types()
    with pytest.raises(ValueError):
        request_type(
            schema_version="2.0",
            origin={"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
            targets=[{
                "target_id": f"t{i}", "facility_id": "library",
                "location": {"lng": 114.31, "lat": 30.5, "crs": "BD09LL"},
            } for i in range(21)],
            travel_mode="walking",
        )


def test_v2_routing_response_cannot_mix_success_and_error_payloads():
    with pytest.raises(ValueError):
        RoutingResponseV2(ok=True, error_code="PROVIDER_ERROR", message="failed", retryable=False)
    with pytest.raises(ValueError):
        RoutingResponseV2(ok=False, routes=[], summary=None)
