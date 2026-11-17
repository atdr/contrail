// Passport's script is these files joined, in the order PASSPORT_SCRIPTS in
// __init__.py gives, into one classic <script>. Each file adds one module to
// this namespace and reads the modules before it; this file is first, so it
// declares it. Each module is a strict IIFE, which is where "use strict" earns
// its place: a classic script is not strict by default.
const Passport = {};

// Formatting: numbers, masses, dates, and how a cabin or reason is shown.
Passport.format = (() => {
  "use strict";

  // One Intl formatter per precision, made once. toLocaleString() builds a
  // new one on every call, which is most of the cost of drawing a long
  // flight list.
  const numberFormats = new Map();
  function formatNumber(value, digits = 0) {
    if (!numberFormats.has(digits))
      numberFormats.set(
        digits,
        new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }),
      );
    return numberFormats.get(digits).format(Number(value));
  }
  const changeFormat = new Intl.NumberFormat(undefined, {
    style: "percent",
    signDisplay: "exceptZero",
    maximumFractionDigits: 0,
  });
  const formatChange = (fraction) => changeFormat.format(fraction);
  const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0);
  const formatKg = (kg, unit = kg >= 1000 ? "t" : "kg") =>
    unit === "t"
      ? `${formatNumber(kg / 1000, 1)} <small>t CO₂e</small>`
      : `${formatNumber(kg, 1)} <small>kg CO₂e</small>`;
  const formatMassText = (kg, unit = kg >= 1000 ? "t" : "kg") =>
    unit === "t" ? `${formatNumber(kg / 1000, 1)} t` : `${formatNumber(kg, 1)} kg`;
  const formatMassAxisTick = (kg, maximum) => formatMassText(kg, maximum >= 1000 ? "t" : "kg");

  // One unit for a set of masses shown together, from the largest, so a
  // tooltip never reads "900 kg" over "1.2 t".
  const massUnit = (values) => (Math.max(0, ...values) >= 1000 ? "t" : "kg");

  function formatRankingValue(value, metric, includeCarbon = false, unit = undefined) {
    if (metric === "distance")
      return `${formatNumber(value, 1)} g${includeCarbon ? " CO₂e / passenger-km" : "/km"}`;
    if (metric === "duration")
      return `${formatNumber(value, 1)} kg${includeCarbon ? " CO₂e / block hour" : "/hr"}`;
    return `${formatMassText(value, unit)}${includeCarbon ? " CO₂e" : ""}`;
  }

  const MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const monthOf = (isoDate) => MONTHS[Number(isoDate.slice(5, 7)) - 1];
  // getUTCDay() counts from Sunday; the charts start the week on Monday.
  const weekdayOf = (isoDate) => WEEKDAYS[(new Date(`${isoDate}T00:00:00Z`).getUTCDay() + 6) % 7];

  // A flight's ISO date in the reader's locale, as the footer's timestamp
  // is. Read and written as UTC: the date is a calendar day, not an instant,
  // so no time zone may move it to the day before.
  const dateFormat = new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
  const formatDate = (isoDate) => dateFormat.format(new Date(`${isoDate}T00:00:00Z`));

  // A month or weekday's full name in the reader's locale, from the short
  // English label the charts use. 1 January 2024 was a Monday.
  const monthFormat = new Intl.DateTimeFormat(undefined, { month: "long", timeZone: "UTC" });
  const weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: "long", timeZone: "UTC" });
  const monthName = (label) => monthFormat.format(Date.UTC(2024, MONTHS.indexOf(label), 1));
  const weekdayName = (label) =>
    weekdayFormat.format(Date.UTC(2024, 0, 1 + WEEKDAYS.indexOf(label)));

  const regionNames =
    typeof Intl.DisplayNames === "function"
      ? new Intl.DisplayNames(undefined, { type: "region" })
      : null;
  function countryName(code) {
    try {
      return regionNames?.of(code) || code;
    } catch {
      return code;
    }
  }

  // Hours as "6 h 45 min", to the minute.
  function formatDuration(hours) {
    const minutes = Math.round(hours * 60);
    const whole = Math.floor(minutes / 60);
    const rest = minutes % 60;
    if (!whole) return `${rest} min`;
    return rest ? `${whole} h ${rest} min` : `${whole} h`;
  }

  function countryFlag(code) {
    return /^[A-Z]{2}$/.test(code)
      ? String.fromCodePoint(...[...code].map((letter) => 127397 + letter.charCodeAt()))
      : "🌐";
  }

  // A ranking's catch-all entry: what flights with no route, airline,
  // aircraft or reason are counted under. It always ranks after every known
  // entry, whatever its size, so a large unknown never reads as the leader.
  const UNKNOWN = "Unknown";
  const isUnknown = (label) => label === UNKNOWN;
  const unknownLast = (a, b) => Number(isUnknown(a)) - Number(isUnknown(b));

  const routeName = (flight) => `${flight.origin || "?"} → ${flight.destination || "?"}`;

  const cssColor = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Everything that depends on which cabin or reason a flight has, in one
  // place. `color` is a CSS custom property, `badge` the icon's text, and
  // `tag` the flight tag's label where it is not the category's own text.
  const CATEGORIES = {
    cabin: {
      premium: { color: "--cabin-premium", badge: "E+", tagClass: "cabin-premium" },
      business: {
        color: "--cabin-business",
        badge: "B",
        tagClass: "cabin-business",
        tag: "Business",
      },
      first: { color: "--cabin-first", badge: "F", tagClass: "cabin-first", tag: "First" },
      private: {
        color: "--cabin-private",
        badge: "VIP",
        wide: true,
        tagClass: "cabin-private",
        tag: "Private",
      },
      economy: { color: "--cabin-economy", badge: "E", tagClass: "cabin-economy" },
      unknown: { color: "--category-unknown", badge: "?", tagClass: "tag-unknown" },
    },
    reason: {
      business: {
        color: "--reason-business",
        badge: "💼",
        tagClass: "reason-business",
        tag: "Business trip",
      },
      leisure: {
        color: "--reason-leisure",
        badge: "☀️",
        tagClass: "reason-leisure",
        tag: "Leisure",
      },
      personal: {
        color: "--reason-leisure",
        badge: "☀️",
        tagClass: "reason-leisure",
        tag: "Personal",
      },
      crew: { color: "--reason-crew", badge: "🧑‍✈️", tagClass: "reason-crew", tag: "Crew" },
      unknown: {
        color: "--category-unknown",
        badge: "?",
        tagClass: "tag-unknown",
        tag: "Reason unknown",
      },
    },
  };

  function categoryKey(kind, label) {
    const key = label.toLowerCase();
    if (kind === "cabin") {
      if (key.includes("premium")) return "premium";
      if (key === "business" || key === "first" || key === "private") return key;
      if (key.includes("economy")) return "economy";
      return "unknown";
    }
    return Object.hasOwn(CATEGORIES.reason, key) ? key : "unknown";
  }

  const category = (kind, label) => CATEGORIES[kind][categoryKey(kind, label)];
  const categoryColor = (kind, label) => cssColor(category(kind, label).color);

  // A flight without cabin data is charted as Economy, the assumption TIM makes.
  function visualCategory(row, kind) {
    if (
      kind === "cabin" &&
      (!row.cabinKnown || (row.cabin || "").toLowerCase().includes("assumed"))
    )
      return "Economy";
    return row[kind] || "Unknown";
  }

  function categoryIcon(kind, label) {
    const entry = category(kind, label);
    const icon = document.createElement("span");
    icon.className = "category-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = entry.badge;
    if (kind === "cabin") {
      icon.style.setProperty("--icon-bg", cssColor(entry.color));
      if (entry.wide) icon.classList.add("wide");
    } else {
      icon.classList.add("reason-icon");
    }
    return icon;
  }

  function flightTags(flight) {
    const tags = document.createElement("div");
    tags.className = "flight-tags";
    ["cabin", "reason"].forEach((kind) => {
      const value = visualCategory(flight, kind);
      const entry = category(kind, value);
      const tag = document.createElement("span");
      tag.className = `flight-tag ${entry.tagClass}`;
      tag.append(categoryIcon(kind, value), document.createTextNode(entry.tag ?? value));
      tags.append(tag);
    });
    return tags;
  }

  // The content of every tooltip on the page, chart or map, so they read as
  // one: a title, rows with a colour swatch, an optional total line, and
  // muted detail rows. A row is { label, value, color?, hatched? }.
  function tooltipContent({ title, rows = [], total = null, details = [] }) {
    const element = (className, text = "") => {
      const node = document.createElement("div");
      node.className = className;
      node.textContent = text;
      return node;
    };
    const line = (className, { label, value, color, hatched }) => {
      const row = element(className);
      if (color) {
        const swatch = element(`tooltip-swatch${hatched ? " hatched" : ""}`);
        swatch.style.setProperty("--swatch", color);
        row.append(swatch);
      }
      row.append(element("tooltip-label", label), element("tooltip-value", value));
      return row;
    };
    const content = element("tooltip-content");
    if (title) content.append(element("tooltip-title", title));
    rows.forEach((row) => {
      content.append(line(row.color ? "tooltip-row" : "tooltip-row plain", row));
    });
    if (total) content.append(line("tooltip-total", total));
    if (details.length) {
      const block = element("tooltip-details");
      details.forEach((detail) => {
        block.append(line("tooltip-row plain", detail));
      });
      content.append(block);
    }
    return content;
  }

  return {
    formatNumber,
    formatChange,
    formatDuration,
    pct,
    formatKg,
    formatMassText,
    formatMassAxisTick,
    formatRankingValue,
    massUnit,
    MONTHS,
    WEEKDAYS,
    monthOf,
    weekdayOf,
    formatDate,
    monthName,
    weekdayName,
    countryName,
    countryFlag,
    UNKNOWN,
    isUnknown,
    unknownLast,
    routeName,
    cssColor,
    categoryColor,
    visualCategory,
    categoryIcon,
    flightTags,
    tooltipContent,
  };
})();
