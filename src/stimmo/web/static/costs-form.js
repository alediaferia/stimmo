// Purchase-cost form: mortgage unit conversion and the loan-to-value slider.
//
// Progressive enhancement only. The form is a plain GET form that works without this file: the
// slider, the live equivalent and the client-side checks below are all hidden or absent until it
// runs, and the server re-validates everything.
//
// Everything this script needs is on #cost-mortgage-field as data attributes, rendered
// (translated, and with the limits taken from costs/rates.py) by templates/_costs_form.html:
//   data-ltv-limit        ordinary mutuo fondiario limit, in % of the value
//   data-ltv-max          ceiling reachable with integrative guarantees, in %
//   data-of-price         "of the price"
//   data-msg-over-price   error: amount above the purchase price
//   data-msg-pct-range    error: percentage outside 0..max
// The calculator page's methodology is a native <details>, collapsed by default and fully usable
// without this file. A link that points into it (#method, #ltv, #payment) should land on its
// text, so open it when the URL fragment names the section or something inside it.
(function () {
  var method = document.getElementById('method');
  if (!method || method.tagName !== 'DETAILS') return;
  function openForHash() {
    var id = decodeURIComponent((location.hash || '').slice(1));
    if (!id) return;
    var target = document.getElementById(id);
    if (target && method.contains(target) && !method.open) {
      method.open = true;
      target.scrollIntoView();
    }
  }
  window.addEventListener('hashchange', openForHash);
  openForHash();
})();

(function () {
  var field = document.getElementById('cost-mortgage-field');
  var price = document.getElementById('cost-price');
  var amount = document.getElementById('cost-mortgage');
  var unit = document.getElementById('cost-mortgage-unit');
  var ltv = document.getElementById('cost-ltv');
  var range = document.getElementById('cost-mortgage-range');
  var equiv = document.getElementById('cost-mortgage-equiv');
  var caution = document.getElementById('cost-ltv-caution');
  var error = document.getElementById('cost-mortgage-error');
  if (!field || !price || !amount || !unit || !ltv || !range || !equiv || !caution || !error) return;

  var LIMIT = parseFloat(field.getAttribute('data-ltv-limit'));
  var MAX = parseFloat(field.getAttribute('data-ltv-max'));
  var OF_PRICE = field.getAttribute('data-of-price') || '';
  var MSG_OVER_PRICE = field.getAttribute('data-msg-over-price') || '';
  var MSG_PCT_RANGE = field.getAttribute('data-msg-pct-range') || '';
  if (!isFinite(LIMIT) || !isFinite(MAX)) return;

  var lang = document.documentElement.lang || 'it';
  var intFmt, pctFmt;
  try {
    intFmt = new Intl.NumberFormat(lang, { maximumFractionDigits: 0 });
    pctFmt = new Intl.NumberFormat(lang, { maximumFractionDigits: 1 });
  } catch (e) {
    intFmt = pctFmt = { format: String };
  }
  // Same shape as i18n.fmt_eur: "€ 360.000" in Italian, "€360,000" otherwise.
  function eur(n) { return (lang.indexOf('it') === 0 ? '€ ' : '€') + intFmt.format(Math.round(n)); }
  function pct(n) { return pctFmt.format(n) + '%'; }

  function num(el) {  // a type=number input reports '' for anything it cannot parse
    var v = parseFloat(el.value);
    return isFinite(v) ? v : null;
  }
  function priceValue() { var p = num(price); return p !== null && p > 0 ? p : null; }
  function round1(x) { return Math.round(x * 10) / 10; }
  function clamp(x) { return Math.min(MAX, Math.max(0, x)); }
  function isPct() { return unit.value === 'pct'; }

  // The euro amount a percentage was derived from. Converting back without touching the
  // percentage (or the price) restores it, so toggling the unit twice does not drift by the
  // rounding of the one-decimal percentage.
  var derived = null;
  var shown = unit.value;

  function convert(to) {
    var v = num(amount);
    var p = priceValue();
    if (v === null || p === null) return;  // nothing to convert, or no price to convert with
    if (to === 'pct') {
      var pc = clamp(round1(v / p * 100));
      derived = { pct: pc, eur: v, price: p };
      amount.value = String(pc);
    } else {
      if (v < 0 || v > MAX) return;  // not a percentage: do not turn it into euros
      amount.value = String(derived && derived.pct === v && derived.price === p
        ? derived.eur : Math.round(p * v / 100));
    }
  }

  function refresh() {
    var byPct = isPct();
    if (byPct) { amount.max = String(MAX); amount.step = '0.1'; }
    else { amount.removeAttribute('max'); amount.step = 'any'; }
    ltv.hidden = !byPct;

    var v = num(amount);
    var p = priceValue();
    var msg = '';
    if (v !== null) {
      if (byPct && (v < 0 || v > MAX)) msg = MSG_PCT_RANGE;
      else if (!byPct && p !== null && v > p) msg = MSG_OVER_PRICE;
    }
    amount.setCustomValidity(msg);
    error.textContent = msg;
    error.hidden = !msg;
    if (msg) amount.setAttribute('aria-invalid', 'true'); else amount.removeAttribute('aria-invalid');

    var eq = '';
    if (v !== null && !msg && p !== null) {
      if (byPct) {
        eq = '= ' + eur(p * v / 100);
      } else {
        var share = v / p * 100;
        eq = (Math.abs(round1(share) - share) < 1e-9 ? '= ' : '≈ ') + pct(share) + ' ' + OF_PRICE;
      }
    }
    equiv.textContent = eq;

    if (byPct) {
      var at = v === null ? 0 : v;
      range.value = String(clamp(at));
      var over = !msg && at > LIMIT;
      ltv.setAttribute('data-zone', over ? 'caution' : 'ok');
      caution.hidden = !over;
      if (v !== null && !msg) {
        range.setAttribute('aria-valuetext',
          pct(v) + ' ' + OF_PRICE + (p !== null ? ', ' + eur(p * v / 100) : ''));
      } else {
        range.removeAttribute('aria-valuetext');
      }
    }
  }

  unit.addEventListener('change', function () {
    if (unit.value !== shown) convert(unit.value);
    shown = unit.value;
    refresh();
  });
  amount.addEventListener('input', refresh);
  amount.addEventListener('change', function () {
    var v = num(amount);  // a percentage is kept to one decimal (the field's step)
    if (isPct() && v !== null && v >= 0 && v <= MAX) amount.value = String(round1(v));
    refresh();
  });
  price.addEventListener('input', refresh);
  range.addEventListener('input', function () {
    amount.value = range.value;
    refresh();
  });
  // Back/forward restores form values after scripts have run; re-read them.
  window.addEventListener('pageshow', function () { shown = unit.value; refresh(); });

  refresh();
})();
