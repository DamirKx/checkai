'use strict';

// --- Справочники ---
const CATEGORIES = [['Продукты', '🥕'], ['Одежда', '🧥'], ['Дом и быт', '🏠'], ['Транспорт', '🚌'], ['Кафе', '☕'], ['Здоровье', '✚'], ['Развлечения', '🎟'], ['Другое', '◌']];
const CATEGORY_ICON = Object.fromEntries(CATEGORIES);
const keys = {
  'Продукты': ['картоф', 'молок', 'хлеб', 'яйц', 'сыр', 'мяс', 'рыб', 'куриц', 'овощ', 'фрукт', 'масло', 'рис', 'греч', 'сахар', 'мук', 'сок', 'вода', 'чай', 'кофе', 'макарон', 'йогурт', 'колбас', 'кефир', 'печен', 'шоколад', 'помидор', 'огур', 'яблок', 'банан', 'лук', 'закусоч'],
  'Одежда': ['кофт', 'футбол', 'брюк', 'джинс', 'куртк', 'пальто', 'плать', 'юбк', 'обув', 'ботин', 'кроссов', 'носк', 'шапк', 'одеж', 'рубаш', 'бель'],
  'Дом и быт': ['моющ', 'порош', 'шампун', 'мыло', 'бумаг', 'пакет', 'посуда', 'лампоч', 'бытов', 'губк', 'салфет'],
  'Транспорт': ['бензин', 'дизел', 'такси', 'проезд', 'метро', 'автобус', 'парков', 'топлив'],
  'Кафе': ['ресторан', 'кафе', 'обед', 'ужин', 'ланч', 'доставка еды', 'американо', 'капучино', 'эспрессо', 'бургер', 'пицца'],
  'Здоровье': ['аптек', 'лекар', 'витамин', 'таблет', 'медицин', 'клиник', 'бинт'],
  'Развлечения': ['кино', 'театр', 'музей', 'игра', 'подписк', 'концерт', 'билет'],
};

// Должно совпадать с MAX_UPLOAD_MB и ALLOWED_EXTENSIONS на сервере (server.py)
const MAX_UPLOAD_MB = 10;
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png', 'image/webp', 'image/bmp', 'image/tiff'];
const ALLOWED_EXT = /\.(jpe?g|png|webp|bmp|tiff?)$/i;
// Сколько чеков запрашивать за раз и сколько показывать в истории до «Показать ещё»
const RECEIPTS_PAGE = 200;
const HISTORY_PAGE = 20;
// Сервер не сообщает этапы распознавания: после загрузки через столько мс показываем «Анализ»
const OCR_STEP_MS = 5000;

// --- Утилиты ---
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(n) || 0) + ' ₸';
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const round2 = n => Math.round(num(n) * 100) / 100;
// Дата чека: YYYY-MM-DD или ДД.ММ.ГГГГ / ДД.ММ.ГГ
const parseDate = s => {
  if (!s) return null;
  s = String(s);
  let m = s.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  m = s.match(/(\d{2})[./-](\d{2})[./-](\d{4}|\d{2})(?!\d)/);
  if (!m) return null;
  const y = +m[3] < 100 ? 2000 + +m[3] : +m[3];
  return new Date(y, +m[2] - 1, +m[1]);
};
const pad2 = n => String(n).padStart(2, '0');
const isoDay = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const toIsoDate = s => { const d = parseDate(s); return d && !Number.isNaN(+d) ? isoDay(d) : ''; };
const toTime = s => { const m = String(s || '').match(/(\d{1,2}):(\d{2})/); return m ? `${pad2(m[1])}:${m[2]}` : ''; };
// Дата покупки для показа: «29.07.2025 · 16:36»
const fmtPurchase = r => {
  const d = parseDate(r.date);
  const day = d && !Number.isNaN(+d) ? d.toLocaleDateString('ru-RU') : (r.date || '');
  return [day, r.time].filter(Boolean).join(' · ');
};
// Для фильтров по периодам берём дату покупки, а если её нет — дату сохранения
const receiptIso = r => { const d = parseDate(r.date); return d && !Number.isNaN(+d) ? d.toISOString() : r.created_at; };
const guessCategory = name => {
  const n = String(name || '').toLowerCase();
  for (const [cat, words] of Object.entries(keys)) if (words.some(w => n.includes(w))) return cat;
  return 'Другое';
};
// Категорию хранит сервер; угадываем по названию только у старых чеков без неё
const categoryOf = it => it.category || guessCategory(it.name);
// Базовые категории + те, что пришли с сервера и которых нет в списке
const categoryNames = (extra = []) => {
  const names = CATEGORIES.map(([n]) => n);
  extra.forEach(c => { if (c && !names.includes(c)) names.splice(names.length - 1, 0, c); });
  return names;
};
const itemsSum = items => round2((items || []).reduce((s, i) => s + num(i.total_price), 0));

let receipts = [], lastResult = null, selectedFile = null, currentUser = null, pendingSave = false, authMode = 'login';
let analyzing = false, historyShown = HISTORY_PAGE;

// --- Тема ---
const THEME_KEY = 'checkai_theme';
function applyTheme(theme, save) {
  document.documentElement.dataset.theme = theme;
  if (save) { try { localStorage.setItem(THEME_KEY, theme); } catch (e) {} }
  const light = theme === 'light';
  $('themeIcon').textContent = light ? '☾' : '☀';
  $('themeBtn').setAttribute('aria-label', light ? 'Включить тёмную тему' : 'Включить светлую тему');
  $('themeBtn').title = light ? 'Тёмная тема' : 'Светлая тема';
  document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
}
$('themeBtn').onclick = () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light', true);
applyTheme(document.documentElement.dataset.theme || 'dark', false);

// --- API и токен ---
const TOKEN_KEY = 'checkai_token';
const getToken = () => { try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; } };
const setToken = t => { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) {} };
const removeToken = () => { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} };
// Сохранённые на устройстве аккаунты для быстрого переключения: [{token,user}]
const ACCOUNTS_KEY = 'checkai_accounts';
const getAccounts = () => {
  try {
    const list = JSON.parse(localStorage.getItem(ACCOUNTS_KEY));
    return Array.isArray(list) ? list.filter(a => a && a.token && a.user) : [];
  } catch (e) { return []; }
};
const saveAccounts = list => { try { localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(list.slice(0, 5))); } catch (e) {} };
const rememberAccount = (token, user) => { if (token && user) saveAccounts([{ token, user }, ...getAccounts().filter(a => a.user.id !== user.id)]); };
const forgetAccount = id => saveAccounts(getAccounts().filter(a => a.user.id !== id));
const forgetToken = token => saveAccounts(getAccounts().filter(a => a.token !== token));
// У ошибки есть поле status, чтобы отличать «нет такого эндпоинта» (404/405) от остальных
const notSupported = e => e.status === 404 || e.status === 405;

const STATUS_TEXT = {
  413: `Файл слишком большой. Максимум — ${MAX_UPLOAD_MB} МБ`,
  415: 'Неподдерживаемый формат файла. Подойдут JPG, PNG, WEBP, BMP или TIFF',
  422: 'Проверьте введённые данные',
  429: 'Слишком много запросов. Подождите немного и попробуйте снова',
  500: 'Ошибка на сервере. Попробуйте ещё раз позже',
  502: 'Сервер временно недоступен. Попробуйте позже',
  503: 'Сервер временно недоступен. Попробуйте позже',
  504: 'Сервер не ответил вовремя. Попробуйте ещё раз',
};
const FIELD_NAMES = { email: 'Email', password: 'Пароль', full_name: 'Имя', name: 'Название товара', store: 'Магазин', category: 'Категория', total: 'Итог', quantity: 'Количество', price_per_unit: 'Цена', total_price: 'Сумма' };
const hasCyrillic = s => /[а-яё]/i.test(s);
// Ошибки валидации FastAPI приходят списком на английском или с приставкой «Value error,»
function validationMessage(d) {
  const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : '';
  const msg = String(d.msg || '').replace(/^Value error,\s*/i, '');
  if (hasCyrillic(msg)) return msg;
  if (field === 'email') return 'Введите корректный email (например, user@example.com)';
  const label = FIELD_NAMES[field] || 'Поле';
  if (d.type === 'missing') return `${label}: обязательное поле`;
  if (d.type === 'string_too_long') return `${label}: слишком длинное значение`;
  if (d.type === 'string_too_short') return `${label}: не может быть пустым`;
  return `${label}: некорректное значение`;
}
function makeError(status, data) {
  const detail = Array.isArray(data.detail) ? data.detail.map(validationMessage).join('; ') : data.detail;
  const err = new Error((typeof detail === 'string' && detail) || STATUS_TEXT[status] || `Ошибка сервера (${status})`);
  err.status = status;
  return err;
}
function handleResponse(status, data, token) {
  // Токен истёк или недействителен — переходим в гостевой режим
  if (status === 401 && token) { forgetToken(token); removeToken(); setGuest(); }
  if (status < 200 || status >= 300) throw makeError(status, data);
  return data;
}
const networkError = () => Object.assign(new Error('Нет связи с сервером. Проверьте подключение и попробуйте снова'), { status: 0 });

async function apiRequest(url, options = {}) {
  const token = getToken();
  options.headers = options.headers || {};
  if (token) options.headers['Authorization'] = `Bearer ${token}`;
  if (options.body && !(options.body instanceof FormData) && !options.headers['Content-Type']) options.headers['Content-Type'] = 'application/json';
  let response;
  try { response = await fetch(url, options); } catch (e) { throw networkError(); }
  const data = await response.json().catch(() => ({}));
  return handleResponse(response.status, data, token);
}
// Загрузка файла через XHR: fetch не умеет сообщать прогресс отправки
function apiUpload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(), token = getToken();
    xhr.open('POST', url);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
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
// created_at приходит без часового пояса (UTC) — добавляем Z, чтобы показать местное время
const fmtDateTime = s => {
  if (!s) return '—';
  const d = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z');
  return Number.isNaN(+d) ? String(s) : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
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
  rememberAccount(getToken(), user);
  const name = user.full_name || user.email;
  $('userAvatar').textContent = initial(user);
  $('userName').textContent = name;
  $('userEmail').textContent = user.email;
  $('userEmail').hidden = !user.full_name;
  $('userMenuBtn').setAttribute('aria-label', 'Меню аккаунта: ' + name);
  $('loginBtn').hidden = true;
  $('userChip').hidden = false;
  $('navProfile').hidden = false;
  $('drawerFoot').textContent = 'Чеки сохраняются в вашем аккаунте';
  renderAccountMenu(); renderProfile(); fillProfileForm();
}
function setGuest() {
  currentUser = null; receipts = [];
  closeAccountMenu(); closeReceipt();
  $('loginBtn').hidden = false;
  $('userChip').hidden = true;
  $('navProfile').hidden = true;
  $('drawerFoot').textContent = 'Войдите, чтобы сохранять чеки';
  if ($('page-profile').classList.contains('active')) showPage('upload');
  rerender();
}
function rerender() { renderHistory(); renderCategories(); renderAnalysis(); renderProfile(); }
function logout() { if (currentUser) forgetAccount(currentUser.id); removeToken(); setGuest(); toast('Вы вышли из аккаунта'); }

// --- Смена аккаунта ---
async function switchAccount(id) {
  const acc = getAccounts().find(a => a.user.id === id), prev = getToken();
  if (!acc || acc.token === prev) return;
  closeAccountMenu(); closeReceipt(); setToken(acc.token);
  try {
    const user = await apiRequest('/api/auth/me');
    setUser(user);
    await loadReceipts();
    toast('Вы вошли как ' + (user.full_name || user.email));
  } catch (e) {
    if (getToken()) { setToken(prev); toast('Не удалось переключиться: ' + e.message); return; }
    // Токен сохранённого аккаунта истёк: возвращаем прежний аккаунт и просим войти заново
    if (prev) { setToken(prev); await checkAuthOnStartup(); }
    openAuth('login');
    $('authEmail').value = acc.user.email;
    $('authPassword').focus();
    toast('Сессия истекла, войдите снова');
  }
}
function renderAccountMenu() {
  const others = getAccounts().filter(a => !currentUser || a.user.id !== currentUser.id);
  $('menuAccounts').innerHTML = others.length
    ? others.map(a => `<button class="menu-item" data-switch="${esc(a.user.id)}" type="button" role="menuitem"><div class="avatar" aria-hidden="true">${esc(initial(a.user))}</div><span>${esc(a.user.full_name || a.user.email)}</span></button>`).join('')
    : '<div class="menu-label">Других аккаунтов на устройстве нет</div>';
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

// --- Профиль: просмотр, редактирование, удаление ---
function renderProfile() {
  if (!currentUser) return;
  const u = currentUser;
  $('profileAvatar').textContent = initial(u);
  $('profileName').textContent = u.full_name || 'Без имени';
  $('profileEmail').textContent = u.email;
  $('profileCreated').textContent = fmtDateTime(u.created_at);
  $('profileReceipts').textContent = receipts.length;
  $('profileSpent').textContent = money(receipts.reduce((s, r) => s + num(r.total), 0));
  $('profileAccounts').innerHTML = getAccounts().map(a => `<div class="account-row"><div class="avatar" aria-hidden="true">${esc(initial(a.user))}</div><div><b>${esc(a.user.full_name || a.user.email)}</b><small>${esc(a.user.email)}</small></div>${a.user.id === u.id ? '<span class="pill">Текущий</span>' : `<button class="text-btn" data-switch="${esc(a.user.id)}" type="button">Перейти</button><button class="text-btn" data-forget="${esc(a.user.id)}" type="button" title="Убрать с этого устройства" aria-label="Убрать аккаунт ${esc(a.user.email)} с этого устройства">✕</button>`}</div>`).join('');
  $('profileAccounts').querySelectorAll('[data-switch]').forEach(b => b.onclick = () => switchAccount(+b.dataset.switch));
  $('profileAccounts').querySelectorAll('[data-forget]').forEach(b => b.onclick = () => { forgetAccount(+b.dataset.forget); renderProfile(); renderAccountMenu(); });
}
function fillProfileForm() {
  if (!currentUser) return;
  $('profileForm').reset();
  $('editName').value = currentUser.full_name || '';
  $('editEmail').value = currentUser.email;
  $('profileError').hidden = true;
  $('deleteForm').reset();
  $('deleteError').hidden = true;
}
$('profileReset').onclick = fillProfileForm;
$('addAccountBtn').onclick = () => openAuth('login');
$('profileForm').onsubmit = async e => {
  e.preventDefault();
  const u = currentUser;
  if (!u) return;
  $('profileError').hidden = true;
  const name = $('editName').value.trim(), email = $('editEmail').value.trim().toLowerCase(), password = $('editPassword').value, current = $('editCurrentPassword').value, body = {};
  if (name !== (u.full_name || '')) body.full_name = name || null;
  if (email !== u.email) body.email = email;
  if (password) body.new_password = password;
  if (!Object.keys(body).length) { showFormError('profileError', 'Вы ничего не изменили'); return; }
  if ((body.email || body.new_password) && !current) { showFormError('profileError', 'Введите текущий пароль, чтобы сменить email или пароль'); return; }
  if (current) body.current_password = current;
  const btn = $('profileSave');
  btn.disabled = true;
  try {
    const data = await apiRequest('/api/auth/me', { method: 'PATCH', body: JSON.stringify(body) });
    if (data.access_token) setToken(data.access_token);
    setUser(data.user || data);
    toast('Данные аккаунта обновлены');
  } catch (err) {
    showFormError('profileError', notSupported(err) ? 'Сервер пока не поддерживает редактирование аккаунта (нужен PATCH /api/auth/me).' : err.message);
  } finally { btn.disabled = false; }
};
$('deleteForm').onsubmit = async e => {
  e.preventDefault();
  const u = currentUser;
  if (!u) return;
  $('deleteError').hidden = true;
  if (!confirm(`Удалить аккаунт ${u.email} и все его чеки? Это действие нельзя отменить.`)) return;
  const btn = $('deleteSubmit');
  btn.disabled = true;
  try {
    await apiRequest('/api/auth/me', { method: 'DELETE', body: JSON.stringify({ password: $('deletePassword').value }) });
    forgetAccount(u.id); removeToken(); setGuest(); showPage('upload');
    toast('Аккаунт удалён');
  } catch (err) {
    showFormError('deleteError', notSupported(err) ? 'Сервер пока не поддерживает удаление аккаунта (нужен DELETE /api/auth/me).' : err.message);
  } finally { btn.disabled = false; }
};

// --- Редактор чека: черновик и форма ---
// Черновик — копия чека, которую меняет форма; на сервер уходит через toPayload
function toDraft(src) {
  return {
    id: src.id ?? null,
    store: src.store || '',
    date: toIsoDate(src.date),
    time: toTime(src.time) || toTime(src.date),
    total: round2(src.total),
    category: src.category || null,
    image_path: src.image_path || null,
    items: (src.items || []).map(it => {
      const quantity = it.quantity == null ? 1 : num(it.quantity);
      const total = it.total_price == null ? round2(quantity * num(it.price_per_unit)) : round2(it.total_price);
      const price = it.price_per_unit == null ? round2(quantity ? total / quantity : total) : round2(it.price_per_unit);
      return { name: it.name || '', quantity, price_per_unit: price, total_price: total, category: categoryOf(it) };
    }),
  };
}
// Категория чека — та, на которую в нём больше всего потрачено
function mainCategory(items, fallback) {
  const sums = {};
  items.forEach(i => { sums[i.category] = (sums[i.category] || 0) + num(i.total_price); });
  return Object.entries(sums).sort((a, b) => b[1] - a[1])[0]?.[0] || fallback || null;
}
function toPayload(draft) {
  const items = draft.items
    .filter(i => i.name.trim())
    .map(i => ({ name: i.name.trim(), quantity: num(i.quantity), price_per_unit: round2(i.price_per_unit), total_price: round2(i.total_price), category: i.category || null }));
  return {
    store: draft.store.trim() || null,
    date: draft.date || null,
    time: draft.time || null,
    category: mainCategory(items, draft.category),
    total: round2(draft.total),
    image_path: draft.image_path,
    items,
  };
}
// Текст ошибки или null, если черновик можно сохранять
function draftProblem(draft) {
  const unnamed = draft.items.findIndex(i => !i.name.trim() && num(i.total_price));
  if (unnamed >= 0) return { message: `У позиции №${unnamed + 1} нет названия`, index: unnamed };
  if (draft.items.some(i => num(i.quantity) < 0 || num(i.price_per_unit) < 0 || num(i.total_price) < 0) || num(draft.total) < 0) return { message: 'Суммы и количество не могут быть отрицательными' };
  return null;
}
const numInput = (key, value, label) => `<label>${label}<input class="field" type="number" min="0" step="any" inputmode="decimal" data-k="${key}" value="${esc(value)}"></label>`;
function mountEditor(root, draft) {
  root.innerHTML = `<div class="editor">
    <div class="edit-head">
      <label>Магазин<input class="field" data-f="store" maxlength="255" value="${esc(draft.store)}" placeholder="Название магазина"></label>
      <label>Дата покупки<input class="field" type="date" data-f="date" value="${esc(draft.date)}"></label>
      <label>Время<input class="field" type="time" data-f="time" value="${esc(draft.time)}"></label>
    </div>
    <div class="edit-items" data-items></div>
    <div><button class="btn soft small" type="button" data-add>＋ Добавить позицию</button></div>
    <div class="edit-foot">
      <label>Итог чека, ₸<input class="field edit-total" type="number" min="0" step="any" inputmode="decimal" data-f="total" value="${esc(draft.total)}"></label>
      <div class="edit-sum" data-sum aria-live="polite"></div>
    </div>
  </div>`;
  // Обработчики вешаем на новый элемент, чтобы повторный mountEditor не дублировал их
  const editor = root.firstElementChild;
  const list = editor.querySelector('[data-items]'), sumBox = editor.querySelector('[data-sum]'), totalInput = editor.querySelector('[data-f="total"]');
  const renderItems = () => {
    const cats = categoryNames(draft.items.map(i => i.category));
    list.innerHTML = draft.items.length ? draft.items.map((it, i) => `<div class="edit-item" data-i="${i}">
      <div class="edit-name">
        <label>Название<input class="field" data-k="name" maxlength="500" value="${esc(it.name)}" placeholder="Товар или услуга"></label>
        <button class="icon-btn remove-item" type="button" data-remove aria-label="Удалить позицию ${esc(it.name || '№' + (i + 1))}">✕</button>
      </div>
      <div class="edit-nums">
        ${numInput('quantity', it.quantity, 'Кол-во')}${numInput('price_per_unit', it.price_per_unit, 'Цена, ₸')}${numInput('total_price', it.total_price, 'Сумма, ₸')}
        <label>Категория<select class="field" data-k="category">${cats.map(c => `<option ${c === it.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
      </div>
    </div>`).join('') : '<div class="empty">Позиций нет. Добавьте их вручную или оставьте только итог.</div>';
    updateSum();
  };
  const updateSum = () => {
    const sum = itemsSum(draft.items), mismatch = draft.items.length && Math.abs(sum - num(draft.total)) >= 1;
    sumBox.innerHTML = `Сумма позиций: <b>${money(sum)}</b>` + (mismatch ? ` — не совпадает с итогом. <button class="text-btn" type="button" data-fix-total>Сделать итогом</button>` : '');
  };
  editor.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.f) {
      draft[t.dataset.f] = t.dataset.f === 'total' ? num(t.value) : t.value;
      if (t.dataset.f === 'total') updateSum();
      return;
    }
    const row = t.closest('[data-i]');
    if (!row || !t.dataset.k) return;
    const item = draft.items[+row.dataset.i], k = t.dataset.k;
    item[k] = k === 'name' || k === 'category' ? t.value : num(t.value);
    // Количество или цена поменялись — пересчитываем сумму строки
    if (k === 'quantity' || k === 'price_per_unit') {
      item.total_price = round2(num(item.quantity) * num(item.price_per_unit));
      row.querySelector('[data-k="total_price"]').value = item.total_price;
    }
    if (k !== 'name' && k !== 'category') updateSum();
  });
  editor.addEventListener('change', e => {
    const t = e.target, row = t.closest('[data-i]');
    // Новой позиции подбираем категорию по названию, пока пользователь не выбрал её сам
    if (row && t.dataset.k === 'name') {
      const item = draft.items[+row.dataset.i];
      if (item.autoCategory) {
        item.category = guessCategory(item.name);
        row.querySelector('[data-k="category"]').value = item.category;
      }
    }
    if (row && t.dataset.k === 'category') delete draft.items[+row.dataset.i].autoCategory;
  });
  editor.addEventListener('click', e => {
    if (e.target.closest('[data-add]')) {
      draft.items.push({ name: '', quantity: 1, price_per_unit: 0, total_price: 0, category: 'Другое', autoCategory: true });
      renderItems();
      list.querySelector(`[data-i="${draft.items.length - 1}"] [data-k="name"]`).focus();
    } else if (e.target.closest('[data-remove]')) {
      draft.items.splice(+e.target.closest('[data-i]').dataset.i, 1);
      renderItems();
    } else if (e.target.closest('[data-fix-total]')) {
      draft.total = itemsSum(draft.items);
      totalInput.value = draft.total;
      updateSum();
      totalInput.focus();
    }
  });
  renderItems();
}
function focusDraftProblem(root, problem) {
  if (problem.index == null) return;
  root.querySelector(`[data-i="${problem.index}"] [data-k="name"]`)?.focus();
}

// --- Подробности покупки (чека) ---
let openedReceipt = null, editDraft = null;
function openReceipt(id) {
  const r = receipts.find(x => String(x.id) === String(id));
  if (!r) return;
  openedReceipt = r;
  const items = r.items || [];
  $('rmView').hidden = false;
  $('rmEditView').hidden = true;
  $('rmStore').textContent = r.store || 'Магазин не определён';
  $('rmDate').textContent = fmtPurchase(r) || 'Дата покупки не распознана';
  $('rmTotal').textContent = money(r.total);
  $('rmMeta').innerHTML = [r.category, items.length + ' позиций', 'Сохранён ' + fmtDateTime(r.created_at)].filter(Boolean).map(t => `<span class="pill">${esc(t)}</span>`).join('');
  const photo = $('rmPhoto');
  if (r.image_path) { photo.onerror = () => { photo.hidden = true; }; photo.src = r.image_path; photo.hidden = false; }
  else { photo.hidden = true; photo.removeAttribute('src'); }
  $('rmItems').innerHTML = items.length
    ? items.map(it => `<div class="item"><div><strong>${esc(it.name)}</strong><small>${esc(it.quantity ?? 1)} × ${money(it.price_per_unit ?? it.total_price)}</small><span class="item-cat">${esc(categoryOf(it))}</span></div><div style="text-align:right"><strong>${money(it.total_price)}</strong></div></div>`).join('')
    : '<div class="empty">В чеке нет товарных позиций</div>';
  // Подсказка, если распознавание потеряло или исказило строку
  const sum = itemsSum(items);
  $('rmWarn').hidden = !(items.length && Math.abs(sum - num(r.total)) >= 1);
  $('rmWarn').textContent = `Сумма позиций (${money(sum)}) не совпадает с итогом чека (${money(r.total)}). Возможно, распознавание пропустило или исказило строку — исправьте через «Редактировать».`;
  openModal('receiptModal', $('receiptClose'));
}
function closeReceipt() { closeModal('receiptModal'); openedReceipt = null; editDraft = null; }
function startEditReceipt() {
  if (!openedReceipt) return;
  editDraft = toDraft(openedReceipt);
  mountEditor($('rmEditor'), editDraft);
  $('rmEditError').hidden = true;
  $('rmView').hidden = true;
  $('rmEditView').hidden = false;
  $('rmEditor').querySelector('[data-f="store"]').focus();
}
async function saveEditedReceipt() {
  if (!openedReceipt || !editDraft) return;
  $('rmEditError').hidden = true;
  const problem = draftProblem(editDraft);
  if (problem) { showFormError('rmEditError', problem.message); focusDraftProblem($('rmEditor'), problem); return; }
  const id = openedReceipt.id, btn = $('rmEditSave');
  btn.disabled = true;
  try {
    await apiRequest('/api/receipts/' + id, { method: 'PUT', body: JSON.stringify(toPayload(editDraft)) });
    await loadReceipts();
    toast('Чек обновлён');
    openReceipt(id);
  } catch (err) {
    // У /api/receipts/{id} уже есть DELETE, поэтому без PUT сервер ответит 405
    if (err.status === 405) showFormError('rmEditError', 'Сервер пока не поддерживает редактирование чеков (нужен PUT /api/receipts/{id}).');
    else if (err.status === 404) { showFormError('rmEditError', 'Чек не найден — возможно, его уже удалили.'); loadReceipts(); }
    else showFormError('rmEditError', err.message);
  } finally { btn.disabled = false; }
}
$('receiptClose').onclick = closeReceipt;
$('receiptModal').onclick = e => { if (e.target.id === 'receiptModal') closeReceipt(); };
$('rmEdit').onclick = startEditReceipt;
$('rmEditSave').onclick = saveEditedReceipt;
$('rmEditCancel').onclick = () => { if (openedReceipt) openReceipt(openedReceipt.id); };
$('rmDownload').onclick = () => { if (openedReceipt) downloadJson(openedReceipt, `receipt_${openedReceipt.id}.json`); };
$('rmDelete').onclick = async () => { if (openedReceipt && await deleteReceipt(openedReceipt.id)) closeReceipt(); };
async function deleteReceipt(id) {
  if (!confirm('Удалить этот чек из истории?')) return false;
  try { await apiRequest('/api/receipts/' + id, { method: 'DELETE' }); await loadReceipts(); toast('Чек удалён'); return true; }
  catch (e) { toast('Не удалось удалить: ' + e.message); return false; }
}
// История загружается порциями, чтобы не упереться в лимит сервера на один запрос
async function fetchAllReceipts() {
  const all = [];
  for (let skip = 0; ; skip += RECEIPTS_PAGE) {
    const page = await apiRequest(`/api/receipts?skip=${skip}&limit=${RECEIPTS_PAGE}`);
    all.push(...page);
    if (page.length < RECEIPTS_PAGE) return all;
  }
}
async function loadReceipts() {
  if (!getToken()) { receipts = []; rerender(); return; }
  try {
    receipts = (await fetchAllReceipts()).map(r => ({ ...r, iso: receiptIso(r) }));
    rerender();
  } catch (e) { toast('Не удалось загрузить историю: ' + e.message); }
}
async function checkAuthOnStartup() {
  if (!getToken()) { setGuest(); return; }
  try { setUser(await apiRequest('/api/auth/me')); await loadReceipts(); }
  catch (e) { setGuest(); if (getToken()) toast('Не удалось связаться с сервером'); }
}

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
  const modal = [...document.querySelectorAll('.modal-backdrop')].find(m => !m.hidden);
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
  $('authTitle').textContent = register ? 'Регистрация' : 'Вход';
  $('nameField').hidden = !register;
  $('authSubmit').textContent = register ? 'Зарегистрироваться' : 'Войти';
  $('authPassword').autocomplete = register ? 'new-password' : 'current-password';
  // Требования к паролю — только при регистрации: старые аккаунты могут иметь короткий пароль
  if (register) { $('authPassword').minLength = 8; $('authPassword').maxLength = 72; }
  else { $('authPassword').removeAttribute('minlength'); $('authPassword').removeAttribute('maxlength'); }
  $('authPasswordHint').hidden = !register;
  $('authError').hidden = true;
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
    setToken(data.access_token);
    setUser(data.user);
    const wasPending = pendingSave;
    closeAuth();
    $('authForm').reset();
    toast(isLogin ? 'Добро пожаловать!' : 'Регистрация успешна!');
    await loadReceipts();
    if (wasPending) await saveReceipt();
  } catch (err) {
    $('authError').textContent = err.message;
    $('authError').hidden = false;
  } finally { btn.disabled = false; }
};

// --- Навигация ---
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
  document.querySelectorAll('.nav-link').forEach(b => {
    const on = b.dataset.page === name;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  closeMenu();
  if (name === 'history') renderHistory();
  if (name === 'categories') renderCategories();
  if (name === 'analysis') renderAnalysis();
  if (name === 'profile') { renderProfile(); fillProfileForm(); }
}
function setMenu(open) {
  $('drawer').classList.toggle('open', open);
  $('overlay').classList.toggle('show', open);
  // Закрытое меню уезжает за экран — убираем его из порядка Tab
  $('drawer').inert = !open;
  $('menuBtn').setAttribute('aria-expanded', open);
  $('menuBtn').setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
  if (open) $('drawer').querySelector('.nav-link.active, .nav-link')?.focus();
}
function closeMenu() { if ($('drawer').classList.contains('open')) setMenu(false); }
$('menuBtn').onclick = () => setMenu(!$('drawer').classList.contains('open'));
$('overlay').onclick = closeMenu;
document.querySelectorAll('.nav-link').forEach(b => b.onclick = () => showPage(b.dataset.page));
document.addEventListener('keydown', e => {
  if (e.key === 'Tab') { trapFocus(e); return; }
  if (e.key !== 'Escape') return;
  if (!$('receiptModal').hidden) closeReceipt();
  else if (!$('authModal').hidden) closeAuth();
  else if (!$('accountMenu').hidden) { closeAccountMenu(); $('userMenuBtn').focus(); }
  else if ($('drawer').classList.contains('open')) { closeMenu(); $('menuBtn').focus(); }
});
let toastTimer = null;
function toast(msg) {
  $('toast').textContent = msg;
  $('toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('show'), 2800);
}

// --- Загрузка фото ---
function showUploadMessage(msg) { $('uploadMessage').textContent = msg; $('uploadMessage').hidden = !msg; }
// Проверяем файл до отправки, чтобы не ждать ответа сервера 413/415
function fileProblem(file) {
  if (/hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(file.name)) return 'Формат HEIC (фото с iPhone) не поддерживается. Сохраните фото как JPG или включите в настройках камеры формат «Наиболее совместимый».';
  if (!(ALLOWED_TYPES.includes(file.type) || (!file.type && ALLOWED_EXT.test(file.name)))) return 'Это не изображение чека. Подойдут JPG, PNG, WEBP, BMP или TIFF.';
  if (!file.size) return 'Файл пустой. Выберите другое фото.';
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `Файл весит ${(file.size / 1024 / 1024).toFixed(1).replace('.', ',')} МБ, а можно не больше ${MAX_UPLOAD_MB} МБ. Уменьшите фото или снимите с меньшим разрешением.`;
  return null;
}
function selectFile(file) {
  if (analyzing) return;
  const problem = fileProblem(file);
  if (problem) { showUploadMessage(problem); return; }
  selectedFile = file;
  if ($('imagePreview').src.startsWith('blob:')) URL.revokeObjectURL($('imagePreview').src);
  $('imagePreview').src = URL.createObjectURL(file);
  $('fileName').textContent = file.name;
  $('dropPrompt').hidden = true;
  $('previewBox').hidden = false;
  showUploadMessage('');
  $('analyzeBtn').focus();
}
function clearFile() {
  selectedFile = null;
  $('fileInput').value = '';
  $('cameraInput').value = '';
  $('previewBox').hidden = true;
  $('dropPrompt').hidden = false;
}
$('maxSizeText').textContent = MAX_UPLOAD_MB;
$('chooseBtn').onclick = () => $('fileInput').click();
$('cameraBtn').onclick = () => $('cameraInput').click();
$('dropzone').onclick = e => {
  if (analyzing || e.target.closest('button') || e.target.tagName === 'INPUT' || !$('previewBox').hidden) return;
  $('fileInput').click();
};
$('fileInput').onchange = e => { const f = e.target.files[0]; if (f) selectFile(f); };
$('cameraInput').onchange = e => { const f = e.target.files[0]; if (f) selectFile(f); };
$('removeBtn').onclick = clearFile;
const dz = $('dropzone');
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, x => { x.preventDefault(); if (!analyzing) dz.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, x => { x.preventDefault(); dz.classList.remove('drag'); }));
dz.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) selectFile(f); });

// --- Распознавание: этапы и блокировка кнопок ---
const STEPS = ['upload', 'ocr', 'ai'];
let stepsTimer = null;
function setStep(step, fraction) {
  const idx = STEPS.indexOf(step);
  $('analyzeSteps').querySelectorAll('li').forEach((li, i) => {
    li.classList.toggle('done', i < idx);
    li.classList.toggle('active', i === idx);
  });
  $('stepsBar').style.width = Math.round((idx + Math.min(fraction, .95)) / STEPS.length * 100) + '%';
}
function startSteps(withUpload) {
  const started = Date.now();
  let uploadedAt = withUpload ? null : started, uploadFraction = 0;
  $('analyzeSteps').hidden = false;
  const tick = () => {
    const seconds = Math.round((Date.now() - started) / 1000);
    if (uploadedAt == null) {
      setStep('upload', uploadFraction);
      $('stepsNote').textContent = `Отправляем фото… ${Math.round(uploadFraction * 100)}%`;
      return;
    }
    const sinceUpload = Date.now() - uploadedAt;
    if (sinceUpload < OCR_STEP_MS) setStep('ocr', sinceUpload / OCR_STEP_MS);
    else setStep('ai', 1 - Math.exp(-(sinceUpload - OCR_STEP_MS) / 30000));
    $('stepsNote').textContent = `Прошло ${seconds} с. Обычно распознавание занимает до минуты — не закрывайте страницу.`;
  };
  tick();
  stepsTimer = setInterval(tick, 400);
  return {
    progress(fraction, done) { uploadFraction = fraction; if (done && uploadedAt == null) uploadedAt = Date.now(); tick(); },
  };
}
function stopSteps() { clearInterval(stepsTimer); stepsTimer = null; $('analyzeSteps').hidden = true; }
function setAnalyzing(on, test) {
  analyzing = on;
  ['analyzeBtn', 'removeBtn', 'testBtn', 'chooseBtn', 'cameraBtn'].forEach(id => { $(id).disabled = on; });
  $('dropzone').classList.toggle('busy', on);
  $('dropzone').setAttribute('aria-busy', on);
  $('analyzeBtn').textContent = on && !test ? 'Распознаём…' : 'Распознать чек';
  $('testBtn').textContent = on && test ? 'Распознаём демо-чек…' : 'Попробовать на демо-чеке →';
}
async function analyze(test = false) {
  if (analyzing || (!test && !selectedFile)) return;
  setAnalyzing(true, test);
  showUploadMessage('');
  const steps = startSteps(!test);
  try {
    let payload;
    if (test) payload = await apiRequest('/api/analyze-test', { method: 'POST' });
    else {
      const fd = new FormData();
      fd.append('file', selectedFile);
      payload = await apiUpload('/api/analyze', fd, steps.progress);
    }
    if (payload.success === false) throw new Error(payload.error || 'Не удалось распознать чек');
    renderResult(payload.data ?? payload);
  } catch (e) {
    showUploadMessage('Не получилось распознать чек: ' + e.message);
  } finally {
    stopSteps();
    setAnalyzing(false, test);
  }
}
$('analyzeBtn').onclick = () => analyze();
$('testBtn').onclick = () => analyze(true);
window.addEventListener('beforeunload', e => { if (analyzing) e.preventDefault(); });

// --- Результат распознавания ---
function renderResult(data) {
  lastResult = toDraft(data);
  $('resultPlaceholder').hidden = true;
  $('resultContent').hidden = false;
  mountEditor($('resultEditor'), lastResult);
  if (window.matchMedia('(max-width: 850px)').matches) $('resultCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
// Гость может распознавать чеки, а для сохранения просим войти
async function saveReceipt() {
  if (!lastResult) return;
  const problem = draftProblem(lastResult);
  if (problem) { toast(problem.message); focusDraftProblem($('resultEditor'), problem); return; }
  if (!getToken()) { pendingSave = true; openAuth('login'); toast('Войдите, чтобы сохранить чек'); return; }
  const btn = $('saveReceiptBtn');
  btn.disabled = true;
  try {
    await apiRequest('/api/receipts/save', { method: 'POST', body: JSON.stringify(toPayload(lastResult)) });
    await loadReceipts();
    toast('Чек сохранён в ваш аккаунт');
    showPage('history');
  } catch (e) {
    if (!getToken()) { pendingSave = true; openAuth('login'); }
    toast('Ошибка при сохранении: ' + e.message);
  } finally { btn.disabled = false; }
}
$('saveReceiptBtn').onclick = saveReceipt;
$('downloadBtn').onclick = () => { if (lastResult) downloadJson(toPayload(lastResult), 'receipt_data.json'); };

// --- История: фильтры, поиск, «Показать ещё», экспорт ---
const inPeriod = (r, p) => {
  if (p === 'all') return true;
  const d = new Date(r.iso || '');
  if (Number.isNaN(+d)) return false;
  const now = new Date();
  if (p === 'year') return d.getFullYear() === now.getFullYear();
  if (p === 'month') return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  return d >= start && d <= now;
};
const filtered = (p = 'all') => receipts.filter(r => inPeriod(r, p));
const matchesSearch = (r, q) => !q || String(r.store || '').toLowerCase().includes(q) || (r.items || []).some(i => String(i.name || '').toLowerCase().includes(q));
const historyList = () => {
  const q = $('historySearch').value.trim().toLowerCase();
  return filtered($('historyPeriod').value).filter(r => matchesSearch(r, q));
};
function renderHistory() {
  const list = historyList(), total = list.reduce((s, r) => s + num(r.total), 0), searching = !!$('historySearch').value.trim();
  $('historyTotal').textContent = money(total);
  $('historyCount').textContent = list.length;
  $('historyAverage').textContent = money(list.length ? total / list.length : 0);
  $('exportCsvBtn').disabled = !list.length;
  const shown = list.slice(0, historyShown);
  let empty;
  if (!currentUser) empty = '<div class="card empty">История хранится в аккаунте. <button class="text-btn" data-open-auth type="button">Войдите или зарегистрируйтесь</button>, чтобы сохранять чеки.</div>';
  else if (searching) empty = '<div class="card empty">Ничего не найдено. Попробуйте другое название магазина или товара.</div>';
  else empty = '<div class="card empty">Пока нет чеков за этот период. Загрузите чек, чтобы начать историю.</div>';
  $('historyList').innerHTML = shown.length
    ? shown.map(r => `<article class="card receipt-row" data-open="${esc(r.id)}" tabindex="0" aria-label="Подробности покупки: ${esc(r.store || 'магазин не определён')}, ${money(r.total)}"><div><h3>${esc(r.store || 'Магазин не определён')}</h3><p>${esc(fmtPurchase(r) || fmtDateTime(r.created_at))} · ${(r.items || []).length} позиций</p></div><div class="receipt-amount">${money(r.total)}</div><button class="text-btn" data-delete="${esc(r.id)}" type="button" aria-label="Удалить чек ${esc(r.store || '')}">Удалить</button></article>`).join('')
    : empty;
  const rest = list.length - shown.length;
  $('historyMore').hidden = rest <= 0;
  $('historyMoreBtn').textContent = `Показать ещё (${Math.min(rest, HISTORY_PAGE)} из ${rest})`;
  document.querySelectorAll('[data-open-auth]').forEach(b => b.onclick = () => openAuth('login'));
  $('historyList').querySelectorAll('[data-delete]').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    b.disabled = true;
    if (!await deleteReceipt(b.dataset.delete)) b.disabled = false;
  });
  $('historyList').querySelectorAll('[data-open]').forEach(row => {
    row.onclick = () => openReceipt(row.dataset.open);
    row.onkeydown = e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === row) { e.preventDefault(); openReceipt(row.dataset.open); } };
  });
}
const resetHistoryPage = () => { historyShown = HISTORY_PAGE; renderHistory(); };
$('historyPeriod').onchange = resetHistoryPage;
let searchTimer = null;
$('historySearch').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(resetHistoryPage, 200); };
$('historyMoreBtn').onclick = () => {
  const firstNew = historyShown;
  historyShown += HISTORY_PAGE;
  renderHistory();
  $('historyList').children[firstNew]?.focus();
};
// CSV для Excel: «;» как разделитель, запятая в дробях, BOM для кириллицы
function csvCell(value) {
  let s = String(value ?? '');
  // Защита от формул в Excel: название магазина из OCR может начинаться с «=»
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
const csvNum = n => String(round2(n)).replace('.', ',');
function exportCsv() {
  const list = historyList();
  if (!list.length) return;
  const rows = [['Дата', 'Время', 'Магазин', 'Итог чека', 'Товар', 'Количество', 'Цена', 'Сумма', 'Категория']];
  list.forEach(r => {
    const d = parseDate(r.date), day = d && !Number.isNaN(+d) ? d.toLocaleDateString('ru-RU') : '';
    const base = [day, r.time || '', r.store || '', csvNum(r.total)];
    const items = r.items || [];
    if (!items.length) rows.push([...base, '', '', '', '', r.category || '']);
    items.forEach(i => rows.push([...base, i.name, csvNum(i.quantity ?? 1), csvNum(i.price_per_unit ?? i.total_price), csvNum(i.total_price), categoryOf(i)]));
  });
  const csv = '﻿' + rows.map(row => row.map(csvCell).join(';')).join('\r\n');
  downloadFile(csv, 'text/csv;charset=utf-8', `checkai_${isoDay(new Date())}.csv`);
  toast(`Выгружено чеков: ${list.length}`);
}
$('exportCsvBtn').onclick = exportCsv;

// --- Категории и анализ ---
function sumsByCategory(list) {
  const sums = {};
  list.forEach(r => (r.items || []).forEach(it => { const c = categoryOf(it); sums[c] = (sums[c] || 0) + num(it.total_price); }));
  return sums;
}
const allItemCategories = () => receipts.flatMap(r => (r.items || []).map(i => i.category));
function renderCategories() {
  const sums = sumsByCategory(filtered($('categoriesPeriod').value));
  const sorted = categoryNames(Object.keys(sums))
    .map(name => ({ name, icon: CATEGORY_ICON[name] || '◌', value: sums[name] || 0 }))
    .sort((a, b) => b.value - a.value);
  $('categoryGrid').innerHTML = sorted.map(c => `<div class="card category-row"><div class="cat-icon" aria-hidden="true">${c.icon}</div><div><h3>${esc(c.name)}</h3><small>по сохранённым чекам</small></div><div class="category-amount">${money(c.value)}</div></div>`).join('');
}
$('categoriesPeriod').onchange = renderCategories;
function renderAnalysis() {
  const p = $('analysisPeriod').value, cat = $('analysisCategory').value, list = filtered(p), allSums = sumsByCategory(list), cats = categoryNames(allItemCategories());
  $('analysisCategory').innerHTML = '<option value="all">Все категории</option>' + cats.map(c => `<option>${esc(c)}</option>`).join('');
  $('analysisCategory').value = cats.includes(cat) ? cat : 'all';
  const chosenCat = $('analysisCategory').value;
  const spent = r => chosenCat === 'all' ? num(r.total) : (r.items || []).reduce((a, i) => a + num(i.total_price), 0);
  const chosen = chosenCat === 'all' ? list : list.map(r => ({ ...r, items: (r.items || []).filter(i => categoryOf(i) === chosenCat) }));
  $('analysisTotal').textContent = money(chosen.reduce((s, r) => s + spent(r), 0));
  $('analysisItems').textContent = chosen.reduce((a, r) => a + (r.items || []).length, 0);
  const tops = Object.entries(allSums).sort((a, b) => b[1] - a[1]);
  $('analysisTop').textContent = tops[0]?.[0] || '—';
  const sums = chosenCat === 'all' ? allSums : { [chosenCat]: allSums[chosenCat] || 0 };
  const sorted = Object.entries(sums).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
  const maxSum = Math.max(...sorted.map(x => x[1]));
  $('breakdown').innerHTML = sorted.length
    ? sorted.map(([n, v]) => `<div class="legend-row"><i class="dot" aria-hidden="true"></i><div>${esc(n)}<div class="progress" aria-hidden="true"><i style="width:${Math.round(v / maxSum * 100)}%"></i></div></div><strong>${money(v)}</strong></div>`).join('')
    : '<div class="empty">Нет данных для выбранного периода</div>';
  const buckets = [], now = new Date();
  if (p === 'week') {
    for (let i = 6; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      buckets.push({ label: ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'][d.getDay()], key: d.toDateString(), sum: 0 });
    }
  } else {
    for (let i = 5; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      buckets.push({ label: d.toLocaleDateString('ru-RU', { month: 'short' }), key: d.getFullYear() + '-' + d.getMonth(), sum: 0 });
    }
  }
  chosen.forEach(r => {
    const d = new Date(r.iso);
    const b = buckets.find(x => x.key === (p === 'week' ? d.toDateString() : d.getFullYear() + '-' + d.getMonth()));
    if (b) b.sum += spent(r);
  });
  const max = Math.max(1, ...buckets.map(b => b.sum));
  $('chart').innerHTML = buckets.map(b => `<div class="bar-wrap" title="${esc(b.label)}: ${money(b.sum)}"><div class="bar" style="height:${Math.max(2, b.sum / max * 82)}%"></div><span class="bar-label">${esc(b.label)}</span></div>`).join('');
  const title = p === 'week' ? 'Последние 7 дней' : p === 'year' ? 'По месяцам' : 'Динамика за 6 месяцев';
  $('chartTitle').textContent = title;
  $('chart').setAttribute('aria-label', title + ': ' + buckets.map(b => `${b.label} — ${money(b.sum)}`).join(', '));
}
$('analysisPeriod').onchange = renderAnalysis;
$('analysisCategory').onchange = renderAnalysis;

checkAuthOnStartup();
