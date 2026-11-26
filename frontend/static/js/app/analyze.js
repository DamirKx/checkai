'use strict';

// --- Загрузка фото ---
function showUploadMessage(msg) { $('uploadMessage').textContent = msg; $('uploadMessage').hidden = !msg; }
// Проверяем файл до отправки, чтобы не ждать ответа сервера 413/415
function fileProblem(file) {
  if (/hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(file.name)) return t('Формат HEIC (фото с iPhone) не поддерживается. Сохраните фото как JPG или включите в настройках камеры формат «Наиболее совместимый».');
  if (!(ALLOWED_TYPES.includes(file.type) || (!file.type && ALLOWED_EXT.test(file.name)))) return t('Это не изображение чека. Подойдут JPG, PNG, WEBP, BMP или TIFF.');
  if (!file.size) return t('Файл пустой. Выберите другое фото.');
  // JPG/PNG/WEBP браузер сам сожмёт перед отправкой (prepareUpload), поэтому для них предел больше
  const limitMb = COMPRESSIBLE_TYPES.includes(file.type) ? MAX_SOURCE_MB : MAX_UPLOAD_MB;
  if (file.size > limitMb * 1024 * 1024) return tooBigText(file.size, limitMb);
  return null;
}
const tooBigText = (bytes, maxMb) => t('Файл весит {size} МБ, а можно не больше {max} МБ. Уменьшите фото или снимите с меньшим разрешением.', { size: new Intl.NumberFormat(LOC(), { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(bytes / 1024 / 1024), max: maxMb });

// --- Сжатие фото перед отправкой (мобильная версия) ---
// Снимок с камеры телефона весит 3–8 МБ, а сервер всё равно уменьшает его перед OCR. Уменьшаем в браузере:
// короткую сторону (ширину чека) — до UPLOAD_SHORT_SIDE. Длинную сторону отдельно не ограничиваем, чтобы длинный
// чек остался читаемым; ограничена только площадь — больше ~16 Мп холст на iPhone не создаётся.
// Любая ошибка — отправляем исходный файл
const MAX_SOURCE_MB = 40, UPLOAD_SHORT_SIDE = 1600, UPLOAD_MAX_PIXELS = 16e6, UPLOAD_QUALITY = 0.85;
const COMPRESS_FROM_BYTES = 1.5 * 1024 * 1024;
const COMPRESSIBLE_TYPES = ['image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png', 'image/webp'];
async function prepareUpload(file) {
  if (!COMPRESSIBLE_TYPES.includes(file.type) || typeof createImageBitmap !== 'function') return file;
  let bitmap;
  try { bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch (e) { return file; }
  try {
    const { width, height } = bitmap;
    const scale = Math.min(1, UPLOAD_SHORT_SIDE / Math.min(width, height), Math.sqrt(UPLOAD_MAX_PIXELS / (width * height)));
    if (scale >= 1 && file.size <= COMPRESS_FROM_BYTES) return file;
    const w = Math.max(1, Math.round(width * scale)), h = Math.max(1, Math.round(height * scale));
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff'; // прозрачный PNG → белый фон, как бумага чека
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bitmap, 0, 0, w, h);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', UPLOAD_QUALITY));
    if (!blob || blob.size >= file.size) return file;
    const name = String(file.name || 'receipt').replace(/\.[^.]+$/, '') + '.jpg';
    return new File([blob], name, { type: 'image/jpeg', lastModified: Date.now() });
  } catch (e) {
    return file;
  } finally {
    bitmap.close?.();
  }
}
function selectFile(file) {
  if (analyzing) return;
  const problem = fileProblem(file);
  if (problem) { showUploadMessage(problem); return; }
  selectedFile = file;
  if ($('imagePreview').src.startsWith('blob:')) URL.revokeObjectURL($('imagePreview').src);
  $('imagePreview').src = URL.createObjectURL(file);
  $('fileName').textContent = file.name;
  $('dropPrompt').hidden = true;
  $('previewBox').hidden = false;
  showUploadMessage('');
  $('analyzeBtn').focus();
}
function clearFile() {
  selectedFile = null;
  $('fileInput').value = '';
  $('cameraInput').value = '';
  $('previewBox').hidden = true;
  $('dropPrompt').hidden = false;
}
$('maxSizeText').textContent = MAX_UPLOAD_MB;
// Пока открыта камера или галерея, фон страницы не рисуется (CSS: html.capturing) — меньше памяти,
// и Android реже выгружает Chrome вместе со снимком. Возвращаем, когда страница снова видна
function startCapture(input) {
  document.documentElement.classList.add('capturing');
  input.click();
}
const endCapture = () => document.documentElement.classList.remove('capturing');
document.addEventListener('visibilitychange', () => { if (!document.hidden) setTimeout(endCapture, 300); });
window.addEventListener('focus', () => setTimeout(endCapture, 300));
$('chooseBtn').onclick = () => startCapture($('fileInput'));
$('cameraBtn').onclick = () => startCapture($('cameraInput'));
$('dropzone').onclick = e => {
  if (analyzing || e.target.closest('button') || e.target.tagName === 'INPUT' || !$('previewBox').hidden) return;
  $('fileInput').click();
};
$('fileInput').onchange = e => { endCapture(); const f = e.target.files[0]; if (f) selectFile(f); };
$('cameraInput').onchange = e => { endCapture(); const f = e.target.files[0]; if (f) selectFile(f); };
// Окно выбора закрыли без фото
$('fileInput').addEventListener('cancel', endCapture);
$('cameraInput').addEventListener('cancel', endCapture);
$('removeBtn').onclick = clearFile;
const dz = $('dropzone');
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, x => { x.preventDefault(); if (!analyzing) dz.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, x => { x.preventDefault(); dz.classList.remove('drag'); }));
dz.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) selectFile(f); });

// --- Распознавание: этапы и блокировка кнопок ---
const STEPS = ['upload', 'ocr', 'ai'];
let stepsTimer = null;
function setStep(step, fraction) {
  const idx = STEPS.indexOf(step);
  $('analyzeSteps').querySelectorAll('li').forEach((li, i) => {
    li.classList.toggle('done', i < idx);
    li.classList.toggle('active', i === idx);
  });
  $('stepsBar').style.width = Math.round((idx + Math.min(fraction, .95)) / STEPS.length * 100) + '%';
}
function startSteps(withUpload) {
  const started = Date.now();
  let uploadedAt = withUpload ? null : started, uploadFraction = 0;
  $('analyzeSteps').hidden = false;
  const tick = () => {
    const seconds = Math.round((Date.now() - started) / 1000);
    if (uploadedAt == null) {
      setStep('upload', uploadFraction);
      $('stepsNote').textContent = t('Отправляем фото… {pct}%', { pct: Math.round(uploadFraction * 100) });
      return;
    }
    const sinceUpload = Date.now() - uploadedAt;
    if (sinceUpload < OCR_STEP_MS) setStep('ocr', sinceUpload / OCR_STEP_MS);
    else setStep('ai', 1 - Math.exp(-(sinceUpload - OCR_STEP_MS) / 30000));
    $('stepsNote').textContent = t('{n} с', { n: seconds });
  };
  tick();
  stepsTimer = setInterval(tick, 400);
  return {
    progress(fraction, done) { uploadFraction = fraction; if (done && uploadedAt == null) uploadedAt = Date.now(); tick(); },
  };
}
function stopSteps() { clearInterval(stepsTimer); stepsTimer = null; $('analyzeSteps').hidden = true; }
let analyzingTest = false;
function setAnalyzing(on, test) {
  analyzing = on;
  analyzingTest = !!test;
  ['analyzeBtn', 'removeBtn', 'testBtn', 'chooseBtn', 'cameraBtn', 'manualBtn'].forEach(id => { $(id).disabled = on; });
  $('dropzone').classList.toggle('busy', on);
  $('dropzone').setAttribute('aria-busy', on);
  $('analyzeBtn').textContent = on && !test ? t('Распознаём…') : t('Распознать чек');
  $('testBtn').textContent = on && test ? t('Распознаём демо-чек…') : t('Попробовать на демо-чеке →');
}
// --- Распознавание через фоновую задачу ---
// POST /api/analyze/async сразу отвечает task_id, а результат забирается опросом GET /api/tasks/{id}.
// Один долгий запрос на 20–30 с на телефоне обрывается, если погас экран или пользователь ушёл в другое
// приложение; опрос же просто продолжается. id задачи — в sessionStorage: если браузер выгрузил вкладку,
// после возврата распознавание подхватывается (resumePendingAnalyze)
const PENDING_TASK_KEY = 'checkai_pending_task';
const TASK_POLL_MS = 1500, TASK_POLL_HIDDEN_MS = 6000, TASK_MAX_MS = 10 * 60 * 1000, TASK_RETRIES = 8;
const savePendingTask = value => {
  try { if (value) sessionStorage.setItem(PENDING_TASK_KEY, JSON.stringify(value)); else sessionStorage.removeItem(PENDING_TASK_KEY); } catch (e) {}
};
const readPendingTask = () => { try { return JSON.parse(sessionStorage.getItem(PENDING_TASK_KEY) || 'null'); } catch (e) { return null; } };
function waitTask(taskId, startedAt) {
  return new Promise((resolve, reject) => {
    let failures = 0, timer = null, busy = false, settled = false;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
      fn(value);
    };
    const schedule = () => { if (!settled) timer = setTimeout(poll, document.hidden ? TASK_POLL_HIDDEN_MS : TASK_POLL_MS); };
    async function poll() {
      if (busy || settled) return;
      clearTimeout(timer);
      if (Date.now() - startedAt > TASK_MAX_MS) return finish(reject, new Error(t('Распознавание заняло слишком много времени. Попробуйте ещё раз')));
      busy = true;
      try {
        const task = await apiRequest('/api/tasks/' + encodeURIComponent(taskId));
        failures = 0;
        if (task.status === 'completed') return finish(resolve, task.result || {});
        if (task.status === 'failed') return finish(reject, new Error(task.error || t('Не удалось распознать чек')));
      } catch (e) {
        // Сеть на телефоне пропадает на секунды, сервер может перезапускаться — повторяем.
        // 4xx (задача не найдена, нет доступа) — повторять бессмысленно
        if (e.status >= 400 && e.status < 500) return finish(reject, e);
        if (++failures > TASK_RETRIES) return finish(reject, e);
      } finally {
        busy = false;
      }
      schedule();
    }
    // Вернулись в приложение — проверяем сразу, не дожидаясь таймера
    function onVisible() { if (!document.hidden) poll(); }
    document.addEventListener('visibilitychange', onVisible);
    poll();
  });
}
async function analyze(test = false) {
  if (analyzing || (!test && !selectedFile)) return;
  setAnalyzing(true, test);
  showUploadMessage('');
  const steps = startSteps(!test);
  try {
    let payload;
    if (test) payload = await apiRequest('/api/analyze-test', { method: 'POST' });
    else {
      const file = await prepareUpload(selectedFile);
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) throw new Error(tooBigText(file.size, MAX_UPLOAD_MB));
      const fd = new FormData();
      fd.append('file', file);
      const started = await apiUpload('/api/analyze/async', fd, steps.progress);
      savePendingTask({ id: started.task_id, at: Date.now(), user: currentUser?.id ?? null });
      payload = await waitTask(started.task_id, Date.now());
    }
    if (payload.success === false) throw new Error(payload.error ? t(payload.error) : t('Не удалось распознать чек'));
    renderResult(payload.data ?? payload);
  } catch (e) {
    showUploadMessage(t('Не получилось распознать чек: {error}', { error: e.message }));
  } finally {
    if (!test) savePendingTask(null);
    stopSteps();
    setAnalyzing(false, test);
  }
}
// Браузер выгрузил вкладку во время распознавания (на телефоне это обычно) — после возврата ждём ту же задачу
function resumePendingAnalyze() {
  const pending = readPendingTask();
  if (!pending || analyzing) return;
  if (!pending.id || Date.now() - pending.at > TASK_MAX_MS || (pending.user ?? null) !== (currentUser?.id ?? null)) { savePendingTask(null); return; }
  showPage('upload');
  setAnalyzing(true, false);
  showUploadMessage('');
  startSteps(false);
  waitTask(pending.id, pending.at)
    .then(data => renderResult(data))
    .catch(e => showUploadMessage(t('Не получилось распознать чек: {error}', { error: e.message })))
    .finally(() => { savePendingTask(null); stopSteps(); setAnalyzing(false, false); });
}
$('analyzeBtn').onclick = () => analyze();
$('testBtn').onclick = () => analyze(true);
window.addEventListener('beforeunload', e => { if (analyzing) e.preventDefault(); });

// --- Результат распознавания ---
function renderResult(data) {
  lastResult = toDraft(data);
  showResultEditor();
  const hint = currencyHintText();
  $('currencyHint').textContent = hint || '';
  $('currencyHint').hidden = !hint;
  scrollToResult();
}
// Валюту не нашли на самом чеке — просим проверить
function currencyHintText() {
  const name = currencyName(currencyInfo(lastResult.currency));
  const source = lastResult.currency_source;
  if (source === 'default') return t('Валюта на чеке не найдена, поставили «{name}». Проверьте поле «Валюта» под позициями.', { name });
  if (source === 'country') return t('Валюта определена по стране чека: «{name}». Если платили в другой валюте, поменяйте её под позициями.', { name });
  return '';
}
const resultKickerText = () => lastResult && !(lastResult.image_path || lastResult.currency_source) ? t('Новый чек') : t('Результат');
const scrollToResult = () => { if (window.matchMedia('(max-width: 850px)').matches) $('resultCard').scrollIntoView({ behavior: 'smooth', block: 'start' }); };
function showResultEditor() {
  $('resultPlaceholder').hidden = true;
  $('resultContent').hidden = false;
  $('resultClose').hidden = false;
  $('resultError').hidden = true;
  $('resultKicker').lastChild.textContent = resultKickerText();
  mountEditor($('resultEditor'), lastResult);
  showDuplicateHint();
}
// Сразу после распознавания: тот же чек уже есть в истории (по фото/фискальному номеру от сервера или по содержимому)
function showDuplicateHint() {
  const d = lastResult, byServer = d?.duplicate_of != null ? receipts.find(r => String(r.id) === String(d.duplicate_of)) : null;
  const dup = d ? byServer || findDuplicate(d) : null;
  $('duplicateHint').hidden = !dup;
  $('duplicateHint').dataset.id = dup ? dup.id : '';
  if (dup) $('duplicateHintText').textContent = byServer ? t('Этот чек уже загружали: {brief}.', { brief: receiptBrief(dup) }) : t('Похоже, этот чек уже сохранён: {brief}.', { brief: receiptBrief(dup) });
}
$('duplicateHintOpen').onclick = () => { const id = $('duplicateHint').dataset.id; if (id) openReceipt(id); };
// «Ввести вручную»: пустой черновик — сегодня, сейчас, основная валюта, одна пустая строка
function startManualReceipt() {
  if (analyzing) return;
  if (lastResult && !confirm(t('Заменить текущий черновик чека пустым?'))) return;
  const now = new Date();
  lastResult = toDraft({ date: isoDay(now), time: `${pad2(now.getHours())}:${pad2(now.getMinutes())}`, currency: baseCurrency(), total: 0, items: [{ name: '', quantity: 1, price_per_unit: 0, total_price: 0 }] });
  showResultEditor();
  $('currencyHint').hidden = true;
  scrollToResult();
  $('resultEditor').querySelector('[data-f="store"]').focus({ preventScroll: true });
}
$('manualBtn').onclick = startManualReceipt;
// Обратное к renderResult: после сохранения чек нельзя сохранить второй раз
function resetResult() {
  lastResult = null;
  $('resultContent').hidden = true;
  $('resultClose').hidden = true;
  $('resultPlaceholder').hidden = false;
  $('resultEditor').innerHTML = '';
  $('resultError').hidden = true;
  $('duplicateHint').hidden = true;
  $('resultKicker').lastChild.textContent = t('Результат');
  $('currencyHint').hidden = true;
  clearFile();
}
// Крестик в шапке карточки: убрать ручной ввод или распознанный, но не сохранённый чек
const draftTouched = d => !!(d.store.trim() || num(d.total) > 0 || d.image_path || d.items.some(i => i.name.trim() || num(i.total_price)));
$('resultClose').onclick = () => {
  if (!lastResult || $('saveReceiptBtn').disabled) return;
  if (draftTouched(lastResult) && !confirm(t('Убрать черновик чека? Он ещё не сохранён.'))) return;
  resetResult();
  // Фокус — туда, откуда обычно начинают: «Ввести вручную» или «Выбрать»
  ($('manualBtn').offsetParent ? $('manualBtn') : $('chooseBtn')).focus();
};
// Гость может распознавать чеки, а для сохранения просим войти
async function saveReceipt() {
  if (!lastResult) return;
  $('resultError').hidden = true;
  const problem = draftProblem(lastResult);
  if (problem) { showFormError('resultError', problem.message); focusDraftProblem($('resultEditor'), problem); return; }
  if (!getToken()) { pendingSave = true; openAuth('login'); toast(t('Войдите, чтобы сохранить чек')); return; }
  if (!confirmDraft(lastResult)) return;
  const btn = $('saveReceiptBtn');
  btn.disabled = true;
  try {
    // В ответе — сохранённый чек с курсом к основной валюте (rate_to_base), он нужен для бюджетов
    const res = await sendReceipt('/api/receipts/save', 'POST', lastResult);
    // Не стали сохранять копию: черновик остаётся, его можно поправить или убрать
    if (!res.saved) { if (res.choice === 'open') openExisting(res.existing); return; }
    const saved = res.saved;
    await loadReceipts();
    resetResult();
    const alert = budgetAlert(saved);
    $('savedModalText').textContent = alert;
    $('savedModalText').hidden = !alert;
    openModal('savedModal', $('savedToHistory'));
  } catch (e) {
    if (!getToken()) { pendingSave = true; openAuth('login'); }
    // Ошибки проверки на сервере (422) — рядом с редактором, как есть
    showFormError('resultError', t('Не удалось сохранить: {error}', { error: e.message }));
    toast(t('Ошибка при сохранении: {error}', { error: e.message }));
  } finally { btn.disabled = false; }
}
$('saveReceiptBtn').onclick = saveReceipt;
$('savedToHistory').onclick = () => { closeModal('savedModal'); showPage('history'); };
$('savedStay').onclick = $('savedModalClose').onclick = () => closeModal('savedModal');
$('savedModal').onclick = e => { if (e.target.id === 'savedModal') closeModal('savedModal'); };
// Пользователь сам выбрал валюту — подсказка «проверьте валюту» больше не нужна
$('resultEditor').addEventListener('input', e => { if (e.target.dataset.f === 'currency') $('currencyHint').hidden = true; });
$('downloadBtn').onclick = () => { if (lastResult) downloadJson(toPayload(lastResult), 'receipt_data.json'); };
