import os
import shutil
import tempfile
import traceback
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="CheckAI — Анализ кассовых чеков", version="1.0.0")

# Ленивая инициализация OCR (чтобы сервер стартовал моментально без подвисаний)
ocr_model = None


def get_ocr():
    global ocr_model
    if ocr_model is None:
        from main import init_ocr
        ocr_model = init_ocr()
    return ocr_model


@app.get("/", response_class=HTMLResponse)
def index():
    """Веб-интерфейс MVP с поддержкой как загрузки файлов, так и быстрого теста images.jpeg."""
    return r"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CheckAI — Анализ чеков</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .font-mono { font-family: 'JetBrains Mono', monospace; }
  </style>
</head>
<body class="bg-slate-50/70 text-slate-800 min-h-screen antialiased selection:bg-emerald-100 selection:text-emerald-800">

  <div class="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    
    <!-- Навбар / Шапка -->
    <header class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-8 mb-8 border-b border-slate-200">
      <div>
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-emerald-600 text-white flex items-center justify-center font-bold text-lg shadow-sm shadow-emerald-600/20">
            C
          </div>
          <div>
            <h1 class="text-xl font-bold tracking-tight text-slate-900 flex items-center gap-2">
              CheckAI
              <span class="text-xs px-2 py-0.5 rounded-md font-mono font-medium bg-slate-100 border border-slate-200 text-slate-600">v1.0</span>
            </h1>
          </div>
        </div>
        <p class="text-xs text-slate-500 mt-1">Оптическое распознавание и структурирование кассовых чеков</p>
      </div>

      <div class="flex items-center gap-2 self-start sm:self-auto">
        <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white border border-slate-200 shadow-sm text-xs font-medium text-slate-700">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          PaddleOCR v3 Ready
        </div>
      </div>
    </header>

    <!-- Основная сетка -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
      
      <!-- Левая колонка: Загрузка -->
      <div class="lg:col-span-5 space-y-5">
        
        <div class="bg-white border border-slate-200/90 rounded-2xl p-6 shadow-sm shadow-slate-100">
          
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-xs font-bold text-slate-400 uppercase tracking-wider">Источник чека</h2>
            <span class="text-[11px] text-slate-400 font-mono">JPG, PNG, WEBP</span>
          </div>

          <!-- Быстрый тест -->
          <button id="testBtn" class="w-full group mb-4 px-4 py-3 rounded-xl bg-slate-50 hover:bg-slate-100/90 border border-slate-200 hover:border-emerald-500/50 text-left transition-all duration-200 flex items-center justify-between">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-lg bg-emerald-50 border border-emerald-200/60 flex items-center justify-center text-emerald-600 group-hover:scale-105 transition">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                </svg>
              </div>
              <div>
                <p class="text-xs font-bold text-slate-800">Тестовый чек images.jpeg</p>
                <p class="text-[11px] text-slate-500">Быстрый запуск без выбора файла</p>
              </div>
            </div>
            <svg class="w-4 h-4 text-slate-400 group-hover:text-emerald-600 transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
            </svg>
          </button>

          <div class="relative flex items-center py-2 mb-4">
            <div class="flex-grow border-t border-slate-200"></div>
            <span class="flex-shrink mx-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">или загрузите свой</span>
            <div class="flex-grow border-t border-slate-200"></div>
          </div>

          <!-- Drag and Drop зона -->
          <div id="dropzone" class="border-2 border-dashed border-slate-200 hover:border-emerald-500 rounded-xl p-6 text-center bg-slate-50/50 hover:bg-emerald-50/20 transition-all duration-200 cursor-pointer group flex flex-col items-center justify-center min-h-[160px]">
            <input type="file" id="fileInput" accept="image/*" class="hidden">
            <div class="w-10 h-10 rounded-full bg-white border border-slate-200 shadow-sm flex items-center justify-center text-slate-500 group-hover:text-emerald-600 group-hover:border-emerald-300 transition mb-3">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
              </svg>
            </div>
            <p class="text-xs font-bold text-slate-700">Перетащите чек в эту область</p>
            <p class="text-[11px] text-slate-400 mt-1">или нажмите для выбора файла</p>
          </div>

          <!-- Контейнер превью -->
          <div id="previewContainer" class="hidden mt-4 pt-4 border-t border-slate-100">
            <div class="flex items-center justify-between mb-2">
              <span id="fileName" class="text-xs font-semibold text-slate-600 truncate max-w-[200px]">чек.jpg</span>
              <button id="removeFileBtn" class="text-xs font-semibold text-rose-500 hover:text-rose-600 transition">Очистить</button>
            </div>
            <div class="rounded-xl overflow-hidden border border-slate-200 bg-slate-50 max-h-56 flex items-center justify-center p-2">
              <img id="imagePreview" src="#" alt="Чек" class="max-h-52 w-auto object-contain rounded-lg">
            </div>
          </div>

          <!-- Кнопка запуска анализа -->
          <button id="analyzeBtn" disabled class="w-full mt-4 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-100 disabled:text-slate-400 disabled:border-slate-200 disabled:cursor-not-allowed text-white font-semibold py-3 px-4 rounded-xl text-sm transition-all duration-200 shadow-sm shadow-emerald-600/20 flex items-center justify-center gap-2">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
            <span>Распознать документ</span>
          </button>

          <!-- Лоадер -->
          <div id="loader" class="hidden mt-4 p-4 rounded-xl bg-slate-50 border border-slate-200 text-center">
            <div class="inline-block animate-spin rounded-full h-5 w-5 border-2 border-emerald-600 border-t-transparent"></div>
            <p class="text-xs font-semibold text-slate-700 mt-2">Обработка изображения в нейросети...</p>
            <p class="text-[11px] text-slate-400 mt-0.5 font-mono">Извлечение строк и расчёт итогов</p>
          </div>

        </div>

      </div>

      <!-- Правая колонка: Результаты -->
      <div class="lg:col-span-7">
        
        <div class="bg-white border border-slate-200/90 rounded-2xl p-6 shadow-sm shadow-slate-100 min-h-[460px] flex flex-col justify-between">
          
          <div>
            <!-- Заголовок результатов -->
            <div class="flex items-center justify-between pb-4 mb-5 border-b border-slate-100">
              <div class="flex items-center gap-2.5">
                <h2 class="text-xs font-bold text-slate-400 uppercase tracking-wider">Результат анализа</h2>
                <span id="itemsBadge" class="hidden text-xs font-mono font-semibold px-2 py-0.5 rounded-md bg-slate-100 border border-slate-200 text-slate-700">
                  0 поз.
                </span>
              </div>
              <div id="headerTotal" class="hidden text-right">
                <span class="text-xs text-slate-400 font-mono">Сумма:</span>
                <span id="headerTotalVal" class="text-sm font-bold text-emerald-600 font-mono ml-1">0.00 ₸</span>
              </div>
            </div>

            <!-- Плейсхолдер пустого состояния -->
            <div id="emptyPlaceholder" class="text-center py-20">
              <div class="w-12 h-12 rounded-2xl bg-slate-50 border border-slate-200 flex items-center justify-center mx-auto mb-3 text-slate-400">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
              </div>
              <p class="text-sm font-semibold text-slate-700">Данные чека пока не сформированы</p>
              <p class="text-xs text-slate-400 mt-1">Выберите файл слева или используйте быстрый тест</p>
            </div>

            <!-- Блок с распознанными данными -->
            <div id="resultContent" class="hidden space-y-5">
              
              <!-- Метаданные (Магазин, Дата, Время) -->
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                <div class="bg-slate-50 border border-slate-200/80 rounded-xl p-3.5">
                  <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Организация</p>
                  <p id="resStore" class="text-sm font-bold text-slate-900 truncate mt-0.5">--</p>
                </div>
                <div class="bg-slate-50 border border-slate-200/80 rounded-xl p-3.5">
                  <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Дата покупки</p>
                  <p id="resDate" class="text-sm font-bold font-mono text-slate-900 mt-0.5">--</p>
                </div>
                <div class="bg-slate-50 border border-slate-200/80 rounded-xl p-3.5 col-span-2 sm:col-span-1">
                  <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Время</p>
                  <p id="resTime" class="text-sm font-bold font-mono text-slate-900 mt-0.5">--</p>
                </div>
              </div>

              <!-- Таблица позиций -->
              <div class="border border-slate-200 rounded-xl overflow-hidden bg-white shadow-sm">
                <div class="max-h-64 overflow-y-auto">
                  <table class="w-full text-left text-xs">
                    <thead class="bg-slate-50 text-slate-500 uppercase text-[10px] font-semibold tracking-wider border-b border-slate-200 sticky top-0">
                      <tr>
                        <th class="py-2.5 px-3.5">Наименование</th>
                        <th class="py-2.5 px-2 text-center">Кол-во</th>
                        <th class="py-2.5 px-3 text-right">Цена</th>
                        <th class="py-2.5 px-3.5 text-right">Сумма</th>
                      </tr>
                    </thead>
                    <tbody id="itemsTableBody" class="divide-y divide-slate-100 text-slate-700">
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- Итоговая плашка -->
              <div class="p-4 rounded-xl bg-slate-900 text-white flex items-center justify-between shadow-sm">
                <div>
                  <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Итого к оплате</p>
                  <p id="resTotal" class="text-2xl font-black font-mono text-white mt-0.5">0.00 ₸</p>
                </div>
                <div class="text-right">
                  <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold font-mono bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                    Успешно
                  </span>
                </div>
              </div>

              <!-- Сырой текст OCR (Аккордеон) -->
              <details class="text-xs text-slate-600 bg-slate-50 p-3.5 rounded-xl border border-slate-200 group">
                <summary class="cursor-pointer font-semibold text-slate-700 hover:text-emerald-600 transition flex items-center justify-between">
                  <span class="flex items-center gap-2">
                    <svg class="w-3.5 h-3.5 text-slate-400 group-hover:text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/>
                    </svg>
                    Распознанный текст OCR (<span id="rawLinesCount">0</span> строк)
                  </span>
                  <span class="text-[10px] text-slate-400 font-mono">показать</span>
                </summary>
                <pre id="rawLinesText" class="mt-3 p-3 bg-white rounded-lg border border-slate-200 overflow-x-auto max-h-48 font-mono text-[11px] text-slate-600 leading-relaxed whitespace-pre-wrap"></pre>
              </details>

            </div>
          </div>

          <!-- Нижняя панель действий -->
          <div id="actionsPanel" class="hidden pt-4 mt-5 border-t border-slate-100 flex items-center gap-3">
            <button id="downloadJsonBtn" class="flex-1 py-2 px-3 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200 text-slate-700 text-xs font-bold transition flex items-center justify-center gap-2 shadow-sm">
              <svg class="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
              <span>Скачать JSON</span>
            </button>
            <button id="copyJsonBtn" class="py-2 px-3 rounded-xl bg-slate-50 hover:bg-slate-100 border border-slate-200 text-slate-700 text-xs font-bold transition flex items-center justify-center gap-2 shadow-sm">
              <svg class="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10a2 2 0 01-2-2v-4a2 2 0 012-2h4l4 4z"/>
              </svg>
              <span id="copyBtnText">Копировать</span>
            </button>
          </div>

        </div>

      </div>

    </div>
  </div>

  <script>
    const fileInput = document.getElementById('fileInput');
    const dropzone = document.getElementById('dropzone');
    const previewContainer = document.getElementById('previewContainer');
    const imagePreview = document.getElementById('imagePreview');
    const fileName = document.getElementById('fileName');
    const removeFileBtn = document.getElementById('removeFileBtn');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const testBtn = document.getElementById('testBtn');
    const loader = document.getElementById('loader');
    const emptyPlaceholder = document.getElementById('emptyPlaceholder');
    const resultContent = document.getElementById('resultContent');
    const actionsPanel = document.getElementById('actionsPanel');
    const itemsTableBody = document.getElementById('itemsTableBody');
    const resStore = document.getElementById('resStore');
    const resDate = document.getElementById('resDate');
    const resTime = document.getElementById('resTime');
    const resTotal = document.getElementById('resTotal');
    const headerTotal = document.getElementById('headerTotal');
    const headerTotalVal = document.getElementById('headerTotalVal');
    const itemsBadge = document.getElementById('itemsBadge');
    const downloadJsonBtn = document.getElementById('downloadJsonBtn');
    const copyJsonBtn = document.getElementById('copyJsonBtn');
    const copyBtnText = document.getElementById('copyBtnText');

    let currentFile = null;
    let lastResult = null;

    // Быстрый тест на images.jpeg
    testBtn.addEventListener('click', async () => {
      setLoading(true);
      try {
        const response = await fetch('/api/analyze-test', { method: 'POST' });
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail || 'Ошибка сервера при тесте');
        }
        const data = await response.json();
        lastResult = data;
        renderResult(data);
      } catch (err) {
        alert('Ошибка при тесте: ' + err.message);
        emptyPlaceholder.classList.remove('hidden');
      } finally {
        setLoading(false);
      }
    });

    // Обработка выбора файла
    fileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files[0]) {
        processFile(e.target.files[0]);
      }
    });

    dropzone.addEventListener('click', () => fileInput.click());

    // Drag and Drop
    ['dragenter', 'dragover'].forEach(name => {
      dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        dropzone.classList.add('border-emerald-500', 'bg-emerald-50/30');
      });
    });

    ['dragleave', 'drop'].forEach(name => {
      dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        dropzone.classList.remove('border-emerald-500', 'bg-emerald-50/30');
      });
    });

    dropzone.addEventListener('drop', (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        processFile(e.dataTransfer.files[0]);
      }
    });

    function processFile(file) {
      currentFile = file;
      fileName.textContent = file.name;
      const reader = new FileReader();
      reader.onload = (e) => {
        imagePreview.src = e.target.result;
        previewContainer.classList.remove('hidden');
        analyzeBtn.disabled = false;
      };
      reader.readAsDataURL(file);
    }

    removeFileBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      currentFile = null;
      fileInput.value = '';
      imagePreview.src = '#';
      previewContainer.classList.add('hidden');
      analyzeBtn.disabled = true;
    });

    function setLoading(isLoading) {
      if (isLoading) {
        loader.classList.remove('hidden');
        emptyPlaceholder.classList.add('hidden');
        resultContent.classList.add('hidden');
        actionsPanel.classList.add('hidden');
        headerTotal.classList.add('hidden');
        analyzeBtn.disabled = true;
        testBtn.disabled = true;
      } else {
        loader.classList.add('hidden');
        analyzeBtn.disabled = !currentFile;
        testBtn.disabled = false;
      }
    }

    // Отправка файла на бэкенд
    analyzeBtn.addEventListener('click', async () => {
      if (!currentFile) return;

      setLoading(true);
      const formData = new FormData();
      formData.append('file', currentFile);

      try {
        const response = await fetch('/api/analyze', {
          method: 'POST',
          body: formData
        });

        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          throw new Error(errData.detail || `Ошибка сервера (${response.status})`);
        }

        const data = await response.json();
        lastResult = data;
        renderResult(data);
      } catch (err) {
        alert('Не удалось обработать чек: ' + err.message);
        emptyPlaceholder.classList.remove('hidden');
      } finally {
        setLoading(false);
      }
    });

    function renderResult(data) {
      resStore.textContent = data.store || 'Не определен';
      resDate.textContent = data.date || '--';
      resTime.textContent = data.time || '--';

      const totalStr = (data.total != null ? Number(data.total).toFixed(2) : '0.00') + ' ₸';
      resTotal.textContent = totalStr;
      headerTotalVal.textContent = totalStr;
      headerTotal.classList.remove('hidden');
      
      const count = data.items ? data.items.length : 0;
      itemsBadge.textContent = `${count} поз.`;
      itemsBadge.classList.remove('hidden');

      itemsTableBody.innerHTML = '';
      if (data.items && data.items.length) {
        data.items.forEach(item => {
          const tr = document.createElement('tr');
          tr.className = 'hover:bg-slate-50 transition';
          const p = item.price_per_unit != null ? Number(item.price_per_unit).toFixed(2) : '--';
          const t = item.total_price != null ? Number(item.total_price).toFixed(2) : '--';
          tr.innerHTML = `
            <td class="py-2.5 px-3.5 font-semibold text-slate-800">${item.name}</td>
            <td class="py-2.5 px-2 text-center font-mono text-slate-500">${item.quantity}</td>
            <td class="py-2.5 px-3 text-right font-mono text-slate-500">${p}</td>
            <td class="py-2.5 px-3.5 text-right font-mono font-bold text-slate-900">${t} ₸</td>
          `;
          itemsTableBody.appendChild(tr);
        });
      } else {
        itemsTableBody.innerHTML = '<tr><td colspan="4" class="text-center py-6 text-slate-400 font-mono">Товары не найдены</td></tr>';
      }

      resultContent.classList.remove('hidden');
      actionsPanel.classList.remove('hidden');

      const rawLines = data.raw_lines || [];
      document.getElementById('rawLinesCount').textContent = rawLines.length;
      document.getElementById('rawLinesText').textContent = rawLines.length 
        ? rawLines.map((l, i) => `${String(i + 1).padStart(2, '0')}: ${l}`).join('\n')
        : 'Строки текста не обнаружены';
    }

    downloadJsonBtn.addEventListener('click', () => {
      if (!lastResult) return;
      const blob = new Blob([JSON.stringify(lastResult, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'receipt_data.json';
      a.click();
    });

    copyJsonBtn.addEventListener('click', async () => {
      if (!lastResult) return;
      try {
        await navigator.clipboard.writeText(JSON.stringify(lastResult, null, 2));
        copyBtnText.textContent = 'Скопировано!';
        setTimeout(() => copyBtnText.textContent = 'Копировать', 2000);
      } catch (e) {
        alert('Не удалось скопировать в буфер');
      }
    });
  </script>
</body>
</html>
    """


@app.post("/api/analyze-test")
async def analyze_test_receipt():
    """Эндпоинт для мгновенного тестирования на локальном файле images.jpeg."""
    test_path = "images.jpeg"
    if not os.path.exists(test_path):
        raise HTTPException(status_code=404, detail="Файл images.jpeg не найден в папке проекта")
    try:
        from main import analyze_receipt
        ocr = get_ocr()
        data = analyze_receipt(test_path, ocr=ocr)
        return data
    except Exception as e:
        traceback.print_exc()
        print(f"\n[CheckAI Ошибка теста]: {e}\n", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze")
async def analyze_receipt_endpoint(file: UploadFile = File(...)):
    """API-эндпоинт: принимает загруженный файл изображения чека и возвращает JSON."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Загружаемый файл должен быть изображением")

    try:
        from main import analyze_receipt
        content = await file.read()
        ocr = get_ocr()
        data = analyze_receipt(content, ocr=ocr)
        return data
    except Exception as e:
        traceback.print_exc()
        print(f"\n[CheckAI Ошибка распознавания]: {e}\n", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("Запуск CheckAI Web Server на http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
