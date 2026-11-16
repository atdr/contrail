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
    massUnit,
    MONTHS,
    cssColor,
    categoryColor,
    categoryIcon,
    visualCategory,
    isUnknown,
    unknownLast,
    tooltipContent,
  } = Passport.format;
  const { sum, withEmissions, rankingMetricValue, rankingCategoryValue } = Passport.aggregate;

  const $ = (id) => document.getElementById(id);

  const chartFont = getComputedStyle(document.documentElement)
    .getPropertyValue("--font-sans")
    .trim();
  // One text scale for every chart: 12px ticks at regular weight. Value
  // labels drawn by the plugins below are the only medium-weight chart text.
  const TICK_SIZE = 12;
  Chart.defaults.font.family = chartFont;
  Chart.defaults.font.size = TICK_SIZE;
  Chart.defaults.font.weight = "normal";

  const totalColor = () => cssColor("--total-value");
  // A total bar's colour: grey for the Unknown entry, which is no one
  // route, airline or aircraft and should not read as one.
  const entryColor = (label) => cssColor(isUnknown(label) ? "--category-unknown" : "--total-value");
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

  // One HTML tooltip for every chart, styled by the stylesheet like the
  // map's, in place of Chart.js's own canvas box: a black 12px panel that
  // matched nothing else on the page. Each chart says what to show with a
  // `build(items)` that returns tooltipContent()'s input, or null for none.
  const tip = document.createElement("div");
  tip.className = "chart-tooltip";
  // Hidden from assistive technology: each canvas carries the same figures
  // in its aria-label (rankingTooltip's details included), and a live region
  // would read every hover.
  tip.setAttribute("aria-hidden", "true");
  tip.hidden = true;
  document.body.append(tip);
  let tipChart = null;

  function hideTooltip() {
    if (tip.hidden) return;
    tip.hidden = true;
    const chart = tipChart;
    tipChart = null;
    // Clear the chart's own active state too, or hovering the same bar
    // again would change nothing and never bring the tooltip back.
    if (chart?.canvas?.isConnected) {
      chart.tooltip.setActiveElements([], { x: 0, y: 0 });
      chart.update("none");
    }
  }

  // Beside the point Chart.js chose, above it where there is room, and
  // always inside the viewport, which on a phone is narrower than a
  // tooltip centred on a bar near the edge.
  function placeTooltip(chart, tooltip) {
    const canvas = chart.canvas.getBoundingClientRect();
    const margin = 8;
    const x = canvas.left + tooltip.caretX;
    const y = canvas.top + tooltip.caretY;
    const { width, height } = tip.getBoundingClientRect();
    const left = Math.min(Math.max(x - width / 2, margin), window.innerWidth - width - margin);
    const above = y - height - 12;
    // Index interaction can pick a point whose caret is below the viewport
    // when a chart is only partly on screen, so either choice is clamped to
    // the bottom edge as well as the top.
    const preferred = above >= margin ? above : y + 12;
    const top = Math.min(preferred, window.innerHeight - height - margin);
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.floor(Math.max(top, margin))}px`;
  }

  // A tooltip row's swatch: the bar's own colour, or the dataset's `swatch`
  // where the bar is filled with a pattern, as an upcoming one is.
  const swatchOf = (item) => item.dataset.swatch ?? item.element.options.backgroundColor;
  const hatchedOf = (item) => Boolean(item.dataset.hatched);

  // A stacked bar's tooltip: its non-zero parts, top of the stack first so
  // the list reads in the order the bar does, all in the unit the total
  // needs, and the total beneath when there is more than one part.
  function stackTooltip(items) {
    const title = String(items[0].label);
    const shown = items.filter((item) => item.raw > 0).reverse();
    if (!shown.length) return { title, rows: [{ label: "No CO₂e", value: "" }] };
    const total = shown.reduce((value, item) => value + item.raw, 0);
    const unit = massUnit([total]);
    return {
      title,
      rows: shown.map((item) => ({
        label: item.dataset.label,
        value: formatMassText(item.raw, unit),
        color: swatchOf(item),
        hatched: hatchedOf(item),
      })),
      total: shown.length > 1 ? { label: "Total", value: formatMassText(total, unit) } : null,
    };
  }

  function tooltipOptions(build) {
    return {
      enabled: false,
      external({ chart, tooltip }) {
        const content =
          tooltip.opacity && tooltip.dataPoints?.length ? build(tooltip.dataPoints) : null;
        if (!content) {
          if (tipChart === chart) hideTooltip();
          return;
        }
        tip.replaceChildren(tooltipContent(content));
        tip.hidden = false;
        tipChart = chart;
        placeTooltip(chart, tooltip);
      },
    };
  }

  // A tooltip belongs to where the pointer is: scrolling moves the bar out
  // from under it, and a tap elsewhere on a touch screen means done.
  document.addEventListener("scroll", hideTooltip, { capture: true, passive: true });
  window.addEventListener("resize", hideTooltip);
  document.addEventListener("pointerdown", (event) => {
    if (tipChart && event.target !== tipChart.canvas) hideTooltip();
  });
  document.addEventListener("pointerleave", hideTooltip);

  // What a click on a chart opens: main.js hands in its panel opener, which
  // takes a filter key and the element to return focus to.
  let select = () => {};
  const onSelect = (handler) => {
    select = handler;
  };
  const panelKey = (kind, value) => `${kind}:${value}`;

  // A click handler for a bar chart along `axis`, the axis its categories
  // sit on. The category is read from the pointer's position on that axis,
  // not from the bar under it, so a click on a short bar's row or on its
  // label counts too. `pick(index)` does the rest.
  function clickAlong(axis, pick) {
    return (event, _elements, chart) => {
      const scale = chart.scales[axis];
      const index = Math.round(scale.getValueForPixel(axis === "y" ? event.y : event.x));
      if (!(index >= 0 && index < chart.data.labels.length)) return;
      hideTooltip();
      pick(index, chart);
    };
  }

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

  function hatchPattern(canvas, color, alpha = 1) {
    const hatch = document.createElement("canvas");
    hatch.width = 9;
    hatch.height = 9;
    const context = hatch.getContext("2d");
    context.globalAlpha = alpha;
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
    return [...categoryTotals.entries()]
      .sort((a, b) => unknownLast(a[0], b[0]) || b[1] - a[1])
      .map((entry) => entry[0]);
  }

  const MEASURE_NAMES = { total: "Total", distance: "Per km", duration: "Per hour" };

  // A ranking entry's tooltip: the full label, its value or its breakdown,
  // then how many flights it holds, its share of the period's CO2e, and the
  // measures the ranking is not sorted by, where the data allows them.
  function rankingTooltip(entry, breakdown, metric, periodKg, color) {
    const value = rankingMetricValue(entry, metric);
    const unit = metric === "total" ? massUnit([value]) : undefined;
    const parts =
      breakdown === "total"
        ? []
        : [...entry[breakdown].keys()]
            .map((category) => [category, rankingCategoryValue(entry, breakdown, category, metric)])
            .filter(([, part]) => part > 0)
            .sort((a, b) => unknownLast(a[0], b[0]) || b[1] - a[1]);
    const rows = parts.length
      ? parts.map(([category, part]) => ({
          label: category,
          value: formatRankingValue(part, metric, false, unit),
          color: categoryColor(breakdown, category),
        }))
      : [
          {
            label: MEASURE_NAMES[metric],
            value: formatRankingValue(value, metric, false, unit),
            color,
          },
        ];
    const others = ["total", "distance", "duration"]
      .filter((measure) => measure !== metric)
      .map((measure) => [measure, rankingMetricValue(entry, measure)])
      .filter(([, other]) => other !== null && other > 0)
      .map(([measure, other]) => ({
        label: measure === "total" ? "CO₂e" : MEASURE_NAMES[measure],
        value: formatRankingValue(other, measure),
      }));
    return {
      title: entry.label,
      rows,
      total:
        parts.length > 1
          ? { label: MEASURE_NAMES[metric], value: formatRankingValue(value, metric, false, unit) }
          : null,
      details: [
        { label: entry.flights === 1 ? "Flight" : "Flights", value: formatNumber(entry.flights) },
        ...(periodKg
          ? [
              {
                label: "Share of CO₂e",
                value: `${formatNumber((entry.metrics.totalKg / periodKg) * 100, 1)}%`,
              },
            ]
          : []),
        ...others,
      ],
    };
  }

  // rankingTooltip's details as one line of text, for the places a hover
  // never reaches: a canvas's aria-label and each "See all" row.
  const rankingDetailsText = (entry, breakdown, metric, periodKg) =>
    rankingTooltip(entry, breakdown, metric, periodKg)
      .details.map((detail) => `${detail.label} ${detail.value}`)
      .join(", ");

  // `kind` is the flight panel filter an entry's bar opens, on entry.value.
  function ranking(id, entries, breakdown, metric, periodKg, kind) {
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
              backgroundColor: entries.map((entry) => entryColor(entry.label)),
              hoverBackgroundColor: entries.map((entry) => entryColor(entry.label)),
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
              return `${entry.label}: ${formatRankingValue(rankingMetricValue(entry, metric), metric, true)}${categoriesText}; ${rankingDetailsText(entry, breakdown, metric, periodKg)}`;
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
        interaction: { mode: "index", axis: "y", intersect: false },
        onClick: clickAlong("y", (index, chart) =>
          select(panelKey(kind, entries[index].value), chart.canvas),
        ),
        layout: { padding: { right: metric === "total" ? 58 : 88 } },
        plugins: {
          legend: { display: false },
          // The whole row answers, not only the bar: a short bar is a
          // small target.
          tooltip: tooltipOptions((items) => {
            const entry = entries[items[0].dataIndex];
            return rankingTooltip(entry, breakdown, metric, periodKg, entryColor(entry.label));
          }),
        },
        scales: {
          x: { display: false, stacked: true, beginAtZero: true, max: maximum * 1.24 },
          y: {
            stacked: true,
            // No tick marks: they take 8px of the gutter even with the grid
            // hidden, which is room the labels were measured to have.
            grid: { display: false, drawTicks: false },
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
  // Each row is a button that opens the flight panel for its entry, so the
  // dialog is also the keyboard's way into a ranking drawn on a canvas.
  // `numbered` false leaves the rank column blank, for a list in its own
  // order, such as months in calendar order, where a rank would mislead.
  function rankingList(id, entries, breakdown, metric, periodKg, kind, numbered = true) {
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
    const rankingLabel = (label, flag) => {
      const element = span("ranking-label");
      if (flag) {
        const mark = span("ranking-flag", flag);
        mark.setAttribute("aria-hidden", "true");
        element.append(mark, " ");
      }
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
        const item = document.createElement("li");
        const row = document.createElement("button");
        row.type = "button";
        row.className = "ranking-row";
        row.dataset.openPanel = panelKey(kind, entry.value);
        item.append(row);
        const bar = span("ranking-bar");
        bar.setAttribute("aria-hidden", "true");
        const fill = span("ranking-fill");
        fill.style.width = `${maximum ? (total / maximum) * 100 : 0}%`;
        const parts =
          breakdown === "total"
            ? [["Total", total, entryColor(entry.label)]]
            : categories.map((category) => [
                category,
                rankingCategoryValue(entry, breakdown, category, metric),
                categoryColor(breakdown, category),
              ]);
        const present = parts.filter(([, value]) => value > 0);
        present.forEach(([, value, color]) => {
          const segment = span("ranking-segment");
          segment.style.width = `${total ? (value / total) * 100 : 0}%`;
          segment.style.background = color;
          fill.append(segment);
        });
        bar.append(fill);
        row.append(
          span("ranking-rank", numbered ? String(index + 1) : ""),
          rankingLabel(entry.label, entry.flag),
          span("ranking-value", formatRankingValue(total, metric)),
          bar,
        );
        // A breakdown is spelled out under the bar, not left to colour or a
        // hover title: touch screens never show a title, and screen readers
        // need the figures as text.
        if (breakdown !== "total") {
          const details = span("ranking-breakdown");
          present.forEach(([category, value, color]) => {
            const item = span("ranking-breakdown-item");
            const swatch = span("ranking-swatch");
            swatch.style.background = color;
            swatch.setAttribute("aria-hidden", "true");
            item.append(swatch, `${category} ${formatRankingValue(value, metric)}`);
            details.append(item);
          });
          row.append(details);
        }
        // What a hover shows on the chart, visible here, where no hover is.
        row.append(span("ranking-details", rankingDetailsText(entry, breakdown, metric, periodKg)));
        return item;
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
        onClick(event, _elements, chart) {
          const [hit] = chart.getElementsAtEventForMode(
            event,
            "nearest",
            { intersect: true, axis: "x" },
            false,
          );
          if (!hit) return;
          hideTooltip();
          select(panelKey(kind, entries[hit.datasetIndex][0]), chart.canvas);
        },
        plugins: {
          legend: { display: false },
          tooltip: tooltipOptions(([item]) => ({
            title: item.dataset.label,
            rows: [
              { label: "CO₂e", value: formatMassText(item.raw), color: swatchOf(item) },
              {
                label: "Share",
                value: `${formatNumber(total ? (item.raw / total) * 100 : 0, 1)}%`,
              },
            ],
          })),
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
      const row = document.createElement("button");
      row.type = "button";
      row.className = "composition-row";
      row.dataset.openPanel = panelKey(kind, label);
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

  // The key to a chart broken down by cabin or reason, in the element `id`:
  // a swatch, badge and name per category, in the order the chart stacks
  // them, and the upcoming hatch where the chart draws one. Hidden for the
  // total, which is one colour and needs no key.
  // `scope` prefixes each entry's filter kind, as "trend." does for the
  // annual trajectory, whose key covers every year, flown and booked.
  function legend(id, breakdown, categories, { upcoming = false, scope = "" } = {}) {
    const list = $(id);
    list.hidden = breakdown === "total" || !categories.length;
    if (list.hidden) {
      list.replaceChildren();
      return;
    }
    // Each entry is a button that opens the flight panel for its category.
    const item = (key, swatch, ...content) => {
      const element = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "legend-button";
      button.dataset.openPanel = key;
      swatch.className = `legend-swatch${swatch.className ? ` ${swatch.className}` : ""}`;
      swatch.setAttribute("aria-hidden", "true");
      button.append(swatch, ...content);
      element.append(button);
      return element;
    };
    const items = categories.map((category) => {
      const swatch = document.createElement("span");
      swatch.style.background = categoryColor(breakdown, category);
      return item(
        panelKey(`${scope}${breakdown}`, category),
        swatch,
        categoryIcon(breakdown, category),
        category,
      );
    });
    if (upcoming) {
      const hatch = document.createElement("span");
      hatch.className = "upcoming";
      items.push(item(panelKey(`${scope}upcoming`, ""), hatch, "Upcoming"));
    }
    list.replaceChildren(...items);
  }

  // `buckets` is what Passport.aggregate.patternBuckets returns, and `kind`
  // the pattern, month or weekday, a column opens the flight panel for.
  function pattern(buckets, breakdown, kind) {
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
        interaction: { mode: "index", intersect: false },
        onClick: clickAlong("x", (index, chart) =>
          select(panelKey(kind, labels[index]), chart.canvas),
        ),
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          tooltip: tooltipOptions(stackTooltip),
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

  // How strongly a year that is not the chosen one is drawn.
  const DIMMED = 0.3;
  const dim = (color) => {
    const hex = /^#[0-9a-f]{6}$/i.test(color) ? color : null;
    return hex
      ? `${hex}${Math.round(DIMMED * 255)
          .toString(16)
          .padStart(2, "0")}`
      : color;
  };

  // `series` is what Passport.aggregate.yearSeries returns; `categories` the
  // breakdown's categories across every flight. With a year chosen, its bars
  // keep their colour and every other year's are dimmed. A click on a year
  // calls `onYear(year)`.
  function trend(series, categories, breakdown, selectedYear, onYear) {
    const chosen = (item) => selectedYear === null || item.year === selectedYear;
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
      const fill = series.map((item) => (chosen(item) ? color : dim(color)));
      const full = hatchPattern($("trend-chart"), color);
      const faint = hatchPattern($("trend-chart"), color, DIMMED);
      const upcomingPattern = series.map((item) => (chosen(item) ? full : faint));
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
          backgroundColor: fill,
          hoverBackgroundColor: fill,
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
          swatch: color,
          hatched: true,
          borderColor: fill,
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
        onClick: clickAlong("x", (index) => onYear(series[index].year)),
        layout: { padding: { top: 28 } },
        plugins: {
          legend: { display: false },
          tooltip: tooltipOptions(stackTooltip),
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
            grid: { color: theme.grid },
            border: { display: false },
            ticks: { color: theme.muted, callback: (value) => formatMassAxisTick(value, maximum) },
          },
        },
      },
    });
  }

  return { onSelect, ranking, rankingList, composition, legend, pattern, trend };
})();
