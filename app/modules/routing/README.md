# Routing Tool

`RoutingService` accepts one BD09LL origin and generic `RouteTarget` items. It
uses Baidu Walking RouteMatrix through an injected provider, preserves input
order, and automatically splits targets into batches of at most 50.

One failed batch becomes `unavailable` routes plus a
`PARTIAL_ROUTING_RESULTS` warning. Only complete provider failure returns a
failure envelope. A distinct target with Baidu `0/0` becomes `no_route`; an
identical origin and target may validly return a successful `0/0` route.

The provider converts public `{lng, lat}` points to Baidu `lat,lng`. It never
exposes the AK or raw Baidu response fields.
