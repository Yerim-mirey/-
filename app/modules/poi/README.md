# POI service

`POIService` is the contract-facing layer for Baidu POI search. It accepts a
validated `POISearchRequest` and calls the injected provider once per mapped
keyword. Results from overlapping keywords are merged by provider UID; records
without a UID use a stable value fingerprint.

The public entry point is `search_pois(request, provider=None)`. Tests can
inject a fake provider; production uses `BaiduPOIProvider` by default.

The current mappings are market → `菜市场`/`农贸市场`/`生鲜市场`, pharmacy →
`药店`/`药房`, and primary school → `小学`.

The service keeps the public response strict: provider failures become
`null` counts and `PARTIAL_POI_RESULTS` warnings. If every requested keyword
fails, it returns the contract's failure envelope. A successful empty search
returns a count of `0`; categories omitted from the request remain `null`
because they were not searched.

This module contains no real Baidu HTTP client or credentials. Real-provider
verification is still required before calling the POI tool complete.

To run the live check, put the server-side AK in the ignored project-root
`.env` as `BAIDU_MAP_AK=...`, then run `python scripts/verify_poi_live.py`.
The script prints counts, a few POI names, and warnings, never the AK.
