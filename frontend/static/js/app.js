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
  // Кнопка-переключатель есть в шапке и на лендинге
  document.querySelectorAll('[data-theme-toggle]').forEach(b => {
    b.firstElementChild.textContent = light ? '☾' : '☀';
    b.setAttribute('aria-label', light ? 'Включить тёмную тему' : 'Включить светлую тему');
    b.title = light ? 'Тёмная тема' : 'Светлая тема';
  });
  document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  renderThemePicks();
}
// Выбор в профиле: dark | light | system (system — тема не сохранена, следуем за ОС)
const systemThemeQuery = matchMedia('(prefers-color-scheme: light)');
const systemTheme = () => systemThemeQuery.matches ? 'light' : 'dark';
const themePref = () => { try { const t = localStorage.getItem(THEME_KEY); return t === 'light' || t === 'dark' ? t : 'system'; } catch (e) { return 'system'; } };
function setThemePref(pref) {
  if (pref !== 'system') { applyTheme(pref, true); return; }
  try { localStorage.removeItem(THEME_KEY); } catch (e) {}
  applyTheme(systemTheme(), false);
}
function renderThemePicks() {
  const pref = themePref();
  document.querySelectorAll('[data-theme-pick]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themePick === pref)));
}
systemThemeQuery.addEventListener?.('change', () => { if (themePref() === 'system') applyTheme(systemTheme(), false); });
document.querySelectorAll('[data-theme-pick]').forEach(b => b.onclick = () => setThemePref(b.dataset.themePick));
document.querySelectorAll('[data-theme-toggle]').forEach(b => b.onclick = () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light', true));
applyTheme(document.documentElement.dataset.theme || 'dark', false);

// Акцентный цвет: violet — по умолчанию (атрибута нет), остальные — [data-accent] в app.css
const ACCENT_KEY = 'checkai_accent';
const ACCENTS = ['violet', 'red', 'orange', 'green', 'blue', 'pink'];
const accentPref = () => document.documentElement.dataset.accent || 'violet';
function setAccent(accent) {
  if (!ACCENTS.includes(accent)) accent = 'violet';
  if (accent === 'violet') delete document.documentElement.dataset.accent;
  else document.documentElement.dataset.accent = accent;
  try { accent === 'violet' ? localStorage.removeItem(ACCENT_KEY) : localStorage.setItem(ACCENT_KEY, accent); } catch (e) {}
  renderAccentPicks();
}
function renderAccentPicks() {
  const cur = accentPref();
  document.querySelectorAll('[data-accent-pick]').forEach(b => {
    const on = b.dataset.accentPick === cur;
    b.setAttribute('aria-checked', String(on));
    b.tabIndex = on ? 0 : -1;
  });
}
document.querySelectorAll('[data-accent-pick]').forEach(b => b.onclick = () => setAccent(b.dataset.accentPick));
$('accentPicks').addEventListener('keydown', e => {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!step) return;
  e.preventDefault();
  const next = ACCENTS[(ACCENTS.indexOf(accentPref()) + step + ACCENTS.length) % ACCENTS.length];
  setAccent(next);
  document.querySelector(`[data-accent-pick="${next}"]`).focus();
});
renderAccentPicks();

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
const FIELD_NAMES = { email: 'Email', password: 'Пароль', full_name: 'Имя', name: 'Название товара', store: 'Магазин', category: 'Категория', total: 'Итог', quantity: 'Количество', price_per_unit: 'Цена', total_price: 'Сумма', date: 'Дата покупки', time: 'Время', currency: 'Валюта' };
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
// Фото профиля: avatar_url с сервера, а пока его нет — сжатая копия в localStorage (PROFILE_TASKS.md, п. 1.2)
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
  // Другой пользователь — рекомендации загружаем заново; тот же (после PATCH /api/auth/me) — оставляем
  if (insights.userId !== user.id) { resetInsights(user.id); loadInsights(); }
  rememberAccount(getToken(), user);
  const name = user.full_name || user.email;
  paintAvatar($('userAvatar'), user);
  $('userName').textContent = name;
  $('userEmail').textContent = user.email;
  $('userEmail').hidden = !user.full_name;
  $('userMenuBtn').setAttribute('aria-label', 'Меню аккаунта: ' + name);
  $('loginBtn').hidden = true;
  $('userChip').hidden = false;
  $('navProfile').hidden = false;
  $('drawerFoot').textContent = 'Чеки сохраняются в вашем аккаунте';
  renderAccess(); renderAccountMenu(); renderProfile(); fillProfileForm();
  closeBudgetEditor(); loadBudgets(); loadCategories(); loadRules();
}
function setGuest() {
  currentUser = null; receipts = []; budgets = emptyBudgets(); budgetsLocal = false; categoryRules = [];
  resetInsights(null);
  closeAccountMenu(); closeReceipt(); closeBudgetEditor(); closeCategoryModals();
  loadCategories();
  $('loginBtn').hidden = false;
  $('userChip').hidden = true;
  $('navProfile').hidden = true;
  $('drawerFoot').textContent = 'Войдите, чтобы сохранять чеки';
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
    ? others.map(a => `<button class="menu-item" data-switch="${esc(a.user.id)}" type="button" role="menuitem">${avatarHtml(a.user)}<span>${esc(a.user.full_name || a.user.email)}</span></button>`).join('')
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

// --- Профиль: обложка, цифры, активность, привычки ---
// Всё, кроме форм, считается на клиенте по уже загруженным чекам (receipts)
const ACTIVITY_WEEKS = 26;
const DAY_MS = 864e5;
const WEEKDAYS_ON = ['по воскресеньям', 'по понедельникам', 'по вторникам', 'по средам', 'по четвергам', 'по пятницам', 'по субботам'];
const prefersReducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const intText = n => new Intl.NumberFormat('ru-RU').format(Math.round(n));
const daysSince = s => { const d = s ? serverDate(s) : null; return d && !Number.isNaN(+d) ? Math.max(0, Math.floor((Date.now() - d) / DAY_MS)) : 0; };
// Траты по дням для тепловой карты и подсказки над ячейкой: {YYYY-MM-DD: {count, total}}
let activityDays = {};

// Число добегает до значения при открытии раздела; при обновлении данных меняется сразу
function countTo(el, value, fmt, animate) {
  cancelAnimationFrame(el._raf);
  if (!animate || !value || prefersReducedMotion.matches) { el.textContent = fmt(value); return; }
  const start = performance.now();
  const step = t => {
    const p = Math.min(1, (t - start) / 900);
    el.textContent = fmt(value * (1 - Math.pow(1 - p, 3)));
    if (p < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}
function renderProfile(animate = false) {
  if (!currentUser) return;
  const u = currentUser, accounts = getAccounts(), spent = baseSummary(receipts), days = daysSince(u.created_at), cur = currencyInfo(baseCurrency());
  paintAvatar($('profileAvatar'), u);
  $('profileName').textContent = u.full_name || u.email.split('@')[0];
  $('profileEmail').textContent = u.email;
  const since = u.created_at ? serverDate(u.created_at) : null;
  $('profileSince').textContent = since && !Number.isNaN(+since) ? 'с нами с ' + since.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '';
  $('profileChips').innerHTML = [
    `<span class="pill">${esc(cur.symbol)} ${esc(cur.code)} · основная валюта</span>`,
    u.full_name ? '' : '<button class="pill pill-action" data-pf-goto="personal" type="button">＋ Добавить имя</button>',
    accounts.length > 1 ? `<button class="pill pill-action" data-pf-goto="accounts" type="button">${accounts.length} ${plural(accounts.length, 'аккаунт', 'аккаунта', 'аккаунтов')} на устройстве</button>` : '',
  ].join('');
  const avg = spent.counted ? spent.total / spent.counted : 0;
  countTo($('profileReceipts'), receipts.length, intText, animate);
  countTo($('profileSpent'), spent.total, n => approx(money(n), spent.converted), animate);
  countTo($('profileAverage'), avg, n => approx(money(n), spent.converted), animate);
  countTo($('profileDays'), days, intText, animate);
  $('profileDaysLabel').textContent = plural(days, 'день', 'дня', 'дней') + ' с нами';
  $('accountsCount').textContent = accounts.length;
  $('dataCount').textContent = receipts.length;
  renderActivity(); renderHabits(); renderStartSteps(); renderProfileAccounts(accounts); renderDataStats();
}

// Тепловая карта за полгода: колонка — неделя с понедельника, последняя — текущая
function renderActivity() {
  activityDays = {};
  receipts.forEach(r => {
    const d = new Date(r.iso || '');
    if (Number.isNaN(+d)) return;
    const s = activityDays[isoDay(d)] ||= { count: 0, total: 0 };
    s.count++;
    s.total += toBase(r, r.total) ?? 0;
  });
  const today = new Date(), todayKey = isoDay(today);
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (today.getDay() + 6) % 7 - (ACTIVITY_WEEKS - 1) * 7);
  const cells = [], months = [], weekdays = Array(7).fill(0);
  let total = 0, best = 0, run = 0, lastMonth = null;
  for (let i = 0; i < ACTIVITY_WEEKS * 7; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i), key = isoDay(d), w = Math.floor(i / 7);
    // Подпись месяца — над первой его неделей; у первой колонки — только если месяц успеет показаться
    if (i % 7 === 0) {
      if (d.getMonth() !== lastMonth && (w > 0 || d.getDate() <= 10)) months.push(`<span style="grid-column:${w + 1}">${esc(d.toLocaleDateString('ru-RU', { month: 'short' }).replace('.', ''))}</span>`);
      lastMonth = d.getMonth();
    }
    const future = key > todayKey, count = activityDays[key]?.count || 0;
    if (!future) {
      run = count ? run + 1 : 0;
      best = Math.max(best, run);
      total += count;
      weekdays[d.getDay()] += count;
    }
    cells.push(`<i class="hm-cell${future ? ' future' : ''}${key === todayKey ? ' today' : ''}" data-l="${Math.min(4, count)}" data-day="${key}" style="--w:${w}"></i>`);
  }
  $('heatmap').parentElement.style.setProperty('--weeks', ACTIVITY_WEEKS);
  $('heatmapMonths').innerHTML = months.join('');
  $('heatmap').innerHTML = cells.join('');
  $('heatmap').setAttribute('aria-label', `Активность за полгода: ${total} ${plural(total, 'чек', 'чека', 'чеков')}`);
  const topDay = weekdays.indexOf(Math.max(...weekdays));
  $('activityStats').innerHTML = [
    ['За полгода', `${total} ${plural(total, 'чек', 'чека', 'чеков')}`],
    ['Рекорд подряд', best ? `${best} ${plural(best, 'день', 'дня', 'дней')}` : '—'],
    ['Чаще всего', total ? WEEKDAYS_ON[topDay] : '—'],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('');
}
$('heatmap').addEventListener('pointerover', e => {
  const cell = e.target.closest('.hm-cell:not(.future)'), tip = $('heatTip');
  if (!cell) { tip.hidden = true; return; }
  const s = activityDays[cell.dataset.day], d = new Date(cell.dataset.day + 'T00:00');
  tip.innerHTML = (s ? `${s.count} ${plural(s.count, 'чек', 'чека', 'чеков')} · ${esc(money(s.total))}` : 'Нет чеков')
    + `<small>${esc(d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' }))}</small>`;
  const r = cell.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(r.left + r.width / 2, 90), window.innerWidth - 90) + 'px';
  tip.style.top = r.top + 'px';
  tip.hidden = false;
});
$('heatmap').addEventListener('pointerleave', () => { $('heatTip').hidden = true; });

// «Привычки»: любимый магазин, топ-категория, самый крупный чек, сравнение с прошлым месяцем
function renderHabits() {
  if (!receipts.length) {
    $('profileHabits').innerHTML = '<li class="pf-empty">Здесь появятся любимый магазин, топ-категория и самый крупный чек.<button class="btn small" data-pf-goto="upload" type="button">Добавить первый чек</button></li>';
    return;
  }
  const stores = {};
  receipts.forEach(r => { const name = String(r.store || '').trim(); if (name) (stores[name.toLowerCase()] ||= { name, count: 0 }).count++; });
  const store = Object.values(stores).sort((a, b) => b.count - a.count)[0];
  const sums = sumsByCategory(receipts), topKey = Object.keys(sums).filter(k => k !== '').sort((a, b) => sums[b] - sums[a])[0];
  let biggest = null, biggestSum = 0;
  receipts.forEach(r => { const v = toBase(r, r.total); if (v != null && v > biggestSum) { biggest = r; biggestSum = v; } });
  const month = monthKey(new Date()), now = monthSpending(month).total, prev = monthSpending(shiftMonth(month, -1)).total;
  const delta = prev ? Math.round((now - prev) / prev * 100) : null;
  const rows = [
    store && { habit: 'store', value: store.name, label: 'Любимый магазин', meta: `${store.count} ${plural(store.count, 'визит', 'визита', 'визитов')}`, data: store.name },
    topKey != null && { habit: 'category', value: categoryLabel(categoryByKey(topKey)), label: 'Больше всего тратите на', meta: money(sums[topKey]) },
    biggest && { habit: 'receipt', value: approx(money(biggestSum), curOf(biggest) !== baseCurrency()), label: 'Самый крупный чек', meta: biggest.store || fmtPurchase(biggest), data: biggest.id },
    { habit: 'analysis', value: money(now), label: 'Этот месяц', cls: delta > 0 ? 'up' : delta < 0 ? 'down' : '',
      meta: delta == null ? 'в прошлом месяце трат не было' : delta === 0 ? 'как в прошлом месяце' : `${delta > 0 ? '↑' : '↓'} ${Math.abs(delta)}% к прошлому` },
  ].filter(Boolean);
  $('profileHabits').innerHTML = rows.map((r, i) => `<li><button class="pf-row" data-habit="${r.habit}" data-value="${esc(r.data ?? '')}" type="button"><span class="pf-row-num">${pad2(i + 1)}</span><span class="pf-row-main"><small>${esc(r.label)}</small><b>${esc(r.value)}</b></span><span class="pf-row-meta ${r.cls || ''}">${esc(r.meta)}</span><span class="pf-row-arrow" aria-hidden="true">→</span></button></li>`).join('');
}
$('profileHabits').onclick = e => {
  const b = e.target.closest('[data-habit]');
  if (!b) return;
  const habit = b.dataset.habit;
  if (habit === 'store') openHistory({ search: b.dataset.value });
  else if (habit === 'category') showPage('categories');
  else if (habit === 'receipt') openReceipt(b.dataset.value);
  else if (habit === 'analysis') { $('analysisPeriod').value = 'month'; showPage('analysis'); }
};

// «Первые шаги»: карточка пропадает, когда всё сделано
function renderStartSteps() {
  const steps = [
    { done: !!currentUser.full_name, text: 'Указать имя', go: 'personal' },
    { done: receipts.length > 0, text: 'Сохранить первый чек', go: 'upload' },
    { done: overallLimit(budgets) > 0, text: 'Задать лимит на месяц', go: 'budgets' },
    { done: categoryRules.length > 0, text: 'Перенести товар в нужную категорию', go: 'categories' },
  ];
  const done = steps.filter(s => s.done).length, all = done === steps.length;
  $('profileStart').hidden = all;
  $('profileGrid').classList.toggle('no-start', all);
  $('startCount').textContent = `${done} из ${steps.length}`;
  $('startBar').style.width = done / steps.length * 100 + '%';
  $('startList').innerHTML = steps.map(s => `<li><button class="pf-step${s.done ? ' done' : ''}" data-pf-goto="${s.go}" type="button"><i aria-hidden="true">${s.done ? '✓' : ''}</i><span>${esc(s.text)}${s.done ? '<span class="sr-only"> — готово</span>' : ''}</span></button></li>`).join('');
}

function renderProfileAccounts(accounts) {
  const id = currentUser.id;
  $('profileAccounts').innerHTML = accounts.map(a => {
    const me = a.user.id === id;
    return `<div class="account-row${me ? ' current' : ''}">${avatarHtml(a.user)}<div><b>${esc(a.user.full_name || a.user.email)}</b><small>${esc(a.user.email)}</small></div>${me ? '<span class="pill">Текущий</span>'
      : `<button class="btn soft small" data-switch="${esc(a.user.id)}" type="button">Перейти</button><button class="icon-btn" data-forget="${esc(a.user.id)}" type="button" title="Убрать с этого устройства" aria-label="Убрать аккаунт ${esc(a.user.email)} с этого устройства">✕</button>`}</div>`;
  }).join('');
  $('profileAccounts').querySelectorAll('[data-switch]').forEach(b => b.onclick = () => switchAccount(+b.dataset.switch));
  $('profileAccounts').querySelectorAll('[data-forget]').forEach(b => b.onclick = () => { forgetAccount(+b.dataset.forget); renderProfile(); renderAccountMenu(); });
}

function renderDataStats() {
  const items = receipts.reduce((s, r) => s + (r.items || []).length, 0), n = receipts.length, c = categories.length;
  $('dataStats').innerHTML = [['Чеков', n], ['Позиций', items], ['Категорий', c], ['Правил', categoryRules.length]]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${intText(v)}</dd></div>`).join('');
  $('exportAllJson').disabled = $('exportAllCsv').disabled = !n;
  $('deleteSummary').textContent = (n ? `Удалятся ${n} ${plural(n, 'чек', 'чека', 'чеков')} с фото, ` : 'Удалятся ')
    + `${c} ${plural(c, 'категория', 'категории', 'категорий')}, правила и лимиты. Восстановить их будет нельзя.`;
}
$('exportAllJson').onclick = () => {
  const u = currentUser;
  if (!u || !receipts.length) return;
  const data = {
    exported_at: new Date().toISOString(),
    user: { email: u.email, full_name: u.full_name, base_currency: baseCurrency(), created_at: u.created_at },
    receipts: receipts.map(({ iso, ...r }) => r), categories, category_rules: categoryRules, budgets,
  };
  downloadFile(JSON.stringify(data, null, 2), 'application/json', `checkai_export_${isoDay(new Date())}.json`);
  toast('Выгрузка готова');
};
$('exportAllCsv').onclick = () => exportCsv(receipts);

// --- Профиль: вкладки настроек ---
const PF_TABS = ['personal', 'security', 'accounts', 'look', 'data', 'danger'];
function openSettings(tab, scroll = false) {
  document.querySelectorAll('[data-pf-tab]').forEach(b => {
    const on = b.dataset.pfTab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.pf-panel').forEach(p => { p.hidden = p.id !== 'pfPanel-' + tab; });
  $('pfTab-' + tab).scrollIntoView({ block: 'nearest', inline: 'nearest' });
  if (scroll) $('profileSettings').scrollIntoView({ behavior: prefersReducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
}
$('pfTabs').onclick = e => { const b = e.target.closest('[data-pf-tab]'); if (b) openSettings(b.dataset.pfTab); };
$('pfTabs').onkeydown = e => {
  const i = PF_TABS.indexOf(document.activeElement?.dataset?.pfTab);
  const next = i < 0 ? null : { ArrowDown: i + 1, ArrowRight: i + 1, ArrowUp: i - 1, ArrowLeft: i - 1, Home: 0, End: PF_TABS.length - 1 }[e.key];
  if (next == null) return;
  e.preventDefault();
  const tab = PF_TABS[(next + PF_TABS.length) % PF_TABS.length];
  openSettings(tab);
  $('pfTab-' + tab).focus();
};
// Чипы, «Первые шаги» и пустые состояния ведут во вкладку настроек или в другой раздел
$('page-profile').addEventListener('click', e => {
  const b = e.target.closest('[data-pf-goto]');
  if (!b) return;
  const to = b.dataset.pfGoto;
  if (!PF_TABS.includes(to)) { showPage(to); return; }
  openSettings(to, true);
  if (to === 'personal') $('editName').focus({ preventScroll: true });
});
$('profileAddReceipt').onclick = () => showPage('upload');
$('addAccountBtn').onclick = () => openAuth('login');

// --- Профиль: формы ---
function fillProfileForm() {
  if (!currentUser) return;
  ['profileForm', 'securityForm', 'deleteForm'].forEach(id => $(id).reset());
  ['profileError', 'securityError', 'deleteError'].forEach(id => { $(id).hidden = true; });
  $('editName').value = currentUser.full_name || '';
  $('editBaseCurrency').innerHTML = currencyOptions(baseCurrency());
  $('editEmail').value = currentUser.email;
  hidePasswords();
  updatePasswordMeter();
  updatePersonalDirty();
}
// «Личное»: кнопки активны, только когда есть что сохранять
function personalChanges() {
  const u = currentUser, body = {};
  if (!u) return body;
  const name = $('editName').value.trim(), base = $('editBaseCurrency').value;
  if (name !== (u.full_name || '')) body.full_name = name || null;
  if (base && base !== baseCurrency()) body.base_currency = base;
  return body;
}
function updatePersonalDirty() {
  const dirty = Object.keys(personalChanges()).length > 0;
  $('profileSave').disabled = $('profileReset').disabled = !dirty;
  $('profileDirty').hidden = !dirty;
}
$('profileForm').oninput = $('profileForm').onchange = updatePersonalDirty;
$('profileReset').onclick = () => {
  $('editName').value = currentUser?.full_name || '';
  $('editBaseCurrency').value = baseCurrency();
  $('profileError').hidden = true;
  updatePersonalDirty();
};
// PATCH /api/auth/me отвечает новой парой токенов и пользователем
async function updateMe(body) {
  const data = await apiRequest('/api/auth/me', { method: 'PATCH', body: JSON.stringify(body) });
  if (data.access_token) setSession(data);
  const user = data.user || data;
  setUser(user);
  return user;
}
const updateMeError = err => notSupported(err) ? 'Сервер пока не поддерживает редактирование аккаунта (нужен PATCH /api/auth/me).' : err.message;
$('profileForm').onsubmit = async e => {
  e.preventDefault();
  if (!currentUser) return;
  $('profileError').hidden = true;
  const body = personalChanges();
  if (!Object.keys(body).length) return;
  $('profileSave').disabled = true;
  try {
    const user = await updateMe(body);
    if (body.base_currency) {
      // Старый сервер молча пропустит неизвестное поле — проверяем, что валюта действительно сменилась
      if (user.base_currency !== body.base_currency) { showFormError('profileError', 'Сервер пока не поддерживает основную валюту — остальные изменения сохранены.'); return; }
      // rate_to_base у чеков пересчитан сервером под новую валюту
      await loadReceipts();
    }
    toast('Данные сохранены');
  } catch (err) {
    showFormError('profileError', updateMeError(err));
  } finally { updatePersonalDirty(); }
};

// «Вход и пароль»: показ пароля и оценка его надёжности
function passwordScore(p) {
  if (!p) return 0;
  if (p.length < 8) return 1;
  let s = 1;
  if (p.length >= 12) s++;
  if (/[a-zа-яё]/.test(p) && /[A-ZА-ЯЁ]/.test(p)) s++;
  if (/\d/.test(p) && /[^a-zа-яё\d]/i.test(p)) s++;
  return s;
}
function updatePasswordMeter() {
  const p = $('editPassword').value, s = passwordScore(p);
  $('pwMeter').dataset.score = s;
  $('editPasswordHint').textContent = !p ? 'Минимум 8 символов' : p.length < 8 ? 'Слишком короткий — минимум 8 символов' : ['', 'Слабый', 'Средний', 'Хороший', 'Надёжный'][s];
}
$('editPassword').oninput = updatePasswordMeter;
function setPasswordShown(button, shown) {
  $(button.dataset.pwToggle).type = shown ? 'text' : 'password';
  button.textContent = shown ? 'Скрыть' : 'Показать';
  button.setAttribute('aria-label', shown ? 'Скрыть пароль' : 'Показать пароль');
}
const hidePasswords = () => document.querySelectorAll('[data-pw-toggle]').forEach(b => setPasswordShown(b, false));
document.querySelectorAll('[data-pw-toggle]').forEach(b => b.onclick = () => setPasswordShown(b, $(b.dataset.pwToggle).type === 'password'));
$('securityForm').onsubmit = async e => {
  e.preventDefault();
  const u = currentUser;
  if (!u) return;
  $('securityError').hidden = true;
  const email = $('editEmail').value.trim().toLowerCase(), password = $('editPassword').value, body = {};
  if (email !== u.email) body.email = email;
  if (password) body.new_password = password;
  if (!Object.keys(body).length) { showFormError('securityError', 'Укажите новый email или новый пароль'); return; }
  body.current_password = $('editCurrentPassword').value;
  const btn = $('securitySave');
  btn.disabled = true;
  try {
    await updateMe(body);
    toast(body.new_password ? 'Пароль обновлён' : 'Email обновлён');
  } catch (err) {
    showFormError('securityError', updateMeError(err));
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
    try {
      localStorage.removeItem(budgetsKey(u.id)); localStorage.removeItem(avatarKey(u));
      [insightsRetryKey, insightsHiddenKey, insightsSeenKey].forEach(k => localStorage.removeItem(k(u.id)));
    } catch (e) {}
    forgetAccount(u.id); removeToken(); setGuest(); showPage('home');
    toast('Аккаунт удалён');
  } catch (err) {
    showFormError('deleteError', notSupported(err) ? 'Сервер пока не поддерживает удаление аккаунта (нужен DELETE /api/auth/me).' : err.message);
  } finally { btn.disabled = false; }
};

// --- Фото профиля: выбор файла, камера, кадрирование ---
// Кадр — квадрат AVATAR_SIZE: фото накрывает круг целиком, его можно двигать и приближать
const AVATAR_SIZE = 256, AVATAR_MAX_FILE = 15 * 1024 * 1024;
let avatarStream = null, avatarCrop = null;
const avatarStep = step => { $('avatarModal').querySelector('.avatar-modal').dataset.step = step; $('avatarError').hidden = true; };
function stopCamera() {
  avatarStream?.getTracks().forEach(t => t.stop());
  avatarStream = null;
  $('avatarVideo').srcObject = null;
}
function openAvatar() {
  if (!currentUser) return;
  paintAvatar($('avatarCurrent'), currentUser);
  $('avatarRemove').hidden = !localAvatar(currentUser);
  avatarCrop = null;
  avatarStep('choose');
  openModal('avatarModal', $('avatarFileBtn'));
}
function closeAvatar() { stopCamera(); avatarCrop = null; closeModal('avatarModal'); }
function refreshAvatars() { paintAvatar($('userAvatar'), currentUser); renderProfile(); renderAccountMenu(); }
$('avatarBtn').onclick = openAvatar;
$('avatarClose').onclick = closeAvatar;

$('avatarFileBtn').onclick = () => $('avatarFile').click();
$('avatarFile').onchange = $('avatarCapture').onchange = e => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  if (!file.type.startsWith('image/')) return showFormError('avatarError', 'Это не изображение');
  if (file.size > AVATAR_MAX_FILE) return showFormError('avatarError', 'Файл больше 15 МБ');
  const url = URL.createObjectURL(file);
  startCrop(url, () => URL.revokeObjectURL(url));
};

// Камера: getUserMedia с превью; где его нет — системная камера через <input capture>
$('avatarCameraBtn').onclick = async () => {
  if (!navigator.mediaDevices?.getUserMedia) { $('avatarCapture').click(); return; }
  try {
    avatarStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
  } catch (err) {
    showFormError('avatarError', err.name === 'NotAllowedError' ? 'Нет доступа к камере — разрешите его в настройках браузера' : 'Камера не найдена или занята другим приложением');
    return;
  }
  if ($('avatarModal').hidden) { stopCamera(); return; }
  avatarStep('camera');
  const video = $('avatarVideo');
  // «Снять» доступна, когда камера отдала первый кадр
  $('avatarShoot').disabled = true;
  video.onloadeddata = () => { $('avatarShoot').disabled = false; $('avatarShoot').focus(); };
  video.srcObject = avatarStream;
  video.play().catch(() => {});
  $('avatarCameraCancel').focus();
};
$('avatarCameraCancel').onclick = () => { stopCamera(); avatarStep('choose'); $('avatarCameraBtn').focus(); };
$('avatarShoot').onclick = () => {
  const v = $('avatarVideo');
  if (!v.videoWidth) return;
  const c = document.createElement('canvas');
  c.width = v.videoWidth; c.height = v.videoHeight;
  const ctx = c.getContext('2d');
  // Снимок — как в превью, зеркально
  ctx.translate(c.width, 0); ctx.scale(-1, 1);
  ctx.drawImage(v, 0, 0);
  stopCamera();
  startCrop(c.toDataURL('image/jpeg', .92));
};

function startCrop(src, done) {
  const img = $('avatarCropImg');
  img.onload = () => {
    done?.();
    avatarStep('crop');
    const S = $('avatarStage').clientWidth, w = img.naturalWidth, h = img.naturalHeight;
    avatarCrop = { S, w, h, base: S / Math.min(w, h), z: 1, x: 0, y: 0 };
    $('avatarZoom').value = 1;
    layoutCrop();
    $('avatarStage').focus();
  };
  img.onerror = () => { done?.(); avatarStep('choose'); showFormError('avatarError', 'Не удалось открыть изображение. Попробуйте JPG или PNG'); };
  img.src = src;
}
// x, y — сдвиг центра фото от центра круга; фото не должно открывать края круга
function layoutCrop() {
  const c = avatarCrop, W = c.w * c.base * c.z, H = c.h * c.base * c.z;
  c.x = Math.max(-(W - c.S) / 2, Math.min((W - c.S) / 2, c.x));
  c.y = Math.max(-(H - c.S) / 2, Math.min((H - c.S) / 2, c.y));
  c.left = (c.S - W) / 2 + c.x; c.top = (c.S - H) / 2 + c.y; c.W = W; c.H = H;
  Object.assign($('avatarCropImg').style, { width: W + 'px', height: H + 'px', left: c.left + 'px', top: c.top + 'px' });
}
function setCropZoom(z) {
  avatarCrop.z = Math.min(3, Math.max(1, z));
  $('avatarZoom').value = avatarCrop.z;
  layoutCrop();
}
$('avatarZoom').oninput = e => { if (avatarCrop) setCropZoom(+e.target.value); };
$('avatarStage').onpointerdown = e => {
  if (!avatarCrop) return;
  const stage = e.currentTarget, sx = e.clientX - avatarCrop.x, sy = e.clientY - avatarCrop.y;
  stage.setPointerCapture(e.pointerId);
  stage.onpointermove = m => { avatarCrop.x = m.clientX - sx; avatarCrop.y = m.clientY - sy; layoutCrop(); };
  stage.onpointerup = stage.onpointercancel = () => { stage.onpointermove = null; };
};
$('avatarStage').addEventListener('wheel', e => { if (!avatarCrop) return; e.preventDefault(); setCropZoom(avatarCrop.z - e.deltaY * .002); }, { passive: false });
$('avatarStage').onkeydown = e => {
  if (!avatarCrop) return;
  const d = { ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10] }[e.key];
  if (d) { e.preventDefault(); avatarCrop.x += d[0]; avatarCrop.y += d[1]; layoutCrop(); }
  else if (e.key === '+' || e.key === '=') setCropZoom(avatarCrop.z + .1);
  else if (e.key === '-') setCropZoom(avatarCrop.z - .1);
};
$('avatarCropCancel').onclick = () => { avatarCrop = null; avatarStep('choose'); $('avatarFileBtn').focus(); };
$('avatarSave').onclick = () => {
  if (!avatarCrop) return;
  const c = avatarCrop, k = AVATAR_SIZE / c.S, cv = document.createElement('canvas');
  cv.width = cv.height = AVATAR_SIZE;
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage($('avatarCropImg'), c.left * k, c.top * k, c.W * k, c.H * k);
  try { localStorage.setItem(avatarKey(currentUser), cv.toDataURL('image/jpeg', .88)); }
  catch (e) { showFormError('avatarError', 'В браузере не хватило места для фото'); return; }
  refreshAvatars(); closeAvatar(); toast('Фото обновлено');
};
$('avatarRemove').onclick = () => {
  try { localStorage.removeItem(avatarKey(currentUser)); } catch (e) {}
  refreshAvatars(); closeAvatar(); toast('Фото удалено');
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
    // id сохранённого чека с тем же фото или фискальным номером — сервер сообщает при распознавании (C.2)
    duplicate_of: src.duplicate_of ?? null,
    // photo | manual: без фото чек считается введённым вручную (так же решает сервер)
    source: src.source || (src.image_path ? 'photo' : 'manual'),
    // Итог считается из позиций, пока его не поменяли руками; у распознанного и сохранённого чека итог свой
    totalAuto: !(num(src.total) > 0),
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
      // Откуда категория: ai | keywords | rule | user (PROFILE_TASKS.md, п. 2.1). Старый сервер поле пропустит
      if (i.category_source) item.category_source = i.category_source;
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
    source: draft.image_path ? 'photo' : 'manual',
    items,
  };
}
// Пределы — те же, что проверяет сервер (NEXT_WEEK_TASKS.md, A.2.2)
const MAX_AMOUNT = 1e9, MAX_QUANTITY = 100000;
// Текст ошибки или null, если черновик можно сохранять; field — поле позиции index, куда поставить фокус
function draftProblem(draft) {
  const named = draft.items.filter(i => i.name.trim());
  if (!named.length && !(num(draft.total) > 0)) return { message: 'Добавьте хотя бы одну позицию с названием или укажите итог чека', index: draft.items.length ? 0 : null };
  const unnamed = draft.items.findIndex(i => !i.name.trim() && num(i.total_price));
  if (unnamed >= 0) return { message: `У позиции №${unnamed + 1} нет названия`, index: unnamed };
  const amounts = [num(draft.total), ...draft.items.flatMap(i => [num(i.quantity), num(i.price_per_unit), num(i.total_price)])];
  if (amounts.some(n => n < 0)) return { message: 'Суммы и количество не могут быть отрицательными' };
  if (amounts.some(n => n > MAX_AMOUNT)) return { message: 'Слишком большое число: суммы — не больше 1 000 000 000' };
  const noQty = draft.items.findIndex(i => i.name.trim() && !(num(i.quantity) > 0));
  if (noQty >= 0) return { message: `У позиции №${noQty + 1} количество должно быть больше нуля`, index: noQty, field: 'quantity' };
  const bigQty = draft.items.findIndex(i => num(i.quantity) > MAX_QUANTITY);
  if (bigQty >= 0) return { message: `У позиции №${bigQty + 1} слишком большое количество (не больше ${intText(MAX_QUANTITY)})`, index: bigQty, field: 'quantity' };
  return null;
}
// Дата покупки в будущем — не ошибка, но переспрашиваем. false — пользователь передумал сохранять
function confirmDraft(draft) {
  if (!draft.date || draft.date <= isoDay(new Date())) return true;
  const d = parseDate(draft.date);
  return confirm(`Дата покупки ${d ? d.toLocaleDateString('ru-RU') : draft.date} — в будущем. Точно сохранить?`);
}
const numInput = (key, value, label) => `<label>${label}<input class="field" type="number" min="0" step="any" inputmode="decimal" data-k="${key}" value="${esc(value)}"></label>`;
// Подсказки в редакторе из уже загруженной истории: магазины и товары, у товара — последняя цена и категория
const HINTS_MAX = 400;
function historyHints() {
  const items = new Map(), stores = new Map();
  receipts.slice().sort((a, b) => String(b.iso || '').localeCompare(String(a.iso || ''))).forEach(r => {
    const store = String(r.store || '').trim();
    if (store && !stores.has(store.toLowerCase())) stores.set(store.toLowerCase(), store);
    (r.items || []).forEach(it => {
      const name = String(it.name || '').trim(), key = name.toLowerCase();
      if (!name || items.has(key)) return;
      items.set(key, { name, price: num(it.price_per_unit ?? it.total_price), currency: curOf(r), category_id: it.category_id ?? null, category: it.category ?? null });
    });
  });
  return { items, stores };
}
const datalist = (id, values) => `<datalist id="${esc(id)}">${values.slice(0, HINTS_MAX).map(v => `<option value="${esc(v)}"></option>`).join('')}</datalist>`;

// Автокатегория для вписанных руками названий: POST /api/categorize (NEXT_WEEK_TASKS.md, A.2.3)
const CATEGORIZE_DELAY_MS = 500;
// Нет эндпоинта (404/405) — больше не спрашиваем до перезагрузки страницы
let categorizeSupported = true;
async function categorizeNames(names) {
  if (!categorizeSupported || !currentUser) return null;
  try {
    const res = await apiRequest('/api/categorize', { method: 'POST', body: JSON.stringify({ items: names.map(name => ({ name })) }) });
    return Array.isArray(res?.items) ? res.items : null;
  } catch (e) {
    if (notSupported(e)) categorizeSupported = false;
    // 429, сеть, ИИ недоступен — категорию просто не подставляем
    return null;
  }
}
// Категорию, выбранную руками или взятую из истории (источник неизвестен), автокатегория не трогает
const AUTO_SOURCES = ['ai', 'keywords', 'rule'];
const canAutoCategorize = it => it.category_source !== 'user' && (itemCategory(it) === UNCATEGORIZED || AUTO_SOURCES.includes(it.category_source));
function catSourceHtml(source) {
  const text = source && CATEGORY_SOURCE_TEXT[source];
  return text ? `<small class="cat-source">${esc(text)}</small>` : '';
}

function mountEditor(root, draft) {
  const hints = historyHints(), listId = root.id + 'Items', storesId = root.id + 'Stores';
  root.innerHTML = `<div class="editor">
    <div class="edit-head">
      <label>Магазин<input class="field" data-f="store" maxlength="255" value="${esc(draft.store)}" placeholder="Название магазина" list="${esc(storesId)}" autocomplete="off"></label>
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
    ${datalist(storesId, [...hints.stores.values()])}${datalist(listId, [...hints.items.values()].map(h => h.name))}
  </div>`;
  // Обработчики вешаем на новый элемент, чтобы повторный mountEditor не дублировал их
  const editor = root.firstElementChild;
  const list = editor.querySelector('[data-items]'), sumBox = editor.querySelector('[data-sum]'), totalInput = editor.querySelector('[data-f="total"]');
  const rowOf = item => list.querySelector(`[data-i="${draft.items.indexOf(item)}"]`);
  const renderItems = () => {
    const sym = currencyInfo(draft.currency).symbol;
    editor.querySelector('[data-total-label]').textContent = `Итог чека, ${sym}`;
    list.innerHTML = draft.items.length ? draft.items.map((it, i) => `<div class="edit-item" data-i="${i}">
      <div class="edit-name">
        <label>Название<input class="field" data-k="name" maxlength="500" value="${esc(it.name)}" placeholder="Товар или услуга" list="${esc(listId)}" autocomplete="off"></label>
        <button class="icon-btn remove-item" type="button" data-remove aria-label="Удалить позицию ${esc(it.name || '№' + (i + 1))}">✕</button>
      </div>
      <div class="edit-nums">
        ${numInput('quantity', it.quantity, 'Кол-во')}${numInput('price_per_unit', it.price_per_unit, `Цена, ${esc(sym)}`)}${numInput('total_price', it.total_price, `Сумма, ${esc(sym)}`)}
        <label>Категория<select class="field" data-k="category">${categoryOptions(catKey(itemCategory(it)))}</select>${catSourceHtml(it.category_source)}</label>
      </div>
    </div>`).join('') : '<div class="empty">Позиций нет</div>';
    updateSum();
  };
  const updateSum = () => {
    const sum = itemsSum(draft.items);
    // Итог не трогали руками — он равен сумме позиций
    if (draft.totalAuto) { draft.total = sum; totalInput.value = sum; }
    const diff = round2(num(draft.total) - sum), mismatch = draft.items.length && sumMismatch(sum, num(draft.total), draft.currency);
    sumBox.innerHTML = `Сумма позиций: <b>${money(sum, draft.currency)}</b>` + (mismatch
      ? ` — итог не совпадает с позициями на ${money(Math.abs(diff), draft.currency)}. <button class="text-btn" type="button" data-fix-total>Сделать итогом</button>`
      : '');
  };
  // Категория в строке без перерисовки списка, чтобы не терять фокус в поле ввода
  const showCategory = item => {
    const row = rowOf(item);
    if (!row) return;
    const select = row.querySelector('[data-k="category"]');
    select.value = catKey(itemCategory(item));
    row.querySelector('.cat-source')?.remove();
    select.insertAdjacentHTML('afterend', catSourceHtml(item.category_source));
  };
  const scheduleCategorize = item => {
    clearTimeout(item._catTimer);
    const name = item.name.trim();
    if (name.length < 2 || !canAutoCategorize(item) || !categorizeSupported || !currentUser) return;
    item._catTimer = setTimeout(async () => {
      const found = (await categorizeNames([name]))?.[0];
      // Пока ждали ответ, строку удалили, переименовали или выбрали категорию руками
      if (!found || !draft.items.includes(item) || item.name.trim() !== name || !canAutoCategorize(item)) return;
      if (found.category_id == null && !found.category) return;
      Object.assign(item, { category_id: found.category_id ?? null, category: found.category ?? null, category_source: found.category_source || 'ai' });
      showCategory(item);
    }, CATEGORIZE_DELAY_MS);
  };
  // Выбрали товар из подсказки: последняя цена (если валюта та же) и категория из истории
  const applyHint = (item, row, hint) => {
    if (hint.currency === draft.currency && hint.price > 0 && !num(item.price_per_unit)) {
      item.price_per_unit = round2(hint.price);
      item.total_price = round2(num(item.quantity) * item.price_per_unit);
      row.querySelector('[data-k="price_per_unit"]').value = item.price_per_unit;
      row.querySelector('[data-k="total_price"]').value = item.total_price;
      updateSum();
    }
    if (item.category_source === 'user') return;
    const cat = hint.category_id != null ? categoryById(hint.category_id) : categories.find(c => c.name === hint.category) || UNCATEGORIZED;
    if (cat === UNCATEGORIZED) { scheduleCategorize(item); return; }
    clearTimeout(item._catTimer);
    Object.assign(item, { category_id: cat.id, category: cat.name, category_source: null });
    showCategory(item);
  };
  const addItem = () => {
    draft.items.push({ name: '', quantity: 1, price_per_unit: 0, total_price: 0, category_id: null, category: null, category_source: null });
    renderItems();
    list.querySelector(`[data-i="${draft.items.length - 1}"] [data-k="name"]`).focus();
  };
  editor.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.f) {
      draft[t.dataset.f] = t.dataset.f === 'total' ? num(t.value) : t.value;
      if (t.dataset.f === 'total') { draft.totalAuto = false; updateSum(); }
      // Подписи «Цена, $» и сумма позиций — в новой валюте
      if (t.dataset.f === 'currency') renderItems();
      return;
    }
    const row = t.closest('[data-i]');
    if (!row || !t.dataset.k) return;
    const item = draft.items[+row.dataset.i], k = t.dataset.k;
    if (k === 'category') {
      // Выбрали вручную — автокатегория эту позицию больше не трогает
      const c = categoryByKey(t.value);
      clearTimeout(item._catTimer);
      Object.assign(item, { category_id: c.id, category: c === UNCATEGORIZED ? null : c.name, category_source: 'user' });
      row.querySelector('.cat-source')?.remove();
      return;
    }
    if (k === 'name') {
      item.name = t.value;
      // Выбор из подсказки приходит без inputType или как insertReplacementText
      const hint = hints.items.get(t.value.trim().toLowerCase());
      if (hint && (!e.inputType || e.inputType === 'insertReplacementText')) applyHint(item, row, hint);
      else scheduleCategorize(item);
      return;
    }
    item[k] = num(t.value);
    // Количество или цена поменялись — пересчитываем сумму строки
    if (k === 'quantity' || k === 'price_per_unit') {
      item.total_price = round2(num(item.quantity) * num(item.price_per_unit));
      row.querySelector('[data-k="total_price"]').value = item.total_price;
    }
    // Сумму строки поправили руками — пересчитываем цену за единицу
    if (k === 'total_price' && num(item.quantity) > 0) {
      item.price_per_unit = round2(item.total_price / num(item.quantity));
      row.querySelector('[data-k="price_per_unit"]').value = item.price_per_unit;
    }
    updateSum();
  });
  // Enter в сумме или категории строки: в последней строке — новая позиция, иначе — к следующей
  editor.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing) return;
    const row = e.target.closest('[data-i]');
    if (!row || !['total_price', 'category'].includes(e.target.dataset.k)) return;
    e.preventDefault();
    const i = +row.dataset.i;
    if (i === draft.items.length - 1) addItem();
    else list.querySelector(`[data-i="${i + 1}"] [data-k="name"]`)?.focus();
  });
  editor.addEventListener('click', e => {
    if (e.target.closest('[data-add]')) addItem();
    else if (e.target.closest('[data-remove]')) {
      const i = +e.target.closest('[data-i]').dataset.i;
      clearTimeout(draft.items[i]._catTimer);
      draft.items.splice(i, 1);
      renderItems();
      // Фокус — на соседнюю строку или на «Добавить позицию»
      (list.querySelector(`[data-i="${Math.min(i, draft.items.length - 1)}"] [data-k="name"]`) || editor.querySelector('[data-add]')).focus();
    } else if (e.target.closest('[data-fix-total]')) {
      draft.totalAuto = true;
      updateSum();
      totalInput.focus();
    }
  });
  renderItems();
}
function focusDraftProblem(root, problem) {
  if (problem.index == null) return;
  root.querySelector(`[data-i="${problem.index}"] [data-k="${problem.field || 'name'}"]`)?.focus();
}

// --- Проверка на дубликат (NEXT_WEEK_TASKS.md, задача C) ---
// Тот же чек, сохранённый второй раз: та же валюта и итог, та же дата, время в пределах 2 минут,
// тот же магазин и почти те же позиции. Правила совпадают с crud.find_duplicate на сервере (C.2)
const DUP_TIME_MIN = 2, DUP_ITEMS_MIN = 0.6, DUP_NODATE_ITEMS_MIN = 0.8;
// Название магазина или товара для сравнения: регистр, ё, знаки и правовая форма (ТОО, ИП…) не важны
const normText = s => (' ' + String(s || '').toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9әғқңөұүһі]+/gi, ' ') + ' ')
  .replace(/ (тоо|ип|ао|too|llp|ltd|llc)(?= )/g, ' ').replace(/\s+/g, ' ').trim();
const minutesOf = s => { const m = String(s || '').match(/(\d{1,2}):(\d{2})/); return m ? +m[1] * 60 + +m[2] : null; };
const sameStore = (a, b) => !a || !b || a === b || a.includes(b) || b.includes(a);
// Доля совпавших позиций (название + сумма строки); null — у одного из чеков позиций нет
function itemsSimilarity(a, b) {
  const key = i => normText(i.name) + '|' + Math.round(num(i.total_price));
  const ka = a.filter(i => String(i.name || '').trim()).map(key), kb = b.filter(i => String(i.name || '').trim()).map(key);
  if (!ka.length || !kb.length) return null;
  const left = {};
  kb.forEach(k => { left[k] = (left[k] || 0) + 1; });
  const same = ka.filter(k => left[k] > 0 && left[k]--).length;
  return same / Math.max(ka.length, kb.length);
}
// Самый похожий сохранённый чек или null. excludeId — сам редактируемый чек
function findDuplicate(draft, excludeId = null) {
  const total = num(draft.total), cur = draft.currency;
  if (!(total > 0)) return null;
  const store = normText(draft.store), date = draft.date || '', time = minutesOf(draft.time);
  let best = null, bestScore = -1;
  receipts.forEach(r => {
    if (excludeId != null && String(r.id) === String(excludeId)) return;
    if (curOf(r) !== cur || sumMismatch(num(r.total), total, cur)) return;
    const rDate = toIsoDate(r.date), rTime = minutesOf(r.time), rStore = normText(r.store);
    if (date && rDate && date !== rDate) return;
    if (time != null && rTime != null && Math.abs(time - rTime) > DUP_TIME_MIN) return;
    if (!sameStore(store, rStore)) return;
    const sim = itemsSimilarity(draft.items, r.items || []);
    // Дата неизвестна у одного из чеков — нужен почти тот же состав
    if (!(date && rDate) && !(sim != null && sim >= DUP_NODATE_ITEMS_MIN)) return;
    if (sim != null && sim < DUP_ITEMS_MIN) return;
    const score = (sim ?? .5) + (time != null && rTime != null ? 1 : 0) + (store && rStore ? 1 : 0);
    if (score > bestScore) { best = r; bestScore = score; }
  });
  return best;
}
const receiptBrief = r => [r.store || 'Магазин не определён', fmtPurchase(r) || fmtDateTime(r.created_at), money(r.total, curOf(r))].join(' · ');
// Окно «Похоже, этот чек уже есть»: 'cancel' | 'open' | 'save'. existing может быть null (сервер не прислал чек)
let duplicateResolve = null;
function askDuplicate(existing, canOpen = true) {
  const items = existing?.items?.length || 0;
  $('duplicateText').textContent = existing
    ? 'В истории уже есть чек с той же суммой, датой и магазином. Если это другая покупка, сохраните его ещё раз.'
    : 'Сервер нашёл в истории такой же чек. Если это другая покупка, сохраните его ещё раз.';
  $('duplicateCard').hidden = !existing;
  $('duplicateCard').innerHTML = existing
    ? `<b>${esc(existing.store || 'Магазин не определён')}${isManual(existing) ? ' ' + MANUAL_BADGE : ''}</b><span>${esc(fmtPurchase(existing) || '')}</span><strong>${esc(money(existing.total, curOf(existing)))}</strong><small>${items} ${plural(items, 'позиция', 'позиции', 'позиций')} · сохранён ${esc(fmtDateTime(existing.created_at))}</small>`
    : '';
  $('duplicateOpen').hidden = !existing || !canOpen;
  duplicateResolve?.('cancel');
  openModal('duplicateModal', $('duplicateCancel'));
  return new Promise(resolve => { duplicateResolve = resolve; });
}
function resolveDuplicate(choice) {
  const resolve = duplicateResolve;
  duplicateResolve = null;
  closeModal('duplicateModal');
  resolve?.(choice);
}
$('duplicateCancel').onclick = $('duplicateClose').onclick = () => resolveDuplicate('cancel');
$('duplicateOpen').onclick = () => resolveDuplicate('open');
$('duplicateSave').onclick = () => resolveDuplicate('save');
$('duplicateModal').onclick = e => { if (e.target.id === 'duplicateModal') resolveDuplicate('cancel'); };
// Сохранение с проверкой: сначала по загруженной истории, затем сервер может ответить 409 (C.2).
// «Всё равно сохранить» — повтор с allow_duplicate: true. Возвращает {saved} или {choice, existing}
async function sendReceipt(url, method, draft, excludeId = null) {
  const payload = toPayload(draft), canOpen = excludeId == null;
  const local = findDuplicate(draft, excludeId);
  if (local) {
    const choice = await askDuplicate(local, canOpen);
    if (choice !== 'save') return { choice, existing: local };
    payload.allow_duplicate = true;
  }
  const send = () => apiRequest(url, { method, body: JSON.stringify(payload) });
  try { return { saved: await send() }; }
  catch (e) {
    if (e.status !== 409 || payload.allow_duplicate) throw e;
    const id = e.data?.duplicate_of, existing = e.data?.duplicate || receipts.find(r => String(r.id) === String(id)) || null;
    const choice = await askDuplicate(existing, canOpen);
    if (choice !== 'save') return { choice, existing };
    payload.allow_duplicate = true;
    return { saved: await send() };
  }
}
// Открыть уже сохранённый чек; если его нет в загруженной истории (прислал сервер) — сначала обновить историю
async function openExisting(existing) {
  if (!existing) return;
  if (!receipts.some(r => String(r.id) === String(existing.id))) await loadReceipts();
  openReceipt(existing.id);
}

// --- Подробности покупки (чека) ---
// Чек, введённый без фото (поле source с сервера, NEXT_WEEK_TASKS.md, A.2.1)
const isManual = r => r?.source === 'manual';
const MANUAL_BADGE = '<span class="pill pill-manual">вручную</span>';
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
  $('rmMeta').innerHTML = (isManual(r) ? MANUAL_BADGE : '') + [r.category, foreign && cur, items.length + ' позиций', 'Сохранён ' + fmtDateTime(r.created_at)].filter(Boolean).map(t => `<span class="pill">${esc(t)}</span>`).join('');
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
  if (!confirmDraft(editDraft)) return;
  const id = openedReceipt.id, btn = $('rmEditSave');
  btn.disabled = true;
  try {
    // Правка могла сделать чек копией другого (например, поменяли дату) — спрашиваем
    const res = await sendReceipt('/api/receipts/' + id, 'PUT', editDraft, id);
    if (!res.saved) return;
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
    // Чеки поменялись — обновляем пометку «устарело» у рекомендаций
    if (insights.loaded && !insights.unsupported) loadInsights();
  } catch (e) { toast('Не удалось загрузить историю: ' + e.message); }
}
async function checkAuthOnStartup() {
  if (!getToken()) { authPending = false; setGuest(); return; }
  try {
    const user = await apiRequest('/api/auth/me');
    authPending = false;
    setUser(user);
    await loadReceipts();
  } catch (e) { authPending = false; setGuest(); if (getToken()) toast('Не удалось связаться с сервером'); }
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
    if ($('page-home').classList.contains('active')) showPage('upload');
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
  $('menuBtn').setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
  if (open) $('drawer').querySelector('.nav-link.active, .nav-link')?.focus();
}
function closeMenu() { if ($('drawer').classList.contains('open')) setMenu(false); }
$('menuBtn').onclick = () => setMenu(!$('drawer').classList.contains('open'));
$('overlay').onclick = closeMenu;
document.querySelectorAll('.nav-link').forEach(b => b.onclick = () => showPage(b.dataset.page));
$('brandLink').onclick = () => showPage(currentUser ? 'upload' : 'home');
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
    $('stepsNote').textContent = `${seconds} с`;
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
  ['analyzeBtn', 'removeBtn', 'testBtn', 'chooseBtn', 'cameraBtn', 'manualBtn'].forEach(id => { $(id).disabled = on; });
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
  showResultEditor();
  // Валюту не нашли на самом чеке — просим проверить
  const name = currencyInfo(lastResult.currency).name;
  const hint = { default: `Валюта на чеке не найдена, поставили «${name}». Проверьте поле «Валюта» под позициями.`, country: `Валюта определена по стране чека: «${name}». Если платили в другой валюте, поменяйте её под позициями.` }[lastResult.currency_source];
  $('currencyHint').textContent = hint || '';
  $('currencyHint').hidden = !hint;
  scrollToResult();
}
const scrollToResult = () => { if (window.matchMedia('(max-width: 850px)').matches) $('resultCard').scrollIntoView({ behavior: 'smooth', block: 'start' }); };
function showResultEditor() {
  $('resultPlaceholder').hidden = true;
  $('resultContent').hidden = false;
  $('resultClose').hidden = false;
  $('resultError').hidden = true;
  $('resultKicker').lastChild.textContent = lastResult.image_path || lastResult.currency_source ? 'Результат' : 'Новый чек';
  mountEditor($('resultEditor'), lastResult);
  showDuplicateHint();
}
// Сразу после распознавания: тот же чек уже есть в истории (по фото/фискальному номеру от сервера или по содержимому)
function showDuplicateHint() {
  const d = lastResult, byServer = d?.duplicate_of != null ? receipts.find(r => String(r.id) === String(d.duplicate_of)) : null;
  const dup = d ? byServer || findDuplicate(d) : null;
  $('duplicateHint').hidden = !dup;
  $('duplicateHint').dataset.id = dup ? dup.id : '';
  if (dup) $('duplicateHintText').textContent = (byServer ? 'Этот чек уже загружали: ' : 'Похоже, этот чек уже сохранён: ') + receiptBrief(dup) + '.';
}
$('duplicateHintOpen').onclick = () => { const id = $('duplicateHint').dataset.id; if (id) openReceipt(id); };
// «Ввести вручную»: пустой черновик — сегодня, сейчас, основная валюта, одна пустая строка
function startManualReceipt() {
  if (analyzing) return;
  if (lastResult && !confirm('Заменить текущий черновик чека пустым?')) return;
  const now = new Date();
  lastResult = toDraft({ date: isoDay(now), time: `${pad2(now.getHours())}:${pad2(now.getMinutes())}`, currency: baseCurrency(), total: 0, items: [{ name: '', quantity: 1, price_per_unit: 0, total_price: 0 }] });
  showResultEditor();
  $('currencyHint').hidden = true;
  scrollToResult();
  $('resultEditor').querySelector('[data-f="store"]').focus({ preventScroll: true });
}
$('manualBtn').onclick = startManualReceipt;
// Обратное к renderResult: после сохранения чек нельзя сохранить второй раз
function resetResult() {
  lastResult = null;
  $('resultContent').hidden = true;
  $('resultClose').hidden = true;
  $('resultPlaceholder').hidden = false;
  $('resultEditor').innerHTML = '';
  $('resultError').hidden = true;
  $('duplicateHint').hidden = true;
  $('resultKicker').lastChild.textContent = 'Результат';
  $('currencyHint').hidden = true;
  clearFile();
}
// Крестик в шапке карточки: убрать ручной ввод или распознанный, но не сохранённый чек
const draftTouched = d => !!(d.store.trim() || num(d.total) > 0 || d.image_path || d.items.some(i => i.name.trim() || num(i.total_price)));
$('resultClose').onclick = () => {
  if (!lastResult || $('saveReceiptBtn').disabled) return;
  if (draftTouched(lastResult) && !confirm('Убрать черновик чека? Он ещё не сохранён.')) return;
  resetResult();
  // Фокус — туда, откуда обычно начинают: «Ввести вручную» или «Выбрать»
  ($('manualBtn').offsetParent ? $('manualBtn') : $('chooseBtn')).focus();
};
// Гость может распознавать чеки, а для сохранения просим войти
async function saveReceipt() {
  if (!lastResult) return;
  $('resultError').hidden = true;
  const problem = draftProblem(lastResult);
  if (problem) { showFormError('resultError', problem.message); focusDraftProblem($('resultEditor'), problem); return; }
  if (!getToken()) { pendingSave = true; openAuth('login'); toast('Войдите, чтобы сохранить чек'); return; }
  if (!confirmDraft(lastResult)) return;
  const btn = $('saveReceiptBtn');
  btn.disabled = true;
  try {
    // В ответе — сохранённый чек с курсом к основной валюте (rate_to_base), он нужен для бюджетов
    const res = await sendReceipt('/api/receipts/save', 'POST', lastResult);
    // Не стали сохранять копию: черновик остаётся, его можно поправить или убрать
    if (!res.saved) { if (res.choice === 'open') openExisting(res.existing); return; }
    const saved = res.saved;
    await loadReceipts();
    resetResult();
    const alert = budgetAlert(saved);
    $('savedModalText').textContent = alert;
    $('savedModalText').hidden = !alert;
    openModal('savedModal', $('savedToHistory'));
  } catch (e) {
    if (!getToken()) { pendingSave = true; openAuth('login'); }
    // Ошибки проверки на сервере (422) — рядом с редактором, как есть
    showFormError('resultError', 'Не удалось сохранить: ' + e.message);
    toast('Ошибка при сохранении: ' + e.message);
  } finally { btn.disabled = false; }
}
$('saveReceiptBtn').onclick = saveReceipt;
$('savedToHistory').onclick = () => { closeModal('savedModal'); showPage('history'); };
$('savedStay').onclick = $('savedModalClose').onclick = () => closeModal('savedModal');
$('savedModal').onclick = e => { if (e.target.id === 'savedModal') closeModal('savedModal'); };
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
// Фильтр по категории: чеки, где есть позиция этой категории (ключ catKey, 'all' — все)
const historyCategory = () => $('historyCategory').value;
const hasCategory = (r, key) => key === 'all' || (r.items || []).some(i => catKey(itemCategory(i)) === key);
const historyList = () => {
  const q = $('historySearch').value.trim().toLowerCase(), cat = historyCategory();
  return filtered($('historyPeriod').value).filter(r => matchesSearch(r, q) && hasCategory(r, cat));
};
// Список категорий в фильтре — по текущим категориям; выбранная сохраняется, если она ещё есть
function fillHistoryCategories() {
  const sel = $('historyCategory'), cur = sel.value, keys = [...categories, UNCATEGORIZED].map(catKey);
  sel.innerHTML = '<option value="all">Все категории</option>' + categoryOptions(null);
  sel.value = keys.includes(cur) ? cur : 'all';
}
// Открыть историю с фильтром: из привычек профиля и карточек рекомендаций
function openHistory({ search = '', period = 'all', category = 'all' } = {}) {
  $('historySearch').value = search;
  $('historyPeriod').value = period;
  fillHistoryCategories();
  $('historyCategory').value = category;
  if ($('historyCategory').value !== category) $('historyCategory').value = 'all';
  showPage('history');
  resetHistoryPage();
}
function renderHistory() {
  fillHistoryCategories();
  const list = historyList(), sum = baseSummary(list), searching = !!$('historySearch').value.trim() || historyCategory() !== 'all';
  // С фильтром по категории «Всего потрачено» — только позиции этой категории
  const cat = historyCategory(), total = cat === 'all' ? sum.total : sumsByCategory(list)[cat] || 0;
  // Сводка — в основной валюте; средний чек — по чекам, которые удалось пересчитать
  $('historyTotal').textContent = approx(money(total), sum.converted);
  $('historyCount').textContent = list.length;
  $('historyAverage').textContent = approx(money(sum.counted ? total / sum.counted : 0), sum.converted);
  showSkipped('historySkipped', sum.skipped);
  $('exportCsvBtn').disabled = !list.length;
  const shown = list.slice(0, historyShown);
  let empty;
  if (!currentUser) empty = '<div class="card empty"><button class="text-btn" data-open-auth type="button">Войдите</button>, чтобы видеть историю.</div>';
  else if (searching) empty = '<div class="card empty">Ничего не найдено</div>';
  else empty = '<div class="card empty">Чеков пока нет</div>';
  $('historyList').innerHTML = shown.length
    ? shown.map(r => `<article class="card receipt-row" data-open="${esc(r.id)}" tabindex="0" aria-label="Подробности покупки: ${esc(r.store || 'магазин не определён')}, ${esc(money(r.total, curOf(r)))}${isManual(r) ? ', введён вручную' : ''}"><div><h3>${esc(r.store || 'Магазин не определён')}${isManual(r) ? ' ' + MANUAL_BADGE : ''}</h3><p>${esc(fmtPurchase(r) || fmtDateTime(r.created_at))} · ${(r.items || []).length} позиций</p></div><div class="receipt-amount">${esc(money(r.total, curOf(r)))}</div><button class="text-btn" data-delete="${esc(r.id)}" type="button" aria-label="Удалить чек ${esc(r.store || '')}">Удалить</button></article>`).join('')
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
$('historyPeriod').onchange = $('historyCategory').onchange = resetHistoryPage;
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
// По умолчанию — то, что видно в истории с фильтрами; из профиля — все чеки
function exportCsv(list = historyList()) {
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
$('exportCsvBtn').onclick = () => exportCsv();

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
    : '<div class="card empty">Категорий нет</div>';
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
  const desc = c.description || (own ? '' : 'Не распределено');
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
  if (!rows.length) return `<div class="card cat-items" id="${esc(id)}"><div class="empty">Пусто</div></div>`;
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
    : '<p class="rules-empty">Правил пока нет</p>';
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
    : '<div class="empty">Нет данных</div>';
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
    $('budgetOverall').innerHTML = '<div class="card empty"><button class="text-btn" type="button" data-budget-edit>Задать лимиты</button></div>';
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

// --- Рекомендации ИИ (NEXT_WEEK_TASKS.md, B.3) ---
// Цифры считает сервер, ИИ только пишет текст. Фронт показывает ответ InsightsOut и ничего не пересчитывает.
// Текст модели выводится только через esc(): в названиях магазинов и товаров может оказаться что угодно
const INSIGHTS_MIN_RECEIPTS = 5, INSIGHTS_POLL_MS = 2000, INSIGHTS_POLL_MAX = 90;
const INSIGHT_ICONS = { over_budget: '⚠', budget_forecast: '◷', category_growth: '↗', frequent_small: '☕', recurring: '↻', budget_suggestion: '◎', uncategorized: '◌', unconverted: '¤' };
// Советы про текущий месяц: «Смотреть траты» открывает историю за месяц
const INSIGHT_MONTH_TYPES = ['over_budget', 'budget_forecast', 'category_growth', 'frequent_small'];
// loaded — первый GET прошёл; unsupported — на сервере нет /api/insights (404/405), блок не показываем
const emptyInsights = userId => ({ userId, loaded: false, unsupported: false, data: null, running: false, error: '', consented: false, retryAt: readInsightsRetry(userId), retryTimer: null, poll: null });
const insightsRetryKey = id => 'checkai_insights_retry_' + id;
const insightsHiddenKey = id => 'checkai_insights_hidden_' + id;
const insightsSeenKey = id => 'checkai_insights_seen_' + id;
function readInsightsRetry(id) { try { return Number(localStorage.getItem(insightsRetryKey(id))) || 0; } catch (e) { return 0; } }
const readIdList = key => { try { const v = JSON.parse(localStorage.getItem(key)); return Array.isArray(v) ? v.map(String) : []; } catch (e) { return []; } };
const writeIdList = (key, list) => { try { localStorage.setItem(key, JSON.stringify(list.slice(-200))); } catch (e) {} };
let insights = emptyInsights(null);

// Заглушка по контракту InsightsOut, пока бэкенд не готов: localStorage.setItem('checkai_insights_stub', '1')
const INSIGHTS_STUB = (() => { try { return localStorage.getItem('checkai_insights_stub') === '1'; } catch (e) { return false; } })();
const insightsStub = { data: null, doneAt: 0 };
function stubInsightsData() {
  const [a, b] = categories, month = monthKey(new Date()), items = [];
  if (a) items.push({ id: `over_budget:${a.id}:${month}`, type: 'over_budget', priority: 1, title: `${a.name}: лимит превышен на 7 000 ₸`, text: 'Потрачено 32 000 ₸ при лимите 25 000 ₸. Больше всего — 14 покупок на 21 000 ₸.', category_id: a.id, action: { kind: 'open_history', category_id: a.id } });
  if (b) items.push({ id: `budget_suggestion:${b.id}:${month}`, type: 'budget_suggestion', priority: 2, title: `Поставить лимит на «${b.name}»`, text: 'Последние 3 месяца вы стабильно тратите 22–24 тысячи. Лимит 25 000 ₸ поможет держать это под контролем.', category_id: b.id, action: { kind: 'set_budget', category_id: b.id, amount: 25000 } });
  items.push({ id: `uncategorized::${month}`, type: 'uncategorized', priority: 3, title: '18% трат без категории', text: 'Разложите их по категориям — советы и лимиты станут точнее.', category_id: null, action: { kind: 'open_categories' } });
  return { insights: { summary: 'Это пример по контракту: сервер рекомендаций ещё не подключён.', items }, created_at: new Date().toISOString(), model: 'rules', stale: false };
}
const insightsApi = {
  get: () => INSIGHTS_STUB ? Promise.resolve(insightsStub.data || { insights: null }) : apiRequest('/api/insights'),
  start: () => {
    if (!INSIGHTS_STUB) return apiRequest('/api/insights', { method: 'POST' });
    insightsStub.doneAt = Date.now() + 3000;
    return Promise.resolve({ task_id: 'stub' });
  },
  task: id => {
    if (!INSIGHTS_STUB) return apiRequest('/api/tasks/' + encodeURIComponent(id));
    if (Date.now() < insightsStub.doneAt) return Promise.resolve({ status: 'processing' });
    insightsStub.data = stubInsightsData();
    return Promise.resolve({ status: 'completed' });
  },
};

// Новый пользователь — новое состояние; тот же (после PATCH /api/auth/me) — состояние остаётся
function resetInsights(userId) {
  clearTimeout(insights.poll); clearTimeout(insights.retryTimer);
  insights = emptyInsights(userId);
}
async function loadInsights() {
  const user = currentUser;
  if (!user) return;
  try {
    const data = await insightsApi.get();
    if (insights.userId !== user.id) return;
    insights.data = data?.insights ? data : null;
    insights.unsupported = false;
  } catch (e) {
    if (insights.userId !== user.id) return;
    if (notSupported(e)) insights.unsupported = true;
  }
  insights.loaded = true;
  renderInsights();
}
// Согласие хранит сервер: preferences.ai_insights или временная колонка ai_insights_consent (B.2.5).
// Есть готовый анализ — значит, согласие уже было
const insightsConsent = () => !!(insights.consented || currentUser?.preferences?.ai_insights || currentUser?.ai_insights_consent || insights.data);
async function grantInsightsConsent() {
  const user = currentUser;
  if (!user || insights.running) return;
  insights.error = '';
  if (!INSIGHTS_STUB) {
    try { await updateMe({ preferences: { ...(user.preferences || {}), ai_insights: true }, ai_insights_consent: true }); }
    catch (e) {
      if (!notSupported(e)) { insights.error = updateMeError(e); renderInsights(); return; }
    }
  }
  if (insights.userId !== user.id) return;
  insights.consented = true;
  startInsights();
}
function setInsightsRetry(seconds) {
  insights.retryAt = Date.now() + Math.max(60, num(seconds) || 3600) * 1000;
  try { localStorage.setItem(insightsRetryKey(insights.userId), String(insights.retryAt)); } catch (e) {}
}
async function startInsights() {
  const user = currentUser;
  if (!user || insights.running || insights.retryAt > Date.now()) return;
  insights.error = '';
  insights.running = true;
  renderInsights();
  try {
    const res = await insightsApi.start();
    if (insights.userId !== user.id) return;
    if (res?.task_id) { pollInsights(res.task_id, user); return; }
    // Данные не изменились — сервер сразу вернул прежний анализ
    if (res?.insights) insights.data = res;
    insights.running = false;
  } catch (e) {
    if (insights.userId !== user.id) return;
    insights.running = false;
    if (e.status === 429) setInsightsRetry(e.data?.retry_after ?? e.data?.detail?.retry_after);
    else if (e.status === 403) { insights.consented = false; insights.error = e.message; }
    else if (notSupported(e)) insights.error = 'Сервер пока не умеет готовить рекомендации';
    else insights.error = e.message;
  }
  renderInsights();
}
// Генерация занимает 5–30 с: опрашиваем GET /api/tasks/{id} раз в 2 с
function pollInsights(taskId, user) {
  let tries = 0;
  const finish = error => {
    if (insights.userId !== user.id) return;
    insights.running = false;
    insights.error = error;
    renderInsights();
  };
  const tick = async () => {
    if (insights.userId !== user.id) return;
    let task = null;
    try { task = await insightsApi.task(taskId); }
    catch (e) {
      // Сеть или 5xx — пробуем ещё; задача пропала (404) — сообщаем
      if (e.status && e.status < 500) return finish(e.status === 404 ? 'Анализ прервался. Попробуйте ещё раз' : e.message);
    }
    if (insights.userId !== user.id) return;
    if (task?.status === 'completed') {
      await loadInsights();
      if (!insights.data && task.result?.insights) insights.data = task.result;
      return finish('');
    }
    if (task?.status === 'failed') return finish(task.error || 'Не удалось подготовить советы. Попробуйте позже');
    if (++tries >= INSIGHTS_POLL_MAX) return finish('Анализ идёт слишком долго. Загляните сюда чуть позже');
    insights.poll = setTimeout(tick, INSIGHTS_POLL_MS);
  };
  insights.poll = setTimeout(tick, INSIGHTS_POLL_MS);
}

const clip = (s, n) => { const t = String(s ?? '').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
const waitText = ms => ms >= 3600e3 ? `${Math.ceil(ms / 3600e3)} ч` : `${Math.max(1, Math.ceil(ms / 60e3))} мин`;
const insightsModel = m => { const s = String(m || ''); return /^gemini/i.test(s) ? 'Gemini' : /^ollama/i.test(s) ? 'Ollama' : s === 'rules' ? 'базовые правила' : s; };
// Карточки по приоритету, без скрытых пользователем
function visibleInsights() {
  const items = insights.data?.insights?.items;
  if (!Array.isArray(items) || !insights.userId) return [];
  const hidden = readIdList(insightsHiddenKey(insights.userId));
  return items.filter(it => it && it.id != null && !hidden.includes(String(it.id))).sort((a, b) => num(a.priority) - num(b.priority));
}
function insightAction(it) {
  const a = it.action || {}, cat = a.category_id != null ? categoryById(Number(a.category_id)) : null;
  if (a.kind === 'set_budget') {
    // Категорию удалили или сумма странная — кнопку не показываем
    if (!cat || cat === UNCATEGORIZED || !(num(a.amount) > 0)) return '';
    if (budgets.categories[catKey(cat)] === round2(a.amount)) return '<span class="ins-done">✓ Лимит поставлен</span>';
    return `<button class="btn small" type="button" data-ins-act="set_budget" data-cat="${esc(catKey(cat))}" data-amount="${esc(round2(a.amount))}">Поставить лимит ${esc(money(a.amount))}</button>`;
  }
  if (a.kind === 'open_history') {
    const key = cat && cat !== UNCATEGORIZED ? catKey(cat) : 'all';
    return `<button class="btn soft small" type="button" data-ins-act="open_history" data-cat="${esc(key)}" data-period="${INSIGHT_MONTH_TYPES.includes(it.type) ? 'month' : 'all'}">Смотреть траты</button>`;
  }
  if (a.kind === 'open_categories') return '<button class="btn soft small" type="button" data-ins-act="open_categories">Разобрать</button>';
  return '';
}
const insightCard = it => `<article class="ins-card" data-insight="${esc(it.id)}">
    <span class="ins-ico" aria-hidden="true">${esc(INSIGHT_ICONS[it.type] || '✦')}</span>
    <div class="ins-main">
      <h3>${esc(clip(it.title, 90))}</h3>
      <p>${esc(clip(it.text, 400))}</p>
      <div class="ins-actions">${insightAction(it)}<button class="text-btn" type="button" data-ins-hide aria-label="Скрыть совет «${esc(clip(it.title, 90))}»">Скрыть</button></div>
    </div>
  </article>`;
const INSIGHTS_SKELETON = '<div class="ins-cards" aria-hidden="true">' + '<div class="ins-card ins-skeleton"><span class="ins-ico"></span><div class="ins-main"><i></i><i></i><i></i></div></div>'.repeat(3) + '</div>';

function renderInsights() {
  renderInsightsTeaser();
  const box = $('insights'), u = currentUser;
  box.hidden = !u || !insights.loaded || insights.unsupported;
  clearTimeout(insights.retryTimer);
  if (box.hidden) return;
  const data = insights.data, wait = insights.retryAt - Date.now(), blocked = wait > 0;
  // Пока действует 429, раз в минуту обновляем «через N ч» и в конце снова включаем кнопку
  if (blocked) insights.retryTimer = setTimeout(renderInsights, Math.min(wait, 60e3) + 100);
  const refresh = $('insightsRefresh');
  // При «устарело» кнопка обновления — в плашке, вторая в шапке не нужна
  refresh.hidden = !data || insights.running || !!data.stale;
  refresh.disabled = blocked;
  refresh.textContent = blocked ? `Обновить через ${waitText(wait)}` : 'Обновить';
  const startBtn = label => `<button class="btn" type="button" data-ins-start ${blocked ? 'disabled' : ''}>${blocked ? `Можно через ${waitText(wait)}` : label}</button>`;
  let html;
  if (insights.running) html = `${INSIGHTS_SKELETON}<p class="ins-status" role="status"><span class="ins-spin" aria-hidden="true"></span>Анализируем расходы…</p>`;
  else if (!data && receipts.length < INSIGHTS_MIN_RECEIPTS) {
    const n = INSIGHTS_MIN_RECEIPTS - receipts.length;
    html = `<div class="ins-intro"><p class="ins-lead">Добавьте ещё ${n} ${plural(n, 'чек', 'чека', 'чеков')} — советам нужна история.</p><div class="button-row"><button class="btn soft" type="button" data-ins-upload>＋ Добавить чек</button></div></div>`;
  } else if (!data && !insightsConsent()) {
    html = `<div class="ins-intro">
      <p class="ins-lead">ИИ проанализирует ваши расходы и даст советы: где перерасход, какой лимит поставить, что повторяется каждый месяц.</p>
      <p class="ins-note">Для этого во внешний сервис (Google Gemini) уйдёт обезличенная сводка — только суммы по категориям и магазинам и ваши лимиты. Имя, email и фото чеков не передаются.</p>
      <div class="button-row"><button class="btn" type="button" data-ins-consent ${blocked ? 'disabled' : ''}>Разрешить и проанализировать</button></div>
    </div>`;
  } else if (!data) {
    html = `<div class="ins-intro"><p class="ins-lead">Посмотрим, что можно улучшить в расходах.</p><div class="button-row">${startBtn('Проанализировать')}</div></div>`;
  } else {
    const items = visibleInsights(), all = data.insights.items || [], when = data.created_at ? serverDate(data.created_at) : null;
    const updated = when && !Number.isNaN(+when) ? `Обновлено ${when.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })} в ${when.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : '';
    html = (data.stale ? `<div class="ins-stale" role="status"><span>Есть новые чеки — обновить советы?</span>${blocked ? `<small>Можно через ${waitText(wait)}</small>` : '<button class="text-btn" type="button" data-ins-start>Обновить</button>'}</div>` : '')
      + (data.insights.summary ? `<p class="ins-summary">${esc(clip(data.insights.summary, 400))}</p>` : '')
      + (items.length ? `<div class="ins-cards">${items.map(insightCard).join('')}</div>`
        : all.length ? '<p class="ins-empty">Все советы скрыты. <button class="text-btn" type="button" data-ins-unhide>Показать снова</button></p>'
        : '<p class="ins-empty">Особых советов нет — расходы выглядят ровно.</p>')
      + `<p class="ins-foot">${esc([updated, insightsModel(data.model)].filter(Boolean).join(' · '))}${data.model === 'rules' && !INSIGHTS_STUB ? '<br>ИИ сейчас недоступен — показаны базовые советы' : ''}</p>`;
  }
  if (insights.error) html += `<div class="auth-error" role="alert">${esc(insights.error)}</div>`;
  $('insightsBody').innerHTML = html;
  // Раздел открыт — советы просмотрены, тизер в профиле перестаёт называть их новыми
  if (data && $('page-analysis').classList.contains('active')) {
    const seen = readIdList(insightsSeenKey(insights.userId)), ids = visibleInsights().map(i => String(i.id)).filter(id => !seen.includes(id));
    if (ids.length) { writeIdList(insightsSeenKey(insights.userId), [...seen, ...ids]); renderInsightsTeaser(); }
  }
}
// Карточка в профиле: «2 новых совета →»
function renderInsightsTeaser() {
  const el = $('profileInsights');
  el.hidden = !currentUser || !insights.loaded || insights.unsupported;
  if (el.hidden) return;
  const items = visibleInsights(), seen = readIdList(insightsSeenKey(insights.userId)), fresh = items.filter(i => !seen.includes(String(i.id))).length;
  $('profileInsightsText').textContent = insights.running ? 'Анализируем расходы…'
    : fresh ? `${fresh} ${plural(fresh, 'новый совет', 'новых совета', 'новых советов')}`
    : items.length ? `${items.length} ${plural(items.length, 'совет', 'совета', 'советов')} по расходам`
    : 'ИИ-советы по вашим расходам';
}
async function applyBudgetSuggestion(btn) {
  const key = btn.dataset.cat, amount = Number(btn.dataset.amount);
  if (!currentUser || !(amount > 0)) return;
  btn.disabled = true;
  try {
    await saveBudgets({ total: budgets.total, categories: { ...budgets.categories, [key]: amount } });
    renderBudgets(); renderStartSteps(); renderInsights();
    toast(`Лимит на «${categoryByKey(key).name}» — ${money(amount)} в месяц`);
    // Лимиты поменялись — сервер пометит анализ устаревшим
    loadInsights();
  } catch (e) { btn.disabled = false; toast('Не удалось поставить лимит: ' + e.message); }
}
$('insights').addEventListener('click', e => {
  const t = e.target.closest('button');
  if (!t || t.disabled) return;
  if (t.id === 'insightsRefresh' || t.hasAttribute('data-ins-start')) startInsights();
  else if (t.hasAttribute('data-ins-consent')) grantInsightsConsent();
  else if (t.hasAttribute('data-ins-upload')) showPage('upload');
  else if (t.hasAttribute('data-ins-unhide')) { try { localStorage.removeItem(insightsHiddenKey(insights.userId)); } catch (err) {} renderInsights(); }
  else if (t.hasAttribute('data-ins-hide')) {
    const id = t.closest('[data-insight]').dataset.insight, key = insightsHiddenKey(insights.userId);
    writeIdList(key, [...readIdList(key), id]);
    renderInsights();
    $('insightsBody').querySelector('[data-ins-hide], [data-ins-unhide]')?.focus();
  } else if (t.dataset.insAct === 'set_budget') applyBudgetSuggestion(t);
  else if (t.dataset.insAct === 'open_history') openHistory({ period: t.dataset.period, category: t.dataset.cat });
  else if (t.dataset.insAct === 'open_categories') showPage('categories');
});
$('profileInsights').onclick = () => showPage('analysis');

renderAccess();
loadCurrencies();
checkAuthOnStartup();

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
  // Подсветка карточки под курсором (CSS читает --mx/--my)
  document.addEventListener('pointermove', e => {
    const card = e.target.closest?.('.card');
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', (e.clientX - r.left) + 'px');
    card.style.setProperty('--my', (e.clientY - r.top) + 'px');
  }, { passive: true });
}
