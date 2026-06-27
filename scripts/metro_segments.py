#!/usr/bin/env python3
"""Split each metro line into station-to-station segments tagged with coverage.

For each line we take the trip that visits the most stations, order its stops
along the route's track geometry, and cut the track into one segment per pair of
consecutive stations. Each segment is tagged with the average bus-coverage score
of its two endpoint stations, so the app can colour-code the whole network.

Reads the local BMRCL GTFS + public/data/bus_coverage.json, writes
public/data/metro_segments.geojson.
"""
import csv
import io
import json
import os
import zipfile
from collections import defaultdict

from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping
from shapely.ops import substring, transform

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS = os.path.join(ROOT, "Bengaluru", "gtfs", "BMRCL_GTFS 1.zip")
COVERAGE = os.path.join(ROOT, "public", "data", "bus_coverage.json")
OUT = os.path.join(ROOT, "public", "data", "metro_segments.geojson")

TO_M = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
TO_DEG = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform


def read(z, name):
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8")))


def main():
    z = zipfile.ZipFile(GTFS)
    routes = {r["route_id"]: r for r in read(z, "routes.txt")}
    trips = read(z, "trips.txt")
    stops = {s["stop_id"]: s for s in read(z, "stops.txt")}
    stop_times = read(z, "stop_times.txt")
    shape_rows = read(z, "shapes.txt")

    cov = {s["stop_id"]: s["coverage_fraction"] for s in json.load(open(COVERAGE))["stations"]}

    # shape_id -> projected LineString
    pts = defaultdict(list)
    for r in shape_rows:
        pts[r["shape_id"]].append(
            (int(r["shape_pt_sequence"]), float(r["shape_pt_lon"]), float(r["shape_pt_lat"]))
        )
    shape_line = {}
    for sid, p in pts.items():
        coords = [TO_M(lon, lat) for _, lon, lat in sorted(p)]
        shape_line[sid] = LineString(coords)

    # ordered stops per trip
    trip_stops = defaultdict(list)
    for st in stop_times:
        trip_stops[st["trip_id"]].append((int(st["stop_sequence"]), st["stop_id"]))

    # per route, pick the trip with the most stops
    best_trip = {}
    for t in trips:
        rid = t["route_id"]
        n = len(trip_stops.get(t["trip_id"], []))
        if rid not in best_trip or n > best_trip[rid][1]:
            best_trip[rid] = (t, n)

    features = []
    for rid, (t, _) in best_trip.items():
        line = shape_line.get(t["shape_id"])
        if line is None:
            continue
        ordered = [sid for _, sid in sorted(trip_stops[t["trip_id"]])]
        # distance of each station along the track
        seq = []
        for sid in ordered:
            s = stops.get(sid)
            if not s:
                continue
            x, y = TO_M(float(s["stop_lon"]), float(s["stop_lat"]))
            seq.append((line.project(Point(x, y)), sid))
        seq.sort()
        rmeta = routes[rid]
        for (d0, a), (d1, b) in zip(seq, seq[1:]):
            if d1 <= d0:
                continue
            seg = substring(line, d0, d1)
            ca, cb = cov.get(a), cov.get(b)
            vals = [v for v in (ca, cb) if v is not None]
            coverage = sum(vals) / len(vals) if vals else None
            features.append({
                "type": "Feature",
                "properties": {
                    "line": rmeta["route_short_name"],
                    "line_color": "#" + rmeta["route_color"],
                    "from": stops[a]["stop_name"],
                    "to": stops[b]["stop_name"],
                    "from_id": a,
                    "to_id": b,
                    "coverage": round(coverage, 4) if coverage is not None else None,
                },
                "geometry": mapping(transform(TO_DEG, seg)),
            })

    with open(OUT, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    print(f"wrote {len(features)} segments to {OUT}")


if __name__ == "__main__":
    main()
