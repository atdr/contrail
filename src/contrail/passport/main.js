// State, controls and the render loop. Every control writes through
// setState(), and setState() redraws whatever reads the keys that changed.
(() => {
  "use strict";

  const {
    formatNumber,
    formatChange,
    pct,
    formatKg,
    formatMassText,
    formatDate,
    countryName,
    countryFlag,
    routeName,
    flightTags,
    visualCategory,
  } = Passport.format;
  const {
    QUALITY,
    sum,
    withEmissions,
    completed,
    pricedCompleted,
    scoped,
    metricRows,
    aggregateBy,
    rankAll,
    groupAll,
    breakdownCategories,
    rankForMetric,
    patternBuckets,
    yearSeries,
    extremes,
  } = Passport.aggregate;
  const charts = Passport.charts;
  const map = Passport.map;
  const panel = Passport.panel;

  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($("passport-data").textContent);
  const world = JSON.parse($("world-data").textContent);
  // A flight's id is its place in the payload: what a flight panel filter,
  // and so a link to one flight, names it by.
  data.flights.forEach((flight, index) => {
    flight.id = index;
  });

  // Each key but `panel` is a segmented control: a button with
  // data-control="<key>" and data-value="<value>" sets it. `year` is null
  // for all time. `panel` is the open flight panel's filter key, or null.
  const DEFAULTS = {
    year: null,
    metric: "total",
    pattern: "month",
    trendBreakdown: "total",
    patternBreakdown: "total",
    driverBreakdown: "total",
    driverMetric: "total",
    networkMetric: "total",
    panel: null,
  };
  const state = { ...DEFAULTS };
  const parseValue = { year: (value) => (value === "all" ? null : Number(value)) };
  const controlValue = (value) => (value === null ? "all" : String(value));

  // What each state key may hold, for reading the URL hash: a hash is typed
  // or pasted by hand, so anything else in it is ignored.
  const MEASURES = ["total", "distance", "duration"];
  const BREAKDOWNS = ["total", "cabin", "reason"];
  const ALLOWED = {
    year: (value) => data.years.includes(Number(value)),
    metric: (value) => MEASURES.includes(value),
    pattern: (value) => ["month", "weekday"].includes(value),
    trendBreakdown: (value) => BREAKDOWNS.includes(value),
    patternBreakdown: (value) => BREAKDOWNS.includes(value),
    driverBreakdown: (value) => BREAKDOWNS.includes(value),
    driverMetric: (value) => MEASURES.includes(value),
    networkMetric: (value) => MEASURES.includes(value),
    panel: (value) => panel.describe(value) !== null,
  };

  // The data behind each ranking's "See all" dialog, as last drawn.
  const rankingData = new Map();

  // The headline figure for `metric` over `rows`: total kg, g per km or kg
  // per hour, or null where no flight carries what it needs.
  function headline(rows, metric) {
    if (metric === "distance") {
      const measured = metricRows(rows, "distanceKm");
      const km = sum(measured, "distanceKm");
      return km ? (sum(measured, "kg") * 1000) / km : null;
    }
    if (metric === "duration") {
      const measured = metricRows(rows, "durationHours");
      const hours = sum(measured, "durationHours");
      return hours ? sum(measured, "kg") / hours : null;
    }
    const priced = pricedCompleted(rows);
    return priced.length ? sum(priced, "kg") : null;
  }

  // "+12% vs 2024" for the chosen year against the one before, or "" when
  // no year is chosen or either year lacks the figure.
  function yearOnYear(rows) {
    if (state.year === null) return "";
    const previous = state.year - 1;
    const before = headline(scoped(data.flights, previous), state.metric);
    const now = headline(rows, state.metric);
    if (!before || now === null) return "";
    return ` · ${formatChange((now - before) / before)} vs ${previous}`;
  }

  function renderHero(rows) {
    const completedRows = completed(rows);
    const priced = withEmissions(completedRows);
    const planned = withEmissions(rows.filter((flight) => !flight.departed));
    const distanceRows = metricRows(rows, "distanceKm");
    const durationRows = metricRows(rows, "durationHours");
    const allDistanceRows = completedRows.filter((flight) => flight.distanceKm !== null);
    const allDurationRows = completedRows.filter((flight) => flight.durationHours !== null);
    const totalKg = sum(priced, "kg");
    const distanceKm = sum(distanceRows, "distanceKm");
    const durationHours = sum(durationRows, "durationHours");

    let value;
    let context;
    if (state.metric === "distance") {
      value = distanceKm
        ? `${formatNumber((sum(distanceRows, "kg") * 1000) / distanceKm, 1)} <small>g CO₂e / passenger-km</small>`
        : "Not available";
      context = "Weighted intensity using one consistent great-circle distance method";
    } else if (state.metric === "duration") {
      value = durationHours
        ? `${formatNumber(sum(durationRows, "kg") / durationHours, 1)} <small>kg CO₂e / block hour</small>`
        : "Needs arrival times";
      context = "Weighted intensity using scheduled gate-to-gate time";
    } else {
      value = formatKg(totalKg);
      context = `Passenger emissions across ${formatNumber(completedRows.length)} completed flights`;
    }

    $("hero-value").innerHTML = value;
    $("hero-context").textContent = `${context}${yearOnYear(rows)}`;
    $("flight-count").textContent = formatNumber(completedRows.length);
    $("distance-total").textContent = allDistanceRows.length
      ? `${formatNumber(sum(allDistanceRows, "distanceKm"))} km`
      : "Not available";
    $("duration-total").textContent = allDurationRows.length
      ? `${formatNumber(sum(allDurationRows, "durationHours"), 1)} h`
      : "Needs arrival times";
    $("planned-total").innerHTML = formatKg(sum(planned, "kg"));
  }

  function renderTrend() {
    const series = yearSeries(data.flights, data.years);
    const categories = breakdownCategories(withEmissions(data.flights), state.trendBreakdown);
    // A click on a year chooses it; a click on the chosen year goes back to
    // all time.
    charts.trend(series, categories, state.trendBreakdown, state.year, (year) =>
      setState({ year: state.year === year ? null : year }),
    );
    charts.legend("trend-legend", state.trendBreakdown, categories, {
      upcoming: series.some((item) => item.planned),
    });

    // With no year chosen, the tile reads the newest year anything was flown
    // in: a year holding only bookings would read zero.
    const current = state.year
      ? series.find((item) => item.year === state.year)
      : series.findLast((item) => withEmissions(completed(item.rows)).length) || series.at(-1);
    const trendUnit = current && Math.max(current.completed, current.planned) >= 1000 ? "t" : "kg";
    $("trend-note-label").textContent = state.year
      ? String(state.year)
      : data.years.length
        ? "Latest year"
        : "No dated flights";
    $("trend-note-value").innerHTML = current
      ? formatKg(current.completed, trendUnit)
      : "No flights";
    $("trend-note-copy").textContent = current?.planned
      ? `${formatMassText(current.planned, trendUnit)} CO₂e comes from upcoming flights.`
      : current
        ? "No upcoming impact remains in this period."
        : "Add flights, then regenerate your Passport.";
  }

  // Draw a ranking's top six and keep the whole list for its dialog.
  // `periodKg` is the period's completed CO2e, what each entry's share in
  // its tooltip is of. `kind` is the flight panel filter an entry opens.
  function showRanking(key, title, entries, breakdown, metric, periodKg, kind) {
    rankingData.set(key, { title, entries, breakdown, metric, periodKg, kind });
    charts.ranking(key, entries.slice(0, 6), breakdown, metric, periodKg, kind);
    document.querySelector(`[data-ranking="${key}"]`).hidden = entries.length <= 6;
  }

  function renderPatterns(rows) {
    const buckets = patternBuckets(rows, state.pattern, state.patternBreakdown);
    charts.pattern(buckets, state.patternBreakdown, state.pattern);
    charts.legend("pattern-legend", state.patternBreakdown, buckets.categories);

    const airportEntries = aggregateBy(rows, (flight) => [flight.origin, flight.destination]);
    const countryEntries = aggregateBy(rows, (flight) => [
      flight.start?.country,
      flight.end?.country,
    ]).map((entry) => ({
      ...entry,
      // The chart draws the name alone: Windows has no flag emoji and shows
      // two letters instead. The flag rides along for the HTML list, which
      // also shows the name, so a missing glyph costs nothing there.
      label: countryName(entry.label),
      flag: countryFlag(entry.label),
    }));
    // The connection rankings sort by their own measure: a control beside
    // them, as each chart's controls are, rather than the drivers' one a
    // section further down that would change them out of sight.
    const periodKg = sum(pricedCompleted(rows), "kg");
    showRanking(
      "airports",
      "All airport connections",
      rankForMetric(airportEntries, state.networkMetric),
      state.patternBreakdown,
      state.networkMetric,
      periodKg,
      "airport",
    );
    showRanking(
      "countries",
      "All country connections",
      rankForMetric(countryEntries, state.networkMetric),
      state.patternBreakdown,
      state.networkMetric,
      periodKg,
      "country",
    );
  }

  function renderExtreme(id, label, item, formatter) {
    const target = $(id);
    target.replaceChildren();
    const heading = document.createElement("span");
    heading.textContent = label;
    target.append(heading);
    if (!item) {
      const empty = document.createElement("p");
      empty.textContent = "Needs the required source data.";
      target.append(empty);
      return;
    }
    const [flight, value] = item;
    // The route is a button stretched over the whole card, so the card opens
    // the flight while its content stays text a card can hold.
    const route = document.createElement("strong");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "card-open";
    open.dataset.openPanel = `flight:${flight.id}`;
    open.textContent = routeName(flight);
    route.append(open);
    const detail = document.createElement("p");
    detail.textContent = `${formatter(value)} · ${flight.flight || "Flight unknown"} · ${flight.date ? formatDate(flight.date) : "Date unknown"}`;
    target.append(route, detail, flightTags(flight));
  }

  function renderExtremes(rows) {
    const { total, distance, duration } = extremes(rows);
    const mass = (value) => `${formatMassText(value)} CO₂e`;
    const perKm = (value) => `${formatNumber(value, 1)} g CO₂e / passenger-km`;
    const perHour = (value) => `${formatNumber(value, 1)} kg CO₂e / block hour`;
    renderExtreme("lightest-total", "Lightest", total[0], mass);
    renderExtreme("heaviest-total", "Heaviest", total.at(-1), mass);
    renderExtreme("lightest-distance", "Lightest", distance[0], perKm);
    renderExtreme("heaviest-distance", "Heaviest", distance.at(-1), perKm);
    renderExtreme("lightest-duration", "Lightest", duration[0], perHour);
    renderExtreme("heaviest-duration", "Heaviest", duration.at(-1), perHour);
  }

  function renderRankings(rows) {
    charts.composition(
      "cabins",
      groupAll(rows, (row) => visualCategory(row, "cabin")),
      "cabin",
    );
    const carrierNames = new Map(
      rows.filter((row) => row.carrierName).map((row) => [row.carrier, row.carrierName]),
    );
    charts.composition(
      "reasons",
      groupAll(rows, (row) => row.reason),
      "reason",
    );
    charts.legend(
      "driver-legend",
      state.driverBreakdown,
      breakdownCategories(pricedCompleted(rows), state.driverBreakdown),
    );
    // Each ranking groups by the value its panel filter matches, then names
    // the entry for display.
    const flightLabel = (row) =>
      `${row.flight || "Unknown"} · ${row.origin || "?"}–${row.destination || "?"} · ${row.date ? formatDate(row.date) : "date unknown"}`;
    [
      ["routes", "All routes", "route", (row) => row.route],
      [
        "carriers",
        "All operating airlines",
        "carrier",
        (row) => row.carrier,
        (code) => (carrierNames.has(code) ? `${carrierNames.get(code)} (${code})` : code),
      ],
      ["aircraft", "All aircraft", "aircraft", (row) => row.aircraft],
      [
        "flights",
        "All flights by impact",
        "flight",
        (row) => String(row.id),
        (id) => flightLabel(data.flights[Number(id)]),
      ],
    ].forEach(([key, title, kind, keyer, name]) => {
      const entries = rankAll(rows, keyer).map((entry) =>
        name ? { ...entry, label: name(entry.value) } : entry,
      );
      showRanking(
        key,
        title,
        rankForMetric(entries, state.driverMetric),
        state.driverBreakdown,
        state.driverMetric,
        sum(pricedCompleted(rows), "kg"),
        kind,
      );
    });
  }

  function renderQuality(rows) {
    const done = completed(rows);
    const count = (key) => done.filter(QUALITY[key].test).length;
    const airports = new Set(
      done.flatMap((flight) => [flight.origin, flight.destination]).filter(Boolean),
    );
    const countries = new Set(
      done.flatMap((flight) => [flight.start?.country, flight.end?.country]).filter(Boolean),
    );
    $("quality-total").textContent = formatNumber(done.length);
    // Each count is a button opening its bucket, from the same table.
    document.querySelectorAll("[data-open-panel^='quality:']").forEach((button) => {
      const value = count(button.dataset.openPanel.slice("quality:".length));
      button.querySelector("strong").textContent = `${value} · ${pct(value, done.length)}%`;
    });
    $("airport-count").textContent = String(airports.size);
    $("country-count").textContent = String(countries.size);
    const assumed = count("cabin:assumed");
    $("methodology").textContent =
      `Impact figures are per passenger. Distance intensity uses ${data.meta.distanceMethod}. Time intensity uses ${data.meta.durationMethod}. ${assumed ? `${assumed} flight${assumed === 1 ? "" : "s"} without cabin data ${assumed === 1 ? "is" : "are"} shown as Economy in charts.` : "Every flight has cabin data."} Missing values are never extrapolated.`;
  }

  // Each section, in page order, with the state keys it reads. A section is
  // redrawn when one of its keys changes; the first render draws them all.
  const SECTIONS = [
    { draw: renderHero, reads: ["year", "metric"] },
    { draw: map.render, reads: ["year"] },
    { draw: renderTrend, reads: ["year", "trendBreakdown"] },
    { draw: renderPatterns, reads: ["year", "pattern", "patternBreakdown", "networkMetric"] },
    { draw: renderExtremes, reads: ["year"] },
    { draw: renderRankings, reads: ["year", "driverBreakdown", "driverMetric"] },
    { draw: renderQuality, reads: ["year"] },
    { draw: renderPanel, reads: ["year", "panel"] },
  ];

  // The element a panel was opened from, which gets focus back on close.
  let panelOpener = null;

  function renderPanel(rows) {
    if (state.panel === null) {
      panel.hide();
      return;
    }
    // A key that names nothing in this file closes rather than shows empty.
    if (!panel.show(state.panel, rows, state.year, panelOpener)) state.panel = null;
    panelOpener = null;
  }

  function openPanel(key, opener = null) {
    panelOpener = opener;
    setState({ panel: key });
  }

  // The view in the URL hash, so a reload or a shared link keeps it. Only
  // what differs from the default is written; replaceState, so the back
  // button leaves the page rather than stepping through every click.
  function writeHash() {
    const params = new URLSearchParams();
    Object.keys(DEFAULTS).forEach((key) => {
      if (state[key] !== DEFAULTS[key]) params.set(key, String(state[key]));
    });
    const hash = params.toString();
    try {
      history.replaceState(null, "", hash ? `#${hash}` : `${location.pathname}${location.search}`);
    } catch {
      // A sandboxed frame may refuse; the view simply is not kept.
    }
  }

  function readHash() {
    const patch = {};
    let params;
    try {
      params = new URLSearchParams(location.hash.slice(1));
    } catch {
      return patch;
    }
    Object.keys(DEFAULTS).forEach((key) => {
      const value = params.get(key);
      if (value === null || !ALLOWED[key](value)) return;
      patch[key] = key === "year" ? Number(value) : value;
    });
    return patch;
  }

  function syncControls() {
    document.querySelectorAll("button[data-control]").forEach((button) => {
      const { control, value } = button.dataset;
      button.setAttribute("aria-pressed", String(value === controlValue(state[control])));
    });
  }

  function render(changed = null) {
    syncControls();
    const rows = scoped(data.flights, state.year);
    SECTIONS.forEach(({ draw, reads }) => {
      if (!changed || reads.some((key) => changed.has(key))) draw(rows);
    });
  }

  function setState(patch) {
    const changed = new Set(Object.keys(patch).filter((key) => state[key] !== patch[key]));
    Object.assign(state, patch);
    if (!changed.size) return;
    render(changed);
    writeHash();
  }

  function buildPeriods() {
    const nav = $("periods");
    [["All time", null], ...data.years.map((year) => [String(year), year])].forEach(
      ([label, year]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.dataset.control = "year";
        button.dataset.value = controlValue(year);
        button.setAttribute("aria-pressed", String(state.year === year));
        nav.append(button);
      },
    );
    const updateControls = () => {
      $("period-newer").disabled = nav.scrollLeft <= 1;
      $("period-older").disabled = nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 1;
    };
    $("period-newer").addEventListener("click", () =>
      nav.scrollBy({ left: -nav.clientWidth * 0.75, behavior: "smooth" }),
    );
    $("period-older").addEventListener("click", () =>
      nav.scrollBy({ left: nav.clientWidth * 0.75, behavior: "smooth" }),
    );
    nav.addEventListener("scroll", updateControls, { passive: true });
    window.addEventListener("resize", updateControls);
    requestAnimationFrame(updateControls);
  }

  function openRanking(key) {
    const ranking = rankingData.get(key);
    if (!ranking?.entries.length) return;
    const metric = ranking.metric || "total";
    const breakdown = ranking.breakdown || "total";
    const metricLabel =
      metric === "distance"
        ? "average CO₂e per km"
        : metric === "duration"
          ? "average CO₂e per hour"
          : "total CO₂e";
    const breakdownLabel = breakdown === "total" ? "" : ` by ${breakdown}`;
    $("ranking-dialog-title").textContent = `${ranking.title} · ${metricLabel}${breakdownLabel}`;
    charts.rankingList(
      "ranking-dialog-list",
      ranking.entries,
      breakdown,
      metric,
      ranking.periodKg,
      ranking.kind,
    );
    $("ranking-dialog").showModal();
    $("ranking-dialog-list").parentElement.scrollTop = 0;
  }

  // One listener for every segmented control, the year buttons included,
  // and one for everything that opens the flight panel.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-control]");
    if (!button) return;
    const { control, value } = button.dataset;
    setState({ [control]: (parseValue[control] || String)(value) });
  });
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-panel]");
    if (opener) openPanel(opener.dataset.openPanel, opener);
  });

  document.querySelectorAll("[data-ranking]").forEach((button) => {
    button.addEventListener("click", () => openRanking(button.dataset.ranking));
  });
  $("ranking-dialog-close").addEventListener("click", () => $("ranking-dialog").close());
  $("ranking-dialog").addEventListener("click", (event) => {
    if (event.target === $("ranking-dialog")) $("ranking-dialog").close();
  });

  // Charts read their colours when drawn, so a scheme change while the page
  // is open redraws every section rather than leaving last scheme's ink.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());

  // A hash edited by hand, or a link followed within the page, applies too.
  window.addEventListener("hashchange", () => setState({ ...DEFAULTS, ...readHash() }));

  Object.assign(state, readHash());
  buildPeriods();
  charts.onSelect(openPanel);
  map.setup(world, openPanel);
  panel.setup({ flights: data.flights, onClose: () => setState({ panel: null }) });
  $("generated").textContent =
    `Generated ${new Date(data.meta.generatedAt).toLocaleString()} with contrail ${data.meta.contrailVersion}. Keep this file private.`;
  render();
})();
