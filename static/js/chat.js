/* Chat: Nachrichten anzeigen, senden, Lesebestaetigung, Live-Abruf. */
(function () {
  'use strict';
  var form = document.getElementById('chat-form');
  if (!form) return;
  var S = window.Seiten;
  var box = document.getElementById('msgs');
  var input = document.getElementById('content');
  var sendBtn = document.getElementById('send');
  var lastId = 0, lastDay = null, myRead = 0, busy = false, timer = null, failures = 0;
  var tickEls = {}; // id -> Haken-Element eigener Nachrichten

  function addDay(day) {
    if (day === lastDay) return;
    lastDay = day;
    var d = document.createElement('div');
    d.className = 'day-sep';
    d.textContent = day;
    box.appendChild(d);
  }

  function addMessage(m) {
    if (m.id <= lastId) return;
    var nearBottom = box.scrollHeight - window.innerHeight - window.scrollY < 220 || lastId === 0;
    addDay(m.day);
    var b = document.createElement('div');
    b.className = 'bubble ' + (m.mine ? 'me' : 'them');
    b.appendChild(document.createTextNode(m.content));
    var t = document.createElement('span');
    t.className = 't';
    t.textContent = m.time;
    if (m.mine) {
      var tick = document.createElement('span');
      tick.className = 'tick' + (m.read ? ' read' : '');
      tick.textContent = '✓✓';
      tick.title = m.read ? 'Gelesen' : 'Zugestellt';
      t.appendChild(tick);
      tickEls[m.id] = tick;
    }
    b.appendChild(t);
    box.appendChild(b);
    lastId = m.id;
    var chips = document.getElementById('chips');
    if (chips) chips.hidden = true;
    var emptyEl = box.querySelector('.chat-empty');
    if (emptyEl) emptyEl.remove();
    if (nearBottom || m.mine) scrollDown();
  }

  function scrollDown() {
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  function applyRead(upTo) {
    if (!upTo || upTo <= myRead) return;
    myRead = upTo;
    Object.keys(tickEls).forEach(function (id) {
      if (parseInt(id, 10) <= upTo) {
        tickEls[id].classList.add('read');
        tickEls[id].title = 'Gelesen';
        delete tickEls[id];
      }
    });
  }

  // Anfangszustand
  var initial = [];
  try { initial = JSON.parse(document.getElementById('initial-messages').textContent); } catch (e) {}
  if (!initial.length) {
    var e = document.createElement('div');
    e.className = 'chat-empty';
    e.textContent = 'Noch keine Nachrichten – sag Hallo und vereinbart euren Tausch!';
    box.appendChild(e);
  }
  initial.forEach(addMessage);
  var lastReadMine = 0;
  initial.forEach(function (m) { if (m.mine && m.read) lastReadMine = Math.max(lastReadMine, m.id); });
  applyRead(lastReadMine);
  setTimeout(function () { window.scrollTo(0, document.body.scrollHeight); }, 50);

  // Abruf neuer Nachrichten (nur sichtbarer Tab markiert als gelesen)
  function poll() {
    if (busy) return schedule();
    busy = true;
    var url = form.dataset.pollUrl + '?after=' + lastId + (document.hidden ? '' : '&mark=1');
    S.get(url).then(function (r) {
      if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
      if (!r.ok) throw new Error('http');
      return r.json();
    }).then(function (data) {
      failures = 0;
      data.messages.forEach(addMessage);
      applyRead(data.my_read_up_to);
      if (data.messages.some(function (m) { return !m.mine; }) && document.hidden && S.notifyState) {
        // der Tab ist im Hintergrund: der normale Benachrichtigungs-Abruf meldet es
      }
      if (S.pollNow && data.messages.some(function (m) { return !m.mine; })) S.pollNow();
    }).catch(function () { failures++; }).then(function () { busy = false; schedule(); });
  }
  function schedule() {
    clearTimeout(timer);
    var base = document.hidden ? 12000 : 3500;
    timer = setTimeout(poll, Math.min(60000, base * Math.pow(2, Math.min(failures, 3))));
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { clearTimeout(timer); poll(); } });
  schedule();

  // Senden
  function send() {
    var text = input.value.trim();
    if (!text || sendBtn.disabled) return;
    sendBtn.disabled = true;
    var fd = new FormData();
    fd.append('content', text);
    S.post(form.dataset.postUrl, fd).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, body: j }; });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.body.error || 'Fehler');
      input.value = '';
      autosize();
      addMessage(res.body.message);
    }).catch(function (err) {
      S.toast({ type: 'error', title: 'Nachricht nicht gesendet', text: err.message || 'Bitte versuche es noch einmal.' });
    }).then(function () { sendBtn.disabled = false; input.focus(); });
  }
  form.addEventListener('submit', function (e) { e.preventDefault(); send(); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && window.matchMedia('(min-width: 700px)').matches) {
      e.preventDefault(); send();
    }
  });
  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  }
  input.addEventListener('input', autosize);

  // Schnellantworten
  document.querySelectorAll('#chips .chip').forEach(function (c) {
    c.addEventListener('click', function () { input.value = c.textContent; autosize(); input.focus(); });
  });

  // Melde-Dialog
  var dlg = document.getElementById('report-dialog');
  document.getElementById('open-report').addEventListener('click', function () {
    var menu = document.querySelector('.chat-menu'); if (menu) menu.open = false;
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  });
  document.getElementById('report-cancel').addEventListener('click', function () { dlg.close(); });
})();
