// Custom pattern validation messages via data-pattern-error attributes.
//
// Browser-native "Please match the requested format" is replaced with a
// translatable, field-specific message rendered server-side as
// data-pattern-error="…" on <input> elements.
//
// Uses event delegation with capture phase because:
//   1. The `invalid` event does not bubble.
//   2. HTMX loads form content dynamically — per-element listeners would
//      miss inputs added after DOMContentLoaded.

document.addEventListener('invalid', function (e) {
    var el = e.target;
    if (el.validity.patternMismatch && el.dataset.patternError) {
        el.setCustomValidity(el.dataset.patternError);
    }
}, true);

// Clear custom validity on input so the browser re-evaluates on next submit.
document.addEventListener('input', function (e) {
    if (e.target.setCustomValidity && e.target.dataset.patternError) {
        e.target.setCustomValidity('');
    }
});
