// The route map: a Leaflet map over the bundled world outline, with no tiles,
// one great-circle line per route and one marker per airport.
Passport.map = (() => {
  "use strict";

  const { formatMassText, tooltipContent } = Passport.format;
  const { pricedCompleted } = Passport.aggregate;

  let routeMap;
  let routeLayer;
  let airportLayer;

  function setup(world) {
    routeMap = L.map("route-map", {
      attributionControl: false,
      minZoom: 1,
      maxZoom: 8,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      worldCopyJump: false,
      maxBounds: [
        [-85, -220],
        [85, 220],
      ],
      maxBoundsViscosity: 0.72,
    });
    routeMap.createPane("land").style.zIndex = "200";
    routeMap.createPane("routes").style.zIndex = "410";
    routeMap.createPane("airports").style.zIndex = "420";
    L.geoJSON(world, {
      pane: "land",
      interactive: false,
      style: { color: "#5f8c80", weight: 0.7, fillColor: "#173f35", fillOpacity: 1 },
    }).addTo(routeMap);
    routeLayer = L.layerGroup().addTo(routeMap);
    airportLayer = L.layerGroup().addTo(routeMap);
    L.control.scale({ position: "topright", imperial: false, maxWidth: 110 }).addTo(routeMap);
    routeMap.setView([20, 8], 1);
    requestAnimationFrame(() => routeMap.invalidateSize());
  }

  function greatCircle(start, end, steps = 48) {
    const radians = Math.PI / 180;
    const vector = (point) => {
      const lat = point.lat * radians;
      const lon = point.lon * radians;
      return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
    };
    const first = vector(start);
    const last = vector(end);
    const angle = Math.acos(
      Math.min(1, Math.max(-1, first[0] * last[0] + first[1] * last[1] + first[2] * last[2])),
    );
    if (angle < 0.000001)
      return [
        [start.lat, start.lon],
        [end.lat, end.lon],
      ];
    const denominator = Math.sin(angle);
    return Array.from({ length: steps + 1 }, (_, index) => {
      const fraction = index / steps;
      const a = Math.sin((1 - fraction) * angle) / denominator;
      const b = Math.sin(fraction * angle) / denominator;
      const x = a * first[0] + b * last[0];
      const y = a * first[1] + b * last[1];
      const z = a * first[2] + b * last[2];
      return [Math.atan2(z, Math.hypot(x, y)) / radians, Math.atan2(y, x) / radians];
    });
  }

  function splitAtDateLine(points) {
    return points
      .reduce(
        (segments, point, index) => {
          if (index && Math.abs(point[1] - points[index - 1][1]) > 180) segments.push([]);
          segments.at(-1).push(point);
          return segments;
        },
        [[]],
      )
      .filter((segment) => segment.length > 1);
  }

  function render(rows) {
    routeLayer.clearLayers();
    airportLayer.clearLayers();
    const routes = new Map();
    pricedCompleted(rows).forEach((flight) => {
      if (!flight.start || !flight.end) return;
      const existing = routes.get(flight.route) || {
        kg: 0,
        start: flight.start,
        end: flight.end,
        distanceKm: flight.distanceKm,
      };
      existing.kg += flight.kg;
      routes.set(flight.route, existing);
    });
    const airports = new Map();
    const maximumDensity = Math.max(
      1,
      ...[...routes.values()].map((route) => route.kg / Math.max(1, route.distanceKm || 1)),
    );
    routes.forEach((route, label) => {
      const density = route.kg / Math.max(1, route.distanceKm || 1);
      const weight = Math.max(0.85, (8 * density) / maximumDensity);
      splitAtDateLine(greatCircle(route.start, route.end)).forEach((segment) => {
        L.polyline(segment, {
          pane: "routes",
          color: "#f5faf7",
          opacity: 0.7,
          weight,
          lineCap: "round",
        })
          .bindTooltip(
            () =>
              tooltipContent({
                title: label,
                rows: [{ label: "CO₂e", value: formatMassText(route.kg) }],
              }),
            { opacity: 1 },
          )
          .addTo(routeLayer);
      });
      [route.start, route.end].forEach((airport) => {
        const existing = airports.get(airport.iata) || { ...airport, kg: 0 };
        existing.kg += route.kg;
        airports.set(airport.iata, existing);
      });
    });
    const maximumAirportImpact = Math.max(
      1,
      ...[...airports.values()].map((airport) => airport.kg),
    );
    // SVG stacks in draw order, so the heaviest airport is drawn last and sits on top.
    [...airports.values()]
      .sort((a, b) => a.kg - b.kg)
      .forEach((airport) => {
        const share = airport.kg / maximumAirportImpact;
        const fillColor = `hsl(${46 - 34 * share} 92% ${63 - 10 * share}%)`;
        L.circleMarker([airport.lat, airport.lon], {
          pane: "airports",
          radius: 3.5 + 4 * Math.sqrt(share),
          color: "#fff",
          weight: 1.2,
          fillColor,
          fillOpacity: 1,
        })
          .bindTooltip(
            () =>
              tooltipContent({
                title: airport.iata,
                rows: [{ label: "CO₂e connected", value: formatMassText(airport.kg) }],
              }),
            { direction: "top", offset: [0, -4], opacity: 1 },
          )
          .addTo(airportLayer);
      });
    const caption = document.querySelector(".map-caption span");
    caption.textContent = routes.size
      ? "Line area · airport colour = CO₂e"
      : "No priced routes in this period";
    requestAnimationFrame(() => routeMap.invalidateSize());
  }

  return { setup, render };
})();
