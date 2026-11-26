'use strict';

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
      else toast(t('Не удалось загрузить бюджеты: {error}', { error: e.message }));
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
  const [y, m] = key.split('-').map(Number), s = fmtDateName(new Date(y, m - 1, 1), { month: 'long', year: 'numeric' });
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
const budgetBar = (label, pct) => `<div class="progress budget-bar" role="progressbar" aria-label="${esc(t('{label}: потрачено {pct}% лимита', { label, pct }))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(pct, 100)}"><i style="width:${Math.min(pct, 100)}%"></i></div>`;
const leftText = (spent, limit) => spent <= limit ? t('осталось {sum}', { sum: money(limit - spent) }) : t('перерасход {sum}', { sum: money(spent - limit) });
const spentOfText = (spent, limit) => t('{spent} из {limit}', { spent: money(spent), limit: money(limit) });

// Прогноз только для текущего месяца: траты в среднем за прошедшие дни × дней в месяце
function budgetForecast(key, spent, limit) {
  const now = new Date();
  if (key !== monthKey(now) || !limit) return '';
  if (spent > limit) return t('Лимит месяца уже превышен на {sum}.', { sum: money(spent - limit) });
  const day = now.getDate(), days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const projected = spent / day * days, perDay = money((limit - spent) / (days - day + 1));
  const pace = projected > limit
    ? t('По текущему темпу к концу месяца выйдет около {sum} — на {over} больше лимита.', { sum: money(projected), over: money(projected - limit) })
    : t('По текущему темпу к концу месяца выйдет около {sum} — укладываетесь в лимит.', { sum: money(projected) });
  return `${pace} ${t('Чтобы уложиться, тратьте не больше {sum} в день.', { sum: perDay })}`;
}
function budgetRow(b) {
  const pct = budgetPct(b.spent, b.limit);
  return `<div class="card budget-row ${budgetState(b.spent, b.limit)}">
    <div class="cat-icon" aria-hidden="true">${esc(b.icon)}</div>
    <div class="budget-main">
      <div class="budget-line"><h3>${esc(categoryName(b))}</h3><span class="budget-pct">${pct}%</span></div>
      ${budgetBar(categoryName(b), pct)}
      <small>${esc(spentOfText(b.spent, b.limit))} · ${esc(leftText(b.spent, b.limit))}</small>
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
  // Точка на вкладке «Бюджеты» в нижней панели (телефон)
  $('budgetDot').hidden = $('budgetBadge').hidden;
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
  $('budgetLimitLabel').textContent = budgets.total || !limit ? t('Бюджет на месяц') : t('Сумма лимитов');
  $('budgetLimit').textContent = limit ? money(limit) : '—';
  $('budgetSpent').textContent = approx(money(spent), spending.converted);
  showSkipped('budgetSkipped', spending.skipped);
  $('budgetLeftLabel').textContent = limit && spent > limit ? t('Перерасход') : t('Осталось');
  $('budgetLeft').textContent = limit ? money(Math.abs(limit - spent)) : '—';
  $('budgetLeft').classList.toggle('over', !!limit && spent > limit);
  if (!hasBudgets(budgets)) {
    $('budgetOverall').innerHTML = `<div class="card empty"><button class="text-btn" type="button" data-budget-edit>${esc(t('Задать лимиты'))}</button></div>`;
  } else {
    const pct = budgetPct(spent, limit), title = budgets.total ? t('Весь месяц') : t('Категории с лимитом'), forecast = budgetForecast(key, spent, limit);
    $('budgetOverall').innerHTML = `<div class="card budget-overall ${state}">
      <div class="budget-line"><h3>${esc(title)}</h3><span class="budget-pct">${pct}%</span></div>
      ${budgetBar(title, pct)}
      <small>${esc(spentOfText(spent, limit))} · ${esc(leftText(spent, limit))}</small>
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
  $('budgetUnlimited').textContent = t('Без лимита: {list}', { list: unlimited.map(([k, v]) => `${categoryName(categoryByKey(k))} — ${money(v)}`).join(', ') });
}
// Предупреждение, если новый чек довёл лимит до 80% или превысил его. receipt — ответ сервера на сохранение
function budgetAlert(receipt) {
  const rate = baseRate(receipt);
  if (!hasBudgets(budgets) || rate == null) return '';
  const spending = monthSpending(receiptMonth({ iso: receiptIso(receipt) })), added = {};
  (receipt.items || []).forEach(i => { const k = catKey(itemCategory(i)); added[k] = (added[k] || 0) + num(i.total_price) * rate; });
  const checks = Object.entries(budgets.categories).filter(([k]) => added[k])
    .map(([k, limit]) => ({ name: `«${categoryName(categoryByKey(k))}»`, limit, spent: spending.byCategory[k] || 0, added: added[k] }));
  if (budgets.total) checks.push({ name: t('месяца'), limit: budgets.total, spent: spending.total, added: num(receipt.total) * rate });
  const rank = { ok: 0, warn: 1, over: 2 };
  const crossed = checks
    .filter(c => rank[budgetState(c.spent, c.limit)] > rank[budgetState(c.spent - c.added, c.limit)])
    .sort((a, b) => b.spent / b.limit - a.spent / a.limit)[0];
  if (!crossed) return '';
  return crossed.spent > crossed.limit
    ? t('Бюджет {name} превышен на {sum}', { name: crossed.name, sum: money(crossed.spent - crossed.limit) })
    : t('Бюджет {name}: потрачено {pct}%', { name: crossed.name, pct: budgetPct(crossed.spent, crossed.limit) });
}
const budgetField = (cat, limit, avg) => `<label class="budget-field"><span><span aria-hidden="true">${esc(cat.icon)}</span> ${esc(categoryName(cat))}</span><input class="field" type="number" min="0" step="any" inputmode="decimal" data-cat="${esc(catKey(cat))}" value="${limit ? esc(limit) : ''}" placeholder="${esc(t('Без лимита'))}"><small>${esc(avg ? t('в среднем {sum} в месяц', { sum: money(avg) }) : t('трат пока не было'))}</small></label>`;
// values — введённые, но не сохранённые лимиты: при смене языка форма перерисовывается без потери ввода
function openBudgetEditor(values = null) {
  if (!currentUser) return;
  // Лимит — только на категории пользователя: у «Без категории» его нет
  const avg = averageSpending(3);
  $('budgetFields').innerHTML = categories.map(c => budgetField(c, values ? values.categories[catKey(c)] : budgets.categories[catKey(c)], avg.byCategory[catKey(c)])).join('');
  $('budgetTotalInput').value = values ? values.total : budgets.total || '';
  $('budgetEditorTitle').textContent = t('Лимиты на месяц, {sym}', { sym: currencyInfo(baseCurrency()).symbol });
  $('budgetTotalHint').textContent = avg.total ? t('В среднем {sum} в месяц. Без общего лимита бюджет — сумма лимитов по категориям', { sum: money(avg.total) }) : t('Без общего лимита бюджет — сумма лимитов по категориям');
  if (values) return;
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
    toast(budgetsLocal ? t('Лимиты сохранены на этом устройстве') : t('Лимиты сохранены'));
  } catch (err) {
    showFormError('budgetError', err.message);
  } finally { btn.disabled = false; }
};
$('budgetEditBtn').onclick = () => { if ($('budgetEditor').hidden) openBudgetEditor(); else closeBudgetEditor(); };
$('budgetCancel').onclick = () => { closeBudgetEditor(); $('budgetEditBtn').focus(); };
$('budgetOverall').onclick = e => { if (e.target.closest('[data-budget-edit]')) openBudgetEditor(); };
$('budgetLoginBtn').onclick = () => openAuth('login');
$('budgetMonth').onchange = renderBudgets;
