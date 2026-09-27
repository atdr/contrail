(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("passport-data").textContent);
  const world = JSON.parse(document.getElementById("world-data").textContent);
  const state = {
    year: null,
    metric: "total",
    pattern: "month",
    trendBreakdown: "total",
    patternBreakdown: "total",
    driverBreakdown: "total",
    driverMetric: "total"
  };
  const charts = new Map();
  const rankingData = new Map();
  let routeMap;
  let routeLayer;
  let airportLayer;
  const $ = (id) => document.getElementById(id);
  const chartFont = getComputedStyle(document.documentElement).getPropertyValue("--font-sans").trim();
  Chart.defaults.font.family = chartFont;
  Chart.defaults.plugins.tooltip.titleFont = { family: chartFont, weight: "600" };
  Chart.defaults.plugins.tooltip.bodyFont = { family: chartFont };
  Chart.defaults.plugins.tooltip.footerFont = { family: chartFont };
  const sum = (rows, field) => rows.reduce((total, row) => total + (row[field] || 0), 0);
  const pct = (part, whole) => whole ? Math.round(part / whole * 100) : 0;
  const formatNumber = (value, digits = 0) => Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
  const formatKg = (kg, unit = kg >= 1000 ? "t" : "kg") => unit === "t" ? `${formatNumber(kg / 1000, 1)} <small>t CO₂e</small>` : `${formatNumber(kg, 1)} <small>kg CO₂e</small>`;
  const formatMassText = (kg, unit = kg >= 1000 ? "t" : "kg") => unit === "t" ? `${formatNumber(kg / 1000, 1)} t` : `${formatNumber(kg, 1)} kg`;
  const formatMassAxisTick = (kg, maximum) => formatMassText(kg, maximum >= 1000 ? "t" : "kg");
  const cssColor = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const totalColor = () => cssColor("--total-value");

  function categoryColor(kind, label) {
    const key = label.toLowerCase();
    if (kind === "cabin") {
      if (key.includes("premium")) return cssColor("--cabin-premium");
      if (key === "business") return cssColor("--cabin-business");
      if (key === "first") return cssColor("--cabin-first");
      if (key === "private") return cssColor("--cabin-private");
      if (key.includes("economy")) return cssColor("--cabin-economy");
    }
    if (kind === "reason") {
      if (key === "business") return cssColor("--reason-business");
      if (key === "leisure" || key === "personal") return cssColor("--reason-leisure");
      if (key === "crew") return cssColor("--reason-crew");
    }
    return cssColor("--category-unknown");
  }

  function visualCategory(row, kind) {
    if (kind === "cabin" && (!row.cabinKnown || (row.cabin || "").toLowerCase().includes("assumed"))) return "Economy";
    return row[kind] || "Unknown";
  }

  function scoped() {
    return data.flights.filter((flight) => state.year === null || flight.year === state.year);
  }

  function withEmissions(rows) {
    return rows.filter((flight) => flight.kg !== null);
  }

  function completed(rows) {
    return rows.filter((flight) => flight.departed);
  }

  function makeButton(label, year) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed", String(state.year === year));
    button.addEventListener("click", () => {
      state.year = year;
      document.querySelectorAll("#periods button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      update();
    });
    return button;
  }

  function buildPeriods() {
    const nav = $("periods");
    nav.append(makeButton("All time", null));
    data.years.forEach((year) => nav.append(makeButton(String(year), year)));
    const updateControls = () => {
      $("period-newer").disabled = nav.scrollLeft <= 1;
      $("period-older").disabled = nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 1;
    };
    $("period-newer").addEventListener("click", () => nav.scrollBy({ left: -nav.clientWidth * 0.75, behavior: "smooth" }));
    $("period-older").addEventListener("click", () => nav.scrollBy({ left: nav.clientWidth * 0.75, behavior: "smooth" }));
    nav.addEventListener("scroll", updateControls, { passive: true });
    window.addEventListener("resize", updateControls);
    requestAnimationFrame(updateControls);
  }

  function metricRows(rows, field) {
    return withEmissions(completed(rows)).filter((flight) => flight[field] !== null);
  }

  function updateHero(rows) {
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
      value = distanceKm ? `${formatNumber(totalKgFor(distanceRows) * 1000 / distanceKm, 1)} <small>g CO₂e / passenger-km</small>` : "Not available";
      context = "Weighted intensity using one consistent great-circle distance method";
    } else if (state.metric === "duration") {
      value = durationHours ? `${formatNumber(totalKgFor(durationRows) / durationHours, 1)} <small>kg CO₂e / block hour</small>` : "Needs arrival times";
      context = "Weighted intensity using scheduled gate-to-gate time";
    } else {
      value = formatKg(totalKg);
      context = `Passenger emissions across ${formatNumber(completedRows.length)} completed flights`;
    }

    $("hero-value").innerHTML = value;
    $("hero-context").textContent = context;
    $("flight-count").textContent = formatNumber(completedRows.length);
    $("distance-total").textContent = allDistanceRows.length ? `${formatNumber(sum(allDistanceRows, "distanceKm"))} km` : "Not available";
    $("duration-total").textContent = allDurationRows.length ? `${formatNumber(sum(allDurationRows, "durationHours"), 1)} h` : "Needs arrival times";
    $("planned-total").innerHTML = formatKg(sum(planned, "kg"));
  }

  function totalKgFor(rows) {
    return sum(rows, "kg");
  }

  function emptyMetricBucket() {
    return { totalKg: 0, distanceKg: 0, distanceKm: 0, durationKg: 0, durationHours: 0 };
  }

  function addToMetricBucket(bucket, row) {
    bucket.totalKg += row.kg;
    if (row.distanceKm !== null) {
      bucket.distanceKg += row.kg;
      bucket.distanceKm += row.distanceKm;
    }
    if (row.durationHours !== null) {
      bucket.durationKg += row.kg;
      bucket.durationHours += row.durationHours;
    }
  }

  function metricValue(bucket, metric) {
    if (metric === "distance") return bucket.distanceKm ? bucket.distanceKg * 1000 / bucket.distanceKm : null;
    if (metric === "duration") return bucket.durationHours ? bucket.durationKg / bucket.durationHours : null;
    return bucket.totalKg;
  }

  function formatRankingValue(value, metric, includeCarbon = false) {
    if (metric === "distance") return `${formatNumber(value, 1)} g${includeCarbon ? " CO₂e / passenger-km" : "/km"}`;
    if (metric === "duration") return `${formatNumber(value, 1)} kg${includeCarbon ? " CO₂e / block hour" : "/hr"}`;
    return `${formatMassText(value)}${includeCarbon ? " CO₂e" : ""}`;
  }

  function groupAll(rows, field, labeler = (row) => row[field]) {
    const values = new Map();
    withEmissions(completed(rows)).forEach((row) => {
      const label = labeler(row) || "Unknown";
      values.set(label, (values.get(label) || 0) + row.kg);
    });
    return [...values.entries()].sort((a, b) => b[1] - a[1]);
  }

  function rankAll(rows, labeler) {
    const values = new Map();
    withEmissions(completed(rows)).forEach((row) => {
      const label = labeler(row) || "Unknown";
      const entry = values.get(label) || { label, total: 0, metrics: emptyMetricBucket(), cabin: new Map(), reason: new Map() };
      addToMetricBucket(entry.metrics, row);
      entry.total = entry.metrics.totalKg;
      ["cabin", "reason"].forEach((kind) => {
        const category = visualCategory(row, kind);
        const bucket = entry[kind].get(category) || emptyMetricBucket();
        addToMetricBucket(bucket, row);
        entry[kind].set(category, bucket);
      });
      values.set(label, entry);
    });
    return [...values.values()].sort((a, b) => b.total - a.total);
  }

  function chartTheme() {
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    return {
      text: getComputedStyle(document.body).color,
      muted: dark ? "#a5b2aa" : "#647068",
      grid: dark ? "rgba(165,178,170,0.18)" : "rgba(100,112,104,0.16)"
    };
  }

  function replaceChart(id, configuration) {
    if (charts.has(id)) charts.get(id).destroy();
    const chart = new Chart($(id), configuration);
    charts.set(id, chart);
    return chart;
  }

  function valueLabels(axis, formatter) {
    return {
      id: `passport-value-labels-${axis}`,
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        const theme = chartTheme();
        ctx.save();
        ctx.fillStyle = theme.text;
        ctx.font = `500 12px ${chartFont}`;
        chart.getDatasetMeta(0).data.forEach((bar, index) => {
          const value = chart.data.datasets[0].data[index];
          const label = formatter(value);
          if (axis === "y") {
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            ctx.fillText(label, bar.x + 8, bar.y);
          } else {
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            ctx.fillText(label, bar.x, bar.y - 7);
          }
        });
        ctx.restore();
      }
    };
  }

  function rankingValueLabels(totals, metric) {
    return {
      id: "passport-ranking-value-labels",
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        const theme = chartTheme();
        ctx.save();
        ctx.fillStyle = theme.text;
        ctx.font = `500 12px ${chartFont}`;
        totals.forEach((total, index) => {
          const bars = chart.data.datasets.map((_, datasetIndex) => chart.getDatasetMeta(datasetIndex).data[index]).filter(Boolean);
          const anchor = bars.reduce((furthest, bar) => !furthest || bar.x > furthest.x ? bar : furthest, null);
          if (!anchor) return;
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          ctx.fillText(formatRankingValue(total, metric), anchor.x + 8, anchor.y);
        });
        ctx.restore();
      }
    };
  }

  function periodValueLabels(totals, unit) {
    return {
      id: "passport-period-value-labels",
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        const theme = chartTheme();
        ctx.save();
        ctx.fillStyle = theme.text;
        ctx.font = `500 12px ${chartFont}`;
        totals.forEach((total, index) => {
          const bars = chart.data.datasets
            .map((dataset, datasetIndex) => dataset.data[index] ? chart.getDatasetMeta(datasetIndex).data[index] : null)
            .filter(Boolean);
          const anchor = bars.reduce((highest, bar) => !highest || bar.y < highest.y ? bar : highest, null);
          if (!anchor) return;
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          ctx.fillText(formatMassText(total, unit), anchor.x, anchor.y - 7);
        });
        ctx.restore();
      }
    };
  }

  function breakdownCategories(rows, breakdown) {
    if (breakdown === "total") return ["Total"];
    const totals = new Map();
    withEmissions(rows).forEach((row) => {
      const category = visualCategory(row, breakdown);
      totals.set(category, (totals.get(category) || 0) + row.kg);
    });
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([label]) => label);
  }

  function breakdownColor(breakdown, category) {
    return breakdown === "total" ? totalColor() : categoryColor(breakdown, category);
  }

  function hatchPattern(canvas, color) {
    const hatch = document.createElement("canvas");
    hatch.width = 9;
    hatch.height = 9;
    const context = hatch.getContext("2d");
    context.fillStyle = `${color}42`;
    context.fillRect(0, 0, 9, 9);
    context.strokeStyle = color;
    context.lineWidth = 2.5;
    context.beginPath();
    context.moveTo(-2, 9);
    context.lineTo(9, -2);
    context.stroke();
    return canvas.getContext("2d").createPattern(hatch, "repeat");
  }

  function stackedBorderRadius(axis, radius = 7) {
    return (context) => {
      const active = context.chart.data.datasets
        .map((dataset, index) => Number(dataset.data[context.dataIndex]) > 0 ? index : null)
        .filter((index) => index !== null);
      const last = active.at(-1);
      if (axis === "y") {
        return {
          topLeft: 0,
          bottomLeft: 0,
          topRight: context.datasetIndex === last ? radius : 0,
          bottomRight: context.datasetIndex === last ? radius : 0
        };
      }
      return {
        bottomLeft: 0,
        bottomRight: 0,
        topLeft: context.datasetIndex === last ? radius : 0,
        topRight: context.datasetIndex === last ? radius : 0
      };
    };
  }

  function rankingMetricValue(entry, metric) {
    if (entry.metrics) return metricValue(entry.metrics, metric);
    return metric === "total" ? entry.total : null;
  }

  function rankingCategoryValue(entry, breakdown, category, metric) {
    const bucket = entry[breakdown].get(category);
    if (!bucket) return 0;
    if (metric === "total") return bucket.totalKg;
    const denominator = metric === "distance" ? entry.metrics.distanceKm : entry.metrics.durationHours;
    if (!denominator) return 0;
    return metric === "distance" ? bucket.distanceKg * 1000 / denominator : bucket.durationKg / denominator;
  }

  function rankForMetric(entries, metric) {
    return entries
      .map((entry) => ({ entry, value: rankingMetricValue(entry, metric) }))
      .filter((item) => item.value !== null)
      .sort((a, b) => b.value - a.value)
      .map((item) => item.entry);
  }

  function renderRanking(id, entries, breakdown = state.driverBreakdown, metric = state.driverMetric) {
    const theme = chartTheme();
    const totals = entries.map((entry) => rankingMetricValue(entry, metric));
    const maximum = Math.max(1, ...totals);
    const categoryTotals = new Map();
    if (breakdown !== "total") {
      entries.forEach((entry) => entry[breakdown].forEach((_, label) => {
        const value = rankingCategoryValue(entry, breakdown, label, metric);
        categoryTotals.set(label, (categoryTotals.get(label) || 0) + value);
      }));
    }
    const categories = [...categoryTotals.entries()].sort((a, b) => b[1] - a[1]).map((entry) => entry[0]);
    const datasets = breakdown === "total" ? [{ label: "Total", data: totals, backgroundColor: totalColor(), hoverBackgroundColor: totalColor(), borderSkipped: false, borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 7, bottomRight: 7 }, barPercentage: 0.72 }] : categories.map((category) => ({
      label: category,
      data: entries.map((entry) => rankingCategoryValue(entry, breakdown, category, metric)),
      backgroundColor: categoryColor(breakdown, category),
      hoverBackgroundColor: categoryColor(breakdown, category),
      borderSkipped: false,
      borderRadius: stackedBorderRadius("y"),
      barPercentage: 0.72
    }));
    $(id).setAttribute("role", "img");
    $(id).setAttribute("aria-label", entries.length ? entries.map((entry) => {
      const categoriesText = breakdown === "total" ? "" : `; ${[...entry[breakdown].keys()].map((label) => `${label} ${formatRankingValue(rankingCategoryValue(entry, breakdown, label, metric), metric, true)}`).join(", ")}`;
      return `${entry.label}: ${formatRankingValue(rankingMetricValue(entry, metric), metric, true)}${categoriesText}`;
    }).join(", ") : `No flights with the data required for ${metric === "distance" ? "distance intensity" : metric === "duration" ? "time intensity" : "total impact"} in this period`);
    replaceChart(id, {
      type: "bar",
      data: {
        labels: entries.map((entry) => entry.label),
        datasets
      },
      plugins: [rankingValueLabels(totals, metric)],
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: "y",
        layout: { padding: { right: metric === "total" ? 58 : 88 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${formatRankingValue(context.raw, metric, true)}` } }
        },
        scales: {
          x: { display: false, stacked: true, beginAtZero: true, max: maximum * 1.24 },
          y: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, color: theme.text, font: { size: 12 } } }
        }
      }
    });
  }

  function categoryIcon(kind, label) {
    const key = label.toLowerCase();
    const icon = document.createElement("span");
    icon.className = "category-icon";
    icon.setAttribute("aria-hidden", "true");
    if (kind === "cabin") {
      const badge = key.includes("premium") ? "E+" : key === "business" ? "B" : key === "first" ? "F" : key === "private" ? "VIP" : key.includes("economy") ? "E" : "?";
      icon.textContent = badge;
      icon.style.setProperty("--icon-bg", categoryColor(kind, label));
      if (badge === "VIP") icon.classList.add("wide");
      return icon;
    }
    icon.classList.add("reason-icon");
    if (key === "business") {
      icon.textContent = "💼";
    } else if (key === "leisure" || key === "personal") {
      icon.textContent = "☀️";
    } else if (key === "crew") {
      icon.textContent = "🧑‍✈️";
    } else {
      icon.textContent = "?";
    }
    return icon;
  }

  function renderComposition(id, entries, kind) {
    const total = entries.reduce((value, entry) => value + entry[1], 0);
    $(`${id}-chart`).setAttribute("role", "img");
    $(`${id}-chart`).setAttribute("aria-label", entries.length ? entries.map(([label, kg]) => `${label}: ${formatNumber(total ? kg / total * 100 : 0, 1)}%`).join(", ") : "No priced flights in this period");
    replaceChart(`${id}-chart`, {
      type: "bar",
      data: {
        labels: [""],
        datasets: entries.map(([label, kg], index) => ({
          label,
          data: [kg],
          backgroundColor: categoryColor(kind, label),
          hoverBackgroundColor: categoryColor(kind, label),
          borderSkipped: false,
          borderRadius: index === 0 ? { topLeft: 14, bottomLeft: 14 } : index === entries.length - 1 ? { topRight: 14, bottomRight: 14 } : 0,
          barThickness: 28
        }))
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${formatMassText(context.raw)} CO₂e · ${formatNumber(total ? context.raw / total * 100 : 0, 1)}%` } }
        },
        scales: {
          x: { display: false, stacked: true, max: total || 1 },
          y: { display: false, stacked: true }
        }
      }
    });

    const legend = $(`${id}-legend`);
    legend.replaceChildren();
    entries.forEach(([label, kg]) => {
      const percentage = total ? kg / total * 100 : 0;
      const row = document.createElement("div");
      row.className = "composition-row";
      const swatch = document.createElement("span");
      swatch.className = "composition-swatch";
      swatch.style.background = categoryColor(kind, label);
      const name = document.createElement("span");
      name.className = "composition-label";
      name.append(categoryIcon(kind, label), document.createTextNode(label));
      const value = document.createElement("span");
      value.className = "composition-value";
      value.textContent = formatMassText(kg);
      const share = document.createElement("span");
      share.className = "composition-percent";
      share.textContent = `${formatNumber(percentage, 1)}%`;
      row.append(swatch, name, value, share);
      legend.append(row);
    });
  }

  function impactByValues(rows, valuesForFlight) {
    const values = new Map();
    withEmissions(completed(rows)).forEach((flight) => {
      [...new Set(valuesForFlight(flight).filter(Boolean))].forEach((label) => {
        const entry = values.get(label) || {
          label,
          total: 0,
          metrics: emptyMetricBucket(),
          cabin: new Map(),
          reason: new Map()
        };
        addToMetricBucket(entry.metrics, flight);
        entry.total = entry.metrics.totalKg;
        ["cabin", "reason"].forEach((kind) => {
          const category = visualCategory(flight, kind);
          const bucket = entry[kind].get(category) || emptyMetricBucket();
          addToMetricBucket(bucket, flight);
          entry[kind].set(category, bucket);
        });
        values.set(label, entry);
      });
    });
    return [...values.values()].sort((a, b) => b.total - a.total);
  }

  function updatePatterns(rows) {
    const validDates = withEmissions(completed(rows)).filter((flight) => flight.date);
    const definitions = {
      month: {
        labels: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        value: (flight) => ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(flight.date.slice(5, 7)) - 1]
      },
      weekday: {
        labels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        value: (flight) => ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(`${flight.date}T00:00:00Z`).getUTCDay()]
      }
    };
    const definition = definitions[state.pattern];
    const categories = breakdownCategories(validDates, state.patternBreakdown);
    const values = new Map(
      definition.labels.map((label) => [label, new Map(categories.map((category) => [category, 0]))])
    );
    validDates.forEach((flight) => {
      const label = definition.value(flight);
      const category = state.patternBreakdown === "total" ? "Total" : visualCategory(flight, state.patternBreakdown);
      values.get(label).set(category, values.get(label).get(category) + flight.kg);
    });
    const totals = definition.labels.map((label) => sum([...values.get(label).values()].map((kg) => ({ kg })), "kg"));
    const maximum = Math.max(0, ...totals);
    const theme = chartTheme();
    $("pattern-chart").setAttribute("aria-label", definition.labels.map((label, index) => {
      const details = state.patternBreakdown === "total" ? "" : `; ${categories.map((category) => `${category} ${formatMassText(values.get(label).get(category))}`).join(", ")}`;
      return `${label}: ${formatMassText(totals[index])} CO₂e${details}`;
    }).join(", "));
    replaceChart("pattern-chart", {
      type: "bar",
      data: {
        labels: definition.labels,
        datasets: categories.map((category) => ({
          label: category,
          data: definition.labels.map((label) => values.get(label).get(category)),
          backgroundColor: breakdownColor(state.patternBreakdown, category),
          hoverBackgroundColor: breakdownColor(state.patternBreakdown, category),
          borderSkipped: false,
          borderRadius: stackedBorderRadius("x"),
          barPercentage: 0.66
        }))
      },
      plugins: [periodValueLabels(totals, maximum >= 1000 ? "t" : "kg")],
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${formatMassText(context.raw)} CO₂e` } }
        },
        scales: {
          x: { stacked: true, grid: { display: false }, border: { color: theme.grid }, ticks: { autoSkip: false, color: theme.muted } },
          y: { stacked: true, beginAtZero: true, suggestedMax: maximum * 1.12, grid: { color: theme.grid }, border: { display: false }, ticks: { color: theme.muted, callback: (value) => formatMassAxisTick(value, maximum) } }
        }
      }
    });

    const countryDisplay = typeof Intl.DisplayNames === "function" ? new Intl.DisplayNames(undefined, { type: "region" }) : null;
    const airportEntries = impactByValues(rows, (flight) => [flight.origin, flight.destination]);
    const countryEntries = impactByValues(rows, (flight) => [flight.start && flight.start.country, flight.end && flight.end.country])
      .map((entry) => ({ ...entry, label: `${countryFlag(entry.label)} ${countryDisplay ? countryDisplay.of(entry.label) || entry.label : entry.label}` }));
    [
      ["airports", "All airport connections", airportEntries],
      ["countries", "All country connections", countryEntries]
    ].forEach(([key, title, entries]) => {
      rankingData.set(key, {
        title,
        entries,
        breakdown: state.patternBreakdown,
        metric: "total"
      });
      renderRanking(key, entries.slice(0, 6), state.patternBreakdown, "total");
      document.querySelector(`[data-ranking="${key}"]`).hidden = entries.length <= 6;
    });
  }

  function countryFlag(code) {
    return /^[A-Z]{2}$/.test(code) ? String.fromCodePoint(...[...code].map((letter) => 127397 + letter.charCodeAt())) : "🌐";
  }

  function routeName(flight) {
    return `${flight.origin || "?"} → ${flight.destination || "?"}`;
  }

  function flightTag(text, className, kind, value) {
    const tag = document.createElement("span");
    tag.className = `flight-tag ${className}`;
    tag.append(categoryIcon(kind, value), document.createTextNode(text));
    return tag;
  }

  function flightTags(flight) {
    const tags = document.createElement("div");
    tags.className = "flight-tags";
    const cabin = visualCategory(flight, "cabin");
    const cabinKey = cabin.toLowerCase();
    if (cabinKey === "first") tags.append(flightTag("First", "cabin-first", "cabin", cabin));
    else if (cabinKey === "business") tags.append(flightTag("Business", "cabin-business", "cabin", cabin));
    else if (cabinKey.includes("premium")) tags.append(flightTag(cabin, "cabin-premium", "cabin", cabin));
    else if (cabinKey === "private") tags.append(flightTag("Private", "cabin-private", "cabin", cabin));
    else if (cabinKey.includes("economy")) tags.append(flightTag(cabin, "cabin-economy", "cabin", cabin));
    else tags.append(flightTag(cabin, "tag-unknown", "cabin", cabin));

    const reason = flight.reason || "Unknown";
    const reasonKey = reason.toLowerCase();
    if (reasonKey === "business") tags.append(flightTag("Business trip", "reason-business", "reason", reason));
    else if (reasonKey === "leisure") tags.append(flightTag("Leisure", "reason-leisure", "reason", reason));
    else if (reasonKey === "personal") tags.append(flightTag("Personal", "reason-leisure", "reason", reason));
    else if (reasonKey === "crew") tags.append(flightTag("Crew", "reason-crew", "reason", reason));
    else tags.append(flightTag("Reason unknown", "tag-unknown", "reason", reason));
    return tags;
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

  function updateExtremes(rows) {
    const done = withEmissions(completed(rows));
    const total = done.map((flight) => [flight, flight.kg]).sort((a, b) => a[1] - b[1]);
    const distance = done.filter((flight) => flight.distanceKm).map((flight) => [flight, flight.kg * 1000 / flight.distanceKm]).sort((a, b) => a[1] - b[1]);
    const duration = done.filter((flight) => flight.durationHours).map((flight) => [flight, flight.kg / flight.durationHours]).sort((a, b) => a[1] - b[1]);
    renderExtreme("lightest-total", "Lightest", total[0], (value) => `${formatMassText(value)} CO₂e`);
    renderExtreme("heaviest-total", "Heaviest", total.at(-1), (value) => `${formatMassText(value)} CO₂e`);
    renderExtreme("lightest-distance", "Lightest", distance[0], (value) => `${formatNumber(value, 1)} g CO₂e / passenger-km`);
    renderExtreme("heaviest-distance", "Heaviest", distance.at(-1), (value) => `${formatNumber(value, 1)} g CO₂e / passenger-km`);
    renderExtreme("lightest-duration", "Lightest", duration[0], (value) => `${formatNumber(value, 1)} kg CO₂e / block hour`);
    renderExtreme("heaviest-duration", "Heaviest", duration.at(-1), (value) => `${formatNumber(value, 1)} kg CO₂e / block hour`);
  }

  function updateRankings(rows) {
    renderComposition("cabins", groupAll(rows, "cabin", (row) => visualCategory(row, "cabin")), "cabin");
    renderComposition("reasons", groupAll(rows, "reason"), "reason");
    const rankings = [
      ["routes", "All routes", rankAll(rows, (row) => row.route)],
      ["carriers", "All operating airlines", rankAll(rows, (row) => row.carrier)],
      ["aircraft", "All aircraft", rankAll(rows, (row) => row.aircraft)],
      ["flights", "All flights by impact", rankAll(rows, (row) => `${row.flight || "Unknown"} · ${row.origin || "?"}–${row.destination || "?"} · ${row.date || "date unknown"}`)]
    ];
    rankings.forEach(([key, title, entries]) => {
      const ranked = rankForMetric(entries, state.driverMetric);
      rankingData.set(key, {
        title,
        entries: ranked,
        breakdown: state.driverBreakdown,
        metric: state.driverMetric
      });
      renderRanking(key, ranked.slice(0, 6));
      const button = document.querySelector(`[data-ranking="${key}"]`);
      button.hidden = ranked.length <= 6;
    });
  }

  function openRanking(key) {
    const ranking = rankingData.get(key);
    if (!ranking || !ranking.entries.length) return;
    const metric = ranking.metric || "total";
    const breakdown = ranking.breakdown || "total";
    const metricLabel = metric === "distance" ? "average CO₂e per km" : metric === "duration" ? "average CO₂e per hour" : "total CO₂e";
    const breakdownLabel = breakdown === "total" ? "" : ` by ${breakdown}`;
    $("ranking-dialog-title").textContent = `${ranking.title} · ${metricLabel}${breakdownLabel}`;
    $("ranking-dialog-chart").style.height = `${Math.max(360, ranking.entries.length * 34)}px`;
    $("ranking-dialog").showModal();
    requestAnimationFrame(() => renderRanking("ranking-dialog-canvas", ranking.entries, breakdown, metric));
  }

  function setupMap() {
    routeMap = L.map("route-map", {
      attributionControl: false,
      minZoom: 1,
      maxZoom: 8,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      worldCopyJump: false,
      maxBounds: [[-85, -220], [85, 220]],
      maxBoundsViscosity: 0.72
    });
    routeMap.createPane("land").style.zIndex = "200";
    routeMap.createPane("routes").style.zIndex = "410";
    routeMap.createPane("airports").style.zIndex = "420";
    L.geoJSON(world, {
      pane: "land",
      interactive: false,
      style: { color: "#5f8c80", weight: 0.7, fillColor: "#173f35", fillOpacity: 1 }
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
    const angle = Math.acos(Math.min(1, Math.max(-1, first[0] * last[0] + first[1] * last[1] + first[2] * last[2])));
    if (angle < 0.000001) return [[start.lat, start.lon], [end.lat, end.lon]];
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
    return points.reduce((segments, point, index) => {
      if (index && Math.abs(point[1] - points[index - 1][1]) > 180) segments.push([]);
      segments.at(-1).push(point);
      return segments;
    }, [[]]).filter((segment) => segment.length > 1);
  }

  function updateMap(rows) {
    routeLayer.clearLayers();
    airportLayer.clearLayers();
    const routes = new Map();
    withEmissions(completed(rows)).forEach((flight) => {
      if (!flight.start || !flight.end) return;
      const existing = routes.get(flight.route) || { kg: 0, start: flight.start, end: flight.end, distanceKm: flight.distanceKm };
      existing.kg += flight.kg;
      routes.set(flight.route, existing);
    });
    const airports = new Map();
    const maximumDensity = Math.max(1, ...[...routes.values()].map((route) => route.kg / Math.max(1, route.distanceKm || 1)));
    routes.forEach((route, label) => {
      const density = route.kg / Math.max(1, route.distanceKm || 1);
      const weight = Math.max(0.85, 8 * density / maximumDensity);
      splitAtDateLine(greatCircle(route.start, route.end)).forEach((segment) => {
        L.polyline(segment, { pane: "routes", color: "#f5faf7", opacity: 0.7, weight, lineCap: "round" })
          .bindTooltip(`${label}<br>${formatMassText(route.kg)} CO₂e`)
          .addTo(routeLayer);
      });
      [route.start, route.end].forEach((airport) => {
        const existing = airports.get(airport.iata) || { ...airport, kg: 0 };
        existing.kg += route.kg;
        airports.set(airport.iata, existing);
      });
    });
    const maximumAirportImpact = Math.max(1, ...[...airports.values()].map((airport) => airport.kg));
    // SVG stacks in draw order, so the heaviest airport is drawn last and sits on top.
    [...airports.values()].sort((a, b) => a.kg - b.kg).forEach((airport) => {
      const share = airport.kg / maximumAirportImpact;
      const fillColor = `hsl(${46 - 34 * share} 92% ${63 - 10 * share}%)`;
      L.circleMarker([airport.lat, airport.lon], { pane: "airports", radius: 3.5 + 4 * Math.sqrt(share), color: "#fff", weight: 1.2, fillColor, fillOpacity: 1 })
        .bindTooltip(`<strong>${airport.iata}</strong><br>${formatMassText(airport.kg)} CO₂e connected`, { direction: "top", offset: [0, -4] })
        .addTo(airportLayer);
    });
    const caption = document.querySelector(".map-caption span");
    caption.textContent = routes.size ? "Line area · airport colour = CO₂e" : "No priced routes in this period";
    requestAnimationFrame(() => routeMap.invalidateSize());
  }

  function updateTrend() {
    const series = [...data.years].sort((a, b) => a - b).map((year) => {
      const rows = data.flights.filter((flight) => flight.year === year);
      return {
        year,
        rows,
        completed: sum(withEmissions(rows.filter((flight) => flight.departed)), "kg"),
        planned: sum(withEmissions(rows.filter((flight) => !flight.departed)), "kg")
      };
    });
    const maximum = Math.max(0, ...series.map((item) => item.completed + item.planned));
    const totals = series.map((item) => item.completed + item.planned);
    const theme = chartTheme();
    const categories = breakdownCategories(withEmissions(data.flights), state.trendBreakdown);
    $("trend-chart").setAttribute("aria-label", series.map((item) => {
      const details = state.trendBreakdown === "total" ? "" : `; ${categories.map((category) => {
        const kg = sum(withEmissions(item.rows.filter((row) => visualCategory(row, state.trendBreakdown) === category)), "kg");
        return `${category} ${formatMassText(kg)}`;
      }).join(", ")}`;
      return `${item.year}: ${formatMassText(item.completed)} completed${item.planned ? `, ${formatMassText(item.planned)} upcoming` : ""}${details}`;
    }).join(", "));
    const datasets = categories.flatMap((category) => {
      const color = breakdownColor(state.trendBreakdown, category);
      const upcomingPattern = hatchPattern($("trend-chart"), color);
      const filterCategory = (row) => state.trendBreakdown === "total" || visualCategory(row, state.trendBreakdown) === category;
      return [
        {
          label: state.trendBreakdown === "total" ? "Completed" : `${category} · completed`,
          data: series.map((item) => sum(withEmissions(item.rows.filter((row) => row.departed && filterCategory(row))), "kg")),
          backgroundColor: color,
          hoverBackgroundColor: color,
          borderSkipped: false,
          borderRadius: stackedBorderRadius("x"),
          barPercentage: 0.72
        },
        {
          label: state.trendBreakdown === "total" ? "Upcoming" : `${category} · upcoming`,
          data: series.map((item) => sum(withEmissions(item.rows.filter((row) => !row.departed && filterCategory(row))), "kg")),
          backgroundColor: upcomingPattern,
          hoverBackgroundColor: upcomingPattern,
          borderColor: color,
          borderWidth: 1,
          borderSkipped: false,
          borderRadius: stackedBorderRadius("x"),
          barPercentage: 0.72
        }
      ];
    });
    replaceChart("trend-chart", {
      type: "bar",
      data: {
        labels: series.map((item) => item.year),
        datasets
      },
      plugins: [periodValueLabels(totals, maximum >= 1000 ? "t" : "kg")],
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${formatMassText(context.raw)} CO₂e` } }
        },
        scales: {
          x: { stacked: true, grid: { display: false }, border: { color: theme.grid }, ticks: { autoSkip: false, color: theme.muted, font: { size: 11 } } },
          y: { stacked: true, beginAtZero: true, grid: { color: theme.grid }, border: { display: false }, ticks: { color: theme.muted, callback: (value) => formatMassAxisTick(value, maximum) } }
        }
      }
    });

    const year = state.year || data.years[0];
    const current = series.find((item) => item.year === year);
    const trendUnit = current && Math.max(current.completed, current.planned) >= 1000 ? "t" : "kg";
    $("trend-note-label").textContent = state.year ? String(state.year) : data.years.length ? "Latest year" : "No dated flights";
    $("trend-note-value").innerHTML = current ? formatKg(current.completed, trendUnit) : "No flights";
    $("trend-note-copy").textContent = current && current.planned ? `${formatMassText(current.planned, trendUnit)} CO₂e comes from upcoming flights.` : current ? "No upcoming impact remains in this period." : "Add flights, then regenerate your Passport.";
  }

  function updateQuality(rows) {
    const done = completed(rows);
    const exact = done.filter((flight) => flight.emissionsSource === "exact").length;
    const typical = done.filter((flight) => flight.emissionsSource === "typical_route_average").length;
    const missing = done.length - exact - typical;
    const cabinKnown = done.filter((flight) => flight.cabinKnown).length;
    const reasonKnown = done.filter((flight) => flight.reason && flight.reason.toLowerCase() !== "unknown").length;
    const distance = done.filter((flight) => flight.distanceKm !== null).length;
    const duration = done.filter((flight) => flight.durationHours !== null).length;
    const airports = new Set(done.flatMap((flight) => [flight.origin, flight.destination]).filter(Boolean));
    const countries = new Set(done.flatMap((flight) => [flight.start && flight.start.country, flight.end && flight.end.country]).filter(Boolean));
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
    $("methodology").textContent = `Impact figures are per passenger. Distance intensity uses ${data.meta.distanceMethod}. Time intensity uses ${data.meta.durationMethod}. ${assumed ? `${assumed} flight${assumed === 1 ? "" : "s"} without cabin data ${assumed === 1 ? "is" : "are"} shown as Economy in charts.` : "Every flight has cabin data."} Missing values are never extrapolated.`;
  }

  function update() {
    const rows = scoped();
    updateHero(rows);
    updateMap(rows);
    updateTrend();
    updatePatterns(rows);
    updateExtremes(rows);
    updateRankings(rows);
    updateQuality(rows);
  }

  document.querySelectorAll("#metric-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.metric = button.dataset.metric;
      document.querySelectorAll("#metric-tabs button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      updateHero(scoped());
    });
  });

  document.querySelectorAll("#pattern-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.pattern = button.dataset.pattern;
      document.querySelectorAll("#pattern-tabs button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      updatePatterns(scoped());
    });
  });

  document.querySelectorAll("#trend-breakdown-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.trendBreakdown = button.dataset.trendBreakdown;
      document.querySelectorAll("#trend-breakdown-tabs button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      updateTrend();
    });
  });

  document.querySelectorAll("#pattern-breakdown-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.patternBreakdown = button.dataset.patternBreakdown;
      document.querySelectorAll("#pattern-breakdown-tabs button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      updatePatterns(scoped());
    });
  });

  document.querySelectorAll("#driver-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.driverBreakdown = button.dataset.breakdown;
      document.querySelectorAll("#driver-tabs button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      updateRankings(scoped());
    });
  });

  document.querySelectorAll("#driver-metric-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.driverMetric = button.dataset.driverMetric;
      document.querySelectorAll("#driver-metric-tabs button").forEach((peer) => peer.setAttribute("aria-pressed", String(peer === button)));
      updateRankings(scoped());
    });
  });

  document.querySelectorAll("[data-ranking]").forEach((button) => {
    button.addEventListener("click", () => openRanking(button.dataset.ranking));
  });
  $("ranking-dialog-close").addEventListener("click", () => $("ranking-dialog").close());
  $("ranking-dialog").addEventListener("click", (event) => {
    if (event.target === $("ranking-dialog")) $("ranking-dialog").close();
  });

  buildPeriods();
  setupMap();
  $("generated").textContent = `Generated ${new Date(data.meta.generatedAt).toLocaleString()} with contrail ${data.meta.contrailVersion}. Keep this file private.`;
  update();
})();
