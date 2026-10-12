// Seitentausch — kleine Komfort-Funktionen, die auf jeder Seite laufen.
// Die seitenspezifische Logik (Swipe-Buttons, Chat-Polling) steckt bewusst
// inline in den jeweiligen Templates, damit sie nah am zugehörigen HTML bleibt.

document.addEventListener('DOMContentLoaded', () => {
  // Flash-Meldungen nach ein paar Sekunden sanft ausblenden.
  document.querySelectorAll('.flash').forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = 'opacity .4s ease';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    }, 4000 + i * 300);
  });
});
