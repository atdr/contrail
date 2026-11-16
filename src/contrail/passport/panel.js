// The flight panel: one dialog listing the flights behind whatever was chosen,
// a bar, a key entry, a card, a count or a map route, with each flight's facts
// a click away. A filter is a string, "<kind>:<value>", so it can sit in
// state, in a data-open-panel attribute and in the URL hash alike. Every
// flight carries `id`, its index in the payload, which main.js assigns.
Passport.panel = (() => {
  "use strict";

  const {
    formatNumber,
    formatMassText,
    formatDate,
    formatDuration,
    MONTHS,
    WEEKDAYS,
    monthOf,
    weekdayOf,
    monthName,
    weekdayName,
    countryName,
    routeName,
    visualCategory,
  } = Passport.format;
  const { QUALITY, completed } = Passport.aggregate;

  const $ = (id) => document.getElementById(id);

  let flights = [];
  let onClose = () => {};

  const carrierLabel = (code) => {
    const named = flights.find((flight) => flight.carrier === code && flight.carrierName);
    return named ? `${named.carrierName} (${code})` : code;
  };
  const flightLabel = (flight) =>
    `${flight.flight || "Flight"} · ${routeName(flight)} · ${flight.date ? formatDate(flight.date) : "date unknown"}`;
  const flightAt = (value) => (/^\d{1,7}$/.test(value) ? flights[Number(value)] : undefined);

  // What each kind of filter is called, which flights it draws from, and
  // which of those it keeps. `pool` is "completed" unless stated, the flights
  // every chart counts; `scope` is "year", the chosen period, unless stated.
  // `valid` refuses a value no control could produce, so a hand-edited hash
  // opens nothing rather than an empty panel under an invented heading. A
  // kind without `valid` takes any non-empty value; `bare` kinds take none.
  const KINDS = {
    route: { eyebrow: "Route", match: (value) => (f) => f.route === value },
    airport: {
      eyebrow: "Airport",
      match: (value) => (f) => f.origin === value || f.destination === value,
    },
    country: {
      eyebrow: "Country",
      label: countryName,
      match: (value) => (f) => f.start?.country === value || f.end?.country === value,
    },
    carrier: {
      eyebrow: "Operating airline",
      label: carrierLabel,
      match: (value) => (f) => f.carrier === value,
    },
    aircraft: { eyebrow: "Aircraft", match: (value) => (f) => f.aircraft === value },
    cabin: { eyebrow: "Cabin", match: (value) => (f) => visualCategory(f, "cabin") === value },
    reason: { eyebrow: "Reason", match: (value) => (f) => visualCategory(f, "reason") === value },
    month: {
      eyebrow: "Month",
      valid: (value) => MONTHS.includes(value),
      label: monthName,
      match: (value) => (f) => Boolean(f.date) && monthOf(f.date) === value,
    },
    weekday: {
      eyebrow: "Weekday",
      valid: (value) => WEEKDAYS.includes(value),
      label: weekdayName,
      match: (value) => (f) => Boolean(f.date) && weekdayOf(f.date) === value,
    },
    year: {
      eyebrow: "Year",
      scope: "all",
      valid: (value) => /^\d{4}$/.test(value),
      match: (value) => (f) => f.year === Number(value),
    },
    quality: {
      eyebrow: "Estimate quality",
      valid: (value) => Object.hasOwn(QUALITY, value),
      label: (value) => QUALITY[value].label,
      match: (value) => QUALITY[value].test,
    },
    flight: {
      eyebrow: "Flight",
      pool: "any",
      scope: "all",
      valid: (value) => Boolean(flightAt(value)),
      label: (value) => flightLabel(flightAt(value)),
      match: (value) => (f) => f === flightAt(value),
    },
    upcoming: {
      eyebrow: "Booked",
      pool: "upcoming",
      bare: true,
      label: () => "Upcoming flights",
      match: () => () => true,
    },
    all: {
      eyebrow: "Completed",
      bare: true,
      label: () => "All flights",
      match: () => () => true,
    },
  };

  // The filter a key names, or null for one that names nothing.
  function describe(key) {
    if (typeof key !== "string" || key.length > 300) return null;
    const split = key.indexOf(":");
    if (split < 1) return null;
    const kind = key.slice(0, split);
    const value = key.slice(split + 1);
    if (!Object.hasOwn(KINDS, kind)) return null;
    const definition = KINDS[kind];
    if (definition.bare ? value : definition.valid ? !definition.valid(value) : !value) return null;
    return {
      kind,
      value,
      definition,
      eyebrow: definition.eyebrow,
      label: definition.label ? definition.label(value) : value,
    };
  }

  function matching(filter, rows) {
    const { definition, value } = filter;
    const source = definition.scope === "all" ? flights : rows;
    const pool =
      definition.pool === "any"
        ? source
        : definition.pool === "upcoming"
          ? // A flight with no date is neither flown nor upcoming.
            source.filter((flight) => !flight.departed && flight.date)
          : completed(source);
    return pool.filter(definition.match(value));
  }

  const intensity = (f) => (f.kg !== null && f.distanceKm ? (f.kg * 1000) / f.distanceKm : null);
  const SOURCES = {
    exact: ["Exact", 0, "Exact TIM estimate"],
    typical_route_average: ["Route avg", 1, "Typical route average"],
  };
  const source = (f) => SOURCES[f.emissionsSource] || ["None", 2, "No estimate"];
  const dash = "—";
  // Table figures to one fixed decimal, so a column of them lines up.
  const tenths = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

  // The table's columns. `value` is what a column sorts by, null last in
  // either direction; `first` the direction a first click sorts it.
  const COLUMNS = [
    {
      key: "date",
      label: "Date",
      first: "descending",
      value: (f) => f.date,
      text: (f) => (f.date ? formatDate(f.date) : "Date unknown"),
    },
    {
      key: "flight",
      label: "Flight",
      value: (f) => f.flight || null,
      text: (f) => f.flight || dash,
    },
    { key: "route", label: "Route", value: routeName, text: routeName },
    { key: "cabin", label: "Cabin", value: (f) => f.cabin, text: (f) => f.cabin },
    {
      key: "kg",
      label: "kg CO₂e",
      numeric: true,
      first: "descending",
      value: (f) => f.kg,
      text: (f) => (f.kg === null ? dash : tenths.format(f.kg)),
    },
    {
      key: "intensity",
      label: "g/km",
      numeric: true,
      first: "descending",
      value: intensity,
      text: (f) => {
        const value = intensity(f);
        return value === null ? dash : tenths.format(value);
      },
    },
    {
      key: "duration",
      label: "Block",
      numeric: true,
      first: "descending",
      value: (f) => f.durationHours,
      text: (f) => (f.durationHours === null ? dash : formatDuration(f.durationHours)),
    },
    {
      key: "source",
      label: "Estimate",
      value: (f) => source(f)[1],
      text: (f) => source(f)[0],
    },
  ];
  const COLUMN = Object.fromEntries(COLUMNS.map((column) => [column.key, column]));

  // The panel as last drawn: its filter, the year it was drawn for, its
  // flights, and what the reader has done to the list since.
  const view = {
    key: null,
    year: null,
    list: [],
    sort: "date",
    direction: "descending",
    query: "",
    expanded: new Set(),
  };
  let opener = null;

  // Lower-cased text a search matches against, made once per flight.
  const searchText = new WeakMap();
  function haystack(flight) {
    if (!searchText.has(flight)) {
      searchText.set(
        flight,
        [
          flight.flight,
          flight.route,
          routeName(flight),
          flight.carrier,
          flight.carrierName,
          flight.aircraft,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase(),
      );
    }
    return searchText.get(flight);
  }

  const missing = (value) => value === null || value === undefined;
  function compare(column, direction) {
    const sign = direction === "ascending" ? 1 : -1;
    return (a, b) => {
      const left = column.value(a);
      const right = column.value(b);
      if (missing(left)) return missing(right) ? 0 : 1;
      if (missing(right)) return -1;
      if (typeof left === "number") return sign * (left - right);
      return sign * String(left).localeCompare(String(right));
    };
  }

  const element = (tag, className = "", text = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };

  function buildHead() {
    const row = document.createElement("tr");
    COLUMNS.forEach((column) => {
      const cell = element("th", column.numeric ? "numeric" : "");
      cell.scope = "col";
      cell.dataset.sort = column.key;
      const button = element("button", "sort-button", column.label);
      button.type = "button";
      cell.append(button);
      row.append(cell);
    });
    $("flight-panel-head").replaceChildren(row);
  }

  function syncSort() {
    document.querySelectorAll("#flight-panel-head th").forEach((cell) => {
      if (cell.dataset.sort === view.sort) cell.setAttribute("aria-sort", view.direction);
      else cell.removeAttribute("aria-sort");
    });
    const select = $("flight-panel-sort");
    const wanted = `${view.sort}:${view.direction}`;
    if ([...select.options].some((option) => option.value === wanted)) select.value = wanted;
  }

  // "20:30 · 4 Mar 2026 (UTC−05:00)" from "2026-03-04T20:30-05:00": the
  // airport's own clock, unconverted, so nothing moves a day.
  function formatScheduled(instant) {
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::[\d.]+)?(Z|[+-]\d{2}:\d{2})$/.exec(
      instant || "",
    );
    if (!match) return null;
    const offset = match[3] === "Z" ? "+00:00" : match[3];
    return `${match[2]} · ${formatDate(match[1])} (UTC${offset.replace("-", "−")})`;
  }

  const CABIN_FIGURES = [
    ["economy", "economy"],
    ["premiumEconomy", "premium economy"],
    ["business", "business"],
    ["first", "first"],
  ];
  function flownCabin(flight) {
    const cabin = visualCategory(flight, "cabin").toLowerCase();
    if (cabin.includes("premium")) return "premiumEconomy";
    return CABIN_FIGURES.some(([key]) => key === cabin) ? cabin : null;
  }

  // Everything known about one flight as a term list, then what TIM gives
  // for each cabin not flown.
  function detail(flight) {
    const facts = element("dl", "flight-facts");
    const add = (term, description) => {
      if (missing(description) || description === "") return;
      const group = element("div");
      group.append(element("dt", "", term), element("dd", "", description));
      facts.append(group);
    };
    const perKm = intensity(flight);
    add("Status", flight.departed ? "Flown" : "Upcoming");
    add("Flight", flight.flight || "Unknown");
    add(
      "Operated by",
      flight.carrierName ? `${flight.carrierName} (${flight.carrier})` : flight.carrier,
    );
    add(
      "Route",
      `${routeName(flight)}${flight.distanceKm ? ` · ${formatNumber(flight.distanceKm)} km great-circle` : ""}`,
    );
    add("Scheduled departure", formatScheduled(flight.departure) || "Not stated");
    add("Scheduled arrival", formatScheduled(flight.arrival) || "Not stated");
    add(
      "Block time",
      flight.durationHours === null ? "Needs both times" : formatDuration(flight.durationHours),
    );
    add("Cabin", flight.cabin);
    add("Aircraft", flight.aircraft);
    add("Reason", flight.reason);
    // One flight reads in kilograms, as the table beside it does.
    add("CO₂e", flight.kg === null ? "No estimate" : `${formatMassText(flight.kg, "kg")} CO₂e`);
    add("Per km", perKm === null ? null : `${formatNumber(perKm, 1)} g CO₂e / passenger-km`);
    add(
      "Per block hour",
      flight.kg !== null && flight.durationHours
        ? `${formatNumber(flight.kg / flight.durationHours, 1)} kg CO₂e / hour`
        : null,
    );
    if (flight.contrailsImpact) {
      const impact = flight.contrailsImpact;
      add("Contrail impact", `${impact[0].toUpperCase()}${impact.slice(1)}`);
    }
    add("Estimate", source(flight)[2]);
    add("Model version", flight.modelVersion ? `TIM ${flight.modelVersion}` : null);

    const body = element("div", "flight-detail-body");
    body.append(facts);
    const figures = flight.cabinKg || {};
    const flown = flownCabin(flight);
    const others = CABIN_FIGURES.filter(([key]) => key !== flown && !missing(figures[key]));
    if (others.length) {
      const list = element("ul", "cabin-figures");
      list.setAttribute("aria-label", "Other cabins on this flight");
      others.forEach(([key, name]) => {
        list.append(
          element(
            "li",
            "",
            `In ${name} this flight would have been ${formatMassText(figures[key], "kg")} CO₂e.`,
          ),
        );
      });
      body.append(list);
    }
    return body;
  }

  // A flight's row, and its detail row beneath when it is open.
  function flightRows(flight) {
    const id = String(flight.id);
    const row = element("tr", "flight-row");
    row.dataset.flight = id;
    const open = view.expanded.has(id);
    COLUMNS.forEach((column) => {
      const cell = element("td", `cell-${column.key}${column.numeric ? " numeric" : ""}`);
      cell.dataset.label = column.label;
      if (column.key === "route") {
        const toggle = element("button", "flight-toggle", column.text(flight));
        toggle.type = "button";
        toggle.setAttribute("aria-expanded", String(open));
        toggle.setAttribute("aria-controls", `flight-detail-${id}`);
        cell.append(toggle);
      } else {
        cell.textContent = column.text(flight);
      }
      row.append(cell);
    });
    if (!open) return [row];
    row.classList.add("expanded");
    const more = element("tr", "flight-detail");
    more.id = `flight-detail-${id}`;
    const cell = element("td");
    cell.colSpan = COLUMNS.length;
    cell.append(detail(flight));
    more.append(cell);
    return [row, more];
  }

  function drawRows() {
    const terms = view.query.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = terms.length
      ? view.list.filter((flight) => terms.every((term) => haystack(flight).includes(term)))
      : view.list.slice();
    shown.sort(compare(COLUMN[view.sort], view.direction));
    const fragment = document.createDocumentFragment();
    shown.forEach((flight) => {
      fragment.append(...flightRows(flight));
    });
    $("flight-panel-rows").replaceChildren(fragment);
    const empty = $("flight-panel-empty");
    empty.hidden = shown.length > 0;
    empty.textContent = view.list.length
      ? "No flights match that search."
      : "No flights in this period.";
    $("flight-panel-status").textContent =
      terms.length && view.list.length ? `Showing ${shown.length} of ${view.list.length}` : "";
    syncSort();
  }

  function drawHead(filter, year) {
    const list = view.list;
    const priced = list.filter((flight) => flight.kg !== null);
    const kg = priced.reduce((total, flight) => total + flight.kg, 0);
    $("flight-panel-kind").textContent =
      filter.definition.scope === "all" || year === null
        ? filter.eyebrow
        : `${filter.eyebrow} · ${year}`;
    $("flight-panel-title").textContent = filter.label;
    $("flight-panel-count").textContent =
      `${formatNumber(list.length)} flight${list.length === 1 ? "" : "s"}`;
    $("flight-panel-total").textContent = `${formatMassText(kg)} CO₂e`;
    const unpriced = list.length - priced.length;
    const note = $("flight-panel-note");
    note.textContent = unpriced ? `${formatNumber(unpriced)} without an estimate` : "";
    note.hidden = !unpriced;
    // A list of one needs neither search nor sorting.
    $("flight-panel-tools").hidden = list.length < 2;
  }

  // Show the panel for `key` over `rows`, the chosen period's flights.
  // Drawn again only when the filter or the period changed, so a redraw of
  // the page behind it keeps the reader's sort, search and open rows.
  // Returns false for a key that names nothing.
  function show(key, rows, year, from = null) {
    const filter = describe(key);
    if (!filter) return false;
    const dialog = $("flight-panel");
    if (from && !dialog.open) opener = from;
    if (view.key !== key || view.year !== year || !dialog.open) {
      if (view.key !== key) {
        view.query = "";
        view.sort = "date";
        view.direction = "descending";
        $("flight-panel-search").value = "";
      }
      view.key = key;
      view.year = year;
      view.list = matching(filter, rows);
      view.expanded = new Set(
        view.list.length === 1 ? view.list.map((flight) => String(flight.id)) : [],
      );
      drawHead(filter, year);
      drawRows();
    }
    if (!dialog.open) {
      dialog.showModal();
      dialog.querySelector(".panel-scroll").scrollTop = 0;
    }
    return true;
  }

  function hide() {
    const dialog = $("flight-panel");
    if (dialog.open) dialog.close();
  }

  function sortBy(key) {
    const column = COLUMN[key];
    if (view.sort === column.key) {
      view.direction = view.direction === "ascending" ? "descending" : "ascending";
    } else {
      view.sort = column.key;
      view.direction = column.first || "ascending";
    }
    drawRows();
  }

  function toggle(row) {
    const id = row.dataset.flight;
    if (view.expanded.has(id)) view.expanded.delete(id);
    else view.expanded.add(id);
    const next = row.nextElementSibling;
    if (next?.classList.contains("flight-detail")) next.remove();
    const replacement = flightRows(flights[Number(id)]);
    row.replaceWith(...replacement);
    replacement[0].querySelector(".flight-toggle").focus();
  }

  function setup(options) {
    flights = options.flights;
    onClose = options.onClose;
    buildHead();
    const dialog = $("flight-panel");
    dialog.addEventListener("close", () => {
      view.key = null;
      const back = opener;
      opener = null;
      onClose();
      if (back?.isConnected) back.focus();
    });
    // A click on the backdrop lands on the dialog itself; the sheet inside
    // fills every other point of it.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    $("flight-panel-close").addEventListener("click", () => dialog.close());
    $("flight-panel-head").addEventListener("click", (event) => {
      const cell = event.target.closest("th[data-sort]");
      if (cell) sortBy(cell.dataset.sort);
    });
    $("flight-panel-sort").addEventListener("change", (event) => {
      const [sort, direction] = event.target.value.split(":");
      if (!Object.hasOwn(COLUMN, sort)) return;
      view.sort = sort;
      view.direction = direction === "ascending" ? "ascending" : "descending";
      drawRows();
    });
    $("flight-panel-search").addEventListener("input", (event) => {
      view.query = event.target.value;
      drawRows();
    });
    $("flight-panel-rows").addEventListener("click", (event) => {
      const row = event.target.closest("tr.flight-row");
      if (row) toggle(row);
    });
  }

  return { setup, describe, show, hide };
})();
