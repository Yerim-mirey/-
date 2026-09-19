# Isochrone Tool

The Tool generates deterministic radial sample points, converts them to the
public Routing `RouteTarget`, and uses only the injected Routing Tool for
travel times. It never calls Baidu directly.

The MVP uses 24 directions, six initial radii, finite outward extension, two
midpoint refinements, and linear radial interpolation. Boundary points are
sorted by bearing and are the single source for the closed GeoJSON Polygon.

Missing samples or directions produce warnings and lower confidence. Fewer
than three bracketed directions returns a failure instead of inventing a
polygon.
