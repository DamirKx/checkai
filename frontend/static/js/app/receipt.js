'use strict';

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
    // id сохранённого чека с тем же фото или фискальным номером — сервер сообщает при распознавании
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
      // Откуда категория: ai | keywords | rule | user. Старый сервер поле пропустит
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
// Пределы — те же, что проверяет сервер
const MAX_AMOUNT = 1e9, MAX_QUANTITY = 100000;
// Текст ошибки или null, если черновик можно сохранять; field — поле позиции index, куда поставить фокус
function draftProblem(draft) {
  const named = draft.items.filter(i => i.name.trim());
  if (!named.length && !(num(draft.total) > 0)) return { message: t('Добавьте хотя бы одну позицию с названием или укажите итог чека'), index: draft.items.length ? 0 : null };
  const unnamed = draft.items.findIndex(i => !i.name.trim() && num(i.total_price));
  if (unnamed >= 0) return { message: t('У позиции №{n} нет названия', { n: unnamed + 1 }), index: unnamed };
  const amounts = [num(draft.total), ...draft.items.flatMap(i => [num(i.quantity), num(i.price_per_unit), num(i.total_price)])];
  if (amounts.some(n => n < 0)) return { message: t('Суммы и количество не могут быть отрицательными') };
  if (amounts.some(n => n > MAX_AMOUNT)) return { message: t('Слишком большое число: суммы — не больше 1 000 000 000') };
  const noQty = draft.items.findIndex(i => i.name.trim() && !(num(i.quantity) > 0));
  if (noQty >= 0) return { message: t('У позиции №{n} количество должно быть больше нуля', { n: noQty + 1 }), index: noQty, field: 'quantity' };
  const bigQty = draft.items.findIndex(i => num(i.quantity) > MAX_QUANTITY);
  if (bigQty >= 0) return { message: t('У позиции №{n} слишком большое количество (не больше {max})', { n: bigQty + 1, max: intText(MAX_QUANTITY) }), index: bigQty, field: 'quantity' };
  return null;
}
// Дата покупки в будущем — не ошибка, но переспрашиваем. false — пользователь передумал сохранять
function confirmDraft(draft) {
  if (!draft.date || draft.date <= isoDay(new Date())) return true;
  const d = parseDate(draft.date);
  return confirm(t('Дата покупки {date} — в будущем. Точно сохранить?', { date: d ? d.toLocaleDateString(LOC()) : draft.date }));
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

// Автокатегория для вписанных руками названий: POST /api/categorize
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
  return text ? `<small class="cat-source">${esc(t(text))}</small>` : '';
}

function mountEditor(root, draft) {
  const hints = historyHints(), listId = root.id + 'Items', storesId = root.id + 'Stores';
  root.innerHTML = `<div class="editor">
    <div class="edit-head">
      <label>${esc(t('Магазин'))}<input class="field" data-f="store" maxlength="255" value="${esc(draft.store)}" placeholder="${esc(t('Название магазина'))}" list="${esc(storesId)}" autocomplete="off"></label>
      <label>${esc(t('Дата покупки'))}<input class="field" type="date" data-f="date" value="${esc(draft.date)}"></label>
      <label>${esc(t('Время'))}<input class="field" type="time" data-f="time" value="${esc(draft.time)}"></label>
    </div>
    <div class="edit-items" data-items></div>
    <div><button class="btn soft small" type="button" data-add>${esc(t('＋ Добавить позицию'))}</button></div>
    <div class="edit-foot">
      <label><span data-total-label></span><input class="field edit-total" type="number" min="0" step="any" inputmode="decimal" data-f="total" value="${esc(draft.total)}"></label>
      <label>${esc(t('Валюта'))}<select class="field" data-f="currency">${currencyOptions(draft.currency)}</select></label>
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
    editor.querySelector('[data-total-label]').textContent = t('Итог чека, {sym}', { sym });
    list.innerHTML = draft.items.length ? draft.items.map((it, i) => `<div class="edit-item" data-i="${i}">
      <div class="edit-name">
        <label>${esc(t('Название'))}<input class="field" data-k="name" maxlength="500" value="${esc(it.name)}" placeholder="${esc(t('Товар или услуга'))}" list="${esc(listId)}" autocomplete="off"></label>
        <button class="icon-btn remove-item" type="button" data-remove aria-label="${esc(t('Удалить позицию {name}', { name: it.name || '№' + (i + 1) }))}">✕</button>
      </div>
      <div class="edit-nums">
        ${numInput('quantity', it.quantity, esc(t('Кол-во')))}${numInput('price_per_unit', it.price_per_unit, esc(t('Цена, {sym}', { sym })))}${numInput('total_price', it.total_price, esc(t('Сумма, {sym}', { sym })))}
        <label>${esc(t('Категория'))}<select class="field" data-k="category">${categoryOptions(catKey(itemCategory(it)))}</select>${catSourceHtml(it.category_source)}</label>
      </div>
    </div>`).join('') : `<div class="empty">${esc(t('Позиций нет'))}</div>`;
    updateSum();
  };
  const updateSum = () => {
    const sum = itemsSum(draft.items);
    // Итог не трогали руками — он равен сумме позиций
    if (draft.totalAuto) { draft.total = sum; totalInput.value = sum; }
    const diff = round2(num(draft.total) - sum), mismatch = draft.items.length && sumMismatch(sum, num(draft.total), draft.currency);
    sumBox.innerHTML = esc(t('Сумма позиций:')) + ` <b>${money(sum, draft.currency)}</b>` + (mismatch
      ? ' — ' + esc(t('итог не совпадает с позициями на {diff}.', { diff: money(Math.abs(diff), draft.currency) })) + ` <button class="text-btn" type="button" data-fix-total>${esc(t('Сделать итогом'))}</button>`
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

// --- Проверка на дубликат ---
// Тот же чек, сохранённый второй раз: та же валюта и итог, та же дата, время в пределах 2 минут,
// тот же магазин и почти те же позиции. Правила совпадают с crud.find_duplicate на сервере
const DUP_TIME_MIN = 2, DUP_ITEMS_MIN = 0.6, DUP_NODATE_ITEMS_MIN = 0.8;
// Название магазина или товара для сравнения: регистр, ё, знаки и правовая форма (ТОО, ИП…) не важны
const normText = s => (' ' + String(s || '').toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9әғқңөұүһі]+/gi, ' ') + ' ') // i18n-ignore
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
const receiptBrief = r => [r.store || t('Магазин не определён'), fmtPurchase(r) || fmtDateTime(r.created_at), money(r.total, curOf(r))].join(' · ');
// Окно «Похоже, этот чек уже есть»: 'cancel' | 'open' | 'save'. existing может быть null (сервер не прислал чек)
let duplicateResolve = null;
function askDuplicate(existing, canOpen = true) {
  const items = existing?.items?.length || 0;
  $('duplicateText').textContent = existing
    ? t('В истории уже есть чек с той же суммой, датой и магазином. Если это другая покупка, сохраните его ещё раз.')
    : t('Сервер нашёл в истории такой же чек. Если это другая покупка, сохраните его ещё раз.');
  $('duplicateCard').hidden = !existing;
  $('duplicateCard').innerHTML = existing
    ? `<b>${esc(existing.store || t('Магазин не определён'))}${isManual(existing) ? ' ' + manualBadge() : ''}</b><span>${esc(fmtPurchase(existing) || '')}</span><strong>${esc(money(existing.total, curOf(existing)))}</strong><small>${items} ${plural(items, 'позиция', 'позиции', 'позиций')} · ${esc(t('сохранён {date}', { date: fmtDateTime(existing.created_at) }))}</small>`
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
// Сохранение с проверкой: сначала по загруженной истории, затем сервер может ответить 409.
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
// Чек, введённый без фото (поле source с сервера)
const isManual = r => r?.source === 'manual';
const manualBadge = () => `<span class="pill pill-manual">${esc(t('вручную'))}</span>`;
let openedReceipt = null, editDraft = null;
function openReceipt(id) {
  const r = receipts.find(x => String(x.id) === String(id));
  if (!r) return;
  openedReceipt = r;
  const items = r.items || [];
  $('rmView').hidden = false;
  $('rmEditView').hidden = true;
  $('rmStore').textContent = r.store || t('Магазин не определён');
  $('rmDate').textContent = fmtPurchase(r) || t('Дата покупки не распознана');
  const cur = curOf(r), foreign = cur !== baseCurrency();
  $('rmTotal').textContent = money(r.total, cur);
  // Чек не в основной валюте: показываем, сколько это по курсу на дату покупки
  $('rmConverted').hidden = !foreign;
  if (foreign) {
    const converted = toBase(r, r.total), d = parseDate(r.date);
    const day = d && !Number.isNaN(+d) ? d.toLocaleDateString(LOC()) : fmtDateTime(r.created_at).split(',')[0];
    $('rmConverted').textContent = converted == null
      ? t('Курс на дату покупки не найден — чек не учитывается в итогах, аналитике и бюджетах.')
      : t('≈ {sum} по курсу НБ РК на {day}', { sum: money(converted), day });
  }
  $('rmMeta').innerHTML = (isManual(r) ? manualBadge() : '') + [categoryNameByName(r.category), foreign && cur, t('{n} позиций', { n: items.length }), t('Сохранён {date}', { date: fmtDateTime(r.created_at) })].filter(Boolean).map(p => `<span class="pill">${esc(p)}</span>`).join('');
  const photo = $('rmPhoto');
  if (r.image_path) { photo.onerror = () => { photo.hidden = true; }; photo.src = r.image_path; photo.hidden = false; }
  else { photo.hidden = true; photo.removeAttribute('src'); }
  $('rmItems').innerHTML = items.length
    ? items.map(it => `<div class="item"><div><strong>${esc(it.name)}</strong><small>${esc(it.quantity ?? 1)} × ${money(it.price_per_unit ?? it.total_price, cur)}</small><span class="item-cat">${esc(categoryLabel(itemCategory(it)))}</span></div><div style="text-align:right"><strong>${money(it.total_price, cur)}</strong></div></div>`).join('')
    : `<div class="empty">${esc(t('В чеке нет товарных позиций'))}</div>`;
  // Подсказка, если распознавание потеряло или исказило строку
  const sum = itemsSum(items);
  $('rmWarn').hidden = !(items.length && sumMismatch(sum, num(r.total), cur));
  $('rmWarn').textContent = t('Сумма позиций ({sum}) не совпадает с итогом чека ({total}). Возможно, распознавание пропустило или исказило строку — исправьте через «Редактировать».', { sum: money(sum, cur), total: money(r.total, cur) });
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
    toast(t('Чек обновлён'));
    openReceipt(id);
  } catch (err) {
    // У /api/receipts/{id} уже есть DELETE, поэтому без PUT сервер ответит 405
    if (err.status === 405) showFormError('rmEditError', t('Сервер пока не поддерживает редактирование чеков (нужен PUT /api/receipts/{id}).'));
    else if (err.status === 404) { showFormError('rmEditError', t('Чек не найден — возможно, его уже удалили.')); loadReceipts(); }
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
  if (!confirm(t('Удалить этот чек из истории?'))) return false;
  try { await apiRequest('/api/receipts/' + id, { method: 'DELETE' }); await loadReceipts(); toast(t('Чек удалён')); return true; }
  catch (e) { toast(t('Не удалось удалить: {error}', { error: e.message })); return false; }
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
  } catch (e) { toast(t('Не удалось загрузить историю: {error}', { error: e.message })); }
}
async function checkAuthOnStartup() {
  if (!getToken()) { authPending = false; setGuest(); return; }
  try {
    const user = await apiRequest('/api/auth/me');
    authPending = false;
    setUser(user);
    await loadReceipts();
  } catch (e) { authPending = false; setGuest(); if (getToken()) toast(t('Не удалось связаться с сервером')); }
}
