#!/usr/bin/env python3
"""Per-station road-following geometry of the bus routes in the coverage calc.

For each metro station we take the routes serving its feeder stops (<=400 m),
pull each route's road-following shape from the BMTC route-shapes shapefile,
clip it to a disk around the station and simplify it, and write a per-station
GeoJSON. The app fetches public/data/routes/<stop_id>.geojson on selection and
draws it at low opacity to show where those buses go.

Route shapes are in Kalianpur 1962 (EPSG:4144) and reprojected to WGS84; their
`route_numb` is joined to the GTFS `route_short_name` (with a prefix fallback).
"""
import csv
import io
import json
import os
import zipfile
from collections import defaultdict

import shapefile  # pyshp
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping
from shapely.ops import transform, unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BMTC = os.path.join(ROOT, "Bengaluru", "gtfs", "BMTC_July24 1 (1).zip")
SHAPES_ZIP = os.path.join(ROOT, "Bengaluru", "gtfs", "bmtc_route_shapes_re.zip")
METRO_STOPS = os.path.join(ROOT, "public", "data", "metro_stops.geojson")
OUT = os.path.join(ROOT, "public", "data", "routes")
COVERED_DIR = os.path.join(ROOT, "public", "data", "covered")

FEEDER_RADIUS = 400.0
ROUTE_CLIP = 4000.0     # m: clip route shapes to the station's catchment disk
SIMPLIFY = 15.0         # m: vertex tolerance
MAX_HEADWAY = 15.0      # min: keep only routes that come at least this often
METRO_NEAR = 500.0      # m: a route is only drawn if its path reaches the station
ROUTE_NEAR = 80.0       # m: covered dots must sit this close to a drawn route

# The route-shapes file is nominally Kalianpur 1962 but its coords are WGS84
# lon/lat with a constant ~185 m offset (pyproj applies no datum shift). This
# UTM translation, fit by ICP against the WGS84 GTFS stops, aligns shapes to
# roads to ~7 m median. Apply after projecting to metres.
OFFSET_X, OFFSET_Y = -128.0, 134.0

TO_M = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
TO_DEG = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform
KAL_TO_M = Transformer.from_crs("EPSG:4144", "EPSG:32643", always_xy=True).transform


def read(z, name):
    return csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8"))


def short_key(name):
    """Normalise a route id for joining (drop terminal-code suffix after space)."""
    return name.split(" ")[0].strip().upper()


def main():
    z = zipfile.ZipFile(BMTC)
    bus_m = {}
    for s in read(z, "stops.txt"):
        try:
            bus_m[s["stop_id"]] = TO_M(float(s["stop_lon"]), float(s["stop_lat"]))
        except (ValueError, KeyError):
            continue

    route_short = {r["route_id"]: r["route_short_name"] for r in read(z, "routes.txt")}
    trip2route = {t["trip_id"]: t["route_id"] for t in read(z, "trips.txt")}

    stop_routes = defaultdict(set)
    for st in read(z, "stop_times.txt"):
        r = trip2route.get(st["trip_id"])
        if r:
            stop_routes[st["stop_id"]].add(r)

    # Load road shapes -> projected LineStrings keyed by normalised route id.
    sz = zipfile.ZipFile(SHAPES_ZIP)
    base = "bmtc_route_shapes_re"
    sf = shapefile.Reader(
        shp=io.BytesIO(sz.read(base + ".shp")),
        dbf=io.BytesIO(sz.read(base + ".dbf")),
        shx=io.BytesIO(sz.read(base + ".shx")),
    )
    fld = [f[0] for f in sf.fields[1:]]
    i_num = fld.index("route_numb")
    i_h8 = fld.index("headway_8_")
    i_h9 = fld.index("headway_9_")
    shapes_by_key = defaultdict(list)
    dropped_infrequent = 0
    for sr in sf.iterShapeRecords():
        pts = sr.shape.points
        if len(pts) < 2:
            continue
        # keep only frequent routes: best AM headway <= MAX_HEADWAY minutes
        hs = []
        for i in (i_h8, i_h9):
            try:
                v = float(sr.record[i])
                if v > 0:
                    hs.append(v)
            except (ValueError, TypeError):
                pass
        if not hs or min(hs) > MAX_HEADWAY:
            dropped_infrequent += 1
            continue
        # shapefile points are (lon, lat); project to metres + apply datum offset
        coords = []
        for x, y in pts:
            mx, my = KAL_TO_M(x, y)
            coords.append((mx + OFFSET_X, my + OFFSET_Y))
        line = LineString(coords).simplify(SIMPLIFY, preserve_topology=False)
        shapes_by_key[short_key(sr.record[i_num])].append(line)
    print(f"frequent route shapes kept; dropped {dropped_infrequent} infrequent/unknown")

    metro = json.load(open(METRO_STOPS))["features"]
    os.makedirs(OUT, exist_ok=True)

    sizes = []
    matched_routes_total = considered_total = 0
    for f in metro:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        mx, my = TO_M(lon, lat)
        mpt = Point(mx, my)
        disk = mpt.buffer(ROUTE_CLIP)

        feeders = [
            sid for sid, (x, y) in bus_m.items()
            if (x - mx) ** 2 + (y - my) ** 2 <= FEEDER_RADIUS ** 2
        ]
        routes = set()
        for sid in feeders:
            routes |= stop_routes.get(sid, set())

        keys = {short_key(route_short[r]) for r in routes if r in route_short}
        considered_total += len(routes)

        parts = []
        matched = 0
        for k in keys:
            lines = shapes_by_key.get(k)
            if not lines:
                continue
            matched += 1
            for ln in lines:
                # only keep shapes whose path actually reaches the metro station
                if ln.distance(mpt) > METRO_NEAR:
                    continue
                clipped = ln.intersection(disk)
                if not clipped.is_empty:
                    parts.append(clipped)
        matched_routes_total += matched

        # Dissolve into one geometry so overlapping routes don't compound opacity.
        features = []
        merged = unary_union(parts) if parts else None
        if merged is not None:
            features.append({
                "type": "Feature",
                "properties": {},
                "geometry": mapping(transform(TO_DEG, merged)),
            })

        out_path = os.path.join(OUT, f"{p['stop_id']}.geojson")
        with open(out_path, "w") as fh:
            json.dump({"type": "FeatureCollection", "features": features}, fh)

        # Keep only covered-stop dots that actually sit on a drawn route, so the
        # highlighted stops stay consistent with the visible bus lines.
        covered_path = os.path.join(COVERED_DIR, f"{p['stop_id']}.geojson")
        if os.path.exists(covered_path):
            cf = json.load(open(covered_path))
            if merged is None:
                cf["features"] = []
            else:
                kept = []
                for ft in cf["features"]:
                    lon2, lat2 = ft["geometry"]["coordinates"]
                    px, py = TO_M(lon2, lat2)
                    if merged.distance(Point(px, py)) <= ROUTE_NEAR:
                        kept.append(ft)
                cf["features"] = kept
            with open(covered_path, "w") as fh:
                json.dump(cf, fh)
        sizes.append((os.path.getsize(out_path), p["name"], len(routes), matched, len(features)))

    sizes.sort(reverse=True)
    total = sum(s for s, *_ in sizes)
    print(f"wrote {len(sizes)} route files, total {total/1e6:.1f} MB")
    print(f"route shape match rate: {matched_routes_total}/{considered_total} "
          f"considered route-instances had a road shape")
    print("largest:")
    for sz_, name, nr, nm, ne in sizes[:5]:
        print(f"  {sz_/1e3:6.0f} KB  {name}  ({nr} routes, {nm} with shapes, {ne} feats)")


if __name__ == "__main__":
    main()
