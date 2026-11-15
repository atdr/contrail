// Aggregation: scoping to a period, filtering, grouping, metric buckets and
// rankings. Pure functions over the embedded flight list; nothing here touches
// the page.
Passport.aggregate = (() => {
  "use strict";

  const { visualCategory, monthOf, weekdayOf, MONTHS, WEEKDAYS, UNKNOWN, unknownLast } =
    Passport.format;

  const sum = (rows, field) => rows.reduce((total, row) => total + (row[field] || 0), 0);
  const withEmissions = (rows) => rows.filter((flight) => flight.kg !== null);
  const completed = (rows) => rows.filter((flight) => flight.departed);
  const pricedCompleted = (rows) => withEmissions(completed(rows));

  const scoped = (flights, year) =>
    flights.filter((flight) => year === null || flight.year === year);

  const metricRows = (rows, field) =>
    pricedCompleted(rows).filter((flight) => flight[field] !== null);

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
    if (metric === "distance")
      return bucket.distanceKm ? (bucket.distanceKg * 1000) / bucket.distanceKm : null;
    if (metric === "duration")
      return bucket.durationHours ? bucket.durationKg / bucket.durationHours : null;
    return bucket.totalKg;
  }

  // Every priced, completed flight counted once under each distinct label
  // `labelsFor` gives it, with a metric bucket for the whole entry and one per
  // cabin and per reason. Heaviest first, with Unknown last.
  function aggregateBy(rows, labelsFor) {
    const values = new Map();
    pricedCompleted(rows).forEach((flight) => {
      [...new Set(labelsFor(flight).filter(Boolean))].forEach((label) => {
        const entry = values.get(label) || {
          label,
          total: 0,
          metrics: emptyMetricBucket(),
          cabin: new Map(),
          reason: new Map(),
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
    return [...values.values()].sort((a, b) => unknownLast(a.label, b.label) || b.total - a.total);
  }

  // One label per flight, "Unknown" where it has none.
  const rankAll = (rows, labeler) => aggregateBy(rows, (row) => [labeler(row) || UNKNOWN]);

  // [label, kg] pairs, heaviest first.
  const groupAll = (rows, labeler) =>
    rankAll(rows, labeler).map((entry) => [entry.label, entry.total]);

  function breakdownCategories(rows, breakdown) {
    if (breakdown === "total") return ["Total"];
    const totals = new Map();
    withEmissions(rows).forEach((row) => {
      const category = visualCategory(row, breakdown);
      totals.set(category, (totals.get(category) || 0) + row.kg);
    });
    return [...totals.entries()]
      .sort((a, b) => unknownLast(a[0], b[0]) || b[1] - a[1])
      .map(([label]) => label);
  }

  function rankingMetricValue(entry, metric) {
    if (entry.metrics) return metricValue(entry.metrics, metric);
    return metric === "total" ? entry.total : null;
  }

  function rankingCategoryValue(entry, breakdown, category, metric) {
    const bucket = entry[breakdown].get(category);
    if (!bucket) return 0;
    if (metric === "total") return bucket.totalKg;
    const denominator =
      metric === "distance" ? entry.metrics.distanceKm : entry.metrics.durationHours;
    if (!denominator) return 0;
    return metric === "distance"
      ? (bucket.distanceKg * 1000) / denominator
      : bucket.durationKg / denominator;
  }

  function rankForMetric(entries, metric) {
    return entries
      .map((entry) => ({ entry, value: rankingMetricValue(entry, metric) }))
      .filter((item) => item.value !== null)
      .sort((a, b) => unknownLast(a.entry.label, b.entry.label) || b.value - a.value)
      .map((item) => item.entry);
  }

  const PATTERNS = {
    month: { labels: MONTHS, value: (flight) => monthOf(flight.date) },
    weekday: { labels: WEEKDAYS, value: (flight) => weekdayOf(flight.date) },
  };

  // Completed CO2e by month or weekday, split by category: the labels in
  // chart order, the categories heaviest first, a Map of label to a Map of
  // category to kg, and each label's total.
  function patternBuckets(rows, pattern, breakdown) {
    const validDates = pricedCompleted(rows).filter((flight) => flight.date);
    const definition = PATTERNS[pattern];
    const categories = breakdownCategories(validDates, breakdown);
    const values = new Map(
      definition.labels.map((label) => [
        label,
        new Map(categories.map((category) => [category, 0])),
      ]),
    );
    validDates.forEach((flight) => {
      const label = definition.value(flight);
      const category = breakdown === "total" ? "Total" : visualCategory(flight, breakdown);
      values.get(label).set(category, values.get(label).get(category) + flight.kg);
    });
    const totals = definition.labels.map((label) =>
      sum(
        [...values.get(label).values()].map((kg) => ({ kg })),
        "kg",
      ),
    );
    return { labels: definition.labels, categories, values, totals };
  }

  // Each year oldest first, with its flights and its completed and planned kg.
  function yearSeries(flights, years) {
    return [...years]
      .sort((a, b) => a - b)
      .map((year) => {
        const rows = flights.filter((flight) => flight.year === year);
        return {
          year,
          rows,
          completed: sum(withEmissions(rows.filter((flight) => flight.departed)), "kg"),
          planned: sum(withEmissions(rows.filter((flight) => !flight.departed)), "kg"),
        };
      });
  }

  // [flight, value] pairs sorted lightest first, for each headline measure.
  function extremes(rows) {
    const done = pricedCompleted(rows);
    return {
      total: done.map((flight) => [flight, flight.kg]).sort((a, b) => a[1] - b[1]),
      distance: done
        .filter((flight) => flight.distanceKm)
        .map((flight) => [flight, (flight.kg * 1000) / flight.distanceKm])
        .sort((a, b) => a[1] - b[1]),
      duration: done
        .filter((flight) => flight.durationHours)
        .map((flight) => [flight, flight.kg / flight.durationHours])
        .sort((a, b) => a[1] - b[1]),
    };
  }

  return {
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
    rankingMetricValue,
    rankingCategoryValue,
    rankForMetric,
    patternBuckets,
    yearSeries,
    extremes,
  };
})();
