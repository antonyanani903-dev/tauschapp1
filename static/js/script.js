/* Seitentausch - gemeinsames Skript: Meldungen, Fetch-Helfer, Live-Benachrichtigungen. */
(function () {
  'use strict';

  var Seiten = (window.Seiten = window.Seiten || {});
  var body = document.body;

  // ---------- Hilfsfunktionen ----------
  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }
  Seiten.csrfToken = csrfToken;

  Seiten.post = function (url, data) {
    var fd = data instanceof FormData ? data : new FormData();
    if (data && !(data instanceof FormData)) {
      Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    }
    return fetch(url, {
      method: 'POST', body: fd, credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken(), 'X-Requested-With': 'fetch' }
    });
  };
  Seiten.get = function (url) {
    return fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
  };
  Seiten.esc = function (s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  };

  // ---------- Flash-Meldungen ausblenden ----------
  document.querySelectorAll('.flash').forEach(function (el) {
    setTimeout(function () { el.classList.add('hide'); }, 6000);
    setTimeout(function () { el.remove(); }, 6600);
  });

  // ---------- Bestaetigungsdialoge (data-confirm) ----------
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // ---------- Doppelklick-Schutz bei Formularen ----------
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (e.defaultPrevented || form.hasAttribute('data-no-lock')) return;
    form.querySelectorAll('button[type=submit]:not([data-no-lock])').forEach(function (b) {
      setTimeout(function () { b.disabled = true; }, 0);
    });
  });
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) document.querySelectorAll('button[type=submit]').forEach(function (b) { b.disabled = false; });
  });

  // ---------- Fotos vor dem Hochladen verkleinern (spart Datenvolumen, vermeidet "zu gross") ----------
  function shrink(file) {
    return new Promise(function (resolve) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type) || !window.createImageBitmap) return resolve(file);
      createImageBitmap(file, { imageOrientation: 'from-image' }).then(function (bmp) {
        var max = 1600, scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
        if (scale === 1 && file.size < 1.2 * 1024 * 1024) return resolve(file);
        var c = document.createElement('canvas');
        c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
        c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
        c.toBlob(function (blob) {
          if (!blob || blob.size >= file.size) return resolve(file);
          resolve(new File([blob], file.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }));
        }, 'image/jpeg', 0.85);
      }).catch(function () { resolve(file); });
    });
  }
  document.querySelectorAll('input[type=file][data-compress]').forEach(function (input) {
    var summary = input.parentElement.querySelector('[data-file-summary]');
    input.addEventListener('change', function () {
      var max = parseInt(input.dataset.max || '5', 10);
      var files = Array.prototype.slice.call(input.files);
      if (files.length > max) {
        files = files.slice(0, max);
        if (summary) summary.textContent = 'Es werden nur die ersten ' + max + ' Fotos verwendet.';
      } else if (summary) { summary.textContent = files.length ? files.length + ' Foto' + (files.length > 1 ? 's' : '') + ' ausgewÃ¤hlt' : ''; }
      if (!files.length || !window.DataTransfer) return;
      Promise.all(files.map(shrink)).then(function (small) {
        try {
          var dt = new DataTransfer();
          small.forEach(function (f) { dt.items.add(f); });
          input.files = dt.files;
        } catch (e) { /* Browser erlaubt das nicht: Originale werden hochgeladen */ }
      });
    });
  });

  // ---------- Toasts ----------
  Seiten.toast = function (opts) {
    var box = document.getElementById('toasts');
    if (!box) return;
    var el = document.createElement(opts.url ? 'a' : 'div');
    el.className = 'toast toast-' + (opts.type || 'info');
    if (opts.url) el.href = opts.url;
    el.innerHTML = '<strong>' + Seiten.esc(opts.title) + '</strong><span>' + Seiten.esc(opts.text || '') + '</span>';
    box.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('in'); });
    var remove = function () { el.classList.remove('in'); setTimeout(function () { el.remove(); }, 300); };
    setTimeout(remove, opts.duration || 7000);
    while (box.children.length > 3) box.firstChild.remove();
  };

  // ---------- Live-Benachrichtigungen ----------
  if (body.dataset.auth !== '1') return;

  var baseTitle = document.title.replace(/^\(\d+\)\s*/, '');
  var state = {
    sinceMatch: parseInt(body.dataset.sinceMatch || '0', 10),
    sinceMessage: parseInt(body.dataset.sinceMessage || '0', 10),
    current: body.dataset.currentMatch || '',
    url: body.dataset.notifyUrl,
    timer: null, busy: false, failures: 0, lastTotal: null
  };
  Seiten.notifyState = state;

  function setBadges(counts) {
    document.querySelectorAll('[data-badge]').forEach(function (el) {
      var n = counts[el.dataset.badge] || 0;
      el.textContent = n > 99 ? '99+' : n;
      el.hidden = n === 0;
    });
    document.title = (counts.total ? '(' + counts.total + ') ' : '') + baseTitle;
    if (state.lastTotal !== null && counts.total !== state.lastTotal) {
      document.dispatchEvent(new CustomEvent('seiten:counts', { detail: counts }));
    }
    state.lastTotal = counts.total;
  }
  Seiten.setBadges = setBadges;

  function browserNotify(ev) {
    if (!('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return;
    try {
      var n = new Notification(ev.title, { body: ev.text, tag: ev.type + '-' + ev.match_id, icon: '/static/images/favicon.png' });
      n.onclick = function () { window.focus(); location.href = ev.url; n.close(); };
    } catch (err) { /* manche Mobilbrowser erlauben das nicht */ }
  }

  Seiten.browserNotifications = {
    supported: 'Notification' in window,
    permission: function () { return 'Notification' in window ? Notification.permission : 'unsupported'; },
    enable: function () {
      if (!('Notification' in window)) return Promise.resolve('unsupported');
      return Notification.requestPermission();
    }
  };

  function handle(data) {
    state.failures = 0;
    setBadges(data.counts);
    data.events.forEach(function (ev) {
      if (ev.type === 'match') {
        document.dispatchEvent(new CustomEvent('seiten:match', { detail: ev }));
      }
      Seiten.toast({ type: ev.type, title: ev.title, text: ev.text, url: ev.url, duration: ev.type === 'match' ? 10000 : 7000 });
      browserNotify(ev);
    });
    if (data.since) {
      state.sinceMatch = Math.max(state.sinceMatch, data.since.match);
      state.sinceMessage = Math.max(state.sinceMessage, data.since.message);
    }
  }

  function poll() {
    if (state.busy || document.hidden) return schedule();
    state.busy = true;
    var q = '?since_match=' + state.sinceMatch + '&since_message=' + state.sinceMessage +
      (state.current ? '&current_match=' + encodeURIComponent(state.current) : '');
    Seiten.get(state.url + q)
      .then(function (r) {
        if (r.status === 401 || r.redirected) { location.href = '/login'; throw new Error('auth'); }
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(handle)
      .catch(function () { state.failures++; })
      .then(function () { state.busy = false; schedule(); });
  }

  function schedule() {
    clearTimeout(state.timer);
    // 8 s normal, bei Fehlern langsamer (max. 60 s)
    var delay = Math.min(60000, 8000 * Math.pow(2, Math.min(state.failures, 3)));
    state.timer = setTimeout(poll, delay);
  }
  Seiten.pollNow = function () { clearTimeout(state.timer); poll(); };

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) Seiten.pollNow();
  });
  window.addEventListener('online', Seiten.pollNow);

  // Der erste Abruf kommt schnell, damit z. B. ein soeben entstandenes Match sofort auftaucht.
  state.timer = setTimeout(poll, 1500);
})();
