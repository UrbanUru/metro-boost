#!/usr/bin/env python3
"""Per metro-station bus-network coverage metric.

For each Namma Metro station:
  1. Find every bus stop within FEEDER_RADIUS (400 m) of the station -> "feeder stops".
  2. Collect all BMTC routes serving any feeder stop -> "identified routes".
  3. Collect every bus stop those routes serve (the route reach).
  4. Buffer each of those stops by STOP_BUFFER (250 m) and union the buffers.
  5. Coverage fraction = area(union  intersect  CATCHMENT disk) / area(CATCHMENT disk),
     where CATCHMENT = 4 km around the station.

Geometry is computed in UTM 43N (EPSG:32643) so distances/areas are in metres.

Reads the local (gitignored) BMTC GTFS + the extracted metro stops, and writes:
  - public/data/bus_coverage.json      : metrics per station
  - public/data/bus_coverage.geojson   : coverage union polygon per station (WGS84)

Usage:  python3 scripts/bus_coverage.py
"""
import csv
import io
import json
import os
import zipfile
from collections import defaultdict

import shapefile  # pyshp
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform, unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BMTC = os.path.join(ROOT, "Bengaluru", "gtfs", "BMTC_July24 1 (1).zip")
SHAPES_ZIP = os.path.join(ROOT, "Bengaluru", "gtfs", "bmtc_route_shapes_re.zip")
WARDS = os.path.join(ROOT, "Bengaluru", "spatial", "Bengaluru_ward_boundaries.shp")
METRO_STOPS = os.path.join(ROOT, "public", "data", "metro_stops.geojson")
OUT = os.path.join(ROOT, "public", "data")
COVERED_OUT = os.path.join(OUT, "covered")

FEEDER_RADIUS = 400.0   # m: bus stops this close to a metro station are "feeders"
STOP_BUFFER = 250.0     # m: walk radius each covered bus stop provides
CATCHMENT = 4000.0      # m: the disk we measure coverage within
MAX_HEADWAY = 15.0      # min: only count routes that come at least this often
FARE = 45.0             # rupees: average metro fare (one trip per unserved person)

# WGS84 <-> UTM 43N (metres), valid for Bengaluru.
TO_M = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
TO_DEG = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform


def read(z, name):
    return csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8"))


def short_key(name):
    return name.split(" ")[0].strip().upper()


def frequent_route_keys():
    """Normalised route numbers that run at least every MAX_HEADWAY minutes."""
    sz = zipfile.ZipFile(SHAPES_ZIP)
    base = "bmtc_route_shapes_re"
    sf = shapefile.Reader(
        shp=io.BytesIO(sz.read(base + ".shp")),
        dbf=io.BytesIO(sz.read(base + ".dbf")),
        shx=io.BytesIO(sz.read(base + ".shx")),
    )
    fld = [f[0] for f in sf.fields[1:]]
    i_num, i_h8, i_h9 = fld.index("route_numb"), fld.index("headway_8_"), fld.index("headway_9_")
    freq = set()
    for rec in sf.records():
        hs = []
        for i in (i_h8, i_h9):
            try:
                v = float(rec[i])
                if v > 0:
                    hs.append(v)
            except (ValueError, TypeError):
                pass
        if hs and min(hs) <= MAX_HEADWAY:
            freq.add(short_key(rec[i_num]))
    return freq


def main():
    z = zipfile.ZipFile(BMTC)

    # Bus stops -> projected metres (+ name and lon/lat for feeder display).
    bus_xy = {}
    bus_info = {}
    for s in read(z, "stops.txt"):
        try:
            lon, lat = float(s["stop_lon"]), float(s["stop_lat"])
            x, y = TO_M(lon, lat)
        except (ValueError, KeyError):
            continue
        bus_xy[s["stop_id"]] = (x, y)
        bus_info[s["stop_id"]] = {"name": s.get("stop_name", ""), "lon": lon, "lat": lat}

    # only frequent routes count toward coverage
    freq_keys = frequent_route_keys()
    route_short = {r["route_id"]: r["route_short_name"] for r in read(z, "routes.txt")}
    frequent_rids = {rid for rid, sn in route_short.items() if short_key(sn) in freq_keys}

    trip2route = {t["trip_id"]: t["route_id"] for t in read(z, "trips.txt")}

    route_stops = defaultdict(set)
    stop_routes = defaultdict(set)
    for st in read(z, "stop_times.txt"):
        r = trip2route.get(st["trip_id"])
        if r and r in frequent_rids:
            sid = st["stop_id"]
            route_stops[r].add(sid)
            stop_routes[sid].add(r)

    metro = json.load(open(METRO_STOPS))["features"]

    # Ward polygons (projected) with per-area population density, for weighting
    # coverage by people rather than area.
    wsf = shapefile.Reader(WARDS)
    wfld = [f[0] for f in wsf.fields[1:]]
    i_pop = wfld.index("POP_TOTAL")
    ward_polys, ward_density = [], []
    for sr in wsf.iterShapeRecords():
        geom = transform(TO_M, shape(sr.shape.__geo_interface__))
        if geom.is_empty or geom.area <= 0:
            continue
        ward_polys.append(geom)
        ward_density.append(float(sr.record[i_pop]) / geom.area)  # people / m^2
    ward_tree = STRtree(ward_polys)

    def population_in(geom):
        if geom.is_empty:
            return 0.0
        total = 0.0
        for i in ward_tree.query(geom):
            inter = geom.intersection(ward_polys[i])
            if not inter.is_empty:
                total += ward_density[i] * inter.area
        return total

    # Simple grid index over bus stops for the 400 m feeder lookup.
    CELL = 500.0
    grid = defaultdict(list)
    for sid, (x, y) in bus_xy.items():
        grid[(int(x // CELL), int(y // CELL))].append(sid)

    def near(x, y, radius):
        out = []
        cmin, cmax = int((x - radius) // CELL), int((x + radius) // CELL)
        rmin, rmax = int((y - radius) // CELL), int((y + radius) // CELL)
        r2 = radius * radius
        for cx in range(cmin, cmax + 1):
            for cy in range(rmin, rmax + 1):
                for sid in grid.get((cx, cy), ()):
                    bx, by = bus_xy[sid]
                    if (bx - x) ** 2 + (by - y) ** 2 <= r2:
                        out.append((sid, ((bx - x) ** 2 + (by - y) ** 2) ** 0.5))
        return out

    os.makedirs(COVERED_OUT, exist_ok=True)
    metrics = []
    poly_features = []
    all_disks = []   # for deduped network totals (catchments overlap heavily)
    all_cov = []

    for f in metro:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        mx, my = TO_M(lon, lat)
        disk = Point(mx, my).buffer(CATCHMENT)
        disk_area = disk.area

        feeders = near(mx, my, FEEDER_RADIUS)
        nearest_dist = min((d for _, d in feeders), default=None)
        if nearest_dist is None:
            # fall back to the single globally nearest stop so distance is defined
            sid, d2 = min(
                ((s, (xy[0] - mx) ** 2 + (xy[1] - my) ** 2) for s, xy in bus_xy.items()),
                key=lambda t: t[1],
            )
            nearest_dist = d2 ** 0.5

        routes = set()
        for sid, _ in feeders:
            routes |= stop_routes.get(sid, set())

        covered = set()
        for r in routes:
            covered |= route_stops[r]

        # Only stops whose 250 m buffer can touch the disk matter.
        relevant_ids = [
            s
            for s in covered
            if s in bus_xy
            and ((bus_xy[s][0] - mx) ** 2 + (bus_xy[s][1] - my) ** 2) ** 0.5
            <= CATCHMENT + STOP_BUFFER
        ]
        relevant = [bus_xy[s] for s in relevant_ids]

        # per-station covered-stop points (dotted on the map)
        covered_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": bus_info[s]["name"]},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [bus_info[s]["lon"], bus_info[s]["lat"]],
                    },
                }
                for s in relevant_ids
            ],
        }
        with open(os.path.join(COVERED_OUT, f"{p['stop_id']}.geojson"), "w") as fh:
            json.dump(covered_fc, fh)

        pop_catchment = population_in(disk)
        all_disks.append(disk)
        if relevant:
            union = unary_union(
                [Point(x, y).buffer(STOP_BUFFER, quad_segs=6) for x, y in relevant]
            )
            cov_geom = union.intersection(disk)
            frac = cov_geom.area / disk_area
            pop_served = population_in(cov_geom)
            all_cov.append(cov_geom)
            # simplify for a much lighter payload (area measured pre-simplify)
            clipped = cov_geom.simplify(10.0, preserve_topology=True)
        else:
            clipped = None
            frac = 0.0
            pop_served = 0.0
        pop_frac = pop_served / pop_catchment if pop_catchment > 0 else 0.0
        pop_unserved = max(0.0, pop_catchment - pop_served)

        feeder_list = [
            {
                "stop_id": sid,
                "name": bus_info[sid]["name"],
                "lon": bus_info[sid]["lon"],
                "lat": bus_info[sid]["lat"],
                "dist_m": round(d, 1),
                "n_routes": len(stop_routes.get(sid, ())),
            }
            for sid, d in sorted(feeders, key=lambda t: t[1])
        ]

        metrics.append({
            "stop_id": p["stop_id"],
            "name": p["name"],
            "lines": p["lines"],
            "feeder_stops": len(feeders),
            "nearest_bus_m": round(nearest_dist, 1),
            "n_routes": len(routes),
            "n_covered_stops": len(covered),
            "coverage_fraction": round(frac, 4),
            "pop_catchment": round(pop_catchment),
            "pop_served": round(pop_served),
            "pop_unserved": round(pop_unserved),
            "pop_coverage_fraction": round(pop_frac, 4),
            "revenue_left": round(pop_unserved * FARE),
            "feeders": feeder_list,
        })

        if clipped is not None and not clipped.is_empty:
            poly_features.append({
                "type": "Feature",
                "properties": {
                    "stop_id": p["stop_id"],
                    "name": p["name"],
                    "coverage_fraction": round(frac, 4),
                },
                "geometry": mapping(transform(TO_DEG, clipped)),
            })

    metrics.sort(key=lambda m: m["coverage_fraction"], reverse=True)
    # Network totals on the *union* of catchments/covered areas (overlap deduped).
    net_catch = population_in(unary_union(all_disks))
    net_served = population_in(unary_union(all_cov)) if all_cov else 0.0
    net_unserved = max(0.0, net_catch - net_served)
    totals = {
        "pop_catchment": round(net_catch),
        "pop_served": round(net_served),
        "pop_unserved": round(net_unserved),
        "revenue_left": round(net_unserved * FARE),
    }
    with open(os.path.join(OUT, "bus_coverage.json"), "w") as fh:
        json.dump({
            "params": {
                "feeder_radius_m": FEEDER_RADIUS,
                "stop_buffer_m": STOP_BUFFER,
                "catchment_m": CATCHMENT,
                "fare": FARE,
            },
            "totals": totals,
            "stations": metrics,
        }, fh, indent=2)
    with open(os.path.join(OUT, "bus_coverage.geojson"), "w") as fh:
        json.dump({"type": "FeatureCollection", "features": poly_features}, fh)

    print(f"wrote metrics for {len(metrics)} stations + {len(poly_features)} polygons")
    print(f"network: {totals['pop_served']:,} served / {totals['pop_catchment']:,} in catchments")
    print(f"unserved: {totals['pop_unserved']:,} people  ->  Rs {totals['revenue_left']:,} left on the table")
    print("top 5 by population access:")
    for m in sorted(metrics, key=lambda m: -m["pop_coverage_fraction"])[:5]:
        print(f"  {m['pop_coverage_fraction']*100:5.1f}%  {m['name']}  "
              f"({m['pop_served']:,}/{m['pop_catchment']:,} people)")


if __name__ == "__main__":
    main()
