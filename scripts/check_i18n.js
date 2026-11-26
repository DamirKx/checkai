#!/usr/bin/env node
// Проверка переводов интерфейса. Запуск из корня репозитория:
//   node scripts/check_i18n.js           — ошибки: нет перевода, не совпали {параметры}, русский текст без t()
//   node scripts/check_i18n.js --list    — дополнительно вывести все ключи
// Код выхода 1, если есть ошибки.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');
const CYR = /[а-яёәғқңөұүһі]/i;
const LANGS = ['kk', 'en'];
const args = process.argv.slice(2);

// --- Словари ---
const sandbox = { window: {} };
for (const lang of LANGS) vm.runInNewContext(read(`frontend/static/js/locales/${lang}.js`), sandbox, { filename: `${lang}.js` });
const dicts = sandbox.window.CHECKAI_LOCALES || {};

const keys = new Map();          // ключ → где встретился
const pluralKeys = new Set();    // ключи «one|few|many»
const unwrapped = [];            // русский текст в js/app/*.js без t()
const REGEX_KEYWORDS = new Set(['return', 'typeof', 'case', 'in', 'of', 'delete', 'void', 'throw', 'new', 'else', 'do', 'yield', 'await']);
const addKey = (key, where) => { if (CYR.test(key) && !keys.has(key)) keys.set(key, where); };

// --- js/app/*.js: разбор с учётом строк, шаблонов, регулярных выражений и комментариев ---
function scanApp(file) {
  const lines = read(file).split('\n');
  // Строки, которые не проверяются (// i18n-ignore, блок i18n-ignore-start…end), и строки-таблицы ключей
  const tableLines = new Set();
  let ignoring = false, inTable = false;
  const cleaned = lines.map((line, i) => {
    if (line.includes('i18n-ignore-start')) { ignoring = true; return ''; }
    if (line.includes('i18n-ignore-end')) { ignoring = false; return ''; }
    if (ignoring || line.includes('// i18n-ignore')) return '';
    if (line.includes('/* i18n-keys */')) inTable = true;
    if (inTable || line.includes('// i18n-key')) tableLines.add(i + 1);
    if (line.includes('/* /i18n-keys */')) inTable = false;
    return line;
  });
  const src = cleaned.join('\n');

  let i = 0, line = 1;
  // Стек контекстов: {type: 'code'|'tpl'|'expr', depth}; стек скобок вызовов: {fn, literals}
  const ctx = [{ type: 'code' }];
  const calls = [];
  let prevSig = '';   // последний значимый символ кода — для отличия «/» деления от регулярного выражения
  let ident = '';     // идентификатор перед «(»
  let lastWord = '';  // последнее слово: после return, typeof и т. п. «/» — начало регулярного выражения

  const onLiteral = (text, startLine, isTemplateText) => {
    if (!CYR.test(text)) return;
    const call = calls.length ? calls[calls.length - 1] : null;
    const where = `${file}:${startLine}`;
    if (!isTemplateText && call && call.fn === 'plural') { call.literals.push(text); return; }
    if (!isTemplateText && call && call.fn === 't') { addKey(text, where); return; }
    if (tableLines.has(startLine)) { addKey(text, where); return; }
    unwrapped.push({ where, text });
  };
  const readQuoted = q => {
    const startLine = line;
    let s = '';
    i++;
    while (i < src.length && src[i] !== q) {
      if (src[i] === '\\') { const n = src[i + 1]; s += n === 'n' ? '\n' : n; i += 2; continue; }
      if (src[i] === '\n') line++;
      s += src[i++];
    }
    i++;
    onLiteral(s, startLine, false);
  };

  while (i < src.length) {
    const c = src[i], top = ctx[ctx.length - 1];
    if (top.type === 'tpl') {
      // Текст шаблонной строки до ${ или закрывающей `
      const startLine = line;
      let s = '';
      while (i < src.length && src[i] !== '`' && !(src[i] === '$' && src[i + 1] === '{')) {
        if (src[i] === '\\') { s += src[i + 1]; i += 2; continue; }
        if (src[i] === '\n') line++;
        s += src[i++];
      }
      onLiteral(s, startLine, true);
      if (src[i] === '`') { ctx.pop(); i++; prevSig = ')'; }
      else { ctx.push({ type: 'expr', depth: 0 }); i += 2; prevSig = '('; }
      continue;
    }
    // Код (в том числе внутри ${…})
    if (c === '\n') { line++; i++; ident = ''; continue; }
    if (c === '/' && src[i + 1] === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (c === '/' && src[i + 1] === '*') { const end = src.indexOf('*/', i + 2); line += (src.slice(i, end).match(/\n/g) || []).length; i = end + 2; continue; }
    if (c === "'" || c === '"') { readQuoted(c); prevSig = ')'; ident = ''; continue; }
    if (c === '`') { ctx.push({ type: 'tpl' }); i++; ident = ''; continue; }
    const afterKeyword = /[\w$]/.test(prevSig) && REGEX_KEYWORDS.has(lastWord);
    if (c === '/' && (prevSig === '' || afterKeyword || '(,=:[!&|?{};+-*%<>~^'.includes(prevSig))) {
      // Регулярное выражение
      i++;
      let inClass = false;
      while (i < src.length) {
        const r = src[i];
        if (r === '\\') { i += 2; continue; }
        if (r === '[') inClass = true; else if (r === ']') inClass = false;
        else if (r === '/' && !inClass) break;
        i++;
      }
      i++;
      while (/[a-z]/i.test(src[i])) i++;
      prevSig = ')';
      continue;
    }
    if (/[\w$.]/.test(c)) { ident += c; lastWord = ident; i++; prevSig = c; continue; }
    if (c === '(') {
      const name = ident.split('.').pop();
      calls.push({ fn: ident === 'I18N.t' || name === 't' && !ident.includes('.') ? 't' : name === 'plural' && !ident.includes('.') ? 'plural' : 'other', literals: [] });
    } else if (c === ')') {
      const call = calls.pop();
      if (call && call.fn === 'plural' && call.literals.length === 3) { const k = call.literals.join('|'); pluralKeys.add(k); addKey(k, `${file}:${line}`); }
    } else if (c === '{' && top.type === 'expr') top.depth++;
    else if (c === '}' && top.type === 'expr') {
      if (top.depth === 0) { ctx.pop(); i++; prevSig = ')'; ident = ''; continue; }
      top.depth--;
    }
    if (!/\s/.test(c)) prevSig = c;
    if (!/[\w$.]/.test(c)) ident = '';
    i++;
  }
}
const APP_DIR = 'frontend/static/js/app';
for (const name of fs.readdirSync(path.join(ROOT, APP_DIR)).filter(n => n.endsWith('.js')).sort()) scanApp(`${APP_DIR}/${name}`);

// --- index.html: текстовые узлы и атрибуты, как их собирает i18n.js ---
function scanHtml(file) {
  let html = read(file)
    .replace(/<script[\s\S]*?<\/script>/g, '')
    .replace(/<(select|div)[^>]*data-no-i18n[^>]*>[\s\S]*?<\/\1>/g, '')
    .replace(/(<[a-z]+[^>]*data-no-i18n[^>]*>)[^<]*/g, '$1');
  const lineOf = idx => html.slice(0, idx).split('\n').length;
  for (const m of html.matchAll(/>([^<]+)</g)) {
    const text = m[1].trim();
    if (CYR.test(text)) addKey(text, `${file}:~${lineOf(m.index)}`);
  }
  for (const m of html.matchAll(/\s(?:aria-label|title|placeholder|alt)="([^"]*)"/g)) {
    if (CYR.test(m[1])) addKey(m[1], `${file}:~${lineOf(m.index)}`);
  }
}
scanHtml('frontend/templates/index.html');

// --- Сверка ---
const errors = [];
const params = s => [...String(s).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort().join(',');
for (const lang of LANGS) {
  const d = dicts[lang] || {};
  for (const [key, where] of keys) {
    if (!Object.prototype.hasOwnProperty.call(d, key)) { errors.push(`[${lang}] нет перевода: ${JSON.stringify(key)}  (${where})`); continue; }
    if (pluralKeys.has(key)) {
      if (lang === 'en' && d[key].split('|').length !== 2) errors.push(`[en] у множественного числа должно быть 2 формы через «|»: ${JSON.stringify(key)}`);
    } else if (params(key) !== params(d[key])) {
      errors.push(`[${lang}] параметры не совпадают: ${JSON.stringify(key)} → ${JSON.stringify(d[key])}`);
    }
    if (!String(d[key]).trim() && String(key).trim()) errors.push(`[${lang}] пустой перевод: ${JSON.stringify(key)}`);
  }
  // Ключи словаря, которых нет в интерфейсе: обычно это тексты ошибок сервера (их фронт переводит по detail)
  const extra = Object.keys(d).filter(k => !keys.has(k));
  console.log(`${lang}: ${Object.keys(d).length} переводов, ${extra.length} не из интерфейса (тексты сервера и запас)`);
  for (const k of extra) {
    if (params(k) !== params(d[k]) && !k.includes('|')) errors.push(`[${lang}] параметры не совпадают: ${JSON.stringify(k)} → ${JSON.stringify(d[k])}`);
  }
}
for (const u of unwrapped) errors.push(`русский текст без t(): ${JSON.stringify(u.text.trim().slice(0, 80))}  (${u.where})`);

console.log(`ключей в интерфейсе: ${keys.size} (из них множественных чисел: ${pluralKeys.size})`);
if (args.includes('--list')) for (const [k, w] of keys) console.log(`  ${JSON.stringify(k)}  ${w}`);
if (errors.length) {
  console.error(`\nОшибок: ${errors.length}`);
  errors.forEach(e => console.error('  ' + e));
  process.exit(1);
}
console.log('OK: все тексты интерфейса переведены');
