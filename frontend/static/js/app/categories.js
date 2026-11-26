'use strict';

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
  catch (e) { toast(t('Не удалось загрузить категории: {error}', { error: e.message })); }
  // Пока шёл запрос, пользователь сменил аккаунт или вышел
  if (currentUser?.id !== user?.id) return;
  categories = (Array.isArray(list) ? list : [])
    // default_key: undefined — сервер его ещё не присылает, null — категория не стандартная
    .map((c, i) => ({ id: c.id ?? null, name: c.name, description: c.description || null, icon: c.icon || '◌', position: c.position ?? i, default_key: c.default_key }))
    .sort((a, b) => a.position - b.position);
  rerender();
}
async function loadRules() {
  const user = currentUser;
  let list = [];
  if (user) {
    try { list = await apiRequest('/api/category-rules'); }
    catch (e) { toast(t('Не удалось загрузить правила: {error}', { error: e.message })); }
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
    : `<div class="card empty">${esc(t('Категорий нет'))}</div>`;
  renderRules();
}
function categoryCard(c, index, sum, rows, converted) {
  const key = catKey(c), open = key === openCategoryKey, own = c !== UNCATEGORIZED, panelId = 'cat-items-' + (key || 'none');
  const menu = own ? `<div class="cat-menu-wrap">
      <button class="icon-btn cat-menu-btn" type="button" data-cat-menu aria-haspopup="menu" aria-expanded="false" aria-label="${esc(t('Действия с категорией «{name}»', { name: categoryName(c) }))}">⋯</button>
      <div class="account-menu cat-menu" role="menu" hidden>
        <button class="menu-item" type="button" role="menuitem" data-cat-action="edit">✎ ${esc(t('Изменить'))}</button>
        <button class="menu-item" type="button" role="menuitem" data-cat-action="up" ${index === 0 ? 'disabled' : ''}>↑ ${esc(t('Выше'))}</button>
        <button class="menu-item" type="button" role="menuitem" data-cat-action="down" ${index >= categories.length - 1 ? 'disabled' : ''}>↓ ${esc(t('Ниже'))}</button>
        <div class="menu-sep"></div>
        <button class="menu-item danger-text" type="button" role="menuitem" data-cat-action="delete">✕ ${esc(t('Удалить'))}</button>
      </div>
    </div>` : '';
  const desc = categoryDescription(c) || (own ? '' : t('Не распределено'));
  return `<div class="card category-row${open ? ' open' : ''}" data-cat="${esc(key)}">
    <button class="cat-open" type="button" aria-expanded="${open}" aria-controls="${esc(panelId)}">
      <span class="cat-icon" aria-hidden="true">${esc(c.icon)}</span>
      <span class="cat-text"><b>${esc(categoryName(c))}</b><small>${esc(desc)}</small></span>
      <span class="cat-total"><b>${esc(approx(money(sum), converted && sum > 0))}</b><small>${rows.length} ${plural(rows.length, 'позиция', 'позиции', 'позиций')}</small></span>
    </button>${menu}
  </div>${open ? categoryItems(c, rows, panelId) : ''}`;
}
function categoryItems(c, rows, id) {
  const key = catKey(c);
  if (!rows.length) return `<div class="card cat-items" id="${esc(id)}"><div class="empty">${esc(t('Пусто'))}</div></div>`;
  // Сначала свежие покупки
  const sorted = rows.slice().sort((a, b) => String(b.r.iso || '').localeCompare(String(a.r.iso || '')));
  const shown = sorted.slice(0, categoryItemsShown), rest = sorted.length - shown.length;
  const moveSelect = (attr, label) => `<select class="field" ${attr} aria-label="${esc(label)}"><option value="__" selected disabled>${esc(t('Переместить в…'))}</option>${categoryOptions(null, key)}</select>`;
  return `<div class="card cat-items" id="${esc(id)}" role="region" aria-label="${esc(t('Товары категории «{name}»', { name: categoryName(c) }))}">
    <div class="cat-bulk" ${pickedItems.size ? '' : 'hidden'}>${esc(t('Выбрано:'))} <b data-picked-count>${pickedItems.size}</b>
      ${moveSelect('data-bulk-move', t('Переместить выбранные товары в категорию'))}
      <button class="text-btn" type="button" data-unpick>${esc(t('Снять выбор'))}</button>
    </div>
    ${shown.map(({ it, r }) => `<div class="cat-item${pickedItems.has(it.id) ? ' picked' : ''}" data-item="${esc(it.id)}"${canDrag ? ' draggable="true"' : ''}>
      <input type="checkbox" data-pick="${esc(it.id)}" ${pickedItems.has(it.id) ? 'checked' : ''} aria-label="${esc(t('Выбрать «{name}»', { name: it.name }))}">
      <span class="cat-item-main"><b>${esc(it.name)}</b><small>${esc((r.store || t('Магазин не определён')) + ' · ' + (fmtPurchase(r) || fmtDateTime(r.created_at)))}</small></span>
      <span class="cat-item-sum">${esc(money(it.total_price, curOf(r)))}</span>
      ${moveSelect(`data-move="${esc(it.id)}"`, t('Переместить «{name}» в категорию', { name: it.name }))}
    </div>`).join('')}
    ${rest > 0 ? `<div class="more-row"><button class="btn soft small" type="button" data-more-items>${esc(t('Показать ещё ({n} из {rest})', { n: Math.min(rest, CATEGORY_ITEMS_PAGE), rest }))}</button></div>` : ''}
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
    toast(t('Перемещено {n} {w} в «{cat}»', { n, w: plural(n, 'позиция', 'позиции', 'позиций'), cat: categoryName(cat) }));
    if (back !== null) focusCategory(back);
  } catch (e) {
    toast(t('Не удалось переместить: {error}', { error: e.message }));
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
  } catch (e) { toast(t('Не удалось изменить порядок: {error}', { error: e.message })); }
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
// Что показали в форме: переведённое название стандартной категории не должно уйти на сервер,
// если пользователь его не менял, иначе категория перестанет быть стандартной
let categoryFormInitial = { name: '', description: '' };
const renderIconPicks = () => {
  $('categoryIconPicks').innerHTML = CATEGORY_ICONS.map(i => `<button class="icon-pick" type="button" data-icon="${esc(i)}" aria-label="${esc(t('Иконка {icon}', { icon: i }))}" aria-pressed="false">${esc(i)}</button>`).join('');
  markIconPick();
};
const markIconPick = () => $('categoryIconPicks').querySelectorAll('[data-icon]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.icon === $('categoryIcon').value.trim())));
renderIconPicks();
function openCategoryModal(cat = null) {
  editingCategory = cat;
  $('categoryModalTitle').textContent = cat ? t('Изменить категорию') : t('Новая категория');
  categoryFormInitial = { name: cat ? categoryName(cat) : '', description: cat ? categoryDescription(cat) || '' : '' };
  $('categoryName').value = categoryFormInitial.name;
  $('categoryIcon').value = cat?.icon || '';
  $('categoryDesc').value = categoryFormInitial.description;
  $('categoryError').hidden = true;
  markIconPick();
  openModal('categoryModal', $('categoryName'));
}
// Те же проверки, что на сервере: длина считается в символах, а не в единицах UTF-16
const charLen = s => [...s].length;
function categoryFormProblem(name, icon, description) {
  const lower = name.toLowerCase();
  if (!name) return t('Введите название категории');
  if (charLen(name) > CATEGORY_NAME_MAX) return t('Название категории: от 1 до {max} символов', { max: CATEGORY_NAME_MAX });
  if (lower === UNCATEGORIZED.name.toLowerCase() || lower === categoryName(UNCATEGORIZED).toLowerCase()) return t('Это название зарезервировано');
  if (categories.some(c => c.id !== editingCategory?.id && (c.name.toLowerCase() === lower || categoryName(c).toLowerCase() === lower))) return t('Категория «{name}» уже есть', { name });
  if (charLen(icon) > CATEGORY_ICON_MAX) return t('Иконка — не больше {max} символов', { max: CATEGORY_ICON_MAX });
  if (charLen(description) > CATEGORY_DESC_MAX) return t('Описание — не больше {max} символов', { max: CATEGORY_DESC_MAX });
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
  const cat = editingCategory, btn = $('categorySave');
  let body = { name, icon: icon || null, description: description || null };
  // Изменение: PATCH меняет только присланные поля — отправляем то, что пользователь действительно поменял
  if (cat) {
    body = {};
    if (name !== categoryFormInitial.name.trim()) body.name = name;
    if (icon !== (cat.icon || '').trim()) body.icon = icon || null;
    if (description !== categoryFormInitial.description.trim()) body.description = description || null;
    if (!Object.keys(body).length) { closeModal('categoryModal'); return; }
  }
  btn.disabled = true;
  try {
    await apiRequest(cat ? `/api/categories/${cat.id}` : '/api/categories', { method: cat ? 'PATCH' : 'POST', body: JSON.stringify(body) });
    closeModal('categoryModal');
    await loadCategories();
    toast(cat ? t('Категория «{name}» сохранена', { name }) : t('Категория «{name}» добавлена', { name }));
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
  $('deleteCategoryTitle').textContent = t('Удалить «{name}»?', { name: categoryName(cat) });
  $('deleteCategoryText').textContent = (count ? t('В ней {n} {w} за всё время.', { n: count, w: plural(count, 'позиция', 'позиции', 'позиций') }) : t('В ней пока нет позиций.'))
    + (limit ? ' ' + t('Лимит бюджета этой категории будет удалён.') : '');
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
    const target = moveTo ? categoryName(categoryById(Number(moveTo))) : null;
    await loadCategories();
    await loadReceipts();
    loadBudgets();
    loadRules();
    const moved = res?.moved_items || 0;
    toast(t('Категория «{name}» удалена', { name: categoryName(cat) }) + (moved ? (target ? t(', позиции перенесены в «{target}»', { target }) : t(', позиции остались без категории')) : ''));
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
        <button class="text-btn" type="button" data-rule-del="${esc(r.id)}" aria-label="${esc(t('Удалить правило «{pattern}»', { pattern: r.pattern }))}">✕</button></div>`;
    }).join('')
    : `<p class="rules-empty">${esc(t('Правил пока нет'))}</p>`;
}
$('rulesList').onclick = async e => {
  const b = e.target.closest('[data-rule-del]');
  if (!b) return;
  b.disabled = true;
  try {
    await apiRequest(`/api/category-rules/${b.dataset.ruleDel}`, { method: 'DELETE' });
    categoryRules = categoryRules.filter(r => String(r.id) !== b.dataset.ruleDel);
    renderRules();
    toast(t('Правило удалено'));
  } catch (err) { b.disabled = false; toast(t('Не удалось удалить правило: {error}', { error: err.message })); }
};
$('categoriesPeriod').onchange = renderCategories;
function renderAnalysis() {
  const p = $('analysisPeriod').value, cat = $('analysisCategory').value, list = filtered(p), allSums = sumsByCategory(list);
  const keys = [...categories, UNCATEGORIZED].map(catKey);
  $('analysisCategory').innerHTML = `<option value="all">${esc(t('Все категории'))}</option>` + categoryOptions(null);
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
  $('analysisTop').textContent = tops[0] ? categoryName(categoryByKey(tops[0][0])) : '—';
  const sums = chosenCat === 'all' ? allSums : { [chosenCat]: allSums[chosenCat] || 0 };
  const sorted = Object.entries(sums).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
  const maxSum = Math.max(...sorted.map(x => x[1]));
  $('breakdown').innerHTML = sorted.length
    ? sorted.map(([n, v]) => `<div class="legend-row"><i class="dot" aria-hidden="true"></i><div>${esc(categoryLabel(categoryByKey(n)))}<div class="progress" aria-hidden="true"><i style="width:${Math.round(v / maxSum * 100)}%"></i></div></div><strong>${money(v)}</strong></div>`).join('')
    : `<div class="empty">${esc(t('Нет данных'))}</div>`;
  const buckets = [], now = new Date();
  if (p === 'week') {
    for (let i = 6; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      buckets.push({ label: [t('Вс'), t('Пн'), t('Вт'), t('Ср'), t('Чт'), t('Пт'), t('Сб')][d.getDay()], key: d.toDateString(), sum: 0 });
    }
  } else {
    for (let i = 5; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      buckets.push({ label: fmtDateName(d, { month: 'short' }), key: d.getFullYear() + '-' + d.getMonth(), sum: 0 });
    }
  }
  chosen.forEach(r => {
    const d = new Date(r.iso);
    const b = buckets.find(x => x.key === (p === 'week' ? d.toDateString() : d.getFullYear() + '-' + d.getMonth()));
    if (b) b.sum += spent(r);
  });
  const max = Math.max(1, ...buckets.map(b => b.sum));
  $('chart').innerHTML = buckets.map(b => `<div class="bar-wrap" title="${esc(b.label)}: ${money(b.sum)}"><div class="bar" style="height:${Math.max(2, b.sum / max * 82)}%"></div><span class="bar-label">${esc(b.label)}</span></div>`).join('');
  const title = p === 'week' ? t('Последние 7 дней') : p === 'year' ? t('По месяцам') : t('Динамика за 6 месяцев');
  $('chartTitle').textContent = title;
  $('chart').setAttribute('aria-label', title + ': ' + buckets.map(b => `${b.label} — ${money(b.sum)}`).join(', '));
}
$('analysisPeriod').onchange = renderAnalysis;
$('analysisCategory').onchange = renderAnalysis;
