'use strict';

// --- Окна: фокус внутри окна и возврат фокуса после закрытия ---
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const modalOpener = {};
function openModal(id, focusEl) {
  if ($(id).hidden) modalOpener[id] = document.activeElement;
  $(id).hidden = false;
  (focusEl || $(id).querySelector(FOCUSABLE))?.focus();
}
function closeModal(id) {
  if ($(id).hidden) return;
  $(id).hidden = true;
  const opener = modalOpener[id];
  delete modalOpener[id];
  if (opener && document.contains(opener) && !opener.closest('[hidden]')) opener.focus();
}
function trapFocus(e) {
  // Верхнее открытое окно — последнее в разметке (окно дубликата открывается поверх окна чека)
  const modal = [...document.querySelectorAll('.modal-backdrop')].filter(m => !m.hidden).pop();
  if (!modal) return;
  const els = [...modal.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
  if (!els.length) return;
  const first = els[0], last = els[els.length - 1];
  if (!modal.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

// --- Окно входа / регистрации ---
function setAuthMode(mode) {
  authMode = mode;
  const register = mode === 'register';
  document.querySelectorAll('.tab').forEach(t => { const on = t.dataset.tab === mode; t.classList.toggle('active', on); t.setAttribute('aria-selected', on); });
  renderAuthTexts();
  $('nameField').hidden = !register;
  $('authPassword').autocomplete = register ? 'new-password' : 'current-password';
  // Требования к паролю — только при регистрации: старые аккаунты могут иметь короткий пароль
  if (register) { $('authPassword').minLength = 8; $('authPassword').maxLength = 72; }
  else { $('authPassword').removeAttribute('minlength'); $('authPassword').removeAttribute('maxlength'); }
  $('authPasswordHint').hidden = !register;
  $('authError').hidden = true;
}
function renderAuthTexts() {
  const register = authMode === 'register';
  $('authTitle').textContent = register ? t('Регистрация') : t('Вход');
  $('authSubmit').textContent = register ? t('Зарегистрироваться') : t('Войти');
}
function openAuth(mode = 'login') {
  setAuthMode(mode);
  closeMenu();
  openModal('authModal', $(mode === 'register' ? 'authName' : 'authEmail'));
}
function closeAuth() { closeModal('authModal'); pendingSave = false; }
document.querySelectorAll('.tab').forEach(t => t.onclick = () => setAuthMode(t.dataset.tab));
$('loginBtn').onclick = () => openAuth('login');
$('authClose').onclick = closeAuth;
$('authModal').onclick = e => { if (e.target.id === 'authModal') closeAuth(); };
$('logoutBtn').onclick = logout;
$('authForm').onsubmit = async e => {
  e.preventDefault();
  const btn = $('authSubmit'), isLogin = authMode === 'login';
  const body = { email: $('authEmail').value.trim(), password: $('authPassword').value };
  if (!isLogin) body.full_name = $('authName').value.trim() || null;
  btn.disabled = true;
  $('authError').hidden = true;
  try {
    const data = await apiRequest(isLogin ? '/api/auth/login' : '/api/auth/register', { method: 'POST', body: JSON.stringify(body) });
    setSession(data);
    // Новый аккаунт получает язык, выбранный на этом устройстве; при входе — язык из аккаунта
    langFromDevice = !isLogin;
    setUser(data.user);
    const wasPending = pendingSave;
    closeAuth();
    $('authForm').reset();
    toast(isLogin ? t('Добро пожаловать!') : t('Регистрация успешна!'));
    if ($('page-home').classList.contains('active')) showPage('upload');
    await loadReceipts();
    if (wasPending) await saveReceipt();
  } catch (err) {
    $('authError').textContent = err.message;
    $('authError').hidden = false;
  } finally { btn.disabled = false; }
};

// --- Навигация ---
const currentPage = () => document.querySelector('.page.active')?.id.slice('page-'.length) || 'home';
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
  // Пункты меню и нижней панели (на телефоне) подсвечиваются одинаково
  document.querySelectorAll('.nav-link[data-page], .tab-link').forEach(b => {
    const on = b.dataset.page === name;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  // Текущий раздел — в записи истории браузера: кнопка «Назад» (жест на Android) возвращает к прошлому разделу
  try { history.replaceState({ ...(history.state || {}), page: name }, ''); } catch (e) {}
  closeMenu();
  if (name === 'history') renderHistory();
  if (name === 'categories') renderCategories();
  if (name === 'analysis') {
    renderAnalysis(); renderInsights();
    // Могли появиться новые чеки или лимиты — сервер скажет, устарели ли советы
    if (currentUser && insights.loaded && !insights.unsupported) loadInsights();
  }
  if (name === 'budgets') renderBudgets();
  if (name === 'profile') { renderProfile(true); fillProfileForm(); }
  else $('heatTip').hidden = true;
}
function setMenu(open) {
  $('drawer').classList.toggle('open', open);
  $('overlay').classList.toggle('show', open);
  // Закрытое меню уезжает за экран — убираем его из порядка Tab
  $('drawer').inert = !open;
  $('menuBtn').setAttribute('aria-expanded', open);
  $('menuBtn').setAttribute('aria-label', open ? t('Закрыть меню') : t('Открыть меню'));
  if (open) $('drawer').querySelector('.nav-link.active, .nav-link')?.focus();
}
function closeMenu() { if ($('drawer').classList.contains('open')) setMenu(false); }
$('menuBtn').onclick = () => setMenu(!$('drawer').classList.contains('open'));
$('overlay').onclick = closeMenu;
// Переход по разделам от пользователя — новая запись в истории; showPage из кода (вход, выход) — без неё
function navigate(name) {
  if (currentPage() !== name) { try { history.pushState({ page: name }, ''); } catch (e) {} }
  showPage(name);
  window.scrollTo({ top: 0, behavior: 'instant' });
}
document.querySelectorAll('.nav-link[data-page], .tab-link').forEach(b => b.onclick = () => navigate(b.dataset.page));
$('brandLink').onclick = () => navigate(currentUser ? 'upload' : 'home');
// «Назад»: сначала закрывает открытое окно или меню (как Esc) и остаётся в разделе, потом — прошлый раздел
window.addEventListener('popstate', e => {
  const overlayOpen = document.querySelector('.modal-backdrop:not([hidden])') || $('drawer').classList.contains('open') || !$('accountMenu').hidden;
  if (overlayOpen) {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    try { history.pushState({ page: currentPage() }, ''); } catch (err) {}
    return;
  }
  const page = e.state?.page;
  // Гостю доступна только главная, вошедшему — всё, кроме неё
  if (!page || !$('page-' + page) || (document.documentElement.dataset.auth === 'guest') !== (page === 'home')) return;
  showPage(page);
});
$('heroRegister').onclick = $('landingRegister').onclick = $('uploadRegisterBtn').onclick = () => openAuth('register');
$('heroLogin').onclick = $('landingLogin').onclick = $('uploadLoginBtn').onclick = () => openAuth('login');
document.addEventListener('keydown', e => {
  if (e.key === 'Tab') { trapFocus(e); return; }
  if (e.key !== 'Escape') return;
  const catMenu = $('categoryGrid').querySelector('.cat-menu:not([hidden])');
  if (!$('duplicateModal').hidden) resolveDuplicate('cancel');
  else if (!$('receiptModal').hidden) closeReceipt();
  else if (!$('avatarModal').hidden) closeAvatar();
  else if (!$('savedModal').hidden) closeModal('savedModal');
  else if (!$('authModal').hidden) closeAuth();
  else if (!$('categoryModal').hidden) closeModal('categoryModal');
  else if (!$('deleteCategoryModal').hidden) closeModal('deleteCategoryModal');
  else if (catMenu) { closeCategoryMenus(); catMenu.previousElementSibling.focus(); }
  else if (!$('accountMenu').hidden) { closeAccountMenu(); $('userMenuBtn').focus(); }
  else if ($('drawer').classList.contains('open')) { closeMenu(); $('menuBtn').focus(); }
});
let toastTimer = null;
function toast(msg, ms = 2800) {
  $('toast').textContent = msg;
  $('toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('show'), ms);
}
