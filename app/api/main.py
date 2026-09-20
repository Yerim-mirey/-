"""Minimal HTTP adapter for the existing Location and Diagnosis contracts."""

import os
from collections.abc import Callable
from functools import partial

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.modules.blindspot.service import analyze_blindspots
from app.modules.diagnosis.service import diagnose
from app.modules.isochrone.service import IsochroneService
from app.modules.location.service import resolve_location
from app.modules.poi.service import POIService
from app.modules.routing.service import RoutingService
from app.providers.baidu.geocoding import BaiduGeocodingProvider
from app.providers.baidu.poi import BaiduPOIProvider
from app.providers.baidu.routing import BaiduRoutingProvider
from app.schemas.common import Meta
from app.schemas.diagnosis import DiagnosisFailure, DiagnosisRequest, DiagnosisResult
from app.schemas.location import LocationFailure, LocationRequest, LocationResult


LocationRunner = Callable[[LocationRequest], LocationResult]
DiagnosisRunner = Callable[[DiagnosisRequest], DiagnosisResult]


def get_location_runner() -> LocationRunner:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    geocode = BaiduGeocodingProvider(ak).geocode if ak else None
    return partial(resolve_location, geocode=geocode)


def get_diagnosis_runner() -> DiagnosisRunner:
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        return lambda _request: _diagnosis_failure("ISOCHRONE_FAILED", "百度服务端 AK 未配置。")
    routing = RoutingService(BaiduRoutingProvider(ak))
    return partial(
        diagnose,
        resolve_location=get_location_runner(),
        search_pois=POIService(BaiduPOIProvider(ak=ak)).search,
        route=routing.route,
        generate_isochrone=IsochroneService(routing.route).generate,
        analyze_blindspots=analyze_blindspots,
    )


def _location_failure(code: str, message: str) -> LocationResult:
    return LocationResult(root=LocationFailure(
        ok=False, error={"code": code, "message": message, "retryable": False},
        warnings=[], meta=Meta(schema_version="1.0", provider=None),
    ))


def _diagnosis_failure(code: str, message: str) -> DiagnosisResult:
    return DiagnosisResult(root=DiagnosisFailure(
        ok=False, error={"code": code, "message": message, "retryable": False},
        warnings=[], meta=Meta(schema_version="1.0", provider=None),
    ))


def _allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS must list explicit origins, not '*'.")
    return origins


app = FastAPI(title="15 分钟生活圈 Core MVP API", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/v1/location/resolve":
        result = _location_failure("INVALID_REQUEST", "位置请求格式无效。")
    elif request.url.path == "/api/v1/diagnosis":
        result = _diagnosis_failure("INVALID_REQUEST", "体检请求格式无效。")
    else:
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(status_code=422, content=result.model_dump(mode="json"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/location/resolve", response_model=LocationResult)
def location_api(request: LocationRequest, run: LocationRunner = Depends(get_location_runner)) -> LocationResult:
    try:
        return run(request)
    except Exception:
        return _location_failure("PROVIDER_ERROR", "位置解析暂不可用。")


@app.post("/api/v1/diagnosis", response_model=DiagnosisResult)
def diagnosis_api(request: DiagnosisRequest, run: DiagnosisRunner = Depends(get_diagnosis_runner)) -> DiagnosisResult:
    try:
        return run(request)
    except Exception:
        return _diagnosis_failure("COMPUTATION_ERROR", "社区体检暂不可用。")
