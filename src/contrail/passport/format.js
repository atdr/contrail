// Passport's script is these files joined, in the order PASSPORT_SCRIPTS in
// __init__.py gives, into one classic <script>. Each file adds one module to
// this namespace and reads the modules before it; this file is first, so it
// declares it. Each module is a strict IIFE, which is where "use strict" earns
// its place: a classic script is not strict by default.
const Passport = {};

// Formatting: numbers, masses, dates, and how a cabin or reason is shown.
Passport.format = (() => {
  "use strict";

  const formatNumber = (value, digits = 0) =>
    Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
  const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0);
  const formatKg = (kg, unit = kg >= 1000 ? "t" : "kg") =>
    unit === "t"
      ? `${formatNumber(kg / 1000, 1)} <small>t CO₂e</small>`
      : `${formatNumber(kg, 1)} <small>kg CO₂e</small>`;
  const formatMassText = (kg, unit = kg >= 1000 ? "t" : "kg") =>
    unit === "t" ? `${formatNumber(kg / 1000, 1)} t` : `${formatNumber(kg, 1)} kg`;
  const formatMassAxisTick = (kg, maximum) => formatMassText(kg, maximum >= 1000 ? "t" : "kg");

  function formatRankingValue(value, metric, includeCarbon = false) {
    if (metric === "distance")
      return `${formatNumber(value, 1)} g${includeCarbon ? " CO₂e / passenger-km" : "/km"}`;
    if (metric === "duration")
      return `${formatNumber(value, 1)} kg${includeCarbon ? " CO₂e / block hour" : "/hr"}`;
    return `${formatMassText(value)}${includeCarbon ? " CO₂e" : ""}`;
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

  function countryFlag(code) {
    return /^[A-Z]{2}$/.test(code)
      ? String.fromCodePoint(...[...code].map((letter) => 127397 + letter.charCodeAt()))
      : "🌐";
  }

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

  return {
    formatNumber,
    pct,
    formatKg,
    formatMassText,
    formatMassAxisTick,
    formatRankingValue,
    MONTHS,
    WEEKDAYS,
    monthOf,
    weekdayOf,
    countryFlag,
    routeName,
    cssColor,
    categoryColor,
    visualCategory,
    categoryIcon,
    flightTags,
  };
})();
