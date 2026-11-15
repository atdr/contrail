// State, controls and the render loop. Every control writes through
// setState(), and setState() redraws whatever reads the keys that changed.
(() => {
  "use strict";

  const {
    formatNumber,
    pct,
    formatKg,
    formatMassText,
    countryFlag,
    routeName,
    flightTags,
    visualCategory,
  } = Passport.format;
  const {
    sum,
    withEmissions,
    completed,
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

  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($("passport-data").textContent);
  const world = JSON.parse($("world-data").textContent);

  // Each key is a segmented control: a button with data-control="<key>" and
  // data-value="<value>" sets it. `year` is null for all time.
  const state = {
    year: null,
    metric: "total",
    pattern: "month",
    trendBreakdown: "total",
    patternBreakdown: "total",
    driverBreakdown: "total",
    driverMetric: "total",
  };
  const parseValue = { year: (value) => (value === "all" ? null : Number(value)) };
  const controlValue = (value) => (value === null ? "all" : String(value));

  // The data behind each ranking's "See all" dialog, as last drawn.
  const rankingData = new Map();

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
    $("hero-context").textContent = context;
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
    charts.trend(series, categories, state.trendBreakdown);

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
  function showRanking(key, title, entries, breakdown, metric) {
    rankingData.set(key, { title, entries, breakdown, metric });
    charts.ranking(key, entries.slice(0, 6), breakdown, metric);
    document.querySelector(`[data-ranking="${key}"]`).hidden = entries.length <= 6;
  }

  function renderPatterns(rows) {
    charts.pattern(
      patternBuckets(rows, state.pattern, state.patternBreakdown),
      state.patternBreakdown,
    );

    const countryDisplay =
      typeof Intl.DisplayNames === "function"
        ? new Intl.DisplayNames(undefined, { type: "region" })
        : null;
    const airportEntries = aggregateBy(rows, (flight) => [flight.origin, flight.destination]);
    const countryEntries = aggregateBy(rows, (flight) => [
      flight.start?.country,
      flight.end?.country,
    ]).map((entry) => ({
      ...entry,
      label: `${countryFlag(entry.label)} ${countryDisplay ? countryDisplay.of(entry.label) || entry.label : entry.label}`,
    }));
    showRanking(
      "airports",
      "All airport connections",
      airportEntries,
      state.patternBreakdown,
      "total",
    );
    showRanking(
      "countries",
      "All country connections",
      countryEntries,
      state.patternBreakdown,
      "total",
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
    const route = document.createElement("strong");
    route.textContent = routeName(flight);
    const detail = document.createElement("p");
    detail.textContent = `${formatter(value)} · ${flight.flight || "Flight unknown"} · ${flight.date || "Date unknown"}`;
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
    charts.composition(
      "reasons",
      groupAll(rows, (row) => row.reason),
      "reason",
    );
    [
      ["routes", "All routes", (row) => row.route],
      ["carriers", "All operating airlines", (row) => row.carrier],
      ["aircraft", "All aircraft", (row) => row.aircraft],
      [
        "flights",
        "All flights by impact",
        (row) =>
          `${row.flight || "Unknown"} · ${row.origin || "?"}–${row.destination || "?"} · ${row.date || "date unknown"}`,
      ],
    ].forEach(([key, title, labeler]) => {
      const ranked = rankForMetric(rankAll(rows, labeler), state.driverMetric);
      showRanking(key, title, ranked, state.driverBreakdown, state.driverMetric);
    });
  }

  function renderQuality(rows) {
    const done = completed(rows);
    const exact = done.filter((flight) => flight.emissionsSource === "exact").length;
    const typical = done.filter(
      (flight) => flight.emissionsSource === "typical_route_average",
    ).length;
    const missing = done.length - exact - typical;
    const cabinKnown = done.filter((flight) => flight.cabinKnown).length;
    const reasonKnown = done.filter(
      (flight) => flight.reason && flight.reason.toLowerCase() !== "unknown",
    ).length;
    const distance = done.filter((flight) => flight.distanceKm !== null).length;
    const duration = done.filter((flight) => flight.durationHours !== null).length;
    const airports = new Set(
      done.flatMap((flight) => [flight.origin, flight.destination]).filter(Boolean),
    );
    const countries = new Set(
      done.flatMap((flight) => [flight.start?.country, flight.end?.country]).filter(Boolean),
    );
    const partition = (count) => `${count} · ${pct(count, done.length)}%`;
    $("quality-total").textContent = formatNumber(done.length);
    $("exact-count").textContent = partition(exact);
    $("typical-count").textContent = partition(typical);
    $("missing-count").textContent = partition(missing);
    $("distance-available").textContent = partition(distance);
    $("distance-missing").textContent = partition(done.length - distance);
    $("duration-available").textContent = partition(duration);
    $("duration-missing").textContent = partition(done.length - duration);
    $("cabin-known").textContent = partition(cabinKnown);
    $("cabin-assumed").textContent = partition(done.length - cabinKnown);
    $("reason-known").textContent = partition(reasonKnown);
    $("reason-missing").textContent = partition(done.length - reasonKnown);
    $("airport-count").textContent = String(airports.size);
    $("country-count").textContent = String(countries.size);
    const assumed = done.length - cabinKnown;
    $("methodology").textContent =
      `Impact figures are per passenger. Distance intensity uses ${data.meta.distanceMethod}. Time intensity uses ${data.meta.durationMethod}. ${assumed ? `${assumed} flight${assumed === 1 ? "" : "s"} without cabin data ${assumed === 1 ? "is" : "are"} shown as Economy in charts.` : "Every flight has cabin data."} Missing values are never extrapolated.`;
  }

  // Each section, in page order, with the state keys it reads. A section is
  // redrawn when one of its keys changes; the first render draws them all.
  const SECTIONS = [
    { draw: renderHero, reads: ["year", "metric"] },
    { draw: map.render, reads: ["year"] },
    { draw: renderTrend, reads: ["year", "trendBreakdown"] },
    { draw: renderPatterns, reads: ["year", "pattern", "patternBreakdown"] },
    { draw: renderExtremes, reads: ["year"] },
    { draw: renderRankings, reads: ["year", "driverBreakdown", "driverMetric"] },
    { draw: renderQuality, reads: ["year"] },
  ];

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
    if (changed.size) render(changed);
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
    $("ranking-dialog-chart").style.height = `${Math.max(360, ranking.entries.length * 34)}px`;
    $("ranking-dialog").showModal();
    requestAnimationFrame(() =>
      charts.ranking("ranking-dialog-canvas", ranking.entries, breakdown, metric),
    );
  }

  // One listener for every segmented control, the year buttons included.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-control]");
    if (!button) return;
    const { control, value } = button.dataset;
    setState({ [control]: (parseValue[control] || String)(value) });
  });

  document.querySelectorAll("[data-ranking]").forEach((button) => {
    button.addEventListener("click", () => openRanking(button.dataset.ranking));
  });
  $("ranking-dialog-close").addEventListener("click", () => $("ranking-dialog").close());
  $("ranking-dialog").addEventListener("click", (event) => {
    if (event.target === $("ranking-dialog")) $("ranking-dialog").close();
  });

  buildPeriods();
  map.setup(world);
  $("generated").textContent =
    `Generated ${new Date(data.meta.generatedAt).toLocaleString()} with contrail ${data.meta.contrailVersion}. Keep this file private.`;
  render();
})();
