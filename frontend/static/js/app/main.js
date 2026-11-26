'use strict';

// После проверки входа: раздел из ярлыка приложения (?page=upload) и незаконченное распознавание
function afterStartup() {
  const page = new URLSearchParams(location.search).get('page');
  if (currentUser && page && /^[a-z]+$/.test(page) && page !== 'home' && $('page-' + page)) showPage(page);
  if (location.search) { try { history.replaceState(history.state, '', location.pathname + location.hash); } catch (e) {} }
  if (currentUser) resumePendingAnalyze();
}

renderAccess();
loadCurrencies();
checkAuthOnStartup().then(afterStartup);

// --- Эффекты интерфейса: только оформление, на данные не влияют ---
if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  // Чек на главной наклоняется за курсором
  const stage = document.querySelector('.receipt-stage');
  if (stage && window.matchMedia('(hover: hover)').matches) {
    window.addEventListener('pointermove', e => {
      if (!$('page-home').classList.contains('active')) return;
      const x = e.clientX / window.innerWidth - .5, y = e.clientY / window.innerHeight - .5;
      stage.style.setProperty('--ry', (x * 18).toFixed(2) + 'deg');
      stage.style.setProperty('--rx', (-y * 14).toFixed(2) + 'deg');
    }, { passive: true });
  }
  // Блоки лендинга появляются, когда до них доходит прокрутка (без JS они просто видны)
  if ('IntersectionObserver' in window) {
    document.documentElement.classList.add('reveal-on');
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
    }), { threshold: .15, rootMargin: '0px 0px -40px 0px' });
    document.querySelectorAll('.reveal').forEach(el => io.observe(el));
  }
  // Подсветка карточки под курсором (CSS читает --mx/--my). Только с мышью: на телефоне pointermove
  // идёт при каждом движении пальца и зря пересчитывает стили
  if (window.matchMedia('(hover: hover)').matches) document.addEventListener('pointermove', e => {
    const card = e.target.closest?.('.card');
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', (e.clientX - r.left) + 'px');
    card.style.setProperty('--my', (e.clientY - r.top) + 'px');
  }, { passive: true });
}
