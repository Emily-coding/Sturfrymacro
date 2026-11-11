// Shared helpers for the dashboard (index.html) and the labour market page (labour.html).

// Numbers with a real minus sign; dp = decimal places (1 unless set), thousands separated
const fmt = (v, dp = 1) => (v < 0 ? "−" : "") + Math.abs(v).toLocaleString("en-GB", {minimumFractionDigits: dp, maximumFractionDigits: dp});
const fmtDate = s => new Date(s + "T12:00").toLocaleDateString("en-GB", {day: "numeric", month: "short"});

// Dates to positions on a timeline: '2026-08' -> 2026.625, '2026-Q3' -> 2026.625,
// '2026-11-06' (a daily market price) -> 2026 plus the fraction of the year gone
function timeX(d) {
  const y = +d.slice(0, 4);
  if (d.includes("Q")) return y + (+d.slice(-1) - 0.5) / 4;
  if (d.length === 10) return y + (Date.UTC(y, +d.slice(5, 7) - 1, +d.slice(8)) - Date.UTC(y, 0, 1)) / (365.25 * 864e5);
  return y + (+d.slice(5, 7) - 0.5) / 12;
}
function niceLabel(d) {
  if (d.includes("Q")) return d.slice(-2) + " " + d.slice(0, 4);
  if (d.length === 10) return fmtDate(d) + " " + d.slice(0, 4);
  return new Date(+d.slice(0, 4), +d.slice(5, 7) - 1).toLocaleDateString("en-GB", {month: "short", year: "numeric"});
}

Chart.defaults.font.family = '"Source Sans 3", system-ui, sans-serif';
Chart.defaults.font.size = 13;
Chart.defaults.color = "#486581";

function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
