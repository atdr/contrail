// Charts: Chart.js defaults, theme, plugins, and every chart Passport draws.
// Each canvas gets one chart, made on its first draw and updated in place on
// every draw after.
Passport.charts = (() => {
  "use strict";

  const {
    formatNumber,
    formatMassText,
    formatMassAxisTick,
    formatRankingValue,
    MONTHS,
    cssColor,
    categoryColor,
    categoryIcon,
    visualCategory,
  } = Passport.format;
  const { sum, withEmissions, rankingMetricValue, rankingCategoryValue } = Passport.aggregate;

  const $ = (id) => document.getElementById(id);

  const chartFont = getComputedStyle(document.documentElement)
    .getPropertyValue("--font-sans")
    .trim();
  Chart.defaults.font.family = chartFont;
  Chart.defaults.plugins.tooltip.titleFont = { family: chartFont, weight: "600" };
  Chart.defaults.plugins.tooltip.bodyFont = { family: chartFont };
  Chart.defaults.plugins.tooltip.footerFont = { family: chartFont };

  const totalColor = () => cssColor("--total-value");
  const breakdownColor = (breakdown, category) =>
    breakdown === "total" ? totalColor() : categoryColor(breakdown, category);

  // Chart colours are the stylesheet's, read at each draw. A custom property
  // set with light-dark() computes to that function rather than a colour, so
  // each is resolved through a hidden element's `color`, which applies the
  // active scheme.
  const probe = document.createElement("span");
  probe.hidden = true;
  document.body.append(probe);
  function themeColor(name) {
    probe.style.color = `var(${name})`;
    return getComputedStyle(probe).color;
  }

  function chartTheme() {
    return {
      text: themeColor("--text"),
      muted: themeColor("--muted"),
      grid: themeColor("--chart-grid"),
    };
  }

  const charts = new Map();

  const TICK_SIZE = 12;

  // Below this width a period chart shortens its axis: month initials, and
  // value labels a size smaller.
  const narrow = (chart) => chart.width < 420;

  // A period chart's x ticks: never rotated, thinned to what fits, and a
  // month as its initial on a narrow chart.
  const periodTicks = (color, extra = {}) => ({
    autoSkip: true,
    autoSkipPadding: 6,
    maxRotation: 0,
    minRotation: 0,
    color,
    ...extra,
    callback(value) {
      const label = String(this.getLabelForValue(value));
      return narrow(this.chart) && MONTHS.includes(label) ? label[0] : label;
    },
  });

  // Every ranking chart's label gutter, the same share of its width within
  // fixed bounds, so rankings of one width start their bars at one x rather
  // than each sizing a gutter to its own longest label.
  const rankingGutter = (chart) => Math.round(Math.min(220, Math.max(96, chart.width * 0.44)));

  const fits = (ctx, text, width) => ctx.measureText(text).width <= width;
  const firstParts = (label, count) => label.split(" · ").slice(0, count).join(" · ");

  // Labels are cut to `width` px in ctx's current font. Whole " · " parts go
  // from the end first, the same number from every label so a ranking reads
  // as one list: a flight keeps its number and route before its date. Then a
  // label still too long loses characters and ends in an ellipsis.
  function sharedParts(ctx, labels, width) {
    const most = Math.max(1, ...labels.map((label) => label.split(" · ").length));
    for (let count = most; count > 1; count -= 1) {
      if (labels.every((label) => fits(ctx, firstParts(label, count), width))) return count;
    }
    return 1;
  }

  function fitLabel(ctx, label, width, parts) {
    const kept = firstParts(label, parts);
    if (fits(ctx, kept, width)) return kept;
    const characters = Array.from(kept);
    let low = 0;
    let high = characters.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (fits(ctx, `${characters.slice(0, middle).join("").trimEnd()}…`, width)) low = middle;
      else high = middle - 1;
    }
    return `${characters.slice(0, low).join("").trimEnd()}…`;
  }

  // Draw `configuration` on the canvas `id`. The first call makes the chart;
  // later calls swap its data and options and redraw it in place. Plugins are
  // fixed when the chart is made, so a plugin that needs this draw's figures
  // reads them from `chart.data.valueLabels`, which travels with the data.
  function draw(id, configuration) {
    const existing = charts.get(id);
    if (!existing) {
      charts.set(id, new Chart($(id), configuration));
      return;
    }
    existing.data = configuration.data;
    existing.options = configuration.options;
    existing.update("none");
  }

  // A value at the end of each horizontal bar: `totals` and `metric` from
  // chart.data.valueLabels.
  const rankingValueLabels = {
    id: "passport-ranking-value-labels",
    afterDatasetsDraw(chart) {
      const { totals, metric } = chart.data.valueLabels;
      const { ctx } = chart;
      ctx.save();
      ctx.fillStyle = chartTheme().text;
      ctx.font = `500 12px ${chartFont}`;
      totals.forEach((total, index) => {
        const bars = chart.data.datasets
          .map((_, datasetIndex) => chart.getDatasetMeta(datasetIndex).data[index])
          .filter(Boolean);
        const anchor = bars.reduce(
          (furthest, bar) => (!furthest || bar.x > furthest.x ? bar : furthest),
          null,
        );
        if (!anchor) return;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(formatRankingValue(total, metric), anchor.x + 8, anchor.y);
      });
      ctx.restore();
    },
  };

  // A mass above each vertical stack: `totals` and `unit` from
  // chart.data.valueLabels. Drawn newest first, each kept inside the canvas
  // at either edge. When bars are narrow a label is wider than its bar, so
  // one that would overlap a neighbouring bar or a label already drawn is
  // left out rather than drawn as a smear.
  const periodValueLabels = {
    id: "passport-period-value-labels",
    afterDatasetsDraw(chart) {
      const { totals, unit } = chart.data.valueLabels;
      const { ctx, width } = chart;
      const size = narrow(chart) ? 11 : 12;
      const stacks = totals.map((_, index) =>
        chart.data.datasets
          .map((dataset, datasetIndex) =>
            dataset.data[index] ? chart.getDatasetMeta(datasetIndex).data[index] : null,
          )
          .filter(Boolean)
          .reduce((highest, bar) => (!highest || bar.y < highest.y ? bar : highest), null),
      );
      ctx.save();
      ctx.fillStyle = chartTheme().text;
      ctx.font = `500 ${size}px ${chartFont}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      let limit = Number.POSITIVE_INFINITY;
      for (let index = totals.length - 1; index >= 0; index -= 1) {
        const anchor = stacks[index];
        if (!anchor) continue;
        const text = formatMassText(totals[index], unit);
        const half = ctx.measureText(text).width / 2;
        const x = Math.min(Math.max(anchor.x, half), width - half);
        const bottom = anchor.y - 7;
        const blocked = stacks.some(
          (bar, other) =>
            bar &&
            other !== index &&
            bar.x + bar.width / 2 > x - half &&
            bar.x - bar.width / 2 < x + half &&
            bar.y < bottom,
        );
        if (blocked || x + half > limit - 4) continue;
        ctx.fillText(text, x, bottom);
        limit = x - half;
      }
      ctx.restore();
    },
  };

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
        .map((dataset, index) => (Number(dataset.data[context.dataIndex]) > 0 ? index : null))
        .filter((index) => index !== null);
      const last = active.at(-1);
      if (axis === "y") {
        return {
          topLeft: 0,
          bottomLeft: 0,
          topRight: context.datasetIndex === last ? radius : 0,
          bottomRight: context.datasetIndex === last ? radius : 0,
        };
      }
      return {
        bottomLeft: 0,
        bottomRight: 0,
        topLeft: context.datasetIndex === last ? radius : 0,
        topRight: context.datasetIndex === last ? radius : 0,
      };
    };
  }

  // A ranking's breakdown categories, heaviest across its entries first;
  // none for the total.
  function rankingCategories(entries, breakdown, metric) {
    const categoryTotals = new Map();
    if (breakdown !== "total") {
      entries.forEach((entry) => {
        entry[breakdown].forEach((_, label) => {
          const value = rankingCategoryValue(entry, breakdown, label, metric);
          categoryTotals.set(label, (categoryTotals.get(label) || 0) + value);
        });
      });
    }
    return [...categoryTotals.entries()].sort((a, b) => b[1] - a[1]).map((entry) => entry[0]);
  }

  function ranking(id, entries, breakdown, metric) {
    const theme = chartTheme();
    const totals = entries.map((entry) => rankingMetricValue(entry, metric));
    const maximum = Math.max(1, ...totals);
    const categories = rankingCategories(entries, breakdown, metric);
    const datasets =
      breakdown === "total"
        ? [
            {
              label: "Total",
              data: totals,
              backgroundColor: totalColor(),
              hoverBackgroundColor: totalColor(),
              borderSkipped: false,
              borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 7, bottomRight: 7 },
              barPercentage: 0.72,
            },
          ]
        : categories.map((category) => ({
            label: category,
            data: entries.map((entry) => rankingCategoryValue(entry, breakdown, category, metric)),
            backgroundColor: categoryColor(breakdown, category),
            hoverBackgroundColor: categoryColor(breakdown, category),
            borderSkipped: false,
            borderRadius: stackedBorderRadius("y"),
            barPercentage: 0.72,
          }));
    $(id).setAttribute("role", "img");
    $(id).setAttribute(
      "aria-label",
      entries.length
        ? entries
            .map((entry) => {
              const categoriesText =
                breakdown === "total"
                  ? ""
                  : `; ${[...entry[breakdown].keys()].map((label) => `${label} ${formatRankingValue(rankingCategoryValue(entry, breakdown, label, metric), metric, true)}`).join(", ")}`;
              return `${entry.label}: ${formatRankingValue(rankingMetricValue(entry, metric), metric, true)}${categoriesText}`;
            })
            .join(", ")
        : `No flights with the data required for ${metric === "distance" ? "distance intensity" : metric === "duration" ? "time intensity" : "total impact"} in this period`,
    );
    draw(id, {
      type: "bar",
      data: {
        labels: entries.map((entry) => entry.label),
        datasets,
        valueLabels: { totals, metric },
      },
      plugins: [rankingValueLabels],
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: "y",
        // Left padding: Chrome draws a flag emoji a few pixels left of where
        // measureText says it starts, so a label filling the gutter lost it.
        layout: { padding: { left: 6, right: metric === "total" ? 58 : 88 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              // The full label: the axis may show it cut short.
              title: (items) =>
                items.length ? items[0].chart.data.labels[items[0].dataIndex] : "",
              label: (context) =>
                `${context.dataset.label}: ${formatRankingValue(context.raw, metric, true)}`,
            },
          },
        },
        scales: {
          x: { display: false, stacked: true, beginAtZero: true, max: maximum * 1.24 },
          y: {
            stacked: true,
            grid: { display: false },
            border: { display: false },
            afterFit: (scale) => {
              scale.width = rankingGutter(scale.chart);
            },
            ticks: {
              autoSkip: false,
              color: theme.text,
              font: { size: TICK_SIZE },
              callback(value) {
                const { ctx } = this.chart;
                const width = rankingGutter(this.chart) - this.options.ticks.padding - 2;
                ctx.save();
                ctx.font = `${TICK_SIZE}px ${chartFont}`;
                const parts = sharedParts(ctx, this.chart.data.labels, width);
                const label = fitLabel(ctx, this.getLabelForValue(value), width, parts);
                ctx.restore();
                return label;
              },
            },
          },
        },
      },
    });
  }

  // A whole ranking as an HTML list in the element `id`: rank, label, value,
  // and a bar sized in CSS, split by category when a breakdown is chosen.
  // HTML rather than a canvas because a canvas tall enough for every flight
  // in a long log passes iOS Safari's canvas size limit and draws blank.
  function rankingList(id, entries, breakdown, metric) {
    const list = $(id);
    const totals = entries.map((entry) => rankingMetricValue(entry, metric));
    const maximum = Math.max(0, ...totals);
    const categories = rankingCategories(entries, breakdown, metric);
    const span = (className, text = "") => {
      const element = document.createElement("span");
      element.className = className;
      element.textContent = text;
      return element;
    };
    // Each " · " part of a label wraps as a whole, so a flight breaks between
    // its route and its date rather than inside the date.
    const rankingLabel = (label) => {
      const element = span("ranking-label");
      const parts = label.split(" · ");
      parts.forEach((part, index) => {
        if (index) element.append(" ");
        element.append(span("", index < parts.length - 1 ? `${part} ·` : part));
      });
      return element;
    };
    list.replaceChildren(
      ...entries.map((entry, index) => {
        const total = totals[index];
        const row = document.createElement("li");
        row.className = "ranking-row";
        const bar = span("ranking-bar");
        bar.setAttribute("aria-hidden", "true");
        const fill = span("ranking-fill");
        fill.style.width = `${maximum ? (total / maximum) * 100 : 0}%`;
        const parts =
          breakdown === "total"
            ? [["Total", total, totalColor()]]
            : categories.map((category) => [
                category,
                rankingCategoryValue(entry, breakdown, category, metric),
                categoryColor(breakdown, category),
              ]);
        const present = parts.filter(([, value]) => value > 0);
        present.forEach(([category, value, color]) => {
          const segment = span("ranking-segment");
          segment.style.width = `${total ? (value / total) * 100 : 0}%`;
          segment.style.background = color;
          segment.title = `${category}: ${formatRankingValue(value, metric, true)}`;
          fill.append(segment);
        });
        bar.append(fill);
        row.append(
          span("ranking-rank", String(index + 1)),
          rankingLabel(entry.label),
          span("ranking-value", formatRankingValue(total, metric)),
          bar,
        );
        if (breakdown !== "total") {
          row.append(
            span(
              "visually-hidden",
              present
                .map(
                  ([category, value]) => `${category} ${formatRankingValue(value, metric, true)}`,
                )
                .join(", "),
            ),
          );
        }
        return row;
      }),
    );
  }

  function composition(id, entries, kind) {
    const total = entries.reduce((value, entry) => value + entry[1], 0);
    $(`${id}-chart`).setAttribute("role", "img");
    $(`${id}-chart`).setAttribute(
      "aria-label",
      entries.length
        ? entries
            .map(([label, kg]) => `${label}: ${formatNumber(total ? (kg / total) * 100 : 0, 1)}%`)
            .join(", ")
        : "No priced flights in this period",
    );
    draw(`${id}-chart`, {
      type: "bar",
      data: {
        labels: [""],
        datasets: entries.map(([label, kg], index) => ({
          label,
          data: [kg],
          backgroundColor: categoryColor(kind, label),
          hoverBackgroundColor: categoryColor(kind, label),
          borderSkipped: false,
          borderRadius:
            index === 0
              ? { topLeft: 14, bottomLeft: 14 }
              : index === entries.length - 1
                ? { topRight: 14, bottomRight: 14 }
                : 0,
          barThickness: 28,
        })),
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) =>
                `${context.dataset.label}: ${formatMassText(context.raw)} CO₂e · ${formatNumber(total ? (context.raw / total) * 100 : 0, 1)}%`,
            },
          },
        },
        scales: {
          x: { display: false, stacked: true, max: total || 1 },
          y: { display: false, stacked: true },
        },
      },
    });

    const legend = $(`${id}-legend`);
    legend.replaceChildren();
    entries.forEach(([label, kg]) => {
      const percentage = total ? (kg / total) * 100 : 0;
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

  // `buckets` is what Passport.aggregate.patternBuckets returns.
  function pattern(buckets, breakdown) {
    const { labels, categories, values, totals } = buckets;
    const maximum = Math.max(0, ...totals);
    const theme = chartTheme();
    $("pattern-chart").setAttribute(
      "aria-label",
      labels
        .map((label, index) => {
          const details =
            breakdown === "total"
              ? ""
              : `; ${categories.map((category) => `${category} ${formatMassText(values.get(label).get(category))}`).join(", ")}`;
          return `${label}: ${formatMassText(totals[index])} CO₂e${details}`;
        })
        .join(", "),
    );
    draw("pattern-chart", {
      type: "bar",
      data: {
        labels,
        datasets: categories.map((category) => ({
          label: category,
          data: labels.map((label) => values.get(label).get(category)),
          backgroundColor: breakdownColor(breakdown, category),
          hoverBackgroundColor: breakdownColor(breakdown, category),
          borderSkipped: false,
          borderRadius: stackedBorderRadius("x"),
          barPercentage: 0.66,
        })),
        valueLabels: { totals, unit: maximum >= 1000 ? "t" : "kg" },
      },
      plugins: [periodValueLabels],
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) => `${context.dataset.label}: ${formatMassText(context.raw)} CO₂e`,
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            border: { color: theme.grid },
            ticks: periodTicks(theme.muted),
          },
          y: {
            stacked: true,
            beginAtZero: true,
            suggestedMax: maximum * 1.12,
            grid: { color: theme.grid },
            border: { display: false },
            ticks: { color: theme.muted, callback: (value) => formatMassAxisTick(value, maximum) },
          },
        },
      },
    });
  }

  // `series` is what Passport.aggregate.yearSeries returns; `categories` the
  // breakdown's categories across every flight.
  function trend(series, categories, breakdown) {
    const maximum = Math.max(0, ...series.map((item) => item.completed + item.planned));
    const totals = series.map((item) => item.completed + item.planned);
    const theme = chartTheme();
    $("trend-chart").setAttribute(
      "aria-label",
      series
        .map((item) => {
          const details =
            breakdown === "total"
              ? ""
              : `; ${categories
                  .map((category) => {
                    const kg = sum(
                      withEmissions(
                        item.rows.filter((row) => visualCategory(row, breakdown) === category),
                      ),
                      "kg",
                    );
                    return `${category} ${formatMassText(kg)}`;
                  })
                  .join(", ")}`;
          return `${item.year}: ${formatMassText(item.completed)} completed${item.planned ? `, ${formatMassText(item.planned)} upcoming` : ""}${details}`;
        })
        .join(", "),
    );
    const datasets = categories.flatMap((category) => {
      const color = breakdownColor(breakdown, category);
      const upcomingPattern = hatchPattern($("trend-chart"), color);
      const filterCategory = (row) =>
        breakdown === "total" || visualCategory(row, breakdown) === category;
      return [
        {
          label: breakdown === "total" ? "Completed" : `${category} · completed`,
          data: series.map((item) =>
            sum(
              withEmissions(item.rows.filter((row) => row.departed && filterCategory(row))),
              "kg",
            ),
          ),
          backgroundColor: color,
          hoverBackgroundColor: color,
          borderSkipped: false,
          borderRadius: stackedBorderRadius("x"),
          barPercentage: 0.72,
        },
        {
          label: breakdown === "total" ? "Upcoming" : `${category} · upcoming`,
          data: series.map((item) =>
            sum(
              withEmissions(item.rows.filter((row) => !row.departed && filterCategory(row))),
              "kg",
            ),
          ),
          backgroundColor: upcomingPattern,
          hoverBackgroundColor: upcomingPattern,
          borderColor: color,
          borderWidth: 1,
          borderSkipped: false,
          borderRadius: stackedBorderRadius("x"),
          barPercentage: 0.72,
        },
      ];
    });
    draw("trend-chart", {
      type: "bar",
      data: {
        labels: series.map((item) => item.year),
        datasets,
        valueLabels: { totals, unit: maximum >= 1000 ? "t" : "kg" },
      },
      plugins: [periodValueLabels],
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) => `${context.dataset.label}: ${formatMassText(context.raw)} CO₂e`,
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            border: { color: theme.grid },
            ticks: periodTicks(theme.muted, { font: { size: 11 } }),
          },
          y: {
            stacked: true,
            beginAtZero: true,
            grid: { color: theme.grid },
            border: { display: false },
            ticks: { color: theme.muted, callback: (value) => formatMassAxisTick(value, maximum) },
          },
        },
      },
    });
  }

  return { ranking, rankingList, composition, pattern, trend };
})();
