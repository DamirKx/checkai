'use strict';

// --- Язык интерфейса ---
// Выбор хранится в localStorage (i18n.js) и у вошедшего — в preferences.language на сервере.
// При входе и при старте язык берётся из аккаунта; новый аккаунт получает язык с этого устройства
let langFromDevice = false;
function renderLangPicks() {
  const lang = I18N.getLang();
  document.querySelectorAll('[data-lang-select]').forEach(s => { s.value = lang; });
  document.querySelectorAll('[data-lang-pick]').forEach(b => {
    const on = b.dataset.langPick === lang;
    b.setAttribute('aria-checked', String(on));
    b.tabIndex = on ? 0 : -1;
  });
}
async function saveLangOnServer(lang) {
  try { await updateMe({ preferences: { language: lang } }); }
  // Старый сервер поле пропускает (язык остаётся только в браузере), 404/405 — тоже не ошибка для пользователя
  catch (e) { if (!notSupported(e)) toast(t('Не удалось сохранить язык в аккаунте: {error}', { error: e.message })); }
}
function chooseLang(lang) {
  const next = I18N.normalize(lang);
  if (!next) return;
  I18N.setLang(next);
  renderLangPicks();
  if (currentUser && I18N.normalize(currentUser.preferences?.language) !== next) saveLangOnServer(next);
}
function applyUserLang(user) {
  const server = I18N.normalize(user?.preferences?.language);
  if (langFromDevice) {
    langFromDevice = false;
    if (server !== I18N.getLang()) saveLangOnServer(I18N.getLang());
    return;
  }
  if (server && server !== I18N.getLang()) { I18N.setLang(server); renderLangPicks(); }
}
document.querySelectorAll('[data-lang-select]').forEach(s => { s.onchange = () => chooseLang(s.value); });
document.querySelectorAll('[data-lang-pick]').forEach(b => { b.onclick = () => chooseLang(b.dataset.langPick); });
$('langPicks').addEventListener('keydown', e => {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!step) return;
  e.preventDefault();
  const langs = I18N.LANGS, next = langs[(langs.indexOf(I18N.getLang()) + step + langs.length) % langs.length];
  chooseLang(next);
  document.querySelector(`[data-lang-pick="${next}"]`).focus();
});
// Смена языка без перезагрузки: статический текст переводит i18n.js, здесь — всё, что рисует app.js.
// Черновик чека и введённые лимиты не теряются: редактор пересобирается из того же объекта черновика
I18N.onChange(() => {
  applyTheme(document.documentElement.dataset.theme || 'dark', false);
  $('menuBtn').setAttribute('aria-label', $('drawer').classList.contains('open') ? t('Закрыть меню') : t('Открыть меню'));
  renderAuthTexts();
  document.querySelectorAll('[data-pw-toggle]').forEach(b => setPasswordShown(b, $(b.dataset.pwToggle).type === 'text'));
  updatePasswordMeter();
  setAnalyzing(analyzing, analyzingTest);
  renderDrawerFoot();
  if (currentUser) $('userMenuBtn').setAttribute('aria-label', t('Меню аккаунта: {name}', { name: currentUser.full_name || currentUser.email }));
  renderIconPicks();
  // Список валют в «Личном» — с сохранением выбранной, но ещё не сохранённой валюты
  if (currentUser) { const v = $('editBaseCurrency').value; $('editBaseCurrency').innerHTML = currencyOptions(baseCurrency()); $('editBaseCurrency').value = v || baseCurrency(); }
  if (lastResult && !$('resultContent').hidden) {
    mountEditor($('resultEditor'), lastResult);
    showDuplicateHint();
    if (!$('currencyHint').hidden) $('currencyHint').textContent = currencyHintText();
  }
  $('resultKicker').lastChild.textContent = resultKickerText();
  if (!$('budgetEditor').hidden) {
    const values = { total: $('budgetTotalInput').value, categories: {} };
    $('budgetFields').querySelectorAll('[data-cat]').forEach(i => { values.categories[i.dataset.cat] = i.value; });
    openBudgetEditor(values);
  }
  rerender();
  renderAccountMenu();
  renderLangPicks();
  // Рекомендации у сервера — отдельно для каждого языка
  if (currentUser && insights.loaded && !insights.unsupported) loadInsights();
});
renderLangPicks();
