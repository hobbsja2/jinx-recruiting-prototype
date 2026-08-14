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

// Reload auto-submit forms as soon as a selection changes. Blank selects are
// disabled first so native GET serialization omits them instead of sending
// values such as player_id=, which optional integer query fields cannot parse.
document.querySelectorAll('form[data-autosubmit] select').forEach(function (select) {
  select.addEventListener('change', function () {
    var form = select.form;
    form.querySelectorAll('select[name]').forEach(function (control) {
      control.disabled = control.value === '';
    });
    form.submit();
  });
});


// Player thumbnail crop selector: move a square viewport over the chosen image.
document.querySelectorAll('form[data-photo-crop]').forEach(function (form) {
  var fileInput = form.querySelector('input[type=file]');
  var editor = form.querySelector('[data-crop-editor]');
  var stage = form.querySelector('[data-crop-stage]');
  var image = form.querySelector('[data-crop-image]');
  var cropWindow = form.querySelector('[data-crop-window]');
  var sizeInput = form.querySelector('[data-crop-size]');
  var fields = {
    x: form.querySelector('input[name=crop_x]'),
    y: form.querySelector('input[name=crop_y]'),
    width: form.querySelector('input[name=crop_width]'),
    height: form.querySelector('input[name=crop_height]')
  };
  var crop = { x: 0, y: 0, size: 0 };
  var objectUrl = '';
  var drag = null;

  function dimensions() {
    return { width: image.clientWidth, height: image.clientHeight };
  }

  function clamp() {
    var area = dimensions();
    crop.size = Math.max(1, Math.min(crop.size, area.width, area.height));
    crop.x = Math.max(0, Math.min(crop.x, area.width - crop.size));
    crop.y = Math.max(0, Math.min(crop.y, area.height - crop.size));
  }

  function render() {
    var area = dimensions();
    if (!area.width || !area.height) return;
    clamp();
    cropWindow.style.width = crop.size + 'px';
    cropWindow.style.height = crop.size + 'px';
    cropWindow.style.transform = 'translate(' + crop.x + 'px,' + crop.y + 'px)';
    fields.x.value = (crop.x / area.width).toFixed(6);
    fields.y.value = (crop.y / area.height).toFixed(6);
    fields.width.value = (crop.size / area.width).toFixed(6);
    fields.height.value = (crop.size / area.height).toFixed(6);
  }

  function resetCrop() {
    var area = dimensions();
    var side = Math.min(area.width, area.height);
    crop = { x: (area.width - side) / 2, y: (area.height - side) / 2, size: side };
    sizeInput.value = '100';
    render();
  }

  fileInput.addEventListener('change', function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      editor.hidden = true;
      return;
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    image.onload = function () {
      editor.hidden = false;
      requestAnimationFrame(resetCrop);
    };
    image.src = objectUrl;
  });

  sizeInput.addEventListener('input', function () {
    var area = dimensions();
    var centerX = crop.x + crop.size / 2;
    var centerY = crop.y + crop.size / 2;
    crop.size = Math.min(area.width, area.height) * (Number(sizeInput.value) / 100);
    crop.x = centerX - crop.size / 2;
    crop.y = centerY - crop.size / 2;
    render();
  });

  cropWindow.addEventListener('pointerdown', function (event) {
    drag = { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      x: crop.x, y: crop.y };
    cropWindow.setPointerCapture(event.pointerId);
    cropWindow.classList.add('dragging');
    event.preventDefault();
  });
  cropWindow.addEventListener('pointermove', function (event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    crop.x = drag.x + event.clientX - drag.clientX;
    crop.y = drag.y + event.clientY - drag.clientY;
    render();
  });
  function endDrag(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null;
    cropWindow.classList.remove('dragging');
  }
  cropWindow.addEventListener('pointerup', endDrag);
  cropWindow.addEventListener('pointercancel', endDrag);
  cropWindow.addEventListener('keydown', function (event) {
    var step = event.shiftKey ? 10 : 2;
    if (event.key === 'ArrowLeft') crop.x -= step;
    else if (event.key === 'ArrowRight') crop.x += step;
    else if (event.key === 'ArrowUp') crop.y -= step;
    else if (event.key === 'ArrowDown') crop.y += step;
    else return;
    event.preventDefault();
    render();
  });
  if (window.ResizeObserver) {
    var observedWidth = 0;
    new ResizeObserver(function () {
      var area = dimensions();
      if (observedWidth && crop.size) {
        var scale = area.width / observedWidth;
        crop.x *= scale;
        crop.y *= scale;
        crop.size *= scale;
        render();
      }
      observedWidth = area.width;
    }).observe(image);
  }
  form.addEventListener('submit', render);
});
