'use strict';

// --- Справочники ---
// Должно совпадать с MAX_UPLOAD_MB и ALLOWED_EXTENSIONS на сервере (server.py)
const MAX_UPLOAD_MB = 10;
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png', 'image/webp', 'image/bmp', 'image/tiff'];
const ALLOWED_EXT = /\.(jpe?g|png|webp|bmp|tiff?)$/i;
// Сколько чеков запрашивать за раз и сколько показывать в истории до «Показать ещё»
const RECEIPTS_PAGE = 200;
const HISTORY_PAGE = 20;
// Сервер не сообщает этапы распознавания: после загрузки через столько мс показываем «Анализ»
const OCR_STEP_MS = 5000;
// Валюты, пока сервер не отдаёт /api/currencies. digits — знаков после запятой при показе
const DEFAULT_CURRENCIES = [
  { code: 'KZT', symbol: '₸', name: 'Казахстанский тенге', digits: 0 },
  { code: 'RUB', symbol: '₽', name: 'Российский рубль', digits: 0 },
  { code: 'USD', symbol: '$', name: 'Доллар США', digits: 2 },
  { code: 'EUR', symbol: '€', name: 'Евро', digits: 2 },
  { code: 'KGS', symbol: 'сом', name: 'Киргизский сом', digits: 2 },
  { code: 'UZS', symbol: 'сум', name: 'Узбекский сум', digits: 0 },
  { code: 'CNY', symbol: '¥', name: 'Китайский юань', digits: 2 },
  { code: 'TRY', symbol: '₺', name: 'Турецкая лира', digits: 2 },
];
// Валюта чеков, сохранённых до появления валют, и чеков, где её не нашли
const DEFAULT_CURRENCY = 'KZT';

// --- Утилиты ---
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const round2 = n => Math.round(num(n) * 100) / 100;
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

// --- Валюты ---
let currencyList = DEFAULT_CURRENCIES, currencies = Object.fromEntries(DEFAULT_CURRENCIES.map(c => [c.code, c]));
const currencyInfo = code => currencies[code] || { code, symbol: code, name: code, digits: 2 };
// Итоги, аналитика и бюджеты — в основной валюте пользователя
const baseCurrency = () => currentUser?.base_currency || DEFAULT_CURRENCY;
// Код валюты из ответа сервера. Старый парсер присылает символ («₸») — переводим его в код
function currencyCode(value) {
  const v = String(value || '').trim();
  if (/^[a-z]{3}$/i.test(v)) return v.toUpperCase();
  return currencyList.find(c => c.symbol === v)?.code || DEFAULT_CURRENCY;
}
const curOf = r => r?.currency ? currencyCode(r.currency) : DEFAULT_CURRENCY;
const money = (n, cur = baseCurrency()) => {
  const c = currencyInfo(cur);
  return new Intl.NumberFormat('ru-RU', { minimumFractionDigits: c.digits, maximumFractionDigits: c.digits }).format(Number(n) || 0) + ' ' + c.symbol;
};
// Расхождение суммы позиций с итогом, которое считаем ошибкой: 1 ₸, но 1 цент
const sumMismatch = (sum, total, cur) => Math.abs(round2(sum - total)) >= (currencyInfo(cur).digits ? 0.01 : 1);
// Множитель к основной валюте считает сервер (rate_to_base). Без него пересчитать можно только чек в основной валюте
const baseRate = r => r.rate_to_base ?? (curOf(r) === baseCurrency() ? 1 : null);
// Сумма чека в основной валюте или null, если курса нет
const toBase = (r, amount) => { const rate = baseRate(r); return rate == null ? null : num(amount) * rate; };
// Сводка по чекам в основной валюте: чеки без курса не складываем, а считаем отдельно по валютам
function baseSummary(list) {
  const s = { total: 0, counted: 0, converted: false, skipped: {} };
  list.forEach(r => {
    const v = toBase(r, r.total), cur = curOf(r);
    if (v == null) { s.skipped[cur] = (s.skipped[cur] || 0) + 1; return; }
    s.total += v;
    s.counted++;
    if (cur !== baseCurrency()) s.converted = true;
  });
  return s;
}
// «≈» перед суммой, в которую вошли пересчитанные по курсу чеки
const approx = (text, converted) => (converted ? '≈ ' : '') + text;
function skippedText(skipped) {
  const parts = Object.entries(skipped).map(([cur, n]) => `${n} ${plural(n, 'чек', 'чека', 'чеков')} в ${cur}`);
  return parts.length ? `Не учтено: ${parts.join(', ')} — нет курса к ${baseCurrency()}.` : '';
}
function showSkipped(id, skipped) {
  const text = skippedText(skipped);
  $(id).textContent = text;
  $(id).hidden = !text;
}
const currencyOptions = selected => {
  const list = currencies[selected] ? currencyList : [...currencyList, currencyInfo(selected)];
  return list.map(c => `<option value="${esc(c.code)}" ${c.code === selected ? 'selected' : ''}>${esc(c.symbol)} ${esc(c.code)} — ${esc(c.name)}</option>`).join('');
};
async function loadCurrencies() {
  try {
    const list = await apiRequest('/api/currencies');
    if (!Array.isArray(list) || !list.length) return;
    currencyList = list.map(c => ({ code: String(c.code).toUpperCase(), symbol: c.symbol || c.code, name: c.name || c.code, digits: Number.isInteger(c.digits) ? c.digits : 2 }));
    currencies = Object.fromEntries(currencyList.map(c => [c.code, c]));
    rerender();
  } catch (e) { /* сервер ещё не отдаёт список (404) — остаётся встроенный */ }
}
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
// --- Категории ---
// Категории пользователя приходят с сервера (у гостя — стандартные, без id).
// «Без категории» — позиция с category_id = null: это не категория, её нельзя изменить или удалить
const UNCATEGORIZED = { id: null, name: 'Без категории', icon: '○', description: null };
const CATEGORY_ICONS = ['💄', '🐶', '🎁', '📚', '💊', '🏋', '✈', '🎮', '🧸', '🍷', '🚗', '💡', '📱', '🧾', '🏠', '◌'];
// Ограничения — те же, что на сервере (CATEGORIES_TASKS.md, п. 0.4)
const CATEGORY_NAME_MAX = 50, CATEGORY_DESC_MAX = 300, CATEGORY_ICON_MAX = 8;
// Откуда у позиции категория после распознавания
const CATEGORY_SOURCE_TEXT = { rule: 'по вашему правилу', ai: 'определил ИИ', keywords: 'по ключевым словам' };
const categoryById = id => categories.find(c => c.id === id) || UNCATEGORIZED;
// Категория позиции: по id, а у гостя — по названию
const itemCategory = it => it.category_id != null ? categoryById(it.category_id)
  : categories.find(c => c.name === it.category) || UNCATEGORIZED;
// Ключ категории для сумм и значений в списках: id, у гостя — название, '' — «Без категории»
const catKey = c => c.id != null ? String(c.id) : c === UNCATEGORIZED ? '' : 'name:' + c.name;
const categoryByKey = key => key === '' ? UNCATEGORIZED
  : String(key).startsWith('name:') ? categories.find(c => c.name === key.slice(5)) || UNCATEGORIZED
  : categoryById(Number(key));
const categoryLabel = c => `${c.icon} ${c.name}`;
// Варианты для списка категорий; current — ключ выбранной
const categoryOptions = (current, exclude) => [...categories, UNCATEGORIZED]
  .filter(c => catKey(c) !== exclude)
  .map(c => `<option value="${esc(catKey(c))}" ${catKey(c) === current ? 'selected' : ''}>${esc(categoryLabel(c))}</option>`).join('');
const itemsSum = items => round2((items || []).reduce((s, i) => s + num(i.total_price), 0));

let categories = [], categoryRules = [], receipts = [], lastResult = null, selectedFile = null, currentUser = null, pendingSave = false, authMode = 'login';
let analyzing = false, historyShown = HISTORY_PAGE;
// Лимиты расходов: total — общий на месяц, categories — {категория: лимит}; budgetsLocal — сервер их пока не хранит
let budgets = { total: null, categories: {} }, budgetsLocal = false;

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

// Один запрос обновления на все одновременные 401
let refreshing = null;
function refreshSession() {
  if (!refreshing) {
    refreshing = (async () => {
      const refreshToken = getRefresh();
      if (!refreshToken) return false;
      try {
        const response = await fetch('/api/auth/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: refreshToken }) });
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
  closeBudgetEditor(); loadBudgets(); loadCategories(); loadRules();
}
function setGuest() {
  currentUser = null; receipts = []; budgets = emptyBudgets(); budgetsLocal = false; categoryRules = [];
  closeAccountMenu(); closeReceipt(); closeBudgetEditor(); closeCategoryModals();
  loadCategories();
  $('loginBtn').hidden = false;
  $('userChip').hidden = true;
  $('navProfile').hidden = true;
  $('drawerFoot').textContent = 'Войдите, чтобы сохранять чеки';
  if ($('page-profile').classList.contains('active')) showPage('upload');
  rerender();
}
function rerender() { renderHistory(); renderCategories(); renderAnalysis(); renderBudgets(); renderProfile(); }
function logout() { if (currentUser) forgetAccount(currentUser.id); removeToken(); setGuest(); toast('Вы вышли из аккаунта'); }

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
    toast('Вы вошли как ' + (user.full_name || user.email));
  } catch (e) {
    if (getToken()) { restore(); toast('Не удалось переключиться: ' + e.message); return; }
    // Токен сохранённого аккаунта истёк: возвращаем прежний аккаунт и просим войти заново
    if (prev) { restore(); await checkAuthOnStartup(); }
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
  const spent = baseSummary(receipts);
  $('profileSpent').textContent = approx(money(spent.total), spent.converted);
  $('profileAccounts').innerHTML = getAccounts().map(a => `<div class="account-row"><div class="avatar" aria-hidden="true">${esc(initial(a.user))}</div><div><b>${esc(a.user.full_name || a.user.email)}</b><small>${esc(a.user.email)}</small></div>${a.user.id === u.id ? '<span class="pill">Текущий</span>' : `<button class="text-btn" data-switch="${esc(a.user.id)}" type="button">Перейти</button><button class="text-btn" data-forget="${esc(a.user.id)}" type="button" title="Убрать с этого устройства" aria-label="Убрать аккаунт ${esc(a.user.email)} с этого устройства">✕</button>`}</div>`).join('');
  $('profileAccounts').querySelectorAll('[data-switch]').forEach(b => b.onclick = () => switchAccount(+b.dataset.switch));
  $('profileAccounts').querySelectorAll('[data-forget]').forEach(b => b.onclick = () => { forgetAccount(+b.dataset.forget); renderProfile(); renderAccountMenu(); });
}
function fillProfileForm() {
  if (!currentUser) return;
  $('profileForm').reset();
  $('editName').value = currentUser.full_name || '';
  $('editEmail').value = currentUser.email;
  $('editBaseCurrency').innerHTML = currencyOptions(baseCurrency());
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
  const base = $('editBaseCurrency').value;
  if (base !== baseCurrency()) body.base_currency = base;
  if (!Object.keys(body).length) { showFormError('profileError', 'Вы ничего не изменили'); return; }
  if ((body.email || body.new_password) && !current) { showFormError('profileError', 'Введите текущий пароль, чтобы сменить email или пароль'); return; }
  if (current) body.current_password = current;
  const btn = $('profileSave');
  btn.disabled = true;
  try {
    const data = await apiRequest('/api/auth/me', { method: 'PATCH', body: JSON.stringify(body) });
    if (data.access_token) setSession(data);
    const user = data.user || data;
    setUser(user);
    if (body.base_currency) {
      // Старый сервер молча пропустит неизвестное поле — проверяем, что валюта действительно сменилась
      if (user.base_currency !== body.base_currency) { showFormError('profileError', 'Сервер пока не поддерживает основную валюту — остальные изменения сохранены.'); return; }
      // rate_to_base у чеков пересчитан сервером под новую валюту
      await loadReceipts();
    }
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
    try { localStorage.removeItem(budgetsKey(u.id)); } catch (e) {}
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
    currency: curOf(src),
    // Откуда распознавание взяло валюту: symbol | country | default (у сохранённых чеков нет)
    currency_source: src.currency_source || null,
    image_path: src.image_path || null,
    items: (src.items || []).map(it => {
      const quantity = it.quantity == null ? 1 : num(it.quantity);
      const total = it.total_price == null ? round2(quantity * num(it.price_per_unit)) : round2(it.total_price);
      const price = it.price_per_unit == null ? round2(quantity ? total / quantity : total) : round2(it.price_per_unit);
      // category — название: у гостя id нет, сервер найдёт категорию по названию при сохранении
      return { name: it.name || '', quantity, price_per_unit: price, total_price: total,
        category_id: it.category_id ?? null, category: it.category ?? null, category_source: it.category_source ?? null };
    }),
  };
}
function toPayload(draft) {
  const items = draft.items
    .filter(i => i.name.trim())
    .map(i => {
      const item = { name: i.name.trim(), quantity: num(i.quantity), price_per_unit: round2(i.price_per_unit), total_price: round2(i.total_price) };
      // Есть id — отправляем его; нет (гость) — название, сервер найдёт категорию пользователя
      if (i.category_id != null) item.category_id = i.category_id;
      else item.category = i.category || null;
      return item;
    });
  // Категорию чека сервер вычисляет сам — по позиции с наибольшей суммой
  return {
    store: draft.store.trim() || null,
    date: draft.date || null,
    time: draft.time || null,
    total: round2(draft.total),
    currency: draft.currency,
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
      <label><span data-total-label></span><input class="field edit-total" type="number" min="0" step="any" inputmode="decimal" data-f="total" value="${esc(draft.total)}"></label>
      <label>Валюта<select class="field" data-f="currency">${currencyOptions(draft.currency)}</select></label>
      <div class="edit-sum" data-sum aria-live="polite"></div>
    </div>
  </div>`;
  // Обработчики вешаем на новый элемент, чтобы повторный mountEditor не дублировал их
  const editor = root.firstElementChild;
  const list = editor.querySelector('[data-items]'), sumBox = editor.querySelector('[data-sum]'), totalInput = editor.querySelector('[data-f="total"]');
  const renderItems = () => {
    const sym = currencyInfo(draft.currency).symbol;
    editor.querySelector('[data-total-label]').textContent = `Итог чека, ${sym}`;
    list.innerHTML = draft.items.length ? draft.items.map((it, i) => `<div class="edit-item" data-i="${i}">
      <div class="edit-name">
        <label>Название<input class="field" data-k="name" maxlength="500" value="${esc(it.name)}" placeholder="Товар или услуга"></label>
        <button class="icon-btn remove-item" type="button" data-remove aria-label="Удалить позицию ${esc(it.name || '№' + (i + 1))}">✕</button>
      </div>
      <div class="edit-nums">
        ${numInput('quantity', it.quantity, 'Кол-во')}${numInput('price_per_unit', it.price_per_unit, `Цена, ${esc(sym)}`)}${numInput('total_price', it.total_price, `Сумма, ${esc(sym)}`)}
        <label>Категория<select class="field" data-k="category">${categoryOptions(catKey(itemCategory(it)))}</select>${it.category_source && CATEGORY_SOURCE_TEXT[it.category_source] ? `<small class="cat-source">${CATEGORY_SOURCE_TEXT[it.category_source]}</small>` : ''}</label>
      </div>
    </div>`).join('') : '<div class="empty">Позиций нет. Добавьте их вручную или оставьте только итог.</div>';
    updateSum();
  };
  const updateSum = () => {
    const sum = itemsSum(draft.items), mismatch = draft.items.length && sumMismatch(sum, num(draft.total), draft.currency);
    sumBox.innerHTML = `Сумма позиций: <b>${money(sum, draft.currency)}</b>` + (mismatch ? ` — не совпадает с итогом. <button class="text-btn" type="button" data-fix-total>Сделать итогом</button>` : '');
  };
  editor.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.f) {
      draft[t.dataset.f] = t.dataset.f === 'total' ? num(t.value) : t.value;
      if (t.dataset.f === 'total') updateSum();
      // Подписи «Цена, $» и сумма позиций — в новой валюте
      if (t.dataset.f === 'currency') renderItems();
      return;
    }
    const row = t.closest('[data-i]');
    if (!row || !t.dataset.k) return;
    const item = draft.items[+row.dataset.i], k = t.dataset.k;
    if (k === 'category') {
      // Выбрали вручную — пометка «определил ИИ» больше не про эту категорию
      const c = categoryByKey(t.value);
      Object.assign(item, { category_id: c.id, category: c === UNCATEGORIZED ? null : c.name, category_source: null });
      row.querySelector('.cat-source')?.remove();
      return;
    }
    item[k] = k === 'name' ? t.value : num(t.value);
    // Количество или цена поменялись — пересчитываем сумму строки
    if (k === 'quantity' || k === 'price_per_unit') {
      item.total_price = round2(num(item.quantity) * num(item.price_per_unit));
      row.querySelector('[data-k="total_price"]').value = item.total_price;
    }
    if (k !== 'name' && k !== 'category') updateSum();
  });
  editor.addEventListener('click', e => {
    if (e.target.closest('[data-add]')) {
      draft.items.push({ name: '', quantity: 1, price_per_unit: 0, total_price: 0, category_id: null, category: null, category_source: null });
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
  const cur = curOf(r), foreign = cur !== baseCurrency();
  $('rmTotal').textContent = money(r.total, cur);
  // Чек не в основной валюте: показываем, сколько это по курсу на дату покупки
  $('rmConverted').hidden = !foreign;
  if (foreign) {
    const converted = toBase(r, r.total), d = parseDate(r.date);
    const day = d && !Number.isNaN(+d) ? d.toLocaleDateString('ru-RU') : fmtDateTime(r.created_at).split(',')[0];
    $('rmConverted').textContent = converted == null
      ? 'Курс на дату покупки не найден — чек не учитывается в итогах, аналитике и бюджетах.'
      : `≈ ${money(converted)} по курсу НБ РК на ${day}`;
  }
  $('rmMeta').innerHTML = [r.category, foreign && cur, items.length + ' позиций', 'Сохранён ' + fmtDateTime(r.created_at)].filter(Boolean).map(t => `<span class="pill">${esc(t)}</span>`).join('');
  const photo = $('rmPhoto');
  if (r.image_path) { photo.onerror = () => { photo.hidden = true; }; photo.src = r.image_path; photo.hidden = false; }
  else { photo.hidden = true; photo.removeAttribute('src'); }
  $('rmItems').innerHTML = items.length
    ? items.map(it => `<div class="item"><div><strong>${esc(it.name)}</strong><small>${esc(it.quantity ?? 1)} × ${money(it.price_per_unit ?? it.total_price, cur)}</small><span class="item-cat">${esc(categoryLabel(itemCategory(it)))}</span></div><div style="text-align:right"><strong>${money(it.total_price, cur)}</strong></div></div>`).join('')
    : '<div class="empty">В чеке нет товарных позиций</div>';
  // Подсказка, если распознавание потеряло или исказило строку
  const sum = itemsSum(items);
  $('rmWarn').hidden = !(items.length && sumMismatch(sum, num(r.total), cur));
  $('rmWarn').textContent = `Сумма позиций (${money(sum, cur)}) не совпадает с итогом чека (${money(r.total, cur)}). Возможно, распознавание пропустило или исказило строку — исправьте через «Редактировать».`;
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
    setSession(data);
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
  if (name === 'budgets') renderBudgets();
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
  const catMenu = $('categoryGrid').querySelector('.cat-menu:not([hidden])');
  if (!$('receiptModal').hidden) closeReceipt();
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
  // Валюту не нашли на самом чеке — просим проверить
  const name = currencyInfo(lastResult.currency).name;
  const hint = { default: `Валюта на чеке не найдена, поставили «${name}». Проверьте поле «Валюта» под позициями.`, country: `Валюта определена по стране чека: «${name}». Если платили в другой валюте, поменяйте её под позициями.` }[lastResult.currency_source];
  $('currencyHint').textContent = hint || '';
  $('currencyHint').hidden = !hint;
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
    // В ответе — сохранённый чек с курсом к основной валюте (rate_to_base), он нужен для бюджетов
    const saved = await apiRequest('/api/receipts/save', { method: 'POST', body: JSON.stringify(toPayload(lastResult)) });
    await loadReceipts();
    const alert = budgetAlert(saved);
    if (alert) toast('Чек сохранён. ' + alert, 6000);
    else toast('Чек сохранён в ваш аккаунт');
    showPage('history');
  } catch (e) {
    if (!getToken()) { pendingSave = true; openAuth('login'); }
    toast('Ошибка при сохранении: ' + e.message);
  } finally { btn.disabled = false; }
}
$('saveReceiptBtn').onclick = saveReceipt;
// Пользователь сам выбрал валюту — подсказка «проверьте валюту» больше не нужна
$('resultEditor').addEventListener('input', e => { if (e.target.dataset.f === 'currency') $('currencyHint').hidden = true; });
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
  const list = historyList(), sum = baseSummary(list), searching = !!$('historySearch').value.trim();
  // Сводка — в основной валюте; средний чек — по чекам, которые удалось пересчитать
  $('historyTotal').textContent = approx(money(sum.total), sum.converted);
  $('historyCount').textContent = list.length;
  $('historyAverage').textContent = approx(money(sum.counted ? sum.total / sum.counted : 0), sum.converted);
  showSkipped('historySkipped', sum.skipped);
  $('exportCsvBtn').disabled = !list.length;
  const shown = list.slice(0, historyShown);
  let empty;
  if (!currentUser) empty = '<div class="card empty">История хранится в аккаунте. <button class="text-btn" data-open-auth type="button">Войдите или зарегистрируйтесь</button>, чтобы сохранять чеки.</div>';
  else if (searching) empty = '<div class="card empty">Ничего не найдено. Попробуйте другое название магазина или товара.</div>';
  else empty = '<div class="card empty">Пока нет чеков за этот период. Загрузите чек, чтобы начать историю.</div>';
  $('historyList').innerHTML = shown.length
    ? shown.map(r => `<article class="card receipt-row" data-open="${esc(r.id)}" tabindex="0" aria-label="Подробности покупки: ${esc(r.store || 'магазин не определён')}, ${esc(money(r.total, curOf(r)))}"><div><h3>${esc(r.store || 'Магазин не определён')}</h3><p>${esc(fmtPurchase(r) || fmtDateTime(r.created_at))} · ${(r.items || []).length} позиций</p></div><div class="receipt-amount">${esc(money(r.total, curOf(r)))}</div><button class="text-btn" data-delete="${esc(r.id)}" type="button" aria-label="Удалить чек ${esc(r.store || '')}">Удалить</button></article>`).join('')
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
  // Цены и суммы — в валюте чека, отдельная колонка — итог в основной валюте (пусто, если курса нет)
  const rows = [['Дата', 'Время', 'Магазин', 'Валюта', 'Итог чека', `Итог в ${baseCurrency()}`, 'Товар', 'Количество', 'Цена', 'Сумма', 'Категория']];
  list.forEach(r => {
    const d = parseDate(r.date), day = d && !Number.isNaN(+d) ? d.toLocaleDateString('ru-RU') : '';
    const inBase = toBase(r, r.total);
    const head = [day, r.time || '', r.store || '', curOf(r), csvNum(r.total), inBase == null ? '' : csvNum(inBase)];
    const items = r.items || [];
    if (!items.length) rows.push([...head, '', '', '', '', r.category || '']);
    items.forEach(i => rows.push([...head, i.name, csvNum(i.quantity ?? 1), csvNum(i.price_per_unit ?? i.total_price), csvNum(i.total_price), itemCategory(i).name]));
  });
  const csv = '﻿' + rows.map(row => row.map(csvCell).join(';')).join('\r\n');
  downloadFile(csv, 'text/csv;charset=utf-8', `checkai_${isoDay(new Date())}.csv`);
  toast(`Выгружено чеков: ${list.length}`);
}
$('exportCsvBtn').onclick = exportCsv;

// --- Категории и анализ ---
// Траты по категориям в основной валюте, по ключу catKey; чеки без курса пропускаем (их показывает baseSummary)
function sumsByCategory(list) {
  const sums = {};
  list.forEach(r => {
    const rate = baseRate(r);
    if (rate == null) return;
    (r.items || []).forEach(it => { const k = catKey(itemCategory(it)); sums[k] = (sums[k] || 0) + num(it.total_price) * rate; });
  });
  return sums;
}
// --- Категории: загрузка, раздел, перенос товаров, правила ---
const CATEGORY_ITEMS_PAGE = 50;
const REMEMBER_KEY = 'checkai_remember_moves';
// Перетаскивание — только с мышью; на телефоне и с клавиатуры переносят через «Переместить в…»
const canDrag = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
const DRAG_TYPE = 'application/x-checkai-items';
// Раскрытая категория (ключ catKey) и отмеченные в ней позиции
let openCategoryKey = null, pickedItems = new Set(), categoryItemsShown = CATEGORY_ITEMS_PAGE;

async function loadCategories() {
  const user = currentUser;
  let list = [];
  try { list = await apiRequest(user ? '/api/categories' : '/api/categories/defaults'); }
  catch (e) { toast('Не удалось загрузить категории: ' + e.message); }
  // Пока шёл запрос, пользователь сменил аккаунт или вышел
  if (currentUser?.id !== user?.id) return;
  categories = (Array.isArray(list) ? list : [])
    .map((c, i) => ({ id: c.id ?? null, name: c.name, description: c.description || null, icon: c.icon || '◌', position: c.position ?? i }))
    .sort((a, b) => a.position - b.position);
  rerender();
}
async function loadRules() {
  const user = currentUser;
  let list = [];
  if (user) {
    try { list = await apiRequest('/api/category-rules'); }
    catch (e) { toast('Не удалось загрузить правила: ' + e.message); }
  }
  if (currentUser?.id !== user?.id) return;
  categoryRules = Array.isArray(list) ? list : [];
  renderRules();
}

function renderCategories() {
  const guest = !currentUser;
  $('categoriesGuest').hidden = !guest;
  $('categoriesBody').hidden = guest;
  $('categoriesControls').hidden = guest;
  if (guest) return;
  const list = filtered($('categoriesPeriod').value), sums = sumsByCategory(list), summary = baseSummary(list);
  showSkipped('categoriesSkipped', summary.skipped);
  // Позиции периода по категориям
  const rows = {};
  list.forEach(r => (r.items || []).forEach(it => { (rows[catKey(itemCategory(it))] ||= []).push({ it, r }); }));
  // «Без категории» показываем, только если в ней что-то есть
  const cards = [...categories, ...(rows[''] ? [UNCATEGORIZED] : [])];
  if (openCategoryKey !== null && !cards.some(c => catKey(c) === openCategoryKey)) openCategoryKey = null;
  $('categoryGrid').innerHTML = cards.length
    ? cards.map((c, i) => categoryCard(c, i, sums[catKey(c)] || 0, rows[catKey(c)] || [], summary.converted)).join('')
    : '<div class="card empty">Категорий нет. Добавьте первую кнопкой «Добавить категорию».</div>';
  renderRules();
}
function categoryCard(c, index, sum, rows, converted) {
  const key = catKey(c), open = key === openCategoryKey, own = c !== UNCATEGORIZED, panelId = 'cat-items-' + (key || 'none');
  const menu = own ? `<div class="cat-menu-wrap">
      <button class="icon-btn cat-menu-btn" type="button" data-cat-menu aria-haspopup="menu" aria-expanded="false" aria-label="Действия с категорией «${esc(c.name)}»">⋯</button>
      <div class="account-menu cat-menu" role="menu" hidden>
        <button class="menu-item" type="button" role="menuitem" data-cat-action="edit">✎ Изменить</button>
        <button class="menu-item" type="button" role="menuitem" data-cat-action="up" ${index === 0 ? 'disabled' : ''}>↑ Выше</button>
        <button class="menu-item" type="button" role="menuitem" data-cat-action="down" ${index >= categories.length - 1 ? 'disabled' : ''}>↓ Ниже</button>
        <div class="menu-sep"></div>
        <button class="menu-item danger-text" type="button" role="menuitem" data-cat-action="delete">✕ Удалить</button>
      </div>
    </div>` : '';
  const desc = c.description || (own ? 'Без описания' : 'Товары, которые не попали ни в одну категорию');
  return `<div class="card category-row${open ? ' open' : ''}" data-cat="${esc(key)}">
    <button class="cat-open" type="button" aria-expanded="${open}" aria-controls="${esc(panelId)}">
      <span class="cat-icon" aria-hidden="true">${esc(c.icon)}</span>
      <span class="cat-text"><b>${esc(c.name)}</b><small>${esc(desc)}</small></span>
      <span class="cat-total"><b>${esc(approx(money(sum), converted && sum > 0))}</b><small>${rows.length} ${plural(rows.length, 'позиция', 'позиции', 'позиций')}</small></span>
    </button>${menu}
  </div>${open ? categoryItems(c, rows, panelId) : ''}`;
}
function categoryItems(c, rows, id) {
  const key = catKey(c);
  if (!rows.length) return `<div class="card cat-items" id="${esc(id)}"><div class="empty">За этот период в категории нет товаров.</div></div>`;
  // Сначала свежие покупки
  const sorted = rows.slice().sort((a, b) => String(b.r.iso || '').localeCompare(String(a.r.iso || '')));
  const shown = sorted.slice(0, categoryItemsShown), rest = sorted.length - shown.length;
  const moveSelect = (attr, label) => `<select class="field" ${attr} aria-label="${esc(label)}"><option value="__" selected disabled>Переместить в…</option>${categoryOptions(null, key)}</select>`;
  return `<div class="card cat-items" id="${esc(id)}" role="region" aria-label="Товары категории «${esc(c.name)}»">
    <div class="cat-bulk" ${pickedItems.size ? '' : 'hidden'}>Выбрано: <b data-picked-count>${pickedItems.size}</b>
      ${moveSelect('data-bulk-move', 'Переместить выбранные товары в категорию')}
      <button class="text-btn" type="button" data-unpick>Снять выбор</button>
    </div>
    ${shown.map(({ it, r }) => `<div class="cat-item${pickedItems.has(it.id) ? ' picked' : ''}" data-item="${esc(it.id)}"${canDrag ? ' draggable="true"' : ''}>
      <input type="checkbox" data-pick="${esc(it.id)}" ${pickedItems.has(it.id) ? 'checked' : ''} aria-label="Выбрать «${esc(it.name)}»">
      <span class="cat-item-main"><b>${esc(it.name)}</b><small>${esc((r.store || 'Магазин не определён') + ' · ' + (fmtPurchase(r) || fmtDateTime(r.created_at)))}</small></span>
      <span class="cat-item-sum">${esc(money(it.total_price, curOf(r)))}</span>
      ${moveSelect(`data-move="${esc(it.id)}"`, `Переместить «${it.name}» в категорию`)}
    </div>`).join('')}
    ${rest > 0 ? `<div class="more-row"><button class="btn soft small" type="button" data-more-items>Показать ещё (${Math.min(rest, CATEGORY_ITEMS_PAGE)} из ${rest})</button></div>` : ''}
  </div>`;
}
const focusCategory = key => $('categoryGrid').querySelector(`[data-cat="${CSS.escape(key)}"] .cat-open`)?.focus();
function toggleCategory(key) {
  openCategoryKey = openCategoryKey === key ? null : key;
  pickedItems.clear();
  categoryItemsShown = CATEGORY_ITEMS_PAGE;
  renderCategories();
  focusCategory(key);
}
// Перенос позиций в другую категорию; remember — сервер запомнит правило для таких товаров
async function moveItems(ids, key) {
  ids = ids.filter(id => id != null && id !== '').map(Number);
  if (!ids.length) return;
  const cat = categoryByKey(key), remember = $('rememberMoves').checked, back = openCategoryKey;
  try {
    const res = await apiRequest('/api/items/move', { method: 'POST', body: JSON.stringify({ item_ids: ids, category_id: cat.id, remember }) });
    pickedItems.clear();
    await loadReceipts();
    if (remember) loadRules();
    const n = res?.moved ?? ids.length;
    toast(`Перемещено ${n} ${plural(n, 'позиция', 'позиции', 'позиций')} в «${cat.name}»`);
    if (back !== null) focusCategory(back);
  } catch (e) {
    toast('Не удалось переместить: ' + e.message);
    renderCategories();
  }
}
function closeCategoryMenus(except) {
  $('categoryGrid').querySelectorAll('.cat-menu').forEach(m => {
    if (m === except) return;
    m.hidden = true;
    m.previousElementSibling.setAttribute('aria-expanded', 'false');
  });
}
// Порядок категорий: меняем местами с соседней и отправляем весь список
async function moveCategory(id, delta) {
  const ids = categories.map(c => c.id), i = ids.indexOf(id), j = i + delta;
  if (i < 0 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  try {
    await apiRequest('/api/categories/order', { method: 'PUT', body: JSON.stringify({ ids }) });
    await loadCategories();
    $('categoryGrid').querySelector(`[data-cat="${CSS.escape(String(id))}"] [data-cat-menu]`)?.focus();
  } catch (e) { toast('Не удалось изменить порядок: ' + e.message); }
}

$('categoryGrid').addEventListener('click', e => {
  const card = e.target.closest('[data-cat]'), key = card?.dataset.cat;
  const menuBtn = e.target.closest('[data-cat-menu]');
  if (menuBtn) {
    const menu = menuBtn.nextElementSibling, open = menu.hidden;
    closeCategoryMenus(menu);
    menu.hidden = !open;
    menuBtn.setAttribute('aria-expanded', String(open));
    if (open) menu.querySelector('.menu-item:not(:disabled)')?.focus();
    return;
  }
  const action = e.target.closest('[data-cat-action]')?.dataset.catAction;
  if (action) {
    closeCategoryMenus();
    const cat = categoryByKey(key);
    if (action === 'edit') openCategoryModal(cat);
    else if (action === 'delete') openDeleteCategory(cat);
    else moveCategory(cat.id, action === 'up' ? -1 : 1);
    return;
  }
  if (e.target.closest('.cat-open')) { toggleCategory(key); return; }
  if (e.target.closest('[data-more-items]')) { categoryItemsShown += CATEGORY_ITEMS_PAGE; renderCategories(); return; }
  if (e.target.closest('[data-unpick]')) { pickedItems.clear(); renderCategories(); focusCategory(openCategoryKey); }
});
$('categoryGrid').addEventListener('change', e => {
  const t = e.target;
  if (t.dataset.pick !== undefined) {
    const id = Number(t.dataset.pick);
    if (t.checked) pickedItems.add(id); else pickedItems.delete(id);
    // Без полной перерисовки, чтобы не терять фокус на чекбоксе
    t.closest('.cat-item').classList.toggle('picked', t.checked);
    const bulk = $('categoryGrid').querySelector('.cat-bulk');
    bulk.hidden = !pickedItems.size;
    bulk.querySelector('[data-picked-count]').textContent = pickedItems.size;
  } else if (t.dataset.move !== undefined) moveItems([t.dataset.move], t.value);
  else if (t.dataset.bulkMove !== undefined) moveItems([...pickedItems], t.value);
});
// Перетаскивание товара на карточку другой категории. Отмечены чекбоксы — переносятся все отмеченные
$('categoryGrid').addEventListener('dragstart', e => {
  const row = e.target.closest?.('.cat-item');
  if (!row) return;
  const id = Number(row.dataset.item), ids = pickedItems.has(id) ? [...pickedItems] : [id];
  e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(ids));
  e.dataTransfer.effectAllowed = 'move';
  row.classList.add('dragging');
  document.body.classList.add('dragging-items');
});
$('categoryGrid').addEventListener('dragend', e => {
  e.target.closest?.('.cat-item')?.classList.remove('dragging');
  document.body.classList.remove('dragging-items');
  $('categoryGrid').querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
});
$('categoryGrid').addEventListener('dragover', e => {
  const card = e.target.closest('.category-row');
  if (!card || card.dataset.cat === openCategoryKey || !e.dataTransfer.types.includes(DRAG_TYPE)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  card.classList.add('drop-target');
});
$('categoryGrid').addEventListener('dragleave', e => {
  const card = e.target.closest('.category-row');
  if (card && !card.contains(e.relatedTarget)) card.classList.remove('drop-target');
});
$('categoryGrid').addEventListener('drop', e => {
  const card = e.target.closest('.category-row');
  if (!card || !e.dataTransfer.types.includes(DRAG_TYPE)) return;
  e.preventDefault();
  card.classList.remove('drop-target');
  let ids = [];
  try { ids = JSON.parse(e.dataTransfer.getData(DRAG_TYPE)); } catch (err) { return; }
  if (card.dataset.cat !== openCategoryKey) moveItems(ids, card.dataset.cat);
});
document.addEventListener('click', e => { if (!e.target.closest('.cat-menu-wrap')) closeCategoryMenus(); });

// Галочка «Запомнить» — общая для раздела, по умолчанию включена
try { $('rememberMoves').checked = localStorage.getItem(REMEMBER_KEY) !== '0'; } catch (e) { $('rememberMoves').checked = true; }
$('rememberMoves').onchange = () => { try { localStorage.setItem(REMEMBER_KEY, $('rememberMoves').checked ? '1' : '0'); } catch (e) {} };

// --- Окно «Новая категория» / «Изменить категорию» ---
let editingCategory = null;
$('categoryIconPicks').innerHTML = CATEGORY_ICONS.map(i => `<button class="icon-pick" type="button" data-icon="${esc(i)}" aria-label="Иконка ${esc(i)}" aria-pressed="false">${esc(i)}</button>`).join('');
const markIconPick = () => $('categoryIconPicks').querySelectorAll('[data-icon]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.icon === $('categoryIcon').value.trim())));
function openCategoryModal(cat = null) {
  editingCategory = cat;
  $('categoryModalTitle').textContent = cat ? 'Изменить категорию' : 'Новая категория';
  $('categoryName').value = cat?.name || '';
  $('categoryIcon').value = cat?.icon || '';
  $('categoryDesc').value = cat?.description || '';
  $('categoryError').hidden = true;
  markIconPick();
  openModal('categoryModal', $('categoryName'));
}
// Те же проверки, что на сервере: длина считается в символах, а не в единицах UTF-16
const charLen = s => [...s].length;
function categoryFormProblem(name, icon, description) {
  if (!name) return 'Введите название категории';
  if (charLen(name) > CATEGORY_NAME_MAX) return `Название категории: от 1 до ${CATEGORY_NAME_MAX} символов`;
  if (name.toLowerCase() === UNCATEGORIZED.name.toLowerCase()) return 'Это название зарезервировано';
  if (categories.some(c => c.id !== editingCategory?.id && c.name.toLowerCase() === name.toLowerCase())) return `Категория «${name}» уже есть`;
  if (charLen(icon) > CATEGORY_ICON_MAX) return `Иконка — не больше ${CATEGORY_ICON_MAX} символов`;
  if (charLen(description) > CATEGORY_DESC_MAX) return `Описание — не больше ${CATEGORY_DESC_MAX} символов`;
  return null;
}
$('categoryIconPicks').onclick = e => {
  const b = e.target.closest('[data-icon]');
  if (!b) return;
  $('categoryIcon').value = b.dataset.icon;
  markIconPick();
};
$('categoryIcon').oninput = markIconPick;
$('categoryForm').onsubmit = async e => {
  e.preventDefault();
  $('categoryError').hidden = true;
  const name = $('categoryName').value.trim(), icon = $('categoryIcon').value.trim(), description = $('categoryDesc').value.trim();
  const problem = categoryFormProblem(name, icon, description);
  if (problem) { showFormError('categoryError', problem); return; }
  const body = { name, icon: icon || null, description: description || null }, cat = editingCategory, btn = $('categorySave');
  btn.disabled = true;
  try {
    await apiRequest(cat ? `/api/categories/${cat.id}` : '/api/categories', { method: cat ? 'PATCH' : 'POST', body: JSON.stringify(body) });
    closeModal('categoryModal');
    await loadCategories();
    toast(cat ? `Категория «${name}» сохранена` : `Категория «${name}» добавлена`);
  } catch (err) {
    showFormError('categoryError', err.message);
  } finally { btn.disabled = false; }
};
$('categoryCancel').onclick = $('categoryModalClose').onclick = () => closeModal('categoryModal');
$('categoryModal').onclick = e => { if (e.target.id === 'categoryModal') closeModal('categoryModal'); };
$('addCategoryBtn').onclick = () => openCategoryModal();
$('categoriesLoginBtn').onclick = () => openAuth('login');

// --- Окно «Удалить категорию» ---
let deletingCategory = null;
function openDeleteCategory(cat) {
  deletingCategory = cat;
  // Позиции за всё время, а не за выбранный период: перенесутся все
  const count = receipts.reduce((n, r) => n + (r.items || []).filter(it => it.category_id === cat.id).length, 0);
  const others = categories.filter(c => c.id !== cat.id), limit = budgets.categories[catKey(cat)];
  $('deleteCategoryTitle').textContent = `Удалить «${cat.name}»?`;
  $('deleteCategoryText').textContent = (count ? `В ней ${count} ${plural(count, 'позиция', 'позиции', 'позиций')} за всё время.` : 'В ней пока нет позиций.')
    + (limit ? ' Лимит бюджета этой категории будет удалён.' : '');
  $('deleteMoveTo').innerHTML = others.map(c => `<option value="${esc(c.id)}">${esc(categoryLabel(c))}</option>`).join('');
  // Переносить нечего или некуда — выбор не показываем
  $('deleteMoveRow').hidden = !others.length || !count;
  $('deleteNoneRow').hidden = !count;
  $('deleteCategoryForm').deleteMode.value = others.length && count ? 'move' : 'none';
  $('deleteCategoryError').hidden = true;
  openModal('deleteCategoryModal', $('deleteCategoryCancel'));
}
$('deleteMoveTo').onchange = () => { $('deleteCategoryForm').deleteMode.value = 'move'; };
$('deleteCategoryForm').onsubmit = async e => {
  e.preventDefault();
  const cat = deletingCategory;
  if (!cat) return;
  const moveTo = $('deleteCategoryForm').deleteMode.value === 'move' && !$('deleteMoveRow').hidden ? $('deleteMoveTo').value : null;
  const btn = $('deleteCategorySubmit');
  btn.disabled = true;
  try {
    const res = await apiRequest(`/api/categories/${cat.id}` + (moveTo ? `?move_to=${encodeURIComponent(moveTo)}` : ''), { method: 'DELETE' });
    closeModal('deleteCategoryModal');
    if (openCategoryKey === catKey(cat)) openCategoryKey = null;
    const target = moveTo ? categoryById(Number(moveTo)).name : null;
    await loadCategories();
    await loadReceipts();
    loadBudgets();
    loadRules();
    const moved = res?.moved_items || 0;
    toast(`Категория «${cat.name}» удалена` + (moved ? (target ? `, позиции перенесены в «${target}»` : ', позиции остались без категории') : ''));
  } catch (err) {
    showFormError('deleteCategoryError', err.message);
  } finally { btn.disabled = false; }
};
$('deleteCategoryCancel').onclick = $('deleteCategoryClose').onclick = () => closeModal('deleteCategoryModal');
$('deleteCategoryModal').onclick = e => { if (e.target.id === 'deleteCategoryModal') closeModal('deleteCategoryModal'); };
function closeCategoryModals() { closeModal('categoryModal'); closeModal('deleteCategoryModal'); closeCategoryMenus(); }

// --- Запомненные правила ---
function renderRules() {
  $('rulesList').innerHTML = categoryRules.length
    ? categoryRules.map(r => {
      const cat = r.category_id != null ? categoryById(r.category_id) : UNCATEGORIZED;
      const shown = cat === UNCATEGORIZED && r.category ? { icon: '◌', name: r.category } : cat;
      return `<div class="rule-row"><span class="rule-pattern">«${esc(r.pattern)}»</span><span aria-hidden="true">→</span><span>${esc(categoryLabel(shown))}</span>
        <button class="text-btn" type="button" data-rule-del="${esc(r.id)}" aria-label="Удалить правило «${esc(r.pattern)}»">✕</button></div>`;
    }).join('')
    : '<p class="rules-empty">Правил пока нет. Они появляются, когда вы переносите товар с галочкой «Запомнить».</p>';
}
$('rulesList').onclick = async e => {
  const b = e.target.closest('[data-rule-del]');
  if (!b) return;
  b.disabled = true;
  try {
    await apiRequest(`/api/category-rules/${b.dataset.ruleDel}`, { method: 'DELETE' });
    categoryRules = categoryRules.filter(r => String(r.id) !== b.dataset.ruleDel);
    renderRules();
    toast('Правило удалено');
  } catch (err) { b.disabled = false; toast('Не удалось удалить правило: ' + err.message); }
};
$('categoriesPeriod').onchange = renderCategories;
function renderAnalysis() {
  const p = $('analysisPeriod').value, cat = $('analysisCategory').value, list = filtered(p), allSums = sumsByCategory(list);
  const keys = [...categories, UNCATEGORIZED].map(catKey);
  $('analysisCategory').innerHTML = '<option value="all">Все категории</option>' + categoryOptions(null);
  $('analysisCategory').value = keys.includes(cat) ? cat : 'all';
  const chosenCat = $('analysisCategory').value;
  // Траты чека в основной валюте; чек без курса не учитываем
  const spent = r => {
    const rate = baseRate(r);
    if (rate == null) return 0;
    return rate * (chosenCat === 'all' ? num(r.total) : (r.items || []).reduce((a, i) => a + num(i.total_price), 0));
  };
  const chosen = chosenCat === 'all' ? list : list.map(r => ({ ...r, items: (r.items || []).filter(i => catKey(itemCategory(i)) === chosenCat) }));
  const summary = baseSummary(list);
  $('analysisTotal').textContent = approx(money(chosen.reduce((s, r) => s + spent(r), 0)), summary.converted);
  showSkipped('analysisSkipped', summary.skipped);
  $('analysisItems').textContent = chosen.reduce((a, r) => a + (r.items || []).length, 0);
  const tops = Object.entries(allSums).sort((a, b) => b[1] - a[1]);
  $('analysisTop').textContent = tops[0] ? categoryByKey(tops[0][0]).name : '—';
  const sums = chosenCat === 'all' ? allSums : { [chosenCat]: allSums[chosenCat] || 0 };
  const sorted = Object.entries(sums).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
  const maxSum = Math.max(...sorted.map(x => x[1]));
  $('breakdown').innerHTML = sorted.length
    ? sorted.map(([n, v]) => `<div class="legend-row"><i class="dot" aria-hidden="true"></i><div>${esc(categoryLabel(categoryByKey(n)))}<div class="progress" aria-hidden="true"><i style="width:${Math.round(v / maxSum * 100)}%"></i></div></div><strong>${money(v)}</strong></div>`).join('')
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

// --- Бюджеты: лимиты расходов на месяц ---
// Лимиты действуют каждый месяц. Сервер хранит их списком [{category_id, limit}], category_id: null — общий лимит.
// budgets.categories — {String(category_id): лимит}, ключи совпадают с catKey
// Пока на сервере нет /api/budgets (404/405), лимиты хранятся в браузере — только на этом устройстве
const BUDGET_WARN = 0.8;
const emptyBudgets = () => ({ total: null, categories: {} });
const budgetsKey = userId => 'checkai_budgets_' + userId;
function budgetsFromList(list) {
  const b = emptyBudgets();
  (Array.isArray(list) ? list : []).forEach(x => {
    const limit = round2(x?.limit);
    if (limit <= 0) return;
    if (x.category_id != null) b.categories[String(x.category_id)] = limit;
    // Старый формат с названием категории (до своих категорий) не переносим — иначе он стал бы общим лимитом
    else if (!x.category) b.total = limit;
  });
  return b;
}
const budgetsToList = b => [
  ...(b.total ? [{ category_id: null, limit: b.total }] : []),
  ...Object.entries(b.categories).map(([id, limit]) => ({ category_id: Number(id), limit })),
];
const hasBudgets = b => !!b.total || Object.keys(b.categories).length > 0;
const readLocalBudgets = userId => { try { return JSON.parse(localStorage.getItem(budgetsKey(userId))) || []; } catch (e) { return []; } };
const writeLocalBudgets = (userId, list) => { try { localStorage.setItem(budgetsKey(userId), JSON.stringify(list)); } catch (e) {} };

async function loadBudgets() {
  const user = currentUser;
  let next = emptyBudgets(), local = false;
  if (user) {
    try { next = budgetsFromList(await apiRequest('/api/budgets')); }
    catch (e) {
      if (notSupported(e)) { local = true; next = budgetsFromList(readLocalBudgets(user.id)); }
      else toast('Не удалось загрузить бюджеты: ' + e.message);
    }
  }
  // Пока шёл запрос, пользователь сменил аккаунт или вышел
  if (currentUser?.id !== user?.id) return;
  budgets = next;
  budgetsLocal = local;
  renderBudgets();
}
async function saveBudgets(next) {
  if (!budgetsLocal) {
    try {
      budgets = budgetsFromList(await apiRequest('/api/budgets', { method: 'PUT', body: JSON.stringify({ budgets: budgetsToList(next) }) }));
      return;
    } catch (e) { if (!notSupported(e)) throw e; budgetsLocal = true; }
  }
  writeLocalBudgets(currentUser.id, budgetsToList(next));
  budgets = next;
}

// Месяц как ключ YYYY-MM; месяц чека — по дате покупки, а без неё по дате сохранения
const monthKey = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`;
const receiptMonth = r => { const d = new Date(r.iso || ''); return Number.isNaN(+d) ? '' : monthKey(d); };
const shiftMonth = (key, delta) => { const [y, m] = key.split('-').map(Number); return monthKey(new Date(y, m - 1 + delta, 1)); };
const monthLabel = key => {
  const [y, m] = key.split('-').map(Number), s = new Date(y, m - 1, 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
  return s.charAt(0).toUpperCase() + s.slice(1);
};
// Траты за месяц в основной валюте: лимиты бюджетов тоже в ней
function monthSpending(key) {
  const list = receipts.filter(r => receiptMonth(r) === key), summary = baseSummary(list);
  return { total: summary.total, byCategory: sumsByCategory(list), converted: summary.converted, skipped: summary.skipped };
}
// Средние траты в месяц за последние полные месяцы (считаем только месяцы, где были чеки)
function averageSpending(months) {
  const current = monthKey(new Date()), keys = Array.from({ length: months }, (_, i) => shiftMonth(current, -i - 1));
  const list = receipts.filter(r => keys.includes(receiptMonth(r)));
  const n = new Set(list.map(receiptMonth)).size || 1, byCategory = sumsByCategory(list);
  Object.keys(byCategory).forEach(k => { byCategory[k] /= n; });
  return { total: baseSummary(list).total / n, byCategory };
}
// Без общего лимита бюджет месяца — сумма лимитов по категориям и траты только в этих категориях
const overallLimit = b => b.total || Object.values(b.categories).reduce((s, v) => s + v, 0);
const overallSpent = (b, spending) => b.total ? spending.total : Object.keys(b.categories).reduce((s, c) => s + (spending.byCategory[c] || 0), 0);
const budgetState = (spent, limit) => spent > limit ? 'over' : spent >= limit * BUDGET_WARN ? 'warn' : 'ok';
const budgetPct = (spent, limit) => limit ? Math.round(spent / limit * 100) : 0;
const budgetBar = (label, pct) => `<div class="progress budget-bar" role="progressbar" aria-label="${esc(label)}: потрачено ${pct}% лимита" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(pct, 100)}"><i style="width:${Math.min(pct, 100)}%"></i></div>`;
const leftText = (spent, limit) => spent <= limit ? 'осталось ' + money(limit - spent) : 'перерасход ' + money(spent - limit);

// Прогноз только для текущего месяца: траты в среднем за прошедшие дни × дней в месяце
function budgetForecast(key, spent, limit) {
  const now = new Date();
  if (key !== monthKey(now) || !limit) return '';
  if (spent > limit) return `Лимит месяца уже превышен на ${money(spent - limit)}.`;
  const day = now.getDate(), days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const projected = spent / day * days, perDay = money((limit - spent) / (days - day + 1));
  const pace = projected > limit
    ? `По текущему темпу к концу месяца выйдет около ${money(projected)} — на ${money(projected - limit)} больше лимита.`
    : `По текущему темпу к концу месяца выйдет около ${money(projected)} — укладываетесь в лимит.`;
  return `${pace} Чтобы уложиться, тратьте не больше ${perDay} в день.`;
}
function budgetRow(b) {
  const pct = budgetPct(b.spent, b.limit);
  return `<div class="card budget-row ${budgetState(b.spent, b.limit)}">
    <div class="cat-icon" aria-hidden="true">${esc(b.icon)}</div>
    <div class="budget-main">
      <div class="budget-line"><h3>${esc(b.name)}</h3><span class="budget-pct">${pct}%</span></div>
      ${budgetBar(b.name, pct)}
      <small>${money(b.spent)} из ${money(b.limit)} · ${leftText(b.spent, b.limit)}</small>
    </div>
  </div>`;
}
function fillBudgetMonths() {
  const sel = $('budgetMonth'), current = monthKey(new Date()), prev = sel.value || current;
  const keys = Array.from({ length: 12 }, (_, i) => shiftMonth(current, -i));
  sel.innerHTML = keys.map(k => `<option value="${k}">${esc(monthLabel(k))}</option>`).join('');
  sel.value = keys.includes(prev) ? prev : current;
}
// Пометка «перерасход» в меню, если в текущем месяце превышен какой-то лимит
function updateBudgetBadge() {
  const spending = monthSpending(monthKey(new Date()));
  $('budgetBadge').hidden = !(currentUser && (
    (budgets.total && spending.total > budgets.total) ||
    Object.entries(budgets.categories).some(([c, limit]) => (spending.byCategory[c] || 0) > limit)));
}
function renderBudgets() {
  const guest = !currentUser;
  $('budgetGuest').hidden = !guest;
  $('budgetBody').hidden = guest;
  $('budgetControls').hidden = guest;
  $('budgetLocalNote').hidden = guest || !budgetsLocal;
  updateBudgetBadge();
  if (guest) return;
  fillBudgetMonths();
  const key = $('budgetMonth').value, spending = monthSpending(key);
  const limit = overallLimit(budgets), spent = overallSpent(budgets, spending), state = budgetState(spent, limit);
  $('budgetLimitLabel').textContent = budgets.total || !limit ? 'Бюджет на месяц' : 'Сумма лимитов';
  $('budgetLimit').textContent = limit ? money(limit) : '—';
  $('budgetSpent').textContent = approx(money(spent), spending.converted);
  showSkipped('budgetSkipped', spending.skipped);
  $('budgetLeftLabel').textContent = limit && spent > limit ? 'Перерасход' : 'Осталось';
  $('budgetLeft').textContent = limit ? money(Math.abs(limit - spent)) : '—';
  $('budgetLeft').classList.toggle('over', !!limit && spent > limit);
  if (!hasBudgets(budgets)) {
    $('budgetOverall').innerHTML = '<div class="card empty">Лимиты ещё не заданы. <button class="text-btn" type="button" data-budget-edit>Настроить лимиты</button></div>';
  } else {
    const pct = budgetPct(spent, limit), title = budgets.total ? 'Весь месяц' : 'Категории с лимитом', forecast = budgetForecast(key, spent, limit);
    $('budgetOverall').innerHTML = `<div class="card budget-overall ${state}">
      <div class="budget-line"><h3>${title}</h3><span class="budget-pct">${pct}%</span></div>
      ${budgetBar(title, pct)}
      <small>${money(spent)} из ${money(limit)} · ${leftText(spent, limit)}</small>
      ${forecast ? `<p>${esc(forecast)}</p>` : ''}
    </div>`;
  }
  // Сначала категории, где лимит ближе всего к исчерпанию
  // Лимит удалённой категории сервер удаляет сам; до перезагрузки бюджетов такие строки просто не показываем
  $('budgetList').innerHTML = Object.entries(budgets.categories)
    .filter(([id]) => categoryById(Number(id)) !== UNCATEGORIZED)
    .map(([id, lim]) => ({ ...categoryById(Number(id)), limit: lim, spent: spending.byCategory[id] || 0 }))
    .sort((a, b) => b.spent / b.limit - a.spent / a.limit)
    .map(budgetRow).join('');
  const unlimited = Object.entries(spending.byCategory).filter(([n, v]) => v > 0 && !budgets.categories[n]).sort((a, b) => b[1] - a[1]);
  $('budgetUnlimited').hidden = !unlimited.length || !hasBudgets(budgets);
  $('budgetUnlimited').textContent = 'Без лимита: ' + unlimited.map(([k, v]) => `${categoryByKey(k).name} — ${money(v)}`).join(', ');
}
// Предупреждение, если новый чек довёл лимит до 80% или превысил его. receipt — ответ сервера на сохранение
function budgetAlert(receipt) {
  const rate = baseRate(receipt);
  if (!hasBudgets(budgets) || rate == null) return '';
  const spending = monthSpending(receiptMonth({ iso: receiptIso(receipt) })), added = {};
  (receipt.items || []).forEach(i => { const k = catKey(itemCategory(i)); added[k] = (added[k] || 0) + num(i.total_price) * rate; });
  const checks = Object.entries(budgets.categories).filter(([k]) => added[k])
    .map(([k, limit]) => ({ name: `«${categoryByKey(k).name}»`, limit, spent: spending.byCategory[k] || 0, added: added[k] }));
  if (budgets.total) checks.push({ name: 'месяца', limit: budgets.total, spent: spending.total, added: num(receipt.total) * rate });
  const rank = { ok: 0, warn: 1, over: 2 };
  const crossed = checks
    .filter(c => rank[budgetState(c.spent, c.limit)] > rank[budgetState(c.spent - c.added, c.limit)])
    .sort((a, b) => b.spent / b.limit - a.spent / a.limit)[0];
  if (!crossed) return '';
  return crossed.spent > crossed.limit
    ? `Бюджет ${crossed.name} превышен на ${money(crossed.spent - crossed.limit)}`
    : `Бюджет ${crossed.name}: потрачено ${budgetPct(crossed.spent, crossed.limit)}%`;
}
const budgetField = (cat, limit, avg) => `<label class="budget-field"><span><span aria-hidden="true">${esc(cat.icon)}</span> ${esc(cat.name)}</span><input class="field" type="number" min="0" step="any" inputmode="decimal" data-cat="${esc(catKey(cat))}" value="${limit ? esc(limit) : ''}" placeholder="Без лимита"><small>${avg ? 'в среднем ' + money(avg) + ' в месяц' : 'трат пока не было'}</small></label>`;
function openBudgetEditor() {
  if (!currentUser) return;
  // Лимит — только на категории пользователя: у «Без категории» его нет
  const avg = averageSpending(3);
  $('budgetFields').innerHTML = categories.map(c => budgetField(c, budgets.categories[catKey(c)], avg.byCategory[catKey(c)])).join('');
  $('budgetTotalInput').value = budgets.total || '';
  $('budgetEditorTitle').textContent = `Лимиты на месяц, ${currencyInfo(baseCurrency()).symbol}`;
  $('budgetTotalHint').textContent = avg.total ? `В среднем ${money(avg.total)} в месяц. Без общего лимита бюджет — сумма лимитов по категориям` : 'Без общего лимита бюджет — сумма лимитов по категориям';
  $('budgetError').hidden = true;
  $('budgetEditor').hidden = false;
  $('budgetEditBtn').setAttribute('aria-expanded', 'true');
  $('budgetTotalInput').focus();
}
function closeBudgetEditor() {
  $('budgetEditor').hidden = true;
  $('budgetEditBtn').setAttribute('aria-expanded', 'false');
}
$('budgetEditor').onsubmit = async e => {
  e.preventDefault();
  if (!currentUser) return;
  $('budgetError').hidden = true;
  const next = emptyBudgets(), total = round2($('budgetTotalInput').value);
  if (total > 0) next.total = total;
  $('budgetFields').querySelectorAll('[data-cat]').forEach(i => { const v = round2(i.value); if (v > 0) next.categories[i.dataset.cat] = v; });
  const btn = $('budgetSave');
  btn.disabled = true;
  try {
    await saveBudgets(next);
    closeBudgetEditor();
    renderBudgets();
    $('budgetEditBtn').focus();
    toast(budgetsLocal ? 'Лимиты сохранены на этом устройстве' : 'Лимиты сохранены');
  } catch (err) {
    showFormError('budgetError', err.message);
  } finally { btn.disabled = false; }
};
$('budgetEditBtn').onclick = () => { if ($('budgetEditor').hidden) openBudgetEditor(); else closeBudgetEditor(); };
$('budgetCancel').onclick = () => { closeBudgetEditor(); $('budgetEditBtn').focus(); };
$('budgetOverall').onclick = e => { if (e.target.closest('[data-budget-edit]')) openBudgetEditor(); };
$('budgetLoginBtn').onclick = () => openAuth('login');
$('budgetMonth').onchange = renderBudgets;

loadCurrencies();
checkAuthOnStartup();
