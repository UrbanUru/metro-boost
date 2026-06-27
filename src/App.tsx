import { useEffect, useMemo, useState } from "react";
import MetroMap, { Feeder, StopFeatureProps, StopStyle } from "./components/MetroMap";

interface StationMetric {
  stop_id: string;
  name: string;
  lines: string[];
  feeder_stops: number;
  nearest_bus_m: number;
  n_routes: number;
  n_covered_stops: number;
  coverage_fraction: number;
  pop_catchment: number;
  pop_served: number;
  pop_unserved: number;
  pop_coverage_fraction: number;
  revenue_left: number;
  feeders: Feeder[];
}
interface Totals {
  pop_catchment: number;
  pop_served: number;
  pop_unserved: number;
  revenue_left: number;
}
interface CoverageData {
  params: { feeder_radius_m: number; stop_buffer_m: number; catchment_m: number; fare: number };
  totals: Totals;
  stations: StationMetric[];
}

// RdYlGn ramp (red = low -> green = high).
const RAMP = ["#d73027", "#f46d43", "#fdae61", "#fee08b", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850"];
function lerp(a: number, b: number, t: number) {
  return Math.round(a + (b - a) * t);
}
function hex(c: string) {
  return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
}
function rampColor(t: number) {
  const x = Math.max(0, Math.min(1, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const [r1, g1, b1] = hex(RAMP[i]);
  const [r2, g2, b2] = hex(RAMP[i + 1]);
  const f = x - i;
  return `rgb(${lerp(r1, r2, f)},${lerp(g1, g2, f)},${lerp(b1, b2, f)})`;
}

const TRIPS_PER_DAY = 2; // a round-trip commute per unserved resident
const enIN = (n: number) => Math.round(n).toLocaleString("en-IN");
function inrShort(r: number) {
  if (r >= 1e7) return `₹${(r / 1e7).toFixed(1)} Cr`;
  if (r >= 1e5) return `₹${(r / 1e5).toFixed(1)} L`;
  return `₹${enIN(r)}`;
}
function peopleShort(n: number) {
  if (n >= 1e7) return `${(n / 1e7).toFixed(1)} Cr`;
  if (n >= 1e5) return `${(n / 1e5).toFixed(1)} L`;
  return enIN(n);
}

export default function App() {
  const [cov, setCov] = useState<CoverageData | null>(null);
  const [geo, setGeo] = useState<GeoJSON.FeatureCollection | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    fetch("/data/bus_coverage.json").then((r) => r.json()).then(setCov);
    fetch("/data/bus_coverage.geojson").then((r) => r.json()).then(setGeo);
  }, []);

  const byId = useMemo(() => {
    const m = new Map<string, StationMetric>();
    cov?.stations.forEach((s) => m.set(s.stop_id, s));
    return m;
  }, [cov]);

  // primary metric = share of *residents* who can reach the station by bus
  const maxFrac = useMemo(
    () => Math.max(0.0001, ...(cov?.stations.map((s) => s.pop_coverage_fraction) ?? [1])),
    [cov]
  );
  const ranked = useMemo(
    () => [...(cov?.stations ?? [])].sort((a, b) => b.pop_coverage_fraction - a.pop_coverage_fraction),
    [cov]
  );

  const stopStyle = useMemo(() => {
    return (stopId: string, _p: StopFeatureProps): StopStyle => {
      const m = byId.get(stopId);
      const isSel = stopId === selected;
      const frac = m?.pop_coverage_fraction ?? 0;
      const t = m ? frac / maxFrac : 0;
      const r = 5 + 8 * t; // size also encodes the metric (bigger = better)
      return {
        radius: isSel ? r + 3 : r,
        color: "#1d4ed8",
        weight: isSel ? 3 : 0, // no border except on the selected stop
        fillColor: m ? rampColor(t) : "#ccc",
        fillOpacity: isSel ? 1 : 0.92,
      };
    };
  }, [byId, maxFrac, selected]);

  const selFeature = useMemo(
    () => geo?.features.find((f) => f.properties?.stop_id === selected) ?? null,
    [geo, selected]
  );
  const selMetric = selected ? byId.get(selected) ?? null : null;
  const catchment = cov?.params.catchment_m ?? 4000;
  const fare = cov?.params.fare ?? 45;
  const totals = cov?.totals;

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <img
            className="logo-img"
            src="/dult-logo.png"
            alt="DULT"
            onError={(e) => {
              const img = e.currentTarget;
              img.style.display = "none";
              const fb = img.nextElementSibling as HTMLElement | null;
              if (fb) fb.style.display = "inline-block";
            }}
          />
          <span className="logo" style={{ display: "none" }}>
            DULT
          </span>
          <span className="brandtext">
            Directorate of Urban Land Transport
            <small>Government of Karnataka</small>
          </span>
        </div>
        {totals && (
          <div className="headline">
            <div className="hl-val">{inrShort(totals.revenue_left * TRIPS_PER_DAY)}/day</div>
            <div className="hl-lbl">
              in unrealised daily fare revenue — {peopleShort(totals.pop_unserved)} residents who
              cannot reach a metro station by frequent bus × ₹{fare} × {TRIPS_PER_DAY} trips/day
            </div>
          </div>
        )}
      </header>

      <MetroMap
        stopStyle={stopStyle}
        onStopClick={(p) => setSelected(p.stop_id)}
        onClear={() => setSelected(null)}
        coverageFeature={selFeature}
        feeders={selMetric?.feeders}
        selectedStopId={selected}
        catchmentMeters={catchment}
      />

      <div className="sidebar">
        <div className="legend">
          <h1>Bus access around each station</h1>
          <p className="sub">
            Share of residents within {(catchment / 1000).toFixed(0)} km of each metro station who
            can reach it on a frequent bus. Greener is better.
          </p>
          <div className="scale">
            <span>Best</span>
            <div
              className="bar"
              style={{
                background: `linear-gradient(90deg, ${rampColor(1)}, ${rampColor(0.5)}, ${rampColor(0)})`,
              }}
            />
            <span>Worst</span>
          </div>
          <ul className="symbols">
            <li>
              <span className="sym dot" style={{ background: rampColor(0.7) }} /> Metro station
            </li>
            <li>
              <span className="sym dash" /> {(catchment / 1000).toFixed(0)} km catchment
            </li>
            <li>
              <span className="sym fill" /> Bus-stop coverage
            </li>
            <li>
              <span className="sym route" /> Frequent bus routes
            </li>
            <li>
              <span className="sym feeder" /> Feeder bus stop (≤{cov?.params.feeder_radius_m ?? 400} m)
            </li>
          </ul>
        </div>
      </div>

      {selMetric && (
        <div className="panel floating">
          <div className="panel-inner">
            <button className="close" onClick={() => setSelected(null)}>
              ×
            </button>
            <h2>{selMetric.name}</h2>
            <div className="lines">{selMetric.lines.join(" · ")} Line</div>
            <div className="hero-lbl">Citizens who can reach this station by bus</div>
            <div className="popbar">
              <div
                className="served"
                style={{ width: `${selMetric.pop_coverage_fraction * 100}%` }}
              />
            </div>
            <div className="popbar-legend">
              <span className="s-served">
                <b>{peopleShort(selMetric.pop_served)}</b> served
              </span>
              <span className="s-unserved">
                <b>{peopleShort(selMetric.pop_unserved)}</b> not served
              </span>
            </div>
            <div
              className="hero-num"
              style={{ color: rampColor(selMetric.pop_coverage_fraction / maxFrac) }}
            >
              {(selMetric.pop_coverage_fraction * 100).toFixed(0)}% served
            </div>

            <div className="money">
              <div className="money-val">{inrShort(selMetric.revenue_left * TRIPS_PER_DAY)}/day</div>
              <div className="money-lbl">
                in unrealised daily fare revenue ({enIN(selMetric.pop_unserved)} riders × ₹{fare} ×{" "}
                {TRIPS_PER_DAY} trips)
              </div>
            </div>

            <dl className="extra">
              <div>
                <dt>Residents in {(catchment / 1000).toFixed(0)} km</dt>
                <dd>{enIN(selMetric.pop_catchment)}</dd>
              </div>
              <div>
                <dt>Frequent routes</dt>
                <dd>{selMetric.n_routes}</dd>
              </div>
              <div>
                <dt>Nearest bus stop</dt>
                <dd>{selMetric.nearest_bus_m} m</dd>
              </div>
            </dl>
          </div>
        </div>
      )}

      <div className="leaderboard">
        <h2>Stations ranked</h2>
        <ol>
          {ranked.map((s, i) => (
            <li
              key={s.stop_id}
              className={s.stop_id === selected ? "active" : ""}
              onClick={() => setSelected(s.stop_id)}
            >
              <span className="rank">{i + 1}</span>
              <span
                className="chip"
                style={{ background: rampColor(s.pop_coverage_fraction / maxFrac) }}
              />
              <span className="lbname">{s.name}</span>
              <span className="lbpct">{(s.pop_coverage_fraction * 100).toFixed(0)}%</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
