'use strict';

// --- Рекомендации ИИ ---
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
// i18n-ignore-start: заглушка для разработки, текст не переводится
function stubInsightsData() {
  const [a, b] = categories, month = monthKey(new Date()), items = [];
  if (a) items.push({ id: `over_budget:${a.id}:${month}`, type: 'over_budget', priority: 1, title: `${a.name}: лимит превышен на 7 000 ₸`, text: 'Потрачено 32 000 ₸ при лимите 25 000 ₸. Больше всего — 14 покупок на 21 000 ₸.', category_id: a.id, action: { kind: 'open_history', category_id: a.id } });
  if (b) items.push({ id: `budget_suggestion:${b.id}:${month}`, type: 'budget_suggestion', priority: 2, title: `Поставить лимит на «${b.name}»`, text: 'Последние 3 месяца вы стабильно тратите 22–24 тысячи. Лимит 25 000 ₸ поможет держать это под контролем.', category_id: b.id, action: { kind: 'set_budget', category_id: b.id, amount: 25000 } });
  items.push({ id: `uncategorized::${month}`, type: 'uncategorized', priority: 3, title: '18% трат без категории', text: 'Разложите их по категориям — советы и лимиты станут точнее.', category_id: null, action: { kind: 'open_categories' } });
  return { insights: { summary: 'Это пример по контракту: сервер рекомендаций ещё не подключён.', items }, created_at: new Date().toISOString(), model: 'rules', stale: false };
}
// i18n-ignore-end
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
    // Срок «обновить можно через…» — по серверу: запомненный в браузере мог остаться от прежнего, более долгого лимита
    if (typeof data?.retry_after === 'number') {
      insights.retryAt = data.retry_after > 0 ? Date.now() + data.retry_after * 1000 : 0;
      try {
        if (insights.retryAt) localStorage.setItem(insightsRetryKey(user.id), String(insights.retryAt));
        else localStorage.removeItem(insightsRetryKey(user.id));
      } catch (e) {}
    }
  } catch (e) {
    if (insights.userId !== user.id) return;
    if (notSupported(e)) insights.unsupported = true;
  }
  insights.loaded = true;
  renderInsights();
}
// Согласие хранит сервер: preferences.ai_insights или временная колонка ai_insights_consent.
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
    else if (notSupported(e)) insights.error = t('Сервер пока не умеет готовить рекомендации');
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
      if (e.status && e.status < 500) return finish(e.status === 404 ? t('Анализ прервался. Попробуйте ещё раз') : e.message);
    }
    if (insights.userId !== user.id) return;
    if (task?.status === 'completed') {
      await loadInsights();
      if (!insights.data && task.result?.insights) insights.data = task.result;
      return finish('');
    }
    if (task?.status === 'failed') return finish(task.error ? t(task.error) : t('Не удалось подготовить советы. Попробуйте позже'));
    if (++tries >= INSIGHTS_POLL_MAX) return finish(t('Анализ идёт слишком долго. Загляните сюда чуть позже'));
    insights.poll = setTimeout(tick, INSIGHTS_POLL_MS);
  };
  insights.poll = setTimeout(tick, INSIGHTS_POLL_MS);
}

const clip = (s, n) => { const t = String(s ?? '').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
const waitText = ms => ms >= 3600e3 ? t('{n} ч', { n: Math.ceil(ms / 3600e3) }) : t('{n} мин', { n: Math.max(1, Math.ceil(ms / 60e3)) });
const insightsModel = m => { const s = String(m || ''); return /^gemini/i.test(s) ? 'Gemini' : /^ollama/i.test(s) ? 'Ollama' : s === 'rules' ? t('базовые правила') : s; };
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
    if (budgets.categories[catKey(cat)] === round2(a.amount)) return `<span class="ins-done">✓ ${esc(t('Лимит поставлен'))}</span>`;
    return `<button class="btn small" type="button" data-ins-act="set_budget" data-cat="${esc(catKey(cat))}" data-amount="${esc(round2(a.amount))}">${esc(t('Поставить лимит {sum}', { sum: money(a.amount) }))}</button>`;
  }
  if (a.kind === 'open_history') {
    const key = cat && cat !== UNCATEGORIZED ? catKey(cat) : 'all';
    return `<button class="btn soft small" type="button" data-ins-act="open_history" data-cat="${esc(key)}" data-period="${INSIGHT_MONTH_TYPES.includes(it.type) ? 'month' : 'all'}">${esc(t('Смотреть траты'))}</button>`;
  }
  if (a.kind === 'open_categories') return `<button class="btn soft small" type="button" data-ins-act="open_categories">${esc(t('Разобрать'))}</button>`;
  return '';
}
const insightCard = it => `<article class="ins-card" data-insight="${esc(it.id)}">
    <span class="ins-ico" aria-hidden="true">${esc(INSIGHT_ICONS[it.type] || '✦')}</span>
    <div class="ins-main">
      <h3>${esc(clip(it.title, 90))}</h3>
      <p>${esc(clip(it.text, 400))}</p>
      <div class="ins-actions">${insightAction(it)}<button class="text-btn" type="button" data-ins-hide aria-label="${esc(t('Скрыть совет «{title}»', { title: clip(it.title, 90) }))}">${esc(t('Скрыть'))}</button></div>
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
  refresh.textContent = blocked ? t('Обновить через {wait}', { wait: waitText(wait) }) : t('Обновить');
  const startBtn = label => `<button class="btn" type="button" data-ins-start ${blocked ? 'disabled' : ''}>${esc(blocked ? t('Можно через {wait}', { wait: waitText(wait) }) : label)}</button>`;
  let html;
  if (insights.running) html = `${INSIGHTS_SKELETON}<p class="ins-status" role="status"><span class="ins-spin" aria-hidden="true"></span>${esc(t('Анализируем расходы…'))}</p>`;
  else if (!data && receipts.length < INSIGHTS_MIN_RECEIPTS) {
    const n = INSIGHTS_MIN_RECEIPTS - receipts.length;
    html = `<div class="ins-intro"><p class="ins-lead">${esc(t('Добавьте ещё {n} {w} — советам нужна история.', { n, w: plural(n, 'чек', 'чека', 'чеков') }))}</p><div class="button-row"><button class="btn soft" type="button" data-ins-upload>${esc(t('＋ Добавить чек'))}</button></div></div>`;
  } else if (!data && !insightsConsent()) {
    html = `<div class="ins-intro">
      <p class="ins-lead">${esc(t('ИИ проанализирует ваши расходы и даст советы: где перерасход, какой лимит поставить, что повторяется каждый месяц.'))}</p>
      <p class="ins-note">${esc(t('Для этого во внешний сервис (Google Gemini) уйдёт обезличенная сводка — только суммы по категориям и магазинам и ваши лимиты. Имя, email и фото чеков не передаются.'))}</p>
      <div class="button-row"><button class="btn" type="button" data-ins-consent ${blocked ? 'disabled' : ''}>${esc(t('Разрешить и проанализировать'))}</button></div>
    </div>`;
  } else if (!data) {
    html = `<div class="ins-intro"><p class="ins-lead">${esc(t('Посмотрим, что можно улучшить в расходах.'))}</p><div class="button-row">${startBtn(t('Проанализировать'))}</div></div>`;
  } else {
    const items = visibleInsights(), all = data.insights.items || [], when = data.created_at ? serverDate(data.created_at) : null;
    const updated = when && !Number.isNaN(+when) ? t('Обновлено {date} в {time}', { date: when.toLocaleDateString(LOC(), { day: '2-digit', month: '2-digit' }), time: when.toLocaleTimeString(LOC(), { hour: '2-digit', minute: '2-digit' }) }) : '';
    html = (data.stale ? `<div class="ins-stale" role="status"><span>${esc(t('Есть новые чеки — обновить советы?'))}</span>${blocked ? `<small>${esc(t('Можно через {wait}', { wait: waitText(wait) }))}</small>` : `<button class="text-btn" type="button" data-ins-start>${esc(t('Обновить'))}</button>`}</div>` : '')
      + (data.insights.summary ? `<p class="ins-summary">${esc(clip(data.insights.summary, 400))}</p>` : '')
      + (items.length ? `<div class="ins-cards">${items.map(insightCard).join('')}</div>`
        : all.length ? `<p class="ins-empty">${esc(t('Все советы скрыты.'))} <button class="text-btn" type="button" data-ins-unhide>${esc(t('Показать снова'))}</button></p>`
        : `<p class="ins-empty">${esc(t('Особых советов нет — расходы выглядят ровно.'))}</p>`)
      + `<p class="ins-foot">${esc([updated, insightsModel(data.model)].filter(Boolean).join(' · '))}${data.model === 'rules' && !INSIGHTS_STUB ? '<br>' + esc(t('ИИ сейчас недоступен — показаны базовые советы')) : ''}</p>`;
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
  $('profileInsightsText').textContent = insights.running ? t('Анализируем расходы…')
    : fresh ? `${fresh} ${plural(fresh, 'новый совет', 'новых совета', 'новых советов')}`
    : items.length ? t('{n} {w} по расходам', { n: items.length, w: plural(items.length, 'совет', 'совета', 'советов') })
    : t('ИИ-советы по вашим расходам');
}
async function applyBudgetSuggestion(btn) {
  const key = btn.dataset.cat, amount = Number(btn.dataset.amount);
  if (!currentUser || !(amount > 0)) return;
  btn.disabled = true;
  try {
    await saveBudgets({ total: budgets.total, categories: { ...budgets.categories, [key]: amount } });
    renderBudgets(); renderStartSteps(); renderInsights();
    toast(t('Лимит на «{name}» — {sum} в месяц', { name: categoryName(categoryByKey(key)), sum: money(amount) }));
    // Лимиты поменялись — сервер пометит анализ устаревшим
    loadInsights();
  } catch (e) { btn.disabled = false; toast(t('Не удалось поставить лимит: {error}', { error: e.message })); }
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
