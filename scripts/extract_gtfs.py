#!/usr/bin/env python3
"""Extract Bengaluru metro lines + stops from the BMRCL GTFS feed into GeoJSON.

Reads the local (gitignored) GTFS zip and writes two compact GeoJSON files into
src/data/ that the web app consumes:
  - metro_lines.geojson  : one LineString per route (real track geometry)
  - metro_stops.geojson  : one Point per stop, tagged with the line(s) it serves

Usage:  python3 scripts/extract_gtfs.py
"""
import csv
import io
import json
import os
import zipfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS = os.path.join(ROOT, "Bengaluru", "gtfs", "BMRCL_GTFS 1.zip")
OUT = os.path.join(ROOT, "public", "data")


def read(z, name):
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8")))


def main():
    z = zipfile.ZipFile(GTFS)
    routes = read(z, "routes.txt")
    trips = read(z, "trips.txt")
    stops = read(z, "stops.txt")
    shape_rows = read(z, "shapes.txt")
    stop_times = read(z, "stop_times.txt")

    # shape_id -> ordered list of [lon, lat]
    pts = defaultdict(list)
    for r in shape_rows:
        pts[r["shape_id"]].append(
            (int(r["shape_pt_sequence"]), float(r["shape_pt_lon"]), float(r["shape_pt_lat"]))
        )
    shape_coords = {
        sid: [[lon, lat] for _, lon, lat in sorted(p)] for sid, p in pts.items()
    }

    # route_id -> representative (longest) shape_id
    s2r = defaultdict(set)
    for t in trips:
        s2r[t["shape_id"]].add(t["route_id"])
    best = {}
    for sid, rids in s2r.items():
        for rid in rids:
            if rid not in best or len(shape_coords[sid]) > len(shape_coords[best[rid]]):
                best[rid] = sid

    # which lines each stop serves (via stop_times -> trips -> route)
    trip2route = {t["trip_id"]: t["route_id"] for t in trips}
    stop_lines = defaultdict(set)
    for st in stop_times:
        rid = trip2route.get(st["trip_id"])
        if rid:
            stop_lines[st["stop_id"]].add(rid)

    route_meta = {r["route_id"]: r for r in routes}

    lines_fc = {"type": "FeatureCollection", "features": []}
    for rid, sid in best.items():
        m = route_meta[rid]
        lines_fc["features"].append({
            "type": "Feature",
            "properties": {
                "route_id": rid,
                "name": m["route_long_name"],
                "line": m["route_short_name"],
                "color": "#" + m["route_color"],
            },
            "geometry": {"type": "LineString", "coordinates": shape_coords[sid]},
        })

    stops_fc = {"type": "FeatureCollection", "features": []}
    for s in stops:
        lines = sorted(stop_lines.get(s["stop_id"], []))
        stops_fc["features"].append({
            "type": "Feature",
            "properties": {
                "stop_id": s["stop_id"],
                "name": s["stop_name"],
                "lines": lines,
                "interchange": len(lines) > 1,
            },
            "geometry": {
                "type": "Point",
                "coordinates": [float(s["stop_lon"]), float(s["stop_lat"])],
            },
        })

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "metro_lines.geojson"), "w") as f:
        json.dump(lines_fc, f)
    with open(os.path.join(OUT, "metro_stops.geojson"), "w") as f:
        json.dump(stops_fc, f)

    print(f"wrote {len(lines_fc['features'])} lines, {len(stops_fc['features'])} stops to {OUT}")


if __name__ == "__main__":
    main()
