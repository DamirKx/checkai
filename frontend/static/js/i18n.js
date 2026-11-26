'use strict';
// Переводы интерфейса. Ключ перевода — сам русский текст:
// для ru t() возвращает строку как есть, поэтому русский сайт не меняется. Словари kk и en — в locales/*.js.
// Нет перевода — показываем русский текст и один раз пишем в консоль.
(function () {
  const LANGS = ['ru', 'kk', 'en'];
  const LOCALES = { ru: 'ru-RU', kk: 'kk-KZ', en: 'en-US' };
  const STORAGE_KEY = 'checkai_lang';
  const dicts = window.CHECKAI_LOCALES || {};
  const hasCyrillic = s => /[а-яёәғқңөұүһі]/i.test(s);

  // 'kk-KZ', 'KZ', 'en-US' → 'kk' / 'en'; неизвестный язык → null
  function normalize(value) {
    const v = String(value || '').trim().toLowerCase();
    if (!v) return null;
    const base = v.split(/[-_]/)[0];
    if (base === 'kz') return 'kk';
    return LANGS.includes(base) ? base : null;
  }
  const storedLang = () => { try { return normalize(localStorage.getItem(STORAGE_KEY)); } catch (e) { return null; } };
  const browserLang = () => {
    const list = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
    for (const l of list) { const n = normalize(l); if (n) return n; }
    return null;
  };

  let lang = storedLang() || browserLang() || 'ru';
  const listeners = [];
  const warned = new Set();

  function lookup(text) {
    if (lang === 'ru') return null;
    const d = dicts[lang];
    if (d && Object.prototype.hasOwnProperty.call(d, text)) return d[text];
    if (!warned.has(text) && hasCyrillic(text)) {
      warned.add(text);
      console.warn(`[i18n] нет перевода (${lang}):`, text);
    }
    return null;
  }
  // t('Вы вошли как {name}', { name }) — подстановка {имя}; без params строка не трогается
  function t(text, params) {
    let s = String(text ?? '');
    const found = lookup(s);
    if (found != null) s = found;
    if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m));
    return s;
  }
  // Множественное число: ключ — русские формы через «|», перевод — формы языка через «|»
  // en: 'receipt|receipts' (1 / остальные), kk: одна форма (после числа слово не меняется)
  function plural(n, one, few, many) {
    const abs = Math.abs(Number(n) || 0);
    if (lang !== 'ru') {
      const found = lookup(`${one}|${few}|${many}`);
      if (found != null) {
        const forms = found.split('|');
        return lang === 'en' && forms.length > 1 ? (abs === 1 ? forms[0] : forms[1]) : forms[0];
      }
    }
    const m10 = abs % 10, m100 = abs % 100;
    return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
  }
  // Локаль для Intl (числа и даты цифрами). Для kk — ru-RU: в Казахстане те же правила («5 000», «15.11.2026»),
  // а Chrome для kk-KZ отдаёт «5,000» и «2026-11-15». Названия месяцев и дней недели — formatDate ниже
  const locale = () => (lang === 'kk' ? 'ru-RU' : LOCALES[lang]);
  const KK_MONTHS = ['қаңтар', 'ақпан', 'наурыз', 'сәуір', 'мамыр', 'маусым', 'шілде', 'тамыз', 'қыркүйек', 'қазан', 'қараша', 'желтоқсан'];
  const KK_MONTHS_SHORT = ['қаң', 'ақп', 'нау', 'сәу', 'мам', 'мау', 'шіл', 'там', 'қыр', 'қаз', 'қар', 'жел'];
  const KK_WEEKDAYS_SHORT = ['жс', 'дс', 'сс', 'ср', 'бс', 'жм', 'сб'];
  // Дата с названием месяца: для ru и en — Intl, для kk — свои названия (поддержка kk в браузерах неполная)
  function formatDate(d, opts = {}) {
    if (lang !== 'kk' || !(opts.month === 'long' || opts.month === 'short' || opts.weekday)) return d.toLocaleDateString(locale(), opts);
    const day = d.getDate(), m = d.getMonth(), y = d.getFullYear();
    if (opts.month === 'short') return KK_MONTHS_SHORT[m];
    if (opts.weekday) return `${KK_WEEKDAYS_SHORT[d.getDay()]}, ${day} ${KK_MONTHS[m]}`;
    if (opts.day && opts.year) return `${y} ж. ${day} ${KK_MONTHS[m]}`;
    if (opts.year) return `${KK_MONTHS[m]} ${y}`;
    return `${day} ${KK_MONTHS[m]}`;
  }

  // --- Статический текст из index.html ---
  // Запоминаем исходный русский текст узлов и атрибутов. При смене языка узел обновляется,
  // только если его с тех пор не менял app.js (значение равно тому, что поставили мы сами)
  const ATTRS = ['aria-label', 'title', 'placeholder', 'alt'];
  const textEntries = [], attrEntries = [];
  const skip = el => !!(el && el.closest && el.closest('script, style, [data-no-i18n]'));
  function collect(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!hasCyrillic(node.nodeValue) || skip(node.parentElement)) continue;
      textEntries.push({ node, ru: node.nodeValue, last: node.nodeValue });
    }
    root.querySelectorAll('*').forEach(el => {
      if (skip(el)) return;
      ATTRS.forEach(a => {
        const v = el.getAttribute(a);
        if (v && hasCyrillic(v)) attrEntries.push({ el, attr: a, ru: v, last: v });
      });
    });
  }
  // Пробелы и переводы строк вокруг текста из разметки сохраняются
  function translateKeepSpace(ru) {
    const m = ru.match(/^(\s*)([\s\S]*?)(\s*)$/);
    return m[1] + t(m[2]) + m[3];
  }
  function applyStatic() {
    textEntries.forEach(e => {
      if (!e.node.isConnected || e.node.nodeValue !== e.last) return;
      const v = translateKeepSpace(e.ru);
      if (v !== e.node.nodeValue) e.node.nodeValue = v;
      e.last = v;
    });
    attrEntries.forEach(e => {
      if (!e.el.isConnected || e.el.getAttribute(e.attr) !== e.last) return;
      const v = t(e.ru);
      if (v !== e.last) e.el.setAttribute(e.attr, v);
      e.last = v;
    });
  }

  function setLang(next, opts = {}) {
    const n = normalize(next);
    if (!n) return false;
    if (opts.save !== false) { try { localStorage.setItem(STORAGE_KEY, n); } catch (e) {} }
    if (n === lang) return false;
    lang = n;
    document.documentElement.lang = n;
    applyStatic();
    listeners.forEach(fn => { try { fn(n, opts); } catch (err) { console.error(err); } });
    return true;
  }

  window.I18N = {
    LANGS,
    t,
    plural,
    locale,
    formatDate,
    normalize,
    getLang: () => lang,
    // Язык выбран явно (переключателем), а не взят из браузера
    hasStored: () => storedLang() != null,
    setLang,
    onChange: fn => listeners.push(fn),
    applyStatic,
  };

  collect(document.documentElement);
  document.documentElement.lang = lang;
  applyStatic();
  document.documentElement.classList.remove('i18n-pending');
})();
