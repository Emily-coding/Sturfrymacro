/* The translation layer: from economic "levers" to the OBR's ~33 inputs.
 *
 * The OBR ready reckoner needs a full set of determinants (earnings, employment,
 * consumption, profits, prices...) and does not check that they hang together. This
 * file turns a few levers an economist would think in (more employment, higher
 * productivity, higher inflation...) into a consistent set of those inputs. Every
 * assumption is a line below, and the page shows the result year by year, where any
 * single input can then be overridden.
 *
 * Conventions: levers are per cent differences in levels (employment, productivity,
 * pay, asset prices), point differences in rates (inflation, interest rates), or the
 * units shown (oil $/barrel, gas p/therm). Each lever reaches its full size over
 * `phase` years from `start` (a year index) and then stays there. Effects combine
 * multiplicatively (1% more workers and 1% higher productivity = 2.01% more output).
 *
 * Main assumptions (and why they are debatable):
 *   - Long run: when employment or productivity rises, the capital stock adjusts, so
 *     output, profits, consumption and investment rise in proportion and factor shares
 *     stay constant. In the short run output rises by less (capital is fixed).
 *   - New workers are paid `newWorkerPay` x the average (default 0.8): people moving
 *     into work from unemployment or inactivity tend to earn less, so average earnings
 *     fall slightly as employment rises (a composition effect).
 *   - Productivity gains pass to pay one for one by default (`wagePassThrough`); any
 *     shortfall shows up as higher profits.
 *   - Inflation is a nominal shock: all cash values rise with the price level, real
 *     ones do not, and real wages are protected (earnings indexed to prices).
 *   - Asset prices do not move unless you say so (`assetsFollowIncomes`), because
 *     house and share prices are driven by much more than UK incomes (most FTSE
 *     profits are earned abroad).
 *   - No monetary response unless `rateRule` is on: then Bank Rate rises 1.5 points
 *     for each point of extra inflation and gilt yields 1 point (Fisher: nominal
 *     yields follow expected inflation).
 */
(function (root) {
  "use strict";

  const LEVERS = [
    {id: "employment", label: "Employment", unit: "%", step: 0.1, min: -5, max: 5,
     help: "More (or fewer) people in work, as a % of employment. Where they come from matters for benefits: choose below."},
    {id: "productivity", label: "Productivity (output per worker)", unit: "%", step: 0.1, min: -5, max: 5,
     help: "Higher output per worker. Raises real GDP, pay (via the pass-through), profits and spending. No effect on prices."},
    {id: "pay", label: "Pay, given output (labour share)", unit: "%", step: 0.1, min: -5, max: 5,
     help: "Higher pay with output unchanged: income shifts from profits to wages. Moves tax from corporation tax to income tax and NICs."},
    {id: "inflation", label: "CPI inflation", unit: "pt", step: 0.1, min: -3, max: 3,
     help: "Higher inflation each year (percentage points). Raises all cash values; the price level keeps the gain."},
    {id: "bank_rate", label: "Bank Rate", unit: "pt", step: 0.1, min: -3, max: 3, help: "Moves deposit rates one for one."},
    {id: "gilt_rate", label: "Gilt yields", unit: "pt", step: 0.1, min: -3, max: 3, help: "Interest rates on new government borrowing."},
    {id: "house_prices", label: "House prices", unit: "%", step: 0.5, min: -20, max: 20, help: "On top of any move with incomes."},
    {id: "property_transactions", label: "Property transactions", unit: "%", step: 0.5, min: -30, max: 30, help: "Number of sales."},
    {id: "equity_prices", label: "Share prices", unit: "%", step: 0.5, min: -30, max: 30, help: "FTSE All-Share, on top of any move with profits."},
    {id: "oil_price", label: "Oil price", unit: "$/barrel", step: 1, min: -40, max: 40, help: "Does not feed through to CPI here: add that with the inflation lever."},
    {id: "gas_price", label: "Gas price", unit: "p/therm", step: 1, min: -60, max: 60, help: "Does not feed through to CPI here: add that with the inflation lever."},
  ];

  const DEFAULT_PARAMS = {
    employmentSource: "unemployment",  // "unemployment" | "inactivity" | "population"
    newWorkerPay: 0.8,                 // pay of new workers relative to the average
    wagePassThrough: 1.0,              // share of a productivity gain that goes to pay
    labourShare: 0.56,                 // compensation share of GDP (OBR table 1.6, ~56%)
    rpiWedge: 1.0,                     // extra RPI inflation per point of extra CPI inflation
    assetsFollowIncomes: false,        // house prices with nominal GDP, shares with profits
    rateRule: false,                   // Bank Rate and gilts respond to inflation
  };

  const pct = factors => 100 * (factors.reduce((a, f) => a * (1 + f / 100), 1) - 1);

  // a lever's path over the years: 0 before `start`, then rising to `size` over `phase` years
  function path(lever, n) {
    const {size = 0, start = 0, phase = 1} = lever || {};
    return Array.from({length: n}, (_, t) => t < start ? 0 : size * Math.min(1, (t - start + 1) / Math.max(1, phase)));
  }

  /* settings: {levers: {id: {size, start, phase}}, params: {...}} -> OBR inputs */
  function translate(basis, settings) {
    const n = basis.years.length;
    const p = Object.assign({}, DEFAULT_PARAMS, settings.params || {});
    const L = id => path((settings.levers || {})[id], n);
    const emp = L("employment"), prod = L("productivity"), pay = L("pay"), infl = L("inflation");
    const share = p.labourShare / (1 - p.labourShare);   // wages per £ of profits
    const known = basis.triple_lock_fixed_years || 0;    // years whose uprating data are already out

    const inputs = {};
    const set = (id, f) => { inputs[id] = Array.from({length: n}, (_, t) => f(t)); };

    // price level (% above baseline) from the extra inflation each year
    const price = []; infl.reduce((lvl, i, t) => (price[t] = 100 * ((1 + lvl / 100) * (1 + i / 100) - 1)), 0);

    // real output: extra workers (at their relative productivity) and productivity
    const outputFromJobs = t => p.newWorkerPay * emp[t];
    const realGdp = t => pct([outputFromJobs(t), prod[t]]);
    const nominalGdp = t => pct([realGdp(t), price[t]]);

    // average earnings: composition of new workers, productivity pass-through, labour share, prices
    const composition = t => 100 * ((1 + p.newWorkerPay * emp[t] / 100) / (1 + emp[t] / 100) - 1);
    const earnings = t => pct([composition(t), p.wagePassThrough * prod[t], pay[t], price[t]]);
    // profits: output growth, plus what productivity does not pay out in wages, less a labour share shift
    const profits = t => pct([outputFromJobs(t), prod[t] + (1 - p.wagePassThrough) * prod[t] * share,
                              -pay[t] * share, price[t]]);

    set("employment", t => emp[t]);
    set("unemployment", t => p.employmentSource === "unemployment"
      ? -basis.baseline.employment[t + 1] / 1000 * emp[t] / 100 : 0);  // millions
    set("earnings", earnings);
    set("mixed_income", nominalGdp);
    set("real_gdp", realGdp);
    for (const id of ["nominal_gdp_fy", "nominal_gdp_cy", "nominal_gdp_march", "nominal_consumption", "durables",
                      "imports", "pnfc_investment", "dwelling_investment"]) set(id, nominalGdp);
    set("real_consumption", realGdp);
    set("profits_nonfinancial", profits);
    set("profits_financial", profits);
    set("personal_deposits", t => p.assetsFollowIncomes ? nominalGdp(t) : 0);

    // asset prices: direct levers, plus incomes if chosen
    const direct = id => L(id);
    set("house_prices", t => pct([direct("house_prices")[t], p.assetsFollowIncomes ? nominalGdp(t) : 0]));
    set("equity_prices", t => pct([direct("equity_prices")[t], p.assetsFollowIncomes ? profits(t) : 0]));
    set("property_transactions", t => direct("property_transactions")[t]);

    // rates of change for uprating: earnings growth and inflation, zero where already published
    const earnGrowth = t => earnings(t) - (t ? earnings(t - 1) : 0);
    set("earnings_growth_q2", t => t < known ? 0 : earnGrowth(t));
    set("earnings_growth_q3", t => t < known ? 0 : earnGrowth(t));
    set("cpi_inflation", t => infl[t]);
    set("cpi_q3", t => t < known ? 0 : infl[t]);
    set("rpi_inflation", t => p.rpiWedge * infl[t]);
    set("rpi_q2", t => t < known ? 0 : p.rpiWedge * infl[t]);

    // interest rates, with the optional response to inflation
    const bank = t => L("bank_rate")[t] + (p.rateRule ? 1.5 * infl[t] : 0);
    set("bank_rate", bank);
    set("deposit_rate", bank);
    set("gilt_rate", t => L("gilt_rate")[t] + (p.rateRule ? 1.0 * infl[t] : 0));

    set("oil_price", t => L("oil_price")[t]);
    set("gas_price", t => L("gas_price")[t]);
    return inputs;
  }

  root.ReckonerLevers = {LEVERS, DEFAULT_PARAMS, translate, path};
})(typeof window !== "undefined" ? window : this);
