// Checkbox dropdown behavior: "Select All" toggling, summary labels, click-away close.
document.querySelectorAll('.checkdrop').forEach(function (drop) {
  var all = drop.querySelector('input.select-all');
  var boxes = Array.prototype.slice.call(drop.querySelectorAll('input[type=checkbox]:not(.select-all)'));
  var label = drop.querySelector('.checkdrop-label');
  var noun = drop.dataset.noun || 'items';

  function sync() {
    var picked = boxes.filter(function (b) { return b.checked; });
    if (all) {
      all.checked = picked.length === boxes.length && boxes.length > 0;
      all.indeterminate = picked.length > 0 && picked.length < boxes.length;
    }
    if (!label) return;
    if (picked.length === 0 || picked.length === boxes.length) label.textContent = 'All ' + noun;
    else if (picked.length === 1) label.textContent = picked[0].value;
    else label.textContent = picked.length + ' ' + noun + ' selected';
  }

  if (all) {
    all.addEventListener('change', function () {
      boxes.forEach(function (b) { b.checked = all.checked; });
      sync();
    });
  }
  boxes.forEach(function (b) { b.addEventListener('change', sync); });
  sync();
});

document.addEventListener('click', function (event) {
  document.querySelectorAll('.checkdrop[open]').forEach(function (drop) {
    if (!drop.contains(event.target)) drop.removeAttribute('open');
  });
});

// Reload pre-generated email templates as soon as a selection changes.
document.querySelectorAll('form[data-autosubmit] select').forEach(function (select) {
  select.addEventListener('change', function () { select.form.submit(); });
});
