/* Entdecken: Kartenstapel mit Wischen, Tasten und Match-Anzeige. */
(function () {
  'use strict';
  var deck = document.getElementById('deck');
  if (!deck) return;
  var S = window.Seiten;
  var books = [];
  try { books = JSON.parse(deck.dataset.books || '[]'); } catch (e) {}
  var more = deck.dataset.more === '1';
  var urlTpl = deck.dataset.swipeUrl; // /swipe/0/like
  var stack = document.getElementById('stack');
  var btns = document.getElementById('swipe-btns');
  var empty = document.getElementById('empty');
  var busy = false;
  var pendingMatch = null;

  function cardHTML(b) {
    var img = b.img
      ? '<img src="' + S.esc(b.img) + '" alt="" draggable="false">'
      : '<div class="cover-placeholder">📖</div>';
    var dist = b.distance ? '<span class="dist">📍 ' + S.esc(b.distance) + '</span>' : '';
    var desc = b.description ? '<p class="desc">' + S.esc(b.description) + '</p>' : '';
    var place = b.place ? ' · ' + S.esc(b.place) : '';
    return '<div class="stamp like">GEFÄLLT</div><div class="stamp pass">WEITER</div>' +
      '<div class="cover">' + img + dist + '</div>' +
      '<div class="cover-info"><p class="title">' + S.esc(b.title) + '</p>' +
      '<p class="meta">' + S.esc(b.author) + '</p>' + desc +
      '<span class="tag">' + S.esc(b.condition) + '</span> ' +
      '<span class="muted">von ' + S.esc(b.owner) + place + '</span></div>';
  }

  function render() {
    stack.innerHTML = '';
    if (!books.length) {
      if (more) { location.reload(); return; }
      stack.hidden = btns.hidden = true;
      deck.querySelector('.hint').hidden = true;
      empty.hidden = false;
      return;
    }
    // hinten liegende Karte zuerst, damit die vordere oben liegt
    if (books[1]) {
      var back = document.createElement('div');
      back.className = 'card behind';
      back.innerHTML = cardHTML(books[1]);
      stack.appendChild(back);
    }
    var top = document.createElement('div');
    top.className = 'card';
    top.innerHTML = cardHTML(books[0]);
    stack.appendChild(top);
    attachDrag(top);
  }

  function decide(decision) {
    if (busy || !books.length) return;
    busy = true;
    var book = books[0];
    var card = stack.querySelector('.card:not(.behind)');
    card.classList.remove('dragging');
    card.style.transform = '';
    card.classList.add(decision === 'like' ? 'fly-right' : 'fly-left');
    document.getElementById('btn-like').disabled = document.getElementById('btn-pass').disabled = true;

    var url = urlTpl.replace('/0/like', '/' + book.id + '/' + decision);
    S.post(url).then(function (r) {
      if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    }).then(function (data) {
      books.shift();
      if (data.matched) pendingMatch = data;
      setTimeout(function () {
        if (pendingMatch) showMatch(pendingMatch);
        render();
        done();
      }, 260);
    }).catch(function () {
      card.classList.remove('fly-right', 'fly-left');
      S.toast({ type: 'error', title: 'Das hat nicht geklappt', text: 'Bitte prüfe deine Verbindung und versuche es noch einmal.' });
      done();
    });
  }
  function done() {
    busy = false;
    document.getElementById('btn-like').disabled = document.getElementById('btn-pass').disabled = false;
  }

  // ---------- Ziehen mit Finger/Maus ----------
  function attachDrag(card) {
    var startX = 0, startY = 0, dx = 0, active = false, horizontal = null;
    var likeStamp = card.querySelector('.stamp.like'), passStamp = card.querySelector('.stamp.pass');

    card.addEventListener('pointerdown', function (e) {
      if (busy) return;
      active = true; horizontal = null; startX = e.clientX; startY = e.clientY; dx = 0;
    });
    card.addEventListener('pointermove', function (e) {
      if (!active) return;
      var mx = e.clientX - startX, my = e.clientY - startY;
      if (horizontal === null && (Math.abs(mx) > 8 || Math.abs(my) > 8)) {
        horizontal = Math.abs(mx) > Math.abs(my);
        if (horizontal) { card.setPointerCapture(e.pointerId); card.classList.add('dragging'); }
      }
      if (!horizontal) return;
      dx = mx;
      card.style.transform = 'translateX(' + dx + 'px) rotate(' + dx / 18 + 'deg)';
      var p = Math.min(1, Math.abs(dx) / 110);
      likeStamp.style.opacity = dx > 0 ? p : 0;
      passStamp.style.opacity = dx < 0 ? p : 0;
    });
    function end() {
      if (!active) return;
      active = false;
      card.classList.remove('dragging');
      if (horizontal && Math.abs(dx) > 110) { decide(dx > 0 ? 'like' : 'skip'); return; }
      card.style.transform = '';
      likeStamp.style.opacity = passStamp.style.opacity = 0;
    }
    card.addEventListener('pointerup', end);
    card.addEventListener('pointercancel', end);
  }

  // ---------- Match-Anzeige ----------
  var overlay = document.getElementById('match-overlay');
  function pic(el, img) {
    el.innerHTML = img ? '<img src="' + S.esc(img) + '" alt="">' : '📖';
  }
  function showMatch(m) {
    pendingMatch = null;
    document.getElementById('match-text').textContent =
      'Du und ' + m.other_name + ' möchtet gegenseitig Bücher tauschen.';
    pic(document.getElementById('mb-mine'), m.my_book.img);
    pic(document.getElementById('mb-theirs'), m.their_book.img);
    document.getElementById('mb-mine-t').textContent = 'Du gibst: ' + m.my_book.title;
    document.getElementById('mb-theirs-t').textContent = 'Du bekommst: ' + m.their_book.title;
    document.getElementById('match-chat').href = m.chat_url;
    overlay.hidden = false;
    if (navigator.vibrate) navigator.vibrate(120);
    if (S.pollNow) S.pollNow(); // Zaehler sofort aktualisieren
  }
  document.getElementById('match-close').addEventListener('click', function () { overlay.hidden = true; });

  document.getElementById('btn-like').addEventListener('click', function () { decide('like'); });
  document.getElementById('btn-pass').addEventListener('click', function () { decide('skip'); });
  document.addEventListener('keydown', function (e) {
    if (!overlay.hidden) { if (e.key === 'Escape') overlay.hidden = true; return; }
    if (/INPUT|SELECT|TEXTAREA/.test((e.target.tagName || ''))) return;
    if (e.key === 'ArrowRight') decide('like');
    if (e.key === 'ArrowLeft') decide('skip');
  });

  render();
})();
