import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export interface StopStyle {
  radius?: number;
  color?: string;
  fillColor?: string;
  fillOpacity?: number;
  weight?: number;
}

export interface StopFeatureProps {
  stop_id: string;
  name: string;
  lines: string[];
  interchange: boolean;
}

export interface Feeder {
  stop_id: string;
  name: string;
  lon: number;
  lat: number;
  dist_m: number;
  n_routes: number;
}

interface MetroMapProps {
  /** Per-stop style override (the heatmap hook). Re-applied when it changes. */
  stopStyle?: (stopId: string, props: StopFeatureProps) => StopStyle;
  onStopClick?: (props: StopFeatureProps) => void;
  /** Clicking the map background (not a station) clears the selection. */
  onClear?: () => void;
  /** Selected station's bus-coverage polygon (WGS84 GeoJSON feature). */
  coverageFeature?: GeoJSON.Feature | null;
  /** Feeder bus stops (<=400m) of the selected station, drawn as markers. */
  feeders?: Feeder[];
  /** Selected station id — draws the catchment disk around it. */
  selectedStopId?: string | null;
  /** Catchment radius in metres for the disk overlay. */
  catchmentMeters?: number;
}

const BENGALURU: L.LatLngExpression = [12.9716, 77.5946];

function defaultStopStyle(_id: string, p: StopFeatureProps): StopStyle {
  return {
    radius: p.interchange ? 6 : 4,
    color: "#333",
    weight: 2,
    fillColor: p.interchange ? "#111" : "#fff",
    fillOpacity: 1,
  };
}

export default function MetroMap({
  stopStyle,
  onStopClick,
  onClear,
  coverageFeature,
  feeders,
  selectedStopId,
  catchmentMeters = 4000,
}: MetroMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const stopsLayerRef = useRef<L.GeoJSON | null>(null);
  const routesLayerRef = useRef<L.LayerGroup | null>(null);
  const overlayRef = useRef<L.LayerGroup | null>(null);
  const latlngRef = useRef<Record<string, L.LatLng>>({});
  const readyRef = useRef(false);

  const styleFn = stopStyle ?? defaultStopStyle;
  // keep the latest style/click handler without forcing a map rebuild
  const styleRef = useRef(styleFn);
  styleRef.current = styleFn;
  const clickRef = useRef(onStopClick);
  clickRef.current = onStopClick;
  const clearRef = useRef(onClear);
  clearRef.current = onClear;

  function applyStopStyles() {
    const layer = stopsLayerRef.current;
    if (!layer) return;
    layer.eachLayer((l) => {
      const cm = l as L.CircleMarker;
      const p = (cm.feature as GeoJSON.Feature).properties as StopFeatureProps;
      const s = styleRef.current(p.stop_id, p);
      cm.setStyle(s);
      if (s.radius != null) cm.setRadius(s.radius);
      if (p.stop_id === selectedStopId) cm.bringToFront();
    });
  }

  // Mount once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, { center: BENGALURU, zoom: 12 });
    mapRef.current = map;

    L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19,
      }
    ).addTo(map);

    // clicking the map background (not a station) clears the selection
    map.on("click", () => clearRef.current?.());

    // routes drawn first (added first) so they sit under the catchment/coverage
    routesLayerRef.current = L.layerGroup().addTo(map);
    overlayRef.current = L.layerGroup().addTo(map);

    const linesLayer = L.geoJSON(null, {
      style: (f) => ({
        color: f?.properties?.color ?? "#666",
        weight: 2,
        opacity: 0.4,
      }),
    }).addTo(map);

    const stopsLayer = L.geoJSON(null, {
      pointToLayer: (feature, latlng) => {
        const p = feature.properties as StopFeatureProps;
        latlngRef.current[p.stop_id] = latlng;
        return L.circleMarker(latlng, styleRef.current(p.stop_id, p));
      },
      onEachFeature: (feature, layer) => {
        const p = feature.properties as StopFeatureProps;
        layer.bindTooltip(p.name, { direction: "top" });
        layer.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          clickRef.current?.(p);
        });
      },
    }).addTo(map);
    stopsLayerRef.current = stopsLayer;

    Promise.all([
      fetch("/data/metro_lines.geojson").then((r) => r.json()),
      fetch("/data/metro_stops.geojson").then((r) => r.json()),
    ]).then(([lines, stops]) => {
      linesLayer.addData(lines);
      stopsLayer.addData(stops);
      readyRef.current = true;
      applyStopStyles();
      const b = linesLayer.getBounds();
      if (b.isValid()) map.fitBounds(b, { padding: [40, 40] });
    });

    return () => {
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-style stops when the style fn or selection changes.
  useEffect(() => {
    if (readyRef.current) applyStopStyles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopStyle, selectedStopId]);

  // Draw catchment disk + coverage polygon + considered bus routes.
  useEffect(() => {
    const overlay = overlayRef.current;
    const routesLayer = routesLayerRef.current;
    const map = mapRef.current;
    if (!overlay || !routesLayer || !map) return;
    overlay.clearLayers();
    routesLayer.clearLayers();
    if (!selectedStopId) return;

    // considered frequent bus routes, dissolved into one feature so overlapping
    // corridors keep a uniform opacity (fetched on demand)
    let cancelled = false;
    fetch(`/data/routes/${selectedStopId}.geojson`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        L.geoJSON(data, {
          style: { color: "#334155", weight: 1.4, opacity: 0.55 },
          interactive: false,
        }).addTo(routesLayer);
      })
      .catch(() => {});

    // small dots at every covered bus stop included in the calculation
    fetch(`/data/covered/${selectedStopId}.geojson`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        L.geoJSON(data, {
          pointToLayer: (_f, latlng) =>
            L.circleMarker(latlng, {
              radius: 2,
              color: "#15803d",
              weight: 0,
              fillColor: "#15803d",
              fillOpacity: 0.4,
            }),
          interactive: false,
        }).addTo(overlay);
      })
      .catch(() => {});

    const center = latlngRef.current[selectedStopId];
    if (center) {
      L.circle(center, {
        radius: catchmentMeters,
        color: "#1d4ed8",
        weight: 1.5,
        fill: false,
        dashArray: "6 6",
      }).addTo(overlay);
    }
    if (coverageFeature) {
      L.geoJSON(coverageFeature, {
        style: {
          color: "#15803d",
          weight: 1,
          fillColor: "#22c55e",
          fillOpacity: 0.35,
        },
      }).addTo(overlay);
    }
    // feeder bus stops (<=400m) we considered, named
    (feeders ?? []).forEach((fd) => {
      L.circleMarker([fd.lat, fd.lon], {
        radius: 5,
        color: "#fff",
        weight: 1.5,
        fillColor: "#ea580c",
        fillOpacity: 1,
      })
        .bindTooltip(`${fd.name} · ${Math.round(fd.dist_m)} m · ${fd.n_routes} routes`, {
          direction: "top",
        })
        .addTo(overlay);
    });
    if (center) {
      map.flyToBounds(
        center.toBounds(catchmentMeters * 2.4),
        { padding: [20, 20], duration: 0.6 }
      );
    }

    return () => {
      cancelled = true;
    };
  }, [coverageFeature, feeders, selectedStopId, catchmentMeters]);

  return <div className="map" ref={containerRef} />;
}
