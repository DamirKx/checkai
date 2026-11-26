'use strict';

// --- Профиль: обложка, цифры, активность, привычки ---
// Всё, кроме форм, считается на клиенте по уже загруженным чекам (receipts)
const ACTIVITY_WEEKS = 26;
const DAY_MS = 864e5;
/* i18n-keys */
const WEEKDAYS_ON = ['по воскресеньям', 'по понедельникам', 'по вторникам', 'по средам', 'по четвергам', 'по пятницам', 'по субботам'];
/* /i18n-keys */
const prefersReducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const intText = n => new Intl.NumberFormat(LOC()).format(Math.round(n));
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
  $('profileSince').textContent = since && !Number.isNaN(+since) ? t('с нами с {date}', { date: fmtDateName(since, { day: 'numeric', month: 'long', year: 'numeric' }) }) : '';
  $('profileChips').innerHTML = [
    `<span class="pill">${esc(cur.symbol)} ${esc(cur.code)} · ${esc(t('основная валюта'))}</span>`,
    u.full_name ? '' : `<button class="pill pill-action" data-pf-goto="personal" type="button">${esc(t('＋ Добавить имя'))}</button>`,
    accounts.length > 1 ? `<button class="pill pill-action" data-pf-goto="accounts" type="button">${esc(t('{n} {w} на устройстве', { n: accounts.length, w: plural(accounts.length, 'аккаунт', 'аккаунта', 'аккаунтов') }))}</button>` : '',
  ].join('');
  const avg = spent.counted ? spent.total / spent.counted : 0;
  countTo($('profileReceipts'), receipts.length, intText, animate);
  countTo($('profileSpent'), spent.total, n => approx(money(n), spent.converted), animate);
  countTo($('profileAverage'), avg, n => approx(money(n), spent.converted), animate);
  countTo($('profileDays'), days, intText, animate);
  $('profileDaysLabel').textContent = t('{w} с нами', { w: plural(days, 'день', 'дня', 'дней') });
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
      if (d.getMonth() !== lastMonth && (w > 0 || d.getDate() <= 10)) months.push(`<span style="grid-column:${w + 1}">${esc(fmtDateName(d, { month: 'short' }).replace('.', ''))}</span>`);
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
  $('heatmap').setAttribute('aria-label', t('Активность за полгода: {n} {w}', { n: total, w: plural(total, 'чек', 'чека', 'чеков') }));
  const topDay = weekdays.indexOf(Math.max(...weekdays));
  $('activityStats').innerHTML = [
    [t('За полгода'), `${total} ${plural(total, 'чек', 'чека', 'чеков')}`],
    [t('Рекорд подряд'), best ? `${best} ${plural(best, 'день', 'дня', 'дней')}` : '—'],
    [t('Чаще всего'), total ? t(WEEKDAYS_ON[topDay]) : '—'],
  ].map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
}
$('heatmap').addEventListener('pointerover', e => {
  const cell = e.target.closest('.hm-cell:not(.future)'), tip = $('heatTip');
  if (!cell) { tip.hidden = true; return; }
  const s = activityDays[cell.dataset.day], d = new Date(cell.dataset.day + 'T00:00');
  tip.innerHTML = (s ? `${s.count} ${plural(s.count, 'чек', 'чека', 'чеков')} · ${esc(money(s.total))}` : esc(t('Нет чеков')))
    + `<small>${esc(fmtDateName(d, { weekday: 'short', day: 'numeric', month: 'long' }))}</small>`;
  const r = cell.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(r.left + r.width / 2, 90), window.innerWidth - 90) + 'px';
  tip.style.top = r.top + 'px';
  tip.hidden = false;
});
$('heatmap').addEventListener('pointerleave', () => { $('heatTip').hidden = true; });

// «Привычки»: любимый магазин, топ-категория, самый крупный чек, сравнение с прошлым месяцем
function renderHabits() {
  if (!receipts.length) {
    $('profileHabits').innerHTML = `<li class="pf-empty">${esc(t('Здесь появятся любимый магазин, топ-категория и самый крупный чек.'))}<button class="btn small" data-pf-goto="upload" type="button">${esc(t('Добавить первый чек'))}</button></li>`;
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
    store && { habit: 'store', value: store.name, label: t('Любимый магазин'), meta: `${store.count} ${plural(store.count, 'визит', 'визита', 'визитов')}`, data: store.name },
    topKey != null && { habit: 'category', value: categoryLabel(categoryByKey(topKey)), label: t('Больше всего тратите на'), meta: money(sums[topKey]) },
    biggest && { habit: 'receipt', value: approx(money(biggestSum), curOf(biggest) !== baseCurrency()), label: t('Самый крупный чек'), meta: biggest.store || fmtPurchase(biggest), data: biggest.id },
    { habit: 'analysis', value: money(now), label: t('Этот месяц'), cls: delta > 0 ? 'up' : delta < 0 ? 'down' : '',
      meta: delta == null ? t('в прошлом месяце трат не было') : delta === 0 ? t('как в прошлом месяце') : t('{arrow} {pct}% к прошлому', { arrow: delta > 0 ? '↑' : '↓', pct: Math.abs(delta) }) },
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
    { done: !!currentUser.full_name, text: t('Указать имя'), go: 'personal' },
    { done: receipts.length > 0, text: t('Сохранить первый чек'), go: 'upload' },
    { done: overallLimit(budgets) > 0, text: t('Задать лимит на месяц'), go: 'budgets' },
    { done: categoryRules.length > 0, text: t('Перенести товар в нужную категорию'), go: 'categories' },
  ];
  const done = steps.filter(s => s.done).length, all = done === steps.length;
  $('profileStart').hidden = all;
  $('profileGrid').classList.toggle('no-start', all);
  $('startCount').textContent = t('{done} из {all}', { done, all: steps.length });
  $('startBar').style.width = done / steps.length * 100 + '%';
  $('startList').innerHTML = steps.map(s => `<li><button class="pf-step${s.done ? ' done' : ''}" data-pf-goto="${s.go}" type="button"><i aria-hidden="true">${s.done ? '✓' : ''}</i><span>${esc(s.text)}${s.done ? `<span class="sr-only"> — ${esc(t('готово'))}</span>` : ''}</span></button></li>`).join('');
}

function renderProfileAccounts(accounts) {
  const id = currentUser.id;
  $('profileAccounts').innerHTML = accounts.map(a => {
    const me = a.user.id === id;
    return `<div class="account-row${me ? ' current' : ''}">${avatarHtml(a.user)}<div><b>${esc(a.user.full_name || a.user.email)}</b><small>${esc(a.user.email)}</small></div>${me ? `<span class="pill">${esc(t('Текущий'))}</span>`
      : `<button class="btn soft small" data-switch="${esc(a.user.id)}" type="button">${esc(t('Перейти'))}</button><button class="icon-btn" data-forget="${esc(a.user.id)}" type="button" title="${esc(t('Убрать с этого устройства'))}" aria-label="${esc(t('Убрать аккаунт {email} с этого устройства', { email: a.user.email }))}">✕</button>`}</div>`;
  }).join('');
  $('profileAccounts').querySelectorAll('[data-switch]').forEach(b => b.onclick = () => switchAccount(+b.dataset.switch));
  $('profileAccounts').querySelectorAll('[data-forget]').forEach(b => b.onclick = () => { forgetAccount(+b.dataset.forget); renderProfile(); renderAccountMenu(); });
}

function renderDataStats() {
  const items = receipts.reduce((s, r) => s + (r.items || []).length, 0), n = receipts.length, c = categories.length;
  $('dataStats').innerHTML = [[t('Чеков'), n], [t('Позиций'), items], [t('Категорий'), c], [t('Правил'), categoryRules.length]]
    .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${intText(v)}</dd></div>`).join('');
  $('exportAllJson').disabled = $('exportAllCsv').disabled = !n;
  const cw = plural(c, 'категория', 'категории', 'категорий');
  $('deleteSummary').textContent = n
    ? t('Удалятся {n} {w} с фото, {c} {cw}, правила и лимиты. Восстановить их будет нельзя.', { n, w: plural(n, 'чек', 'чека', 'чеков'), c, cw })
    : t('Удалятся {c} {cw}, правила и лимиты. Восстановить их будет нельзя.', { c, cw });
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
  toast(t('Выгрузка готова'));
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
const updateMeError = err => notSupported(err) ? t('Сервер пока не поддерживает редактирование аккаунта (нужен PATCH /api/auth/me).') : err.message;
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
      if (user.base_currency !== body.base_currency) { showFormError('profileError', t('Сервер пока не поддерживает основную валюту — остальные изменения сохранены.')); return; }
      // rate_to_base у чеков пересчитан сервером под новую валюту
      await loadReceipts();
    }
    toast(t('Данные сохранены'));
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
  $('editPasswordHint').textContent = !p ? t('Минимум 8 символов') : p.length < 8 ? t('Слишком короткий — минимум 8 символов') : ['', t('Слабый'), t('Средний'), t('Хороший'), t('Надёжный')][s];
}
$('editPassword').oninput = updatePasswordMeter;
function setPasswordShown(button, shown) {
  $(button.dataset.pwToggle).type = shown ? 'text' : 'password';
  button.textContent = shown ? t('Скрыть') : t('Показать');
  button.setAttribute('aria-label', shown ? t('Скрыть пароль') : t('Показать пароль'));
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
  if (!Object.keys(body).length) { showFormError('securityError', t('Укажите новый email или новый пароль')); return; }
  body.current_password = $('editCurrentPassword').value;
  const btn = $('securitySave');
  btn.disabled = true;
  try {
    await updateMe(body);
    toast(body.new_password ? t('Пароль обновлён') : t('Email обновлён'));
  } catch (err) {
    showFormError('securityError', updateMeError(err));
  } finally { btn.disabled = false; }
};
$('deleteForm').onsubmit = async e => {
  e.preventDefault();
  const u = currentUser;
  if (!u) return;
  $('deleteError').hidden = true;
  if (!confirm(t('Удалить аккаунт {email} и все его чеки? Это действие нельзя отменить.', { email: u.email }))) return;
  const btn = $('deleteSubmit');
  btn.disabled = true;
  try {
    await apiRequest('/api/auth/me', { method: 'DELETE', body: JSON.stringify({ password: $('deletePassword').value }) });
    try {
      localStorage.removeItem(budgetsKey(u.id)); localStorage.removeItem(avatarKey(u));
      [insightsRetryKey, insightsHiddenKey, insightsSeenKey].forEach(k => localStorage.removeItem(k(u.id)));
    } catch (e) {}
    forgetAccount(u.id); removeToken(); setGuest(); showPage('home');
    toast(t('Аккаунт удалён'));
  } catch (err) {
    showFormError('deleteError', notSupported(err) ? t('Сервер пока не поддерживает удаление аккаунта (нужен DELETE /api/auth/me).') : err.message);
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
  if (!file.type.startsWith('image/')) return showFormError('avatarError', t('Это не изображение'));
  if (file.size > AVATAR_MAX_FILE) return showFormError('avatarError', t('Файл больше 15 МБ'));
  const url = URL.createObjectURL(file);
  startCrop(url, () => URL.revokeObjectURL(url));
};

// Камера: getUserMedia с превью; где его нет — системная камера через <input capture>
$('avatarCameraBtn').onclick = async () => {
  if (!navigator.mediaDevices?.getUserMedia) { $('avatarCapture').click(); return; }
  try {
    avatarStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
  } catch (err) {
    showFormError('avatarError', err.name === 'NotAllowedError' ? t('Нет доступа к камере — разрешите его в настройках браузера') : t('Камера не найдена или занята другим приложением'));
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
  img.onerror = () => { done?.(); avatarStep('choose'); showFormError('avatarError', t('Не удалось открыть изображение. Попробуйте JPG или PNG')); };
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
  catch (e) { showFormError('avatarError', t('В браузере не хватило места для фото')); return; }
  refreshAvatars(); closeAvatar(); toast(t('Фото обновлено'));
};
$('avatarRemove').onclick = () => {
  try { localStorage.removeItem(avatarKey(currentUser)); } catch (e) {}
  refreshAvatars(); closeAvatar(); toast(t('Фото удалено'));
};
