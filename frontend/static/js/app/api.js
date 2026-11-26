'use strict';

// --- API и токен ---
const TOKEN_KEY = 'checkai_token';
const getToken = () => { try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; } };
const setToken = t => { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) {} };
// Access-токен живёт час; refresh-токен (30 дней) обменивается на новую пару через /api/auth/refresh
const REFRESH_KEY = 'checkai_refresh';
const getRefresh = () => { try { return localStorage.getItem(REFRESH_KEY); } catch (e) { return null; } };
const setRefresh = t => { try { if (t) localStorage.setItem(REFRESH_KEY, t); else localStorage.removeItem(REFRESH_KEY); } catch (e) {} };
// Выход и истёкшая сессия: убираем оба токена
const removeToken = () => { try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); } catch (e) {} };
// Сохранённые на устройстве аккаунты для быстрого переключения: [{token,refresh,user}]
const ACCOUNTS_KEY = 'checkai_accounts';
const getAccounts = () => {
  try {
    const list = JSON.parse(localStorage.getItem(ACCOUNTS_KEY));
    return Array.isArray(list) ? list.filter(a => a && a.token && a.user) : [];
  } catch (e) { return []; }
};
const saveAccounts = list => { try { localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(list.slice(0, 5))); } catch (e) {} };
const rememberAccount = (token, user) => { if (token && user) saveAccounts([{ token, refresh: getRefresh(), user }, ...getAccounts().filter(a => a.user.id !== user.id)]); };
// Ответ входа, регистрации, смены данных и обновления: {access_token, refresh_token, user}
function setSession(data) {
  setToken(data.access_token);
  setRefresh(data.refresh_token || null);
  if (data.user) rememberAccount(data.access_token, data.user);
}
const forgetAccount = id => saveAccounts(getAccounts().filter(a => a.user.id !== id));
const forgetToken = token => saveAccounts(getAccounts().filter(a => a.token !== token));
// У ошибки есть поле status, чтобы отличать «нет такого эндпоинта» (404/405) от остальных
const notSupported = e => e.status === 404 || e.status === 405;

/* i18n-keys */
const STATUS_TEXT = {
  413: 'Файл слишком большой. Максимум — {mb} МБ',
  415: 'Неподдерживаемый формат файла. Подойдут JPG, PNG, WEBP, BMP или TIFF',
  422: 'Проверьте введённые данные',
  429: 'Слишком много запросов. Подождите немного и попробуйте снова',
  500: 'Ошибка на сервере. Попробуйте ещё раз позже',
  502: 'Сервер временно недоступен. Попробуйте позже',
  503: 'Сервер временно недоступен. Попробуйте позже',
  504: 'Сервер не ответил вовремя. Попробуйте ещё раз',
};
const FIELD_NAMES = { email: 'Email', password: 'Пароль', full_name: 'Имя', name: 'Название товара', store: 'Магазин', category: 'Категория', total: 'Итог', quantity: 'Количество', price_per_unit: 'Цена', total_price: 'Сумма', date: 'Дата покупки', time: 'Время', currency: 'Валюта' };
/* /i18n-keys */
const hasCyrillic = s => /[а-яё]/i.test(s);
// Ошибки валидации FastAPI приходят списком на английском или с приставкой «Value error,»
function validationMessage(d) {
  const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : '';
  const msg = String(d.msg || '').replace(/^Value error,\s*/i, '');
  // Сообщение сервера на русском — переводим, если знаем его; уже переведённое сервером t() не меняет
  if (hasCyrillic(msg)) return t(msg);
  if (field === 'email') return t('Введите корректный email (например, user@example.com)');
  const label = t(FIELD_NAMES[field] || 'Поле');
  if (d.type === 'missing') return t('{label}: обязательное поле', { label });
  if (d.type === 'string_too_long') return t('{label}: слишком длинное значение', { label });
  if (d.type === 'string_too_short') return t('{label}: не может быть пустым', { label });
  return t('{label}: некорректное значение', { label });
}
function makeError(status, data) {
  const detail = Array.isArray(data.detail) ? data.detail.map(validationMessage).join('; ') : typeof data.detail === 'string' ? t(data.detail) : data.detail;
  const err = new Error((typeof detail === 'string' && detail) || (STATUS_TEXT[status] && t(STATUS_TEXT[status], { mb: MAX_UPLOAD_MB })) || t('Ошибка сервера ({status})', { status }));
  err.status = status;
  // Тело ответа целиком: например, retry_after у 429 от /api/insights
  err.data = data;
  return err;
}
function handleResponse(status, data, token) {
  // Токен истёк или недействителен — переходим в гостевой режим
  if (status === 401 && token) { forgetToken(token); removeToken(); setGuest(); }
  if (status < 200 || status >= 300) throw makeError(status, data);
  return data;
}
const networkError = () => Object.assign(new Error(t('Нет связи с сервером. Проверьте подключение и попробуйте снова')), { status: 0 });

// Один запрос обновления на все одновременные 401
let refreshing = null;
function refreshSession() {
  if (!refreshing) {
    refreshing = (async () => {
      const refreshToken = getRefresh();
      if (!refreshToken) return false;
      try {
        const response = await fetch('/api/auth/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept-Language': I18N.getLang() }, body: JSON.stringify({ refresh_token: refreshToken }) });
        if (!response.ok) return false;
        const data = await response.json();
        // Пока шёл запрос, пользователь сменил аккаунт — новые токены уже не нужны
        if (getRefresh() !== refreshToken) return true;
        setSession(data);
        return true;
      } catch (e) { return false; }
    })().finally(() => { refreshing = null; });
  }
  return refreshing;
}

async function apiRequest(url, options = {}, retried = false) {
  const token = getToken();
  options.headers = options.headers || {};
  if (token) options.headers['Authorization'] = `Bearer ${token}`;
  // Сервер отвечает ошибками и рекомендациями на языке интерфейса
  options.headers['Accept-Language'] = I18N.getLang();
  if (options.body && !(options.body instanceof FormData) && !options.headers['Content-Type']) options.headers['Content-Type'] = 'application/json';
  let response;
  try { response = await fetch(url, options); } catch (e) { throw networkError(); }
  const data = await response.json().catch(() => ({}));
  // Access-токен истёк — обновляем его и повторяем запрос один раз
  if (response.status === 401 && token && !retried && await refreshSession()) return apiRequest(url, options, true);
  return handleResponse(response.status, data, token);
}
// Загрузка файла через XHR: fetch не умеет сообщать прогресс отправки
function apiUpload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(), token = getToken();
    xhr.open('POST', url);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.setRequestHeader('Accept-Language', I18N.getLang());
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total, false); };
    xhr.upload.onload = () => onProgress(1, true);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      try { resolve(handleResponse(xhr.status, data, token)); } catch (err) { reject(err); }
    };
    xhr.onerror = () => reject(networkError());
    xhr.send(formData);
  });
}

// --- Сессия пользователя ---
const initial = u => String(u?.full_name || u?.email || '?').trim().charAt(0).toUpperCase() || '?';
// Фото профиля: avatar_url с сервера, а пока его нет — сжатая копия в localStorage
// В ключе и дата регистрации: id удалённого аккаунта база может выдать новому
const avatarKey = u => `checkai_avatar_${u.id}_${u.created_at || ''}`;
const localAvatar = u => { try { return u ? localStorage.getItem(avatarKey(u)) : null; } catch (e) { return null; } };
const avatarSrc = u => u?.avatar_url || localAvatar(u);
function paintAvatar(el, u) {
  const src = avatarSrc(u);
  el.classList.toggle('has-photo', !!src);
  if (!src) { el.textContent = initial(u); return; }
  const img = new Image();
  img.src = src; img.alt = '';
  el.replaceChildren(img);
}
const avatarHtml = u => {
  const src = avatarSrc(u);
  return src ? `<div class="avatar has-photo" aria-hidden="true"><img src="${esc(src)}" alt=""></div>` : `<div class="avatar" aria-hidden="true">${esc(initial(u))}</div>`;
};
// created_at приходит без часового пояса (UTC) — добавляем Z, чтобы показать местное время
const serverDate = s => new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z');
const fmtDateTime = s => {
  if (!s) return '—';
  const d = serverDate(s);
  return Number.isNaN(+d) ? String(s) : d.toLocaleString(LOC(), { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
};
const showFormError = (id, msg) => { $(id).textContent = msg; $(id).hidden = false; };
function downloadFile(content, type, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], { type }));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function downloadJson(obj, filename) {
  const { iso, ...clean } = obj;
  downloadFile(JSON.stringify(clean, null, 2), 'application/json', filename);
}
function setUser(user) {
  currentUser = user;
  // Другой пользователь — рекомендации загружаем заново; тот же (после PATCH /api/auth/me) — оставляем
  if (insights.userId !== user.id) { resetInsights(user.id); loadInsights(); }
  rememberAccount(getToken(), user);
  const name = user.full_name || user.email;
  paintAvatar($('userAvatar'), user);
  $('userName').textContent = name;
  $('userEmail').textContent = user.email;
  $('userEmail').hidden = !user.full_name;
  $('userMenuBtn').setAttribute('aria-label', t('Меню аккаунта: {name}', { name }));
  $('loginBtn').hidden = true;
  $('userChip').hidden = false;
  $('navProfile').hidden = false;
  renderDrawerFoot();
  renderAccess(); renderAccountMenu(); renderProfile(); fillProfileForm();
  closeBudgetEditor(); loadBudgets(); loadCategories(); loadRules();
  applyUserLang(user);
}
const renderDrawerFoot = () => { $('drawerFoot').textContent = currentUser ? t('Чеки сохраняются в вашем аккаунте') : t('Войдите, чтобы сохранять чеки'); };
function setGuest() {
  currentUser = null; receipts = []; budgets = emptyBudgets(); budgetsLocal = false; categoryRules = [];
  resetInsights(null);
  closeAccountMenu(); closeReceipt(); closeBudgetEditor(); closeCategoryModals();
  loadCategories();
  $('loginBtn').hidden = false;
  $('userChip').hidden = true;
  $('navProfile').hidden = true;
  renderDrawerFoot();
  if ($('page-profile').classList.contains('active')) showPage('home');
  rerender();
}
// Гость видит только лендинг без шапки; вошедший — приложение с шапкой, главная у него — «Добавить чек».
// Пока при старте проверяется сохранённый токен, считаем пользователя вошедшим, чтобы лендинг не мигал
let authPending = !!getToken();
function renderAccess() {
  const guest = !currentUser && !(authPending && getToken());
  document.documentElement.dataset.auth = guest ? 'guest' : 'user';
  $('uploadGuest').hidden = !guest;
  $('uploadBody').hidden = guest;
  const onHome = $('page-home').classList.contains('active');
  if (guest && !onHome) showPage('home');
  else if (!guest && onHome) showPage('upload');
}
function rerender() { renderAccess(); renderHistory(); renderCategories(); renderAnalysis(); renderBudgets(); renderProfile(); renderInsights(); }
function logout() { if (currentUser) forgetAccount(currentUser.id); removeToken(); setGuest(); toast(t('Вы вышли из аккаунта')); }

// --- Смена аккаунта ---
async function switchAccount(id) {
  const acc = getAccounts().find(a => a.user.id === id), prev = getToken(), prevRefresh = getRefresh();
  if (!acc || acc.token === prev) return;
  const restore = () => { setToken(prev); setRefresh(prevRefresh); };
  closeAccountMenu(); closeReceipt(); setToken(acc.token); setRefresh(acc.refresh || null);
  try {
    const user = await apiRequest('/api/auth/me');
    setUser(user);
    await loadReceipts();
    toast(t('Вы вошли как {name}', { name: user.full_name || user.email }));
  } catch (e) {
    if (getToken()) { restore(); toast(t('Не удалось переключиться: {error}', { error: e.message })); return; }
    // Токен сохранённого аккаунта истёк: возвращаем прежний аккаунт и просим войти заново
    if (prev) { restore(); await checkAuthOnStartup(); }
    openAuth('login');
    $('authEmail').value = acc.user.email;
    $('authPassword').focus();
    toast(t('Сессия истекла, войдите снова'));
  }
}
function renderAccountMenu() {
  const others = getAccounts().filter(a => !currentUser || a.user.id !== currentUser.id);
  $('menuAccounts').innerHTML = others.length
    ? others.map(a => `<button class="menu-item" data-switch="${esc(a.user.id)}" type="button" role="menuitem">${avatarHtml(a.user)}<span>${esc(a.user.full_name || a.user.email)}</span></button>`).join('')
    : `<div class="menu-label">${esc(t('Других аккаунтов на устройстве нет'))}</div>`;
  $('menuAccounts').querySelectorAll('[data-switch]').forEach(b => b.onclick = () => switchAccount(+b.dataset.switch));
}
function openAccountMenu() { renderAccountMenu(); $('accountMenu').hidden = false; $('userMenuBtn').setAttribute('aria-expanded', 'true'); }
function closeAccountMenu() { $('accountMenu').hidden = true; $('userMenuBtn').setAttribute('aria-expanded', 'false'); }
$('userMenuBtn').onclick = e => { e.stopPropagation(); $('accountMenu').hidden ? openAccountMenu() : closeAccountMenu(); };
document.addEventListener('click', e => { if (!$('accountMenu').hidden && !e.target.closest('#userChip')) closeAccountMenu(); });
$('accountMenu').querySelectorAll('[data-menu]').forEach(b => b.onclick = () => {
  closeAccountMenu();
  const action = b.dataset.menu;
  if (action === 'profile') showPage('profile');
  else if (action === 'add') openAuth('login');
  else if (action === 'logout') logout();
});
