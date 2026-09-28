"""Versioned canonical facility catalog for community life-circle queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any


CAPABILITIES = frozenset({"poi_search", "routing", "coverage", "diagnosis", "planning"})
_TOKEN = re.compile(r"[\s\u3000·•,，。；;、()（）_\-/]+")


def _normalize(value: str) -> str:
    return _TOKEN.sub("", value).casefold()


@dataclass(frozen=True, slots=True)
class FacilityGroup:
    id: str
    name_zh: str


@dataclass(frozen=True, slots=True)
class FacilityDefinition:
    id: str
    group_id: str
    name_zh: str
    aliases: tuple[str, ...]
    search_terms: tuple[str, ...]
    capabilities: frozenset[str]
    metric_profile: str | None
    data_notes: str
    basis: str
    exploratory: bool = False


class FacilityCatalog:
    def __init__(
        self,
        *,
        version: str,
        groups: tuple[FacilityGroup, ...],
        facilities: tuple[FacilityDefinition, ...],
    ) -> None:
        self.version = version
        self.groups = groups
        self.facilities = facilities
        self._by_id = {item.id: item for item in facilities}
        self._aliases: dict[str, str] = {}
        self._group_ids = {item.id for item in groups}
        for facility in facilities:
            for name in (facility.id, facility.name_zh, *facility.aliases):
                normalized = _normalize(name)
                existing = self._aliases.get(normalized)
                if existing is not None and existing != facility.id:
                    raise ValueError(f"Facility alias {name!r} maps to multiple IDs")
                self._aliases[normalized] = facility.id
        self._validate()

    def _validate(self) -> None:
        if not self.version.strip() or not self.groups or not self.facilities:
            raise ValueError("Facility catalog requires version, groups, and facilities")
        if len(self._group_ids) != len(self.groups):
            raise ValueError("Facility group IDs must be unique")
        if len(self._by_id) != len(self.facilities):
            raise ValueError("Facility IDs must be unique")
        for item in self.facilities:
            if item.group_id not in self._group_ids:
                raise ValueError(f"Unknown group {item.group_id!r} for {item.id!r}")
            if not item.id.strip() or not item.name_zh.strip() or not item.aliases:
                raise ValueError(f"Facility {item.id!r} requires an ID, name, and aliases")
            if not item.capabilities <= CAPABILITIES:
                raise ValueError(f"Unknown capability in facility {item.id!r}")
            if "poi_search" in item.capabilities and not item.search_terms:
                raise ValueError(f"Searchable facility {item.id!r} requires search terms")
            if ("coverage" in item.capabilities or "diagnosis" in item.capabilities or "planning" in item.capabilities) and not item.metric_profile:
                raise ValueError(f"Analytical facility {item.id!r} requires a metric profile")
            if "planning" in item.capabilities and "diagnosis" not in item.capabilities:
                raise ValueError(f"Planning facility {item.id!r} must support diagnosis")

    def get(self, facility_id: str) -> FacilityDefinition:
        try:
            return self._by_id[facility_id]
        except KeyError:
            raise KeyError(f"Unknown facility ID: {facility_id}") from None

    def resolve_mentions(self, mentions: list[str]) -> list[str]:
        resolved = []
        for mention in mentions:
            facility_id = self._aliases.get(_normalize(mention))
            if facility_id and facility_id not in resolved:
                resolved.append(facility_id)
        return resolved

    def unresolved_mentions(self, mentions: list[str]) -> list[str]:
        return [mention for mention in mentions if _normalize(mention) not in self._aliases]

    def exploratory_definition(self, term: str) -> FacilityDefinition:
        clean = term.strip()
        if not clean:
            raise ValueError("Exploratory facility term cannot be empty")
        return FacilityDefinition(
            id="custom_query",
            group_id="custom_query",
            name_zh=clean,
            aliases=(clean,),
            search_terms=(clean,),
            capabilities=frozenset({"poi_search", "routing"}),
            metric_profile=None,
            data_notes="目录外地图探索查询；不用于标准供给诊断或规划判断。",
            basis="user_query",
            exploratory=True,
        )


def _definition(raw: dict[str, Any]) -> FacilityDefinition:
    return FacilityDefinition(
        id=raw["id"],
        group_id=raw["group_id"],
        name_zh=raw["name_zh"],
        aliases=tuple(raw["aliases"]),
        search_terms=tuple(raw["search_terms"]),
        capabilities=frozenset(raw["capabilities"]),
        metric_profile=raw.get("metric_profile"),
        data_notes=raw["data_notes"],
        basis=raw["basis"],
    )


@lru_cache(maxsize=1)
def load_facility_catalog() -> FacilityCatalog:
    path = files("app.modules.poi").joinpath("facility_catalog.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    groups = tuple(FacilityGroup(**item) for item in raw["groups"])
    facilities = tuple(_definition(item) for item in raw["facilities"])
    return FacilityCatalog(version=raw["version"], groups=groups, facilities=facilities)
