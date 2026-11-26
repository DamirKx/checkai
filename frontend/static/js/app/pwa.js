'use strict';

// --- Мобильная версия (PWA): установка, service worker, клавиатура, сеть ---
const isStandalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
// Chrome и Edge предлагают установку событием beforeinstallprompt; Safari — только через «Поделиться»
let installPrompt = null;
function renderInstall() {
  const canPrompt = !!installPrompt && !isStandalone();
  $('installBtn').hidden = !canPrompt;
  $('installBtnProfile').hidden = !canPrompt;
  $('appInstallText').textContent = isStandalone() ? t('CheckAI установлен на этом устройстве.')
    : canPrompt ? t('Иконка на главном экране и окно без адресной строки — как у обычного приложения.')
    : isIos() ? t('На iPhone и iPad: откройте сайт в Safari → «Поделиться» → «На экран „Домой“».')
    : !window.isSecureContext ? t('Установить приложение можно, когда сайт открыт по https.')
    : t('Откройте сайт в Chrome на телефоне и выберите в меню «Установить приложение».');
}
async function promptInstall() {
  const event = installPrompt;
  if (!event) return;
  installPrompt = null;
  event.prompt();
  try { await event.userChoice; } catch (e) {}
  renderInstall();
}
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installPrompt = e; renderInstall(); });
window.addEventListener('appinstalled', () => { installPrompt = null; renderInstall(); toast(t('CheckAI установлен')); });
$('installBtn').onclick = () => { closeMenu(); promptInstall(); };
$('installBtnProfile').onclick = promptInstall;
I18N.onChange(renderInstall);
renderInstall();

// Service worker: на рабочем сайте (https) включён. На localhost выключен, чтобы кэш не прятал правки
// при разработке; включить для проверки — localStorage.checkai_sw = '1' и перезагрузить страницу
function serviceWorkerEnabled() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return false;
  let flag = null;
  try { flag = localStorage.getItem('checkai_sw'); } catch (e) {}
  return ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) ? flag === '1' : flag !== '0';
}
if (serviceWorkerEnabled()) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(err => console.warn('[pwa] service worker:', err)));
} else if ('serviceWorker' in navigator) {
  // Выключили — убираем прежнюю регистрацию и её кэш
  navigator.serviceWorker.getRegistrations().then(list => list.forEach(r => r.unregister())).catch(() => {});
}

// Пока открыта экранная клавиатура, нижняя панель скрыта (CSS: html.kb-open)
const TEXT_INPUT_SKIP = ['checkbox', 'radio', 'range', 'button', 'submit', 'reset', 'file', 'color', 'image'];
const isTextField = el => !!el && (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !TEXT_INPUT_SKIP.includes(el.type)) || el.isContentEditable);
if (matchMedia('(pointer: coarse)').matches) {
  document.addEventListener('focusin', e => document.documentElement.classList.toggle('kb-open', isTextField(e.target)));
  document.addEventListener('focusout', () => setTimeout(() => {
    if (!isTextField(document.activeElement)) document.documentElement.classList.remove('kb-open');
  }, 60));
}

// Сеть на телефоне то пропадает, то появляется: предупреждаем и обновляем данные, когда связь вернулась
window.addEventListener('offline', () => toast(t('Нет сети. Данные обновятся, когда связь вернётся'), 4000));
window.addEventListener('online', () => { toast(t('Связь восстановлена')); if (currentUser) loadReceipts(); });
