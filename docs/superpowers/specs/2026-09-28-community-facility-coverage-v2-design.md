# Community Facility Coverage v2 Design

**Status:** Approved design direction; implementation is authorized. Beijing live acceptance is deferred until after the backend changes.

## Goal

Expand the 15-minute life-circle backend from three fixed facility types into a standards-led, extensible service-facility catalog that can answer common facility discovery and accessibility questions across Chinese cities, while keeping evidence limits explicit and preserving the existing v1 API.

## Product boundary

The product answers questions about community and neighborhood facilities, services, accessibility, spatial coverage, and evidence-based planning. It does not promise unrestricted answers about unrelated topics, nor does it infer opening hours, capacity, price, service quality, or operational status without a suitable data source.

The initial directory is organized into seven groups:

1. Education and children: childcare, kindergarten, primary and secondary schools, after-school care, education/training.
2. Health and medical care: community health centers/stations, clinics, hospitals, pharmacies, rehabilitation and mental-health service points.
3. Elderly care and disability support: eldercare facilities, day services, meal assistance, home-care/support services, disability support.
4. Daily commerce and convenience: wet markets, supermarkets, convenience stores, community canteens, food services, postal/express, banks, hair salons, laundry and repair services.
5. Culture, sports and green space: libraries, community cultural venues, sports venues/courts, outdoor fitness, parks/green spaces, playgrounds and public toilets.
6. Transport and municipal services: bus stops, metro stations, parking, EV charging and recycling/waste service points.
7. Community governance and safety: community service centers, neighborhood committees, police stations and fire stations/micro-stations.

The catalog is a curated baseline rather than an assertion that every concept is uniformly represented as a POI in every city. Explicit user-specified facilities outside the baseline use a bounded exploratory POI lookup path.

## Architecture

### One versioned facility catalog

Add a versioned catalog as the single source of facility metadata. Each entry has a stable canonical ID, group ID, Chinese display name, aliases/synonyms, provider search terms, enabled capabilities, metric-profile reference, data-quality notes, and source/basis metadata. The loader validates unique IDs, non-empty search terms for POI-searchable entries, and valid capability/profile combinations at startup.

The catalog marks each facility as one or more of:

- `poi_search`: provider search terms can find candidate locations.
- `routing`: routes can be requested for returned POIs.
- `coverage`: a documented service-distance profile supports spatial coverage estimation.
- `diagnosis`: evidence and policy exist to compare the facility category against a stated life-circle criterion.
- `planning`: the available evidence can support a bounded planning proposal.

A capability flag is not a claim of citywide data completeness. The response keeps provider, query, and uncertainty evidence traceable.

### Recognition and lookup

The Orchestrator extracts facility mentions, location, intent, and user goal. Deterministic catalog resolution canonicalizes known aliases. The model cannot create arbitrary canonical IDs. Ambiguous mentions are clarified. A facility absent from the curated catalog can use the exploratory path only when the request clearly names a physical facility or service point.

For catalog entries, the POI provider searches the configured terms and the service deduplicates results by provider UID, falling back to normalized name/address/coordinates. Exploratory results retain the user's query term and are marked `exploratory`; they are excluded from standard category counts and diagnosis metrics.

A failed or empty search is not interpreted as proof that a facility does not exist. Results distinguish confirmed zero from unavailable/partial/unknown data.

### Metric policies and evidence quality

Keep user-requested walking times such as 15 minutes available for ordinary accessibility queries. For diagnosis and planning, use only a facility-specific metric profile with a stated source and threshold. Do not apply the current 900-second, 1,000-meter, 200-meter-grid settings as universal adequacy standards.

When a category has POIs but no applicable diagnostic policy, answer location/count/accessibility questions and explicitly state that a standards-based supply diagnosis is unavailable. Population, service capacity, accessibility barriers, opening status, price, and service quality are not inferred from POI coordinates.

Quality is reported in separate dimensions: POI category/source confidence, search completeness, routing completeness, and metric-policy applicability. A high route success rate must not imply high POI classification confidence.

Budget exhaustion and provider failures produce partial results with explicit unknowns. They never silently become zero or “no facilities.” Existing bounded route batching and adaptive route sampling remain in use.

## API and compatibility

Keep `/api/v1` behavior and schemas for `market`, `pharmacy`, and `primary_school` unchanged as a compatibility surface.

Add `/api/v2` endpoints for the facility catalog and generalized Agent/Core operations. V2 uses canonical facility IDs as strings validated against the catalog, generic count/metric collections keyed by facility ID, explicit capabilities, and separate quality dimensions. Add a catalog read endpoint for frontend selectors and capability messaging. V1 and V2 adapt to the same application services; provider calls and analysis algorithms are not duplicated.

V1 requests that name unsupported categories do not silently narrow to the old three categories; they return a clear unsupported/clarification state. New frontend integration should use V2.

## Initial delivery boundaries

The first registry release includes the seven groups and the listed baseline types. Every entry receives an explicit capability profile. POI lookup and route accessibility can be enabled broadly where provider search terms exist; coverage/diagnosis/planning are enabled only when their metric and evidence requirements are documented. The custom exploratory path broadens location lookup without claiming standardized diagnosis.

Do not add live opening-hours, appointment, inventory, prices, population estimates, parcel-level development feasibility, or authoritative capacity analysis in this delivery.

## Verification and rollout

- Preserve all existing v1 contracts and tests.
- Add offline tests for catalog integrity, aliases, ambiguous/unsupported requests, custom exploratory behavior, provider-term deduplication, dynamic facility IDs/counts, metric capability gating, and data-quality states.
- Verify v1 and v2 endpoints independently and verify they share service behavior.
- Run the complete offline suite and contract checks. Do not make new live Baidu/LLM calls as part of implementation.
- Defer the Beijing full end-to-end run until the user requests live acceptance after the code is ready. That later run should be one bounded case, log actual provider/model usage and duration, and never auto-retry the entire case.
