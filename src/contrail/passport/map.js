// The route map: Natural Earth's countries on an Equal Earth projection, in
// one SVG, with a great-circle line per route and a dot per airport. Each
// route is a GeoJSON LineString drawn through d3.geoPath, which bends it
// along the great circle and cuts it at the antimeridian.
//
// There is no pan or zoom. The view fits the routes in scope, refitting on
// every redraw, so a finger scrolls the page over the map as it does
// anywhere else. A route opens the flight panel through `select`. An
// airport is focused first: the view fits its routes and a bar offers its
// flights and a way back. Activating it again opens its flights too. The
// panel is a modal sheet, so opening it on the first click would cover the
// very view that click drew.
//
// Keyboard: the airports are one tab stop. Arrow keys move to the nearest
// airport that way, Enter or Space activates it, Escape shows every route.
// Routes have no tab stop of their own; See all lists them as buttons, as
// the rankings do for their canvases.
Passport.map = (() => {
  "use strict";

  const { formatMassText, formatNumber, massUnit } = Passport.format;
  const { pricedCompleted } = Passport.aggregate;

  const SVG = "http://www.w3.org/2000/svg";
  // world-atlas keys each country by its ISO 3166 numeric code. Antarctica
  // holds no airport anyone flies to, and on an equal-area projection it is
  // a band across the whole foot of the frame.
  const ANTARCTICA = "010";
  // How far a fit may zoom in from the whole world. One short hop would
  // otherwise fill the frame with a few blocky 1:110m coastlines.
  const MAX_ZOOM = 8;
  const SIDE_PADDING = 24;
  // The spacing, in degrees, of the longitudes tried for the map's cut.
  const CUT_STEP = 2;
  // Where a keyboard step looks: an airport off the arrow's line costs this
  // many times its sideways distance, so Right prefers the one due east.
  const OFF_AXIS = 2.5;

  const $ = (id) => document.getElementById(id);
  let frame;
  let svg;
  let land;
  let borders;
  let select = () => {};
  let listRoutes = () => {};

  // As last drawn: the routes and airports in scope, and the element each
  // is drawn as, so a hover restyles rather than redraws.
  let routes = [];
  let airports = [];
  const routeElements = new Map();
  const airportElements = new Map();
  let focused = null; // the IATA code of the airport in focus, or null
  let active = null; // the airport holding the tab stop
  let hovered = null; // { route } or { airport } under the pointer or keyboard
  let size = [0, 0];

  const element = (name, attributes = {}) => {
    const node = document.createElementNS(SVG, name);
    Object.entries(attributes).forEach(([key, value]) => {
      node.setAttribute(key, String(value));
    });
    return node;
  };

  // The charts' tooltip, hidden from assistive technology as theirs is:
  // each airport carries its figures in its accessible name.
  // `tipShown` says whether the map put it up, so a tap elsewhere takes down
  // the map's tooltip and never a chart's.
  let tipShown = false;
  function showTooltip(content, x, y) {
    Passport.charts.showTooltipAt(content, x, y);
    tipShown = true;
  }
  function hideTooltip() {
    if (tipShown) Passport.charts.hideTooltip();
    tipShown = false;
  }

  // `onList(iata)` is asked to list the routes See all stands for: those
  // of the airport in focus, or every route on the map for null.
  function setup(world, onSelect, onList = () => {}) {
    select = onSelect;
    listRoutes = onList;
    frame = $("route-map-frame");
    svg = $("route-map");
    const countries = world.objects.countries;
    land = {
      type: "FeatureCollection",
      features: topojson
        .feature(world, countries)
        .features.filter((country) => country.id !== ANTARCTICA),
    };
    // Shared borders only, so each coast is drawn once, by the land's fill.
    borders = topojson.mesh(world, countries, (a, b) => a !== b);

    svg.addEventListener("pointermove", onPointer);
    svg.addEventListener("pointerleave", () => {
      if (!svg.contains(document.activeElement)) setHover(null);
      hideTooltip();
    });
    svg.addEventListener("click", onClick);
    svg.addEventListener("keydown", onKey);
    svg.addEventListener("focusin", (event) => {
      const airport = event.target.closest(".airport");
      if (airport) onAirportFocus(airport.dataset.iata);
    });
    svg.addEventListener("focusout", () => {
      setHover(null);
      hideTooltip();
    });
    document.addEventListener("pointerdown", (event) => {
      if (!svg.contains(event.target)) hideTooltip();
    });
    $("map-reset").addEventListener("click", reset);
    $("map-list").addEventListener("click", () => listRoutes(focused));

    // Refit to the frame's new size, once per frame at most.
    let pending = false;
    new ResizeObserver(() => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        if (frame.clientWidth !== size[0] || frame.clientHeight !== size[1]) draw();
      });
    }).observe(frame);
  }

  // The routes and airports `scope` holds: priced, completed flights with
  // both ends on the map. Airports are lightest first, the order they are
  // drawn in, so the heaviest sits on top where dots overlap.
  function collect(scope) {
    const byLabel = new Map();
    pricedCompleted(scope).forEach((flight) => {
      if (!flight.start || !flight.end) return;
      const route = byLabel.get(flight.route) || {
        label: flight.route,
        kg: 0,
        flights: 0,
        start: flight.start,
        end: flight.end,
        distanceKm: flight.distanceKm,
        line: {
          type: "LineString",
          coordinates: [
            [flight.start.lon, flight.start.lat],
            [flight.end.lon, flight.end.lat],
          ],
        },
      };
      route.kg += flight.kg;
      route.flights += 1;
      byLabel.set(flight.route, route);
    });
    const byCode = new Map();
    byLabel.forEach((route) => {
      new Map([route.start, route.end].map((point) => [point.iata, point])).forEach((point) => {
        const airport = byCode.get(point.iata) || { ...point, kg: 0, flights: 0, routes: [] };
        airport.kg += route.kg;
        airport.flights += route.flights;
        airport.routes.push(route);
        byCode.set(point.iata, airport);
      });
    });
    return {
      routes: [...byLabel.values()],
      airports: [...byCode.values()].sort((a, b) => a.kg - b.kg),
    };
  }

  // The longitude to centre the view on. The projection cuts every line at
  // the longitude opposite, so the cut goes where the fewest routes cross:
  // through the Pacific for most logs, which is the map everyone knows, and
  // elsewhere when a Pacific crossing would otherwise be drawn in two
  // halves at opposite edges. A great circle that misses the poles spans
  // the shorter way between its ends' longitudes.
  function centralLongitude(shown) {
    const spans = shown.map((route) => {
      const delta = ((((route.end.lon - route.start.lon) % 360) + 540) % 360) - 180;
      return delta >= 0 ? [route.start.lon, delta] : [route.end.lon, -delta];
    });
    const crossings = (cut) =>
      spans.filter(([from, width]) => (((cut - from) % 360) + 360) % 360 < width).length;
    let best = 180;
    let fewest = crossings(best);
    for (let offset = CUT_STEP; offset <= 180 && fewest; offset += CUT_STEP) {
      [180 - offset, offset - 180].forEach((cut) => {
        const count = crossings(cut);
        if (count < fewest) {
          fewest = count;
          best = cut;
        }
      });
    }
    return best - 180;
  }

  // An Equal Earth projection fitted to `shown`, or to the world where
  // nothing is shown, inside the frame less the bars drawn over it.
  function projectionFor(shown, width, height) {
    const top = $("map-toolbar").offsetHeight + 16;
    const bottom = $("map-caption").offsetHeight + 16;
    const extent = [
      [SIDE_PADDING, top],
      [Math.max(SIDE_PADDING + 1, width - SIDE_PADDING), Math.max(top + 1, height - bottom)],
    ];
    const projection = d3.geoEqualEarth().rotate([-centralLongitude(shown), 0]);
    projection.fitExtent(extent, land);
    if (!shown.length) return projection;
    // Measured at the world's scale, which is always finite: fitExtent() on
    // the routes themselves divides by their size, and a route from an
    // airport to itself has none.
    const lines = {
      type: "FeatureCollection",
      features: shown.map((route) => ({ type: "Feature", properties: {}, geometry: route.line })),
    };
    const [[x0, y0], [x1, y1]] = d3.geoPath(projection).bounds(lines);
    if (![x0, y0, x1, y1].every(Number.isFinite)) return projection;
    const fit = Math.min(
      (extent[1][0] - extent[0][0]) / (x1 - x0),
      (extent[1][1] - extent[0][1]) / (y1 - y0),
    );
    // Never past the zoom cap, which is also where a size of zero lands.
    const zoom = Number.isFinite(fit) ? Math.min(fit, MAX_ZOOM) : MAX_ZOOM;
    const centre = projection.invert([(x0 + x1) / 2, (y0 + y1) / 2]);
    if (!centre?.every(Number.isFinite)) return projection;
    const world = { scale: projection.scale(), translate: projection.translate() };
    projection.scale(world.scale * zoom);
    const [x, y] = projection(centre);
    const [tx, ty] = projection.translate();
    projection.translate([
      tx + (extent[0][0] + extent[1][0]) / 2 - x,
      ty + (extent[0][1] + extent[1][1]) / 2 - y,
    ]);
    if (![projection.scale(), ...projection.translate()].every(Number.isFinite)) {
      projection.scale(world.scale).translate(world.translate);
    }
    return projection;
  }

  const routeTooltip = (route) => ({
    title: route.label,
    rows: [
      { label: "CO₂e", value: formatMassText(route.kg) },
      { label: "Flights", value: formatNumber(route.flights) },
    ],
  });

  const airportTooltip = (airport) => ({
    title: airport.iata,
    rows: [
      { label: "CO₂e connected", value: formatMassText(airport.kg) },
      { label: "Routes", value: formatNumber(airport.routes.length) },
      { label: "Flights", value: formatNumber(airport.flights) },
    ],
  });

  const plural = (count, noun) => `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;

  const airportName = (airport) =>
    `${airport.iata}: ${formatMassText(airport.kg)} CO₂e connected, ${plural(airport.routes.length, "route")}, ${plural(airport.flights, "flight")}`;

  function render(scope) {
    ({ routes, airports } = collect(scope));
    // A focus survives a redraw while its airport is still on the map, so a
    // scheme change keeps it; a year without the airport shows every route.
    if (!airports.some((airport) => airport.iata === focused)) focused = null;
    if (!airports.some((airport) => airport.iata === active)) active = null;
    hovered = null;
    hideTooltip();
    draw();
  }

  function draw() {
    const hadFocus = svg.contains(document.activeElement);
    const width = frame.clientWidth;
    const height = frame.clientHeight;
    size = [width, height];
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    const focus = airports.find((airport) => airport.iata === focused);
    renderBars(focus);

    const projection = projectionFor(focus ? focus.routes : routes, width, height);
    const path = d3.geoPath(projection);
    const routeLayer = element("g", { class: "routes" });
    const airportLayer = element("g", { class: "airports" });
    routeElements.clear();
    airportElements.clear();

    // Line width is CO2e per km, so a line's area, width times length, is
    // its route's CO2e.
    const density = (route) => route.kg / Math.max(1, route.distanceKm || 1);
    const maximumDensity = Math.max(0, ...routes.map(density));
    routes.forEach((route) => {
      const d = path(route.line);
      if (!d) return;
      const group = element("g", { class: "route" });
      group.dataset.route = route.label;
      group.append(
        element("path", { class: "route-hit", d }),
        element("path", {
          class: "route-line",
          d,
          "stroke-width": maximumDensity
            ? Math.max(1, (7 * density(route)) / maximumDensity).toFixed(2)
            : 1,
        }),
      );
      routeLayer.append(group);
      routeElements.set(route.label, group);
    });

    const heaviest = Math.max(0, ...airports.map((airport) => airport.kg));
    active = active || focused || airports.at(-1)?.iata || null;
    airports.forEach((airport) => {
      const [x, y] = projection([airport.lon, airport.lat]);
      const share = heaviest ? Math.sqrt(airport.kg / heaviest) : 0;
      const group = element("g", {
        class: "airport",
        role: "button",
        tabindex: airport.iata === active ? 0 : -1,
        transform: `translate(${x.toFixed(1)} ${y.toFixed(1)})`,
        "aria-label": airportName(airport),
      });
      group.dataset.iata = airport.iata;
      if (airport.iata === focused) group.setAttribute("aria-current", "true");
      const dot = element("circle", { class: "airport-dot", r: (3.5 + 3.5 * share).toFixed(2) });
      // Mixed in CSS from the scheme's two ends, so a scheme change
      // recolours every dot with no redraw.
      dot.style.fill = `color-mix(in oklab, var(--map-airport-high) ${Math.round(share * 100)}%, var(--map-airport-low))`;
      group.append(element("circle", { class: "airport-hit", r: 12 }), dot);
      airportLayer.append(group);
      airportElements.set(airport.iata, group);
    });

    svg.replaceChildren(
      element("path", { class: "map-land", d: path(land) || "" }),
      element("path", { class: "map-borders", d: path(borders) || "" }),
      routeLayer,
      airportLayer,
    );
    applyHighlight();
    if (hadFocus) airportElements.get(active)?.focus();
  }

  // The focus bar, See all, the caption and the colour key.
  function renderBars(focus) {
    $("map-focus").hidden = !focus;
    if (focus) {
      $("map-focus-label").textContent =
        `${focus.iata} · ${formatMassText(focus.kg)} CO₂e · ${plural(focus.routes.length, "route")}`;
      const flights = $("map-focus-flights");
      flights.dataset.openPanel = `airport:${focus.iata}`;
      flights.setAttribute("aria-label", `${focus.iata} flights`);
    }
    const list = $("map-list");
    list.hidden = routes.length === 0;
    list.setAttribute(
      "aria-label",
      focus ? `See all routes at ${focus.iata}` : "See all routes on the map",
    );
    $("map-caption-text").textContent = routes.length
      ? "Line area = route CO₂e"
      : "No priced routes in this period";
    $("map-legend").hidden = airports.length === 0;
    if (airports.length) {
      const unit = massUnit([airports.at(-1).kg]);
      $("map-legend-low").textContent = formatMassText(airports[0].kg, unit);
      $("map-legend-high").textContent = formatMassText(airports.at(-1).kg, unit);
    }
  }

  // Light what `hovered` names, or else the airport in focus, and dim the
  // rest: a route lights its two airports, an airport its routes and the
  // airports at their far ends.
  function applyHighlight() {
    const target = hovered || (focused ? { airport: focused } : null);
    svg.classList.toggle("has-highlight", Boolean(target));
    const litRoutes = new Set();
    const litAirports = new Set();
    const light = (route) => {
      litRoutes.add(route.label);
      litAirports.add(route.start.iata).add(route.end.iata);
    };
    if (target?.route) {
      const route = routes.find((item) => item.label === target.route);
      if (route) light(route);
    } else if (target?.airport) {
      litAirports.add(target.airport);
      airports.find((airport) => airport.iata === target.airport)?.routes.forEach(light);
    }
    routeElements.forEach((group, label) => {
      group.classList.toggle("is-lit", litRoutes.has(label));
    });
    airportElements.forEach((group, iata) => {
      group.classList.toggle("is-lit", litAirports.has(iata));
    });
  }

  function setHover(next) {
    if (next?.route === hovered?.route && next?.airport === hovered?.airport) return;
    hovered = next;
    applyHighlight();
  }

  function onPointer(event) {
    const airport = event.target.closest(".airport");
    const route = !airport && event.target.closest(".route");
    if (airport) {
      setHover({ airport: airport.dataset.iata });
      const data = airports.find((item) => item.iata === airport.dataset.iata);
      showTooltip(airportTooltip(data), event.clientX, event.clientY);
    } else if (route) {
      setHover({ route: route.dataset.route });
      const data = routes.find((item) => item.label === route.dataset.route);
      showTooltip(routeTooltip(data), event.clientX, event.clientY);
    } else {
      if (!svg.contains(document.activeElement)) setHover(null);
      hideTooltip();
    }
  }

  function onClick(event) {
    const airport = event.target.closest(".airport");
    if (airport) {
      activate(airport.dataset.iata);
      return;
    }
    const route = event.target.closest(".route");
    if (route) {
      hideTooltip();
      select(`route:${route.dataset.route}`);
    }
  }

  // The first activation focuses an airport; the next opens its flights,
  // handing the panel the airport to return focus to.
  function activate(iata) {
    if (focused === iata) {
      hideTooltip();
      select(`airport:${iata}`, airportElements.get(iata));
      return;
    }
    focused = iata;
    active = iata;
    hovered = null;
    draw();
  }

  function reset() {
    if (!focused) return;
    const returnTo = focused;
    focused = null;
    active = returnTo;
    draw();
    // The reset button hides with the focus, so focus goes to the airport.
    airportElements.get(returnTo)?.focus();
  }

  function onAirportFocus(iata) {
    if (active !== iata) {
      airportElements.get(active)?.setAttribute("tabindex", "-1");
      active = iata;
      airportElements.get(iata).setAttribute("tabindex", "0");
    }
    setHover({ airport: iata });
    const box = airportElements.get(iata).getBoundingClientRect();
    const data = airports.find((item) => item.iata === iata);
    showTooltip(airportTooltip(data), box.left + box.width / 2, box.top);
  }

  const DIRECTIONS = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  };

  // The airport nearest `from` in direction [dx, dy] on screen, or null.
  function step(from, [dx, dy]) {
    const centre = (box) => [box.left + box.width / 2, box.top + box.height / 2];
    const [ox, oy] = centre(airportElements.get(from).getBoundingClientRect());
    let best = null;
    let bestScore = Number.POSITIVE_INFINITY;
    airportElements.forEach((group, iata) => {
      if (iata === from) return;
      const [cx, cy] = centre(group.getBoundingClientRect());
      const along = (cx - ox) * dx + (cy - oy) * dy;
      if (along <= 0) return;
      const score = along + OFF_AXIS * Math.abs((cx - ox) * dy - (cy - oy) * dx);
      if (score < bestScore) {
        bestScore = score;
        best = iata;
      }
    });
    return best;
  }

  function onKey(event) {
    const airport = event.target.closest(".airport");
    if (!airport) return;
    if (event.key in DIRECTIONS) {
      event.preventDefault();
      const next = step(airport.dataset.iata, DIRECTIONS[event.key]);
      if (next) airportElements.get(next).focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(airport.dataset.iata);
    } else if (event.key === "Escape" && focused) {
      event.preventDefault();
      reset();
    }
  }

  return { setup, render };
})();
