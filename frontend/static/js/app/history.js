'use strict';

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
  sel.innerHTML = `<option value="all">${esc(t('Все категории'))}</option>` + categoryOptions(null);
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
  if (!currentUser) empty = `<div class="card empty"><button class="text-btn" data-open-auth type="button">${esc(t('Войдите'))}</button>${esc(t(', чтобы видеть историю.'))}</div>`;
  else if (searching) empty = `<div class="card empty">${esc(t('Ничего не найдено'))}</div>`;
  else empty = `<div class="card empty">${esc(t('Чеков пока нет'))}</div>`;
  $('historyList').innerHTML = shown.length
    ? shown.map(r => `<article class="card receipt-row" data-open="${esc(r.id)}" tabindex="0" aria-label="${esc(t('Подробности покупки: {store}, {sum}', { store: r.store || t('магазин не определён'), sum: money(r.total, curOf(r)) }) + (isManual(r) ? t(', введён вручную') : ''))}"><div><h3>${esc(r.store || t('Магазин не определён'))}${isManual(r) ? ' ' + manualBadge() : ''}</h3><p>${esc(fmtPurchase(r) || fmtDateTime(r.created_at))} · ${esc(t('{n} позиций', { n: (r.items || []).length }))}</p></div><div class="receipt-amount">${esc(money(r.total, curOf(r)))}</div><button class="text-btn" data-delete="${esc(r.id)}" type="button" aria-label="${esc(t('Удалить чек {store}', { store: r.store || '' }))}">${esc(t('Удалить'))}</button></article>`).join('')
    : empty;
  const rest = list.length - shown.length;
  $('historyMore').hidden = rest <= 0;
  $('historyMoreBtn').textContent = t('Показать ещё ({n} из {rest})', { n: Math.min(rest, HISTORY_PAGE), rest });
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
  // Заголовки — на языке интерфейса; формат дат и чисел одинаковый для всех языков
  const rows = [[t('Дата'), t('Время'), t('Магазин'), t('Валюта'), t('Итог чека'), t('Итог в {cur}', { cur: baseCurrency() }), t('Товар'), t('Количество'), t('Цена'), t('Сумма'), t('Категория')]];
  list.forEach(r => {
    const d = parseDate(r.date), day = d && !Number.isNaN(+d) ? d.toLocaleDateString('ru-RU') : '';
    const inBase = toBase(r, r.total);
    const head = [day, r.time || '', r.store || '', curOf(r), csvNum(r.total), inBase == null ? '' : csvNum(inBase)];
    const items = r.items || [];
    if (!items.length) rows.push([...head, '', '', '', '', categoryNameByName(r.category) || '']);
    items.forEach(i => rows.push([...head, i.name, csvNum(i.quantity ?? 1), csvNum(i.price_per_unit ?? i.total_price), csvNum(i.total_price), categoryName(itemCategory(i))]));
  });
  const csv = '﻿' + rows.map(row => row.map(csvCell).join(';')).join('\r\n');
  downloadFile(csv, 'text/csv;charset=utf-8', `checkai_${isoDay(new Date())}.csv`);
  toast(t('Выгружено чеков: {n}', { n: list.length }));
}
$('exportCsvBtn').onclick = () => exportCsv();
