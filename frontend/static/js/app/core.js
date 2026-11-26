'use strict';

// --- Справочники ---
// Должно совпадать с MAX_UPLOAD_MB и ALLOWED_EXTENSIONS на сервере (app/config.py, app/uploads.py)
const MAX_UPLOAD_MB = 10;
const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png', 'image/webp', 'image/bmp', 'image/tiff'];
const ALLOWED_EXT = /\.(jpe?g|png|webp|bmp|tiff?)$/i;
// Сколько чеков запрашивать за раз и сколько показывать в истории до «Показать ещё»
const RECEIPTS_PAGE = 200;
const HISTORY_PAGE = 20;
// Сервер не сообщает этапы распознавания: после загрузки через столько мс показываем «Анализ»
const OCR_STEP_MS = 5000;
// Валюты, пока сервер не отдаёт /api/currencies. digits — знаков после запятой при показе
/* i18n-keys */
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
/* /i18n-keys */
// Валюта чеков, сохранённых до появления валют, и чеков, где её не нашли
const DEFAULT_CURRENCY = 'KZT';

// --- Утилиты ---
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const round2 = n => Math.round(num(n) * 100) / 100;
// Переводы (i18n.js): ключ — русский текст, для ru строка не меняется
const t = (text, params) => I18N.t(text, params);
// Формы — русские; для kk и en форма берётся из словаря по ключу «one|few|many»
const plural = (n, one, few, many) => I18N.plural(n, one, few, many);
// Локаль для дат и чисел: ru-RU / kk-KZ / en-US
const LOC = () => I18N.locale();
// Дата с названием месяца или дня недели на языке интерфейса
const fmtDateName = (d, opts) => I18N.formatDate(d, opts);

// --- Валюты ---
let currencyList = DEFAULT_CURRENCIES, currencies = Object.fromEntries(DEFAULT_CURRENCIES.map(c => [c.code, c]));
const currencyInfo = code => currencies[code] || { code, symbol: code, name: code, digits: 2 };
// Название валюты на языке интерфейса: свой перевод, иначе Intl.DisplayNames, иначе название с сервера
function currencyName(c) {
  if (I18N.getLang() === 'ru') return c.name;
  const own = t(c.name);
  if (own !== c.name) return own;
  try {
    const loc = LOC();
    if (loc.startsWith(I18N.getLang())) {
      const n = new Intl.DisplayNames([loc], { type: 'currency' }).of(c.code);
      if (n && n !== c.code) return n.charAt(0).toUpperCase() + n.slice(1);
    }
  } catch (e) {}
  return c.name;
}
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
  return new Intl.NumberFormat(LOC(), { minimumFractionDigits: c.digits, maximumFractionDigits: c.digits }).format(Number(n) || 0) + ' ' + c.symbol;
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
  const parts = Object.entries(skipped).map(([cur, n]) => t('{n} {w} в {cur}', { n, w: plural(n, 'чек', 'чека', 'чеков'), cur }));
  return parts.length ? t('Не учтено: {list} — нет курса к {base}.', { list: parts.join(', '), base: baseCurrency() }) : '';
}
function showSkipped(id, skipped) {
  const text = skippedText(skipped);
  $(id).textContent = text;
  $(id).hidden = !text;
}
const currencyOptions = selected => {
  const list = currencies[selected] ? currencyList : [...currencyList, currencyInfo(selected)];
  return list.map(c => `<option value="${esc(c.code)}" ${c.code === selected ? 'selected' : ''}>${esc(c.symbol)} ${esc(c.code)} — ${esc(currencyName(c))}</option>`).join('');
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
  const day = d && !Number.isNaN(+d) ? d.toLocaleDateString(LOC()) : (r.date || '');
  return [day, r.time].filter(Boolean).join(' · ');
};
// Для фильтров по периодам берём дату покупки, а если её нет — дату сохранения
const receiptIso = r => { const d = parseDate(r.date); return d && !Number.isNaN(+d) ? d.toISOString() : r.created_at; };
// --- Категории ---
// Категории пользователя приходят с сервера (у гостя — стандартные, без id).
// «Без категории» — позиция с category_id = null: это не категория, её нельзя изменить или удалить
const UNCATEGORIZED = { id: null, name: 'Без категории', icon: '○', description: null }; // i18n-key
const CATEGORY_ICONS = ['💄', '🐶', '🎁', '📚', '💊', '🏋', '✈', '🎮', '🧸', '🍷', '🚗', '💡', '📱', '🧾', '🏠', '◌'];
// Ограничения — те же, что на сервере
const CATEGORY_NAME_MAX = 50, CATEGORY_DESC_MAX = 300, CATEGORY_ICON_MAX = 8;
// Откуда у позиции категория после распознавания
const CATEGORY_SOURCE_TEXT = { rule: 'по вашему правилу', ai: 'определил ИИ', keywords: 'по ключевым словам' }; // i18n-key
const categoryById = id => categories.find(c => c.id === id) || UNCATEGORIZED;
// Категория позиции: по id, а у гостя — по названию
const itemCategory = it => it.category_id != null ? categoryById(it.category_id)
  : categories.find(c => c.name === it.category) || UNCATEGORIZED;
// Ключ категории для сумм и значений в списках: id, у гостя — название, '' — «Без категории»
const catKey = c => c.id != null ? String(c.id) : c === UNCATEGORIZED ? '' : 'name:' + c.name;
const categoryByKey = key => key === '' ? UNCATEGORIZED
  : String(key).startsWith('name:') ? categories.find(c => c.name === key.slice(5)) || UNCATEGORIZED
  : categoryById(Number(key));
// Стандартные категории: в базе — на русском, на экране — на языке интерфейса.
// default_key присылает сервер (null — категорию переименовали). Пока поля нет, узнаём её по русскому названию.
// Название и описание по-прежнему хранятся и отправляются на русском: логика работает с c.name
/* i18n-keys */
const DEFAULT_CATEGORY_TEXT = {
  groceries: ['Продукты', 'Еда и напитки для дома: молочные продукты, хлеб, мясо, овощи, фрукты, крупы, сладости'],
  clothing: ['Одежда', 'Одежда, обувь, аксессуары'],
  home: ['Дом и быт', 'Бытовая химия, хозтовары, посуда, мелочи для дома'],
  transport: ['Транспорт', 'Топливо, такси, проезд, парковка'],
  cafe: ['Кафе', 'Готовая еда и напитки вне дома: кафе, рестораны, кофейни, доставка еды'],
  health: ['Здоровье', 'Аптека, лекарства, витамины, медицинские услуги'],
  entertainment: ['Развлечения', 'Кино, театр, концерты, игры, подписки'],
  other: ['Другое', 'Всё, что не подошло в другие категории'],
};
/* /i18n-keys */
const DEFAULT_KEY_BY_NAME = Object.fromEntries(Object.entries(DEFAULT_CATEGORY_TEXT).map(([k, [name]]) => [name, k]));
function defaultKey(c) {
  if (!c || c === UNCATEGORIZED) return null;
  if (c.default_key !== undefined) return DEFAULT_CATEGORY_TEXT[c.default_key] ? c.default_key : null;
  return DEFAULT_KEY_BY_NAME[c.name] || null;
}
const categoryName = c => c === UNCATEGORIZED ? t(UNCATEGORIZED.name) : defaultKey(c) ? t(DEFAULT_CATEGORY_TEXT[defaultKey(c)][0]) : c.name;
// Описание переводим, только если пользователь его не менял
const categoryDescription = c => {
  const k = defaultKey(c);
  return k && c.description === DEFAULT_CATEGORY_TEXT[k][1] ? t(c.description) : c.description;
};
// Категория чека приходит названием (r.category)
const categoryNameByName = name => name ? categoryName(categories.find(c => c.name === name) || { name }) : name;
const categoryLabel = c => `${c.icon} ${categoryName(c)}`;
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
    b.setAttribute('aria-label', light ? t('Включить тёмную тему') : t('Включить светлую тему'));
    b.title = light ? t('Тёмная тема') : t('Светлая тема');
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
