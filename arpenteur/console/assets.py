"""The console's own script and its stylesheet, served by the app.

Kept in Python rather than in a `static/` directory for one reason: this is
served by the same process that renders the pages, there is no build step and
no bundler, and a file on disk would need packaging rules to survive an install
that a string constant does not. It is served with a long cache lifetime and a
content hash in the URL, so it is fetched once.

Everything the console does in the browser is here. Not in `<script>` tags
inside rendered fragments: a component that emitted its own handler would bind
it once per instance on the page, and a fragment swapped in by htmx would bind
it again on every swap.

Four things live here and nothing else does.

**Telling you a request is happening.** htmx has the classes; the console had
no rules for them and no handler for a request that failed, so a click that hit
a 500 did nothing at all and looked identical to a click that did nothing
because there was nothing to do.

**Reordering.** Pointer, keyboard and buttons, against the same endpoint.

**Dropping files.**

**The palette.** One shortcut, one dialog.
"""

from __future__ import annotations

import hashlib

CONSOLE_JS = r"""
(function () {
  'use strict';

  // --- saying that something is happening ------------------------------------
  //
  // htmx adds `htmx-request` to the indicator element for the life of a
  // request. What it cannot do is tell you a request failed: the default is to
  // swap nothing and log to the console, which from the other side of the
  // screen is indistinguishable from a button that does not work.

  function say(message, kind) {
    var main = document.querySelector('main');
    if (!main) return;
    var old = main.querySelector('.banner-live');
    if (old) old.remove();
    var box = document.createElement('div');
    box.className = 'banner banner-' + (kind || 'bad') + ' banner-live';
    box.setAttribute('role', 'alert');
    box.textContent = message;
    main.prepend(box);
    box.scrollIntoView({block: 'nearest'});
  }

  document.body.addEventListener('htmx:responseError', function (e) {
    var status = e.detail.xhr.status;
    say('The server refused that: ' + status + ' ' + e.detail.xhr.statusText +
        '. Nothing was changed.');
  });
  document.body.addEventListener('htmx:sendError', function () {
    say('Could not reach the server. Nothing was changed.');
  });
  document.body.addEventListener('htmx:afterSwap', function (e) {
    var focus = e.detail.target.querySelector('[autofocus]');
    if (focus) focus.focus();
  });

  // --- reordering -------------------------------------------------------------
  //
  // Three ways to move a row, one endpoint. WCAG 2.2 SC 2.5.7 wants a path that
  // needs neither dragging nor a keyboard, which is why the buttons are not
  // optional and are not hidden behind a disclosure.

  function list(node) { return node.closest('.sort-list[data-sortable]'); }
  function rows(ol) {
    return Array.prototype.filter.call(ol.children, function (n) {
      return n.classList.contains('sort-item');
    });
  }

  // The live region is found by id rather than by position, because a sortable
  // <tbody> cannot hold a <div> beside it and a parser would move one out of
  // the table before this ever ran.
  function announce(ol, message) {
    var say = ol.dataset.sortSay && document.getElementById(ol.dataset.sortSay);
    if (say) say.textContent = message;
  }

  function renumber(ol) {
    var items = rows(ol);
    items.forEach(function (li, i) {
      var up = li.querySelector('[data-sort-up]');
      var down = li.querySelector('[data-sort-down]');
      if (up) up.disabled = (i === 0);
      if (down) down.disabled = (i === items.length - 1);
    });
  }

  function save(ol) {
    var body = new URLSearchParams();
    rows(ol).forEach(function (li) { body.append('order', li.dataset.sortId); });
    fetch(ol.dataset.sortPost, {
      method: 'POST', body: body,
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}
    }).then(function (r) {
      if (!r.ok) {
        say('That order was not saved (' + r.status + '). Reloading so the ' +
            'page shows what is actually stored.');
        setTimeout(function () { location.reload(); }, 1200);
      }
    }).catch(function () { location.reload(); });
  }

  function move(li, by) {
    var ol = li.parentElement, items = rows(ol), at = items.indexOf(li);
    var to = at + by;
    if (to < 0 || to >= items.length) return false;
    if (by < 0) ol.insertBefore(li, items[to]);
    else ol.insertBefore(items[to], li);
    renumber(ol);
    announce(ol, 'Moved to position ' + (to + 1) + ' of ' + items.length + '.');
    save(ol);
    return true;
  }

  document.addEventListener('click', function (e) {
    var up = e.target.closest('[data-sort-up]');
    var down = e.target.closest('[data-sort-down]');
    if (!up && !down) return;
    e.preventDefault();
    var li = (up || down).closest('.sort-item');
    if (!li || !list(li)) return;
    move(li, up ? -1 : 1);
    (up || down).focus();
  });

  // Keyboard: space picks the row up, the arrows move it, space puts it down.
  // Escape puts it back where it started, which is the difference between a
  // control somebody will try and one they will not.
  var carried = null, wasAt = -1;

  document.addEventListener('keydown', function (e) {
    var grip = e.target.closest('[data-sort-grip]');
    if (!grip) return;
    var li = grip.closest('.sort-item'), ol = li && list(li);
    if (!ol) return;

    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      if (carried === li) {
        carried = null;
        li.classList.remove('carried');
        announce(ol, 'Put down.');
      } else {
        carried = li;
        wasAt = rows(ol).indexOf(li);
        li.classList.add('carried');
        announce(ol, 'Picked up. Use the arrow keys, then space to put it down.');
      }
      return;
    }
    if (e.key === 'Escape' && carried === li) {
      e.preventDefault();
      var items = rows(ol);
      ol.insertBefore(li, items[wasAt] || null);
      li.classList.remove('carried');
      carried = null;
      renumber(ol);
      announce(ol, 'Put back.');
      save(ol);
      return;
    }
    if (carried !== li) return;
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      e.preventDefault(); move(li, -1); grip.focus();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      e.preventDefault(); move(li, 1); grip.focus();
    }
  });

  // Pointer. The row is moved in the DOM as it is dropped and saved behind it,
  // because a list that waits for the server to answer before it moves feels
  // broken even when it is not.
  var held = null;

  document.addEventListener('dragstart', function (e) {
    var li = e.target.closest('.sort-item');
    if (!li || !list(li)) return;
    held = li;
    li.classList.add('carried');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', li.dataset.sortId);
  });
  document.addEventListener('dragend', function () {
    if (held) held.classList.remove('carried');
    held = null;
  });
  document.addEventListener('dragover', function (e) {
    if (!held) return;
    var over = e.target.closest('.sort-item');
    if (!over || over === held || over.parentElement !== held.parentElement) return;
    e.preventDefault();
    var items = rows(held.parentElement);
    if (items.indexOf(over) < items.indexOf(held)) {
      over.parentElement.insertBefore(held, over);
    } else {
      over.parentElement.insertBefore(over, held);
    }
  });
  document.addEventListener('drop', function (e) {
    if (!held) return;
    e.preventDefault();
    var ol = held.parentElement;
    held.classList.remove('carried');
    held = null;
    renumber(ol);
    save(ol);
  });

  // --- dropping files ---------------------------------------------------------

  // The form's own fields go up with the files. What a document is and which
  // project it belongs to are chosen in the same box you drop onto, and a
  // control the drop did not read would be a control that silently did nothing.
  function upload(zone, files) {
    if (!files || !files.length) return;
    var progress = zone.querySelector('.drop-progress');
    var form = zone.querySelector('form');
    var input = zone.querySelector('input[type=file]');
    var field = (input && input.name) || 'file';
    var body = new FormData();
    if (form) {
      new FormData(form).forEach(function (value, key) {
        if (key !== field) body.append(key, value);
      });
    }
    Array.prototype.forEach.call(files, function (f) { body.append(field, f); });
    zone.classList.add('busy');
    if (progress) {
      progress.textContent = files.length === 1
        ? 'Uploading ' + files[0].name + '…'
        : 'Uploading ' + files.length + ' files…';
    }
    fetch(zone.dataset.dropPost, {
      method: 'POST', body: body, headers: {'HX-Request': 'true'}
    }).then(function (r) { return r.text().then(function (t) { return [r, t]; }); })
      .then(function (pair) {
        zone.classList.remove('busy');
        if (!pair[0].ok) { say('That upload failed: ' + pair[0].status + '.'); return; }
        var into = zone.dataset.dropTarget &&
                   document.querySelector(zone.dataset.dropTarget);
        if (into) {
          into.outerHTML = pair[1];
        } else {
          location.reload();
        }
      }).catch(function () {
        zone.classList.remove('busy');
        say('That upload did not reach the server.');
      });
  }

  document.addEventListener('dragover', function (e) {
    var zone = e.target.closest('[data-dropzone]');
    if (!zone || !e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    zone.classList.add('over');
  });
  document.addEventListener('dragleave', function (e) {
    var zone = e.target.closest('[data-dropzone]');
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove('over');
  });
  document.addEventListener('drop', function (e) {
    var zone = e.target.closest('[data-dropzone]');
    if (!zone || !e.dataTransfer.files.length) return;
    e.preventDefault();
    zone.classList.remove('over');
    upload(zone, e.dataTransfer.files);
  });
  document.addEventListener('change', function (e) {
    var input = e.target.closest('[data-dropzone] input[type=file]');
    if (!input || !input.files.length) return;
    upload(input.closest('[data-dropzone]'), input.files);
    input.value = '';
  });

  // --- the palette ------------------------------------------------------------
  //
  // One shortcut for everything with a name. The console has eighty-seven
  // routes behind eleven links, so most of it is reachable only by knowing
  // which page it hangs off.

  function palette() { return document.getElementById('palette'); }

  document.addEventListener('keydown', function (e) {
    var box = palette();
    if (!box) return;
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (box.open) { box.close(); return; }
      box.showModal();
      var input = box.querySelector('input[name=q]');
      if (input) { input.value = ''; input.focus(); }
      var results = box.querySelector('#palette-results');
      if (results) results.innerHTML = '';
      return;
    }
    if (!box.open) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      var hits = Array.prototype.slice.call(box.querySelectorAll('.hit'));
      if (!hits.length) return;
      var at = hits.indexOf(document.activeElement);
      var to = e.key === 'ArrowDown' ? at + 1 : at - 1;
      if (to < 0) to = hits.length - 1;
      if (to >= hits.length) to = 0;
      hits[to].focus();
    }
  });

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-palette-open]')) {
      e.preventDefault();
      var box = palette();
      if (box && !box.open) {
        box.showModal();
        var input = box.querySelector('input[name=q]');
        if (input) input.focus();
      }
    }
  });
})();
"""

# Served under a URL carrying this digest, so the browser may cache it forever
# and still pick up a change the moment there is one. A console that ships a
# stale script after a deploy is a console whose bug reports are about a version
# nobody is running.
CONSOLE_JS_HASH = hashlib.sha256(CONSOLE_JS.encode()).hexdigest()[:12]
CONSOLE_JS_URL = f"/static/console.{CONSOLE_JS_HASH}.js"
