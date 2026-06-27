# Metro Boost — Plan

Visualizing **Bangalore Metro: projected vs. actual ridership**, and explaining *why*
certain stations outperform their projections while others fall short — then using
those factors to **predict** ridership for stations on upcoming lines.

## Goal

Intuitively visualize the factors that contribute to a station's ridership, by:
1. Showing every station's actual-vs-projected performance on a map heatmap.
2. Drilling into a curated set of stations to compare contributing factors.
3. Scoring each station on each factor.
4. Using those factors to predict ridership for not-yet-open stations.

## Main flow

1. **System map (overview)**
   - Full Bangalore metro map, all lines + stations.
   - Each station rendered as a **heatmap** encoding actual ÷ projected
     (over-performers hot, under-performers cold).
   - Entry point: click any station, or jump to the curated deep-dive set.

2. **Station selection (the deep-dive set)**
   - **2 top + 1 bottom performer per operational line.**
     - 2 operational lines (Purple, Green) → **6 stations.**
   - **+ 2 predicted stations** on an upcoming line or two (Yellow / Pink / Blue)
     — no actual ridership yet; we predict from factors.

3. **Factor comparison + scoring**
   For each selected station, measure and visualize contributing factors:
   - **Walkability** — isochrones (5/10/15-min walk catchment polygons on the map).
   - **Bus frequency / feeder connectivity** — nearby routes & service frequency,
     visualized (e.g. frequency rings, route count, departures/hr).
   - **Parking** — parking capacity near the station.
   - **Trip attractors** — POIs in catchment (offices/IT parks, malls, schools,
     hospitals, transit hubs).
   - _…extensible to more factors._
   - **Score** each station 0–N per factor → composite. Compare side-by-side
     (radar chart per station, ranked bars per factor).

4. **Prediction**
   - Fit the relationship between factor scores and actual performance across the
     6 known stations.
   - Apply it to the 2 upcoming-line stations to estimate their ridership.
   - Show predicted value + which factors drive it.

## The core insight to land

Make it visually obvious *why* a hot station is hot: e.g. dense walk isochrone +
high bus frequency + many trip attractors → high ridership; the cold one lacks them.

## Stack (proposed)

- **Build:** Vite + React + TypeScript
- **Map:** Leaflet + OpenStreetMap tiles (real station coordinates)
- **Charts:** Recharts (rankings, radar, comparisons); D3 for custom viz if needed
- **Isochrones:** OpenRouteService / Valhalla / Mapbox Isochrone API
  - _Decide: precompute isochrones to static GeoJSON (no runtime API key) vs. live API._
- **Styling:** Tailwind (recommended)
- **Hosting:** static — Vercel / Netlify / GitHub Pages

## Data

- **Ridership:** provided by user. Need to confirm format + fields:
  - `station`, `line`, `lat`, `lng`, `projected_ridership`, `actual_ridership`, `year`
- **Factor data — sources per factor:**
  - Walkability → routing engine isochrones (precomputed GeoJSON per station).
  - Bus frequency → BMTC routes/GTFS near station, or manual counts.
  - Parking → station parking capacity (BMRCL) / OSM parking POIs.
  - Trip attractors → OSM POIs within catchment, categorized.
- Pipeline: raw → cleaned JSON/GeoJSON in `/src/data` → loaded by app.

## Pages / sections (draft)

1. **Hero: system heatmap map** — all stations, performance-encoded.
2. **Deep-dive picker** — the 8 stations (6 known + 2 predicted), grouped by line.
3. **Factor comparison** — per-station factor panels: isochrone on map,
   bus-frequency viz, parking, attractors, + radar/score.
4. **Prediction** — the 2 upcoming stations with predicted ridership + drivers.
5. **Station detail** — full numbers + all factors for one station.

## Open questions

- [ ] Confirm station selection logic (6 known + 2 predicted — is that right?).
- [ ] Which factor data do you already have vs. need us to derive?
- [ ] Isochrones: precompute to static files, or call a live API?
- [ ] How should the score combine factors — equal weight, or weighted/fit to data?
- [ ] Single year snapshot or time series of ridership?
- [ ] Audience + tone: guided scrolly story vs. free-explore dashboard?
- [ ] Branding / visual direction?

## Milestones (draft)

1. Lock ridership data schema + load real data.
2. Scaffold Vite/React/TS + Leaflet app.
3. System heatmap map (overview + heatmap encoding).
4. Pick the 8 deep-dive stations; build factor data pipeline.
5. Factor comparison + scoring (isochrones, bus, parking, attractors, radar).
6. Prediction model for upcoming stations.
7. Polish, narrative, deploy.
