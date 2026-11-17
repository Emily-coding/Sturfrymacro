/* The OBR ready reckoner, recalculated in the browser.
 *
 * Reckoner.compute(basis, inputs) takes a "basis" from data/fiscal/reckoner.json
 * (elasticities and baselines, built by builders/fiscal/build.py) and the user's
 * scenario: {inputId: [difference in year 1, year 2, ...]} as per cent differences in
 * levels or point differences in rates (see the units in reckoner.json's "inputs").
 *
 * It follows the OBR workbook step by step:
 *   1. derived inputs (price levels from inflation, the triple lock, ...)
 *   2. "vectors": the version of each input a tax or spending line reads (rescaled,
 *      and lagged a year for benefit uprating)
 *   3. effects: sum of elasticity x vector over every term, giving £ million by line
 *   4. aggregates: receipts, spending, debt interest (with its financing loop),
 *      borrowing, debt, and the same as a share of GDP
 * Everything is linear except a few rules the workbook also applies: rounding of
 * inflation and earnings figures used for uprating, the zero floor and 3% cap on
 * CPI uprating, and the triple lock (the highest of earnings growth, inflation, 2.5%).
 *
 * No page code here, so the same file runs in tests (builders/fiscal/check_engine.py).
 */
(function (root) {
  "use strict";

  const round = (x, dp) => { const k = 10 ** dp; return Math.round(x * k) / k; };
  // Excel's ROUND rounds halves away from zero; Math.round rounds them up.
  const xlRound = (x, dp) => Math.sign(x) * round(Math.abs(x), dp);

  // Spending lines that are current (the rest are capital), for the current budget.
  const CURRENT_LINES = new Set([
    "PSCE in RDEL", "Scottish Government current spending", "Welfare spending",
    "Locally financed current expenditure", "Unfunded public sector pensions",
    "General government depreciation", "Other current primary expenditure",
    "Company and other tax credits", "BBC current expenditure",
  ]);
  const INTEREST_RECEIPTS = "Interest and dividend receipts";

  /* Step 1: the inputs the workbook derives from others. Baseline arrays start one
   * year before the scenario years (index 0 = basis.base_year), so year t of the
   * scenario is baseline index t + 1. */
  function derived(basis, x) {
    const n = basis.years.length, b = basis.baseline;
    const get = (id, t) => (x[id] && x[id][t]) || 0;
    const out = {};
    const make = (id, f) => { out[id] = Array.from({length: n}, (_, t) => f(t)); };

    // gas price as a per cent change (the energy profits levy reads it that way)
    make("gas_price_pct", t => b.gas_price[t + 1] ? 100 * get("gas_price", t) / b.gas_price[t + 1] : 0);

    // Q3 CPI for benefit uprating, rounded to 1dp, with a zero floor or a 3% cap
    const cpiQ3 = t => xlRound(b.cpi_q3[t + 1] + get("cpi_q3", t), 1);
    make("cpi_q3_floor", t => Math.max(cpiQ3(t), 0) - b.cpi_q3_floor[t + 1]);
    make("cpi_q3_cap", t => Math.min(cpiQ3(t), 3) - b.cpi_q3_cap[t + 1]);

    // financial-year earnings growth implied by the earnings level path
    const earn = t => t < 0 ? b.earnings[0] : b.earnings[t + 1] * (1 + get("earnings", t) / 100);
    make("earnings_growth_fy", t => 100 * (earn(t) / earn(t - 1) - 1) - b.earnings_growth_fy[t + 1]);

    // triple lock: highest of Q2 earnings growth, CPI inflation and 2.5%, to 1dp
    const cpiForLock = basis.triple_lock_cpi;  // "cpi_inflation" (workbook) or "cpi_q3" (legislation)
    make("triple_lock", t => {
      if (t < basis.triple_lock_fixed_years) return 0;  // already announced
      const earnings = b.earnings_growth_q2[t + 1] + get("earnings_growth_q2", t);
      const cpi = cpiForLock === "cpi_q3" ? cpiQ3(t) : b.cpi_inflation[t + 1] + get("cpi_inflation", t);
      return xlRound(Math.max(earnings, cpi, 2.5), 1) - b.triple_lock[t + 1];
    });

    // change in employment in millions (working-age benefits read it this way)
    make("employment_millions", t => b.employment[t + 1] * get("employment", t) / 100 / 1000);

    // price levels: chain the scenario inflation rates from the base year
    const level = (lvl, infl, input) => {
      let s = b[lvl][0];
      return Array.from({length: n}, (_, t) => {
        s *= 1 + (b[infl][t + 1] + get(input, t)) / 100;
        return 100 * (s / b[lvl][t + 1] - 1);
      });
    };
    out.rpi_index = level("rpi_level", "rpi_inflation", "rpi_inflation");
    out.cpi_index = level("cpi_level", "cpi_inflation", "cpi_inflation");
    out.rpi_q2_index = level("rpi_q2_level", "rpi_q2", "rpi_q2");
    return out;
  }

  /* Step 2: one vector per determinant row of the workbook. */
  function vector(basis, spec, sources) {
    const n = basis.years.length, src = sources[spec.source] || [];
    return Array.from({length: n}, (_, t) => {
      const s = t - spec.shift;
      return s >= 0 && s < n ? (src[s] || 0) * spec.factor : 0;
    });
  }

  /* opts.delReal: departmental spending (RDEL and CDEL) is held fixed in real terms
   * rather than in cash (the OBR's convention), i.e. it rises with the CPI price level.
   * Off by default, so the default reproduces the OBR's workbook. */
  function compute(basis, x, opts = {}) {
    const years = basis.years, n = years.length;
    const zeros = () => new Array(n).fill(0);
    const sources = Object.assign({}, x, derived(basis, x));

    // Step 3a: receipts and primary spending, £ million by line
    const vecs = basis.vectors.map(v => v.source === "primary_deficit" ? null : vector(basis, v, sources));
    const byStream = {receipts: {}, spending: {}};
    for (const term of basis.terms) {
      if (term.block === "debt") continue;
      const bucket = byStream[term.block];
      (bucket[term.stream] = bucket[term.stream] || zeros())[term.e] += term.coef * vecs[term.v][term.d];
    }

    // Step 4a: lines of the receipts and spending tables (£ billion)
    const welfare = new Set(basis.welfare_streams);
    const receiptLines = {}, spendingLines = {};
    for (const [stream, v] of Object.entries(byStream.receipts)) receiptLines[stream] = v.map(z => z / 1000);
    for (const [stream, v] of Object.entries(byStream.spending)) {
      const line = welfare.has(stream) ? "Welfare spending" : stream;
      const acc = spendingLines[line] = spendingLines[line] || zeros();
      v.forEach((z, t) => { acc[t] += z / 1000; });
    }
    if (opts.delReal) {
      for (const line of ["PSCE in RDEL", "PSGI in CDEL"]) {
        const base = basis.lines[line];
        if (base) spendingLines[line] = base.map((lvl, t) => lvl * sources.cpi_index[t] / 100);
      }
    }
    const sumLines = (lines, keep) => {
      const s = zeros();
      for (const [k, v] of Object.entries(lines)) if (keep(k)) v.forEach((z, t) => { s[t] += z; });
      return s;
    };
    const primaryReceipts = sumLines(receiptLines, k => k !== INTEREST_RECEIPTS);
    const interestReceipts = receiptLines[INTEREST_RECEIPTS] || zeros();
    const receipts = primaryReceipts.map((z, t) => z + interestReceipts[t]);
    const primarySpending = sumLines(spendingLines, () => true);
    const currentPrimary = sumLines(spendingLines, k => CURRENT_LINES.has(k));
    // the workbook rounds this to 7dp before it drives debt interest
    const primaryDeficit = primarySpending.map((z, t) => xlRound(z - primaryReceipts[t], 7));

    // Step 3b: debt interest (£ million): rates, RPI and extra borrowing, then the
    // financing loop (the extra interest is itself borrowed)
    sources.primary_deficit = primaryDeficit;
    const debtVecs = basis.vectors.map(v => v.source === "primary_deficit" ? vector(basis, v, sources) : null);
    const firstRound = zeros();
    for (const term of basis.terms) {
      if (term.block !== "debt") continue;
      const v = vecs[term.v] || debtVecs[term.v];
      firstRound[term.e] += term.coef * v[term.d];
    }
    const loop = zeros();
    for (const l of basis.loop) loop[l.e] += l.coef * firstRound[l.d];
    const debtInterest = firstRound.map((z, t) => (z + loop[t]) / 1000);

    // Step 4b: aggregates (£ billion), changes from the baseline
    const spending = primarySpending.map((z, t) => z + debtInterest[t]);
    const borrowing = spending.map((z, t) => z - receipts[t]);
    const currentSpending = currentPrimary.map((z, t) => z + debtInterest[t]);
    const currentDeficit = currentSpending.map((z, t) => z - receipts[t]);
    const cumulate = a => { let s = 0; return a.map(z => (s += z)); };
    const debt = cumulate(borrowing);

    // shares of GDP: scenario level / scenario GDP minus baseline level / baseline GDP
    const L = basis.lines, A = basis.aggregates, b = basis.baseline;
    const gdpBase = t => b.nominal_gdp_fy[t + 1], gdpMarchBase = t => b.nominal_gdp_march[t + 1];
    const gdpScen = t => gdpBase(t) * (1 + ((x.nominal_gdp_fy || [])[t] || 0) / 100);
    const gdpMarchScen = t => gdpMarchBase(t) * (1 + ((x.nominal_gdp_march || [])[t] || 0) / 100);
    const share = (base, change, march) => base.map((lvl, t) => {
      const [g0, g1] = march ? [gdpMarchBase(t), gdpMarchScen(t)] : [gdpBase(t), gdpScen(t)];
      return 100 * ((lvl + change[t]) / g1 - lvl / g0);
    });
    const base = {
      receipts: L["Public sector current receipts"],
      spending: L["Total managed expenditure"],
      borrowing: L["Total managed expenditure"].map((z, t) => z - L["Public sector current receipts"][t]),
      current_deficit: A.current_deficit,
      debt: A.psnd,
      psnfl: A.psnfl,
    };

    return {
      years,
      bn: {receipts, spending, borrowing, current_deficit: currentDeficit, debt_interest: debtInterest,
           debt, psnfl: debt, primary_deficit: primaryDeficit},
      pct_gdp: {
        receipts: share(base.receipts, receipts), spending: share(base.spending, spending),
        borrowing: share(base.borrowing, borrowing), current_deficit: share(base.current_deficit, currentDeficit),
        debt: share(base.debt, debt, true), psnfl: share(base.psnfl, debt, true),
      },
      baseline: base,
      lines: {receipts: receiptLines, spending: spendingLines, debt_interest: debtInterest},
      derived: sources,
    };
  }

  /* The effect of each input on its own (useful for "what drives this"). Not exactly
   * additive: the triple lock, rounding and the GDP denominators are non-linear. */
  function contributions(basis, x, measure = "borrowing") {
    const out = {};
    for (const [id, v] of Object.entries(x)) {
      if (!v || !v.some(z => z)) continue;
      out[id] = compute(basis, {[id]: v}).bn[measure];
    }
    return out;
  }

  root.Reckoner = {compute, contributions, derived};
})(typeof window !== "undefined" ? window : this);
