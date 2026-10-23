# CheckAI — руководство бэкенд-разработчика

Как устроен сервер, как его запустить, где что лежит и по каким правилам писать код.
Короткая шпаргалка по запуску для всех ролей — в [TEAM_GUIDE.md](TEAM_GUIDE.md), контракт API для фронта — в [README.md](README.md#спецификация-api), задачи — в [ROADMAP.md](ROADMAP.md).

---

## Содержание

1. [Быстрый старт](#1-быстрый-старт)
2. [Переменные окружения](#2-переменные-окружения)
3. [Осторожно: что сервер делает с данными сам](#3-осторожно-что-сервер-делает-с-данными-сам)
4. [Архитектура](#4-архитектура)
5. [Карта файлов](#5-карта-файлов)
6. [Модель данных](#6-модель-данных)
7. [Авторизация](#7-авторизация)
8. [Распознавание чеков](#8-распознавание-чеков)
9. [Список эндпоинтов](#9-список-эндпоинтов)
10. [Правила и соглашения](#10-правила-и-соглашения)
11. [Как добавить эндпоинт](#11-как-добавить-эндпоинт)
12. [Миграции базы данных](#12-миграции-базы-данных)
13. [Тесты](#13-тесты)
14. [PostgreSQL и Docker](#14-postgresql-и-docker)
15. [Что ждёт фронтенд](#15-что-ждёт-фронтенд)
16. [Известные проблемы](#16-известные-проблемы)
17. [Git и порядок работы](#17-git-и-порядок-работы)

---

## 1. Быстрый старт

Нужен **Python 3.11 или новее** (Docker-образ собран на 3.12; закреплённый `numpy 2.3` на 3.10 не ставится). Ollama для бэкенда не нужна.

```powershell
# 1. Окружение и зависимости
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# 2. Файл .env — без SECRET_KEY сервер не запустится
copy .env.example .env                # Linux/macOS: cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
# вставьте полученную строку в .env: SECRET_KEY=...

# 3. Запуск с автоперезагрузкой
python -m uvicorn server:app --reload
```

- Интерфейс: http://localhost:8000
- Swagger (все эндпоинты, можно вызывать прямо из браузера): http://localhost:8000/docs
- База: `data/checkai.db` (SQLite). Миграции применяются сами при старте.

Что учесть:

- Старт занимает несколько секунд: `server.py` импортирует PaddlePaddle.
- **Первое распознавание** чека долгое: PaddleOCR загружает модель, а при самом первом запуске ещё и скачивает её в `~/.paddlex`. Последующие — быстрее.
- Без Ollama чеки распознаются встроенным парсером на регулярных выражениях (`app/parser.py`). Чтобы не ждать попытки подключиться к Ollama, поставьте `USE_AI_PARSER=0`.

Через Docker то же самое — `docker compose up` (нужен тот же `.env` рядом с `docker-compose.yml`). Папки `app/`, `migrations/`, `frontend/` и `server.py` смонтированы в контейнер, но `--reload` в контейнере не включён: после правок бэкенда — `docker compose restart web`.

---

## 2. Переменные окружения

Читаются в [app/config.py](app/config.py) из окружения и из `.env`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `SECRET_KEY` | — (обязательна) | Ключ подписи JWT. Смена ключа разлогинивает всех. Короче 32 символов — предупреждение в логе |
| `DATABASE_URL` | `sqlite:///data/checkai.db` | Строка подключения SQLAlchemy. Для Postgres: `postgresql://user:pass@host:5432/db` |
| `UPLOADS_DIR` | `data/receipts` | Папка с фото чеков. Если меняете базу — меняйте и её (см. раздел 3) |
| `MAX_UPLOAD_MB` | `10` | Максимальный размер фото. На фронте то же число зашито в `app.js` (`MAX_UPLOAD_MB`) — меняйте в двух местах |
| `ORPHAN_CLEANUP_HOURS` | `6` | Период очистки несохранённых фото. `0` — не чистить |
| `TRUST_PROXY` | `0` | `1` — брать IP клиента из `X-Forwarded-For`. Только за своим nginx, иначе лимит запросов обходится подделкой заголовка |
| `USE_AI_PARSER` | `auto` | `auto` — пробовать Qwen через Ollama, при ошибке встроенный парсер. `0` — только встроенный |
| `OLLAMA_HOST` | `http://localhost:11434` (библиотека `ollama`) | Адрес Ollama. В Docker — `http://ollama:11434` |

---

## 3. Осторожно: что сервер делает с данными сам

**Очистка фото.** При старте и затем раз в `ORPHAN_CLEANUP_HOURS` часов сервер удаляет из `UPLOADS_DIR` все изображения старше суток, на которые не ссылается ни один чек **в текущей базе** (`cleanup_orphan_images` в [server.py](server.py)). Если подключить другую или пустую базу к той же папке — фото чеков из вашей основной базы будут удалены. Поэтому:

> Экспериментируете с другой базой — всегда задавайте вместе с `DATABASE_URL` и свой `UPLOADS_DIR`.

```powershell
$env:DATABASE_URL = "sqlite:///data/sandbox.db"
$env:UPLOADS_DIR  = "data/sandbox_receipts"
python -m uvicorn server:app --reload
```

Всё внутри `data/` игнорируется git, кроме `.gitkeep`.

**Миграции.** При импорте `server.py` вызывается `database.init_db()`, который доводит базу до последней миграции. Это происходит и при запуске тестов, и при запуске `uvicorn`. Старую базу без истории Alembic он сначала «усыновляет» (помечает ревизией `0001_baseline`).

---

## 4. Архитектура

```
Браузер (frontend/, без сборки)
   │  fetch /api/*  + Authorization: Bearer <access>
   ▼
server.py — FastAPI
   ├─ middleware: лимит запросов, отсечение больших загрузок, nosniff
   ├─ обработчик 422: убирает «Value error,» из текстов ошибок
   ├─ эндпоинты (def, выполняются в пуле потоков)
   │     │
   │     ├─ app/auth.py      JWT, пароли, текущий пользователь
   │     ├─ app/schemas.py   Pydantic: проверка входа и формат ответа
   │     ├─ app/crud.py      все запросы к БД
   │     │     └─ app/models.py → app/database.py → SQLite / PostgreSQL
   │     └─ app/ocr.py       распознавание (под общей блокировкой)
   │           ├─ PaddleOCR → строки текста
   │           ├─ ml/app/agent_parser.py   Qwen через Ollama (если доступна)
   │           └─ app/parser.py            запасной парсер на regex
   ├─ /data/receipts/*  — статика: только фото чеков
   └─ /static/*         — CSS и JS интерфейса
```

Главные решения:

- **Эндпоинты объявлены через `def`, а не `async def`.** FastAPI выполняет их в пуле потоков, поэтому долгий OCR не блокирует остальные запросы. Не делайте эндпоинты `async`, если внутри синхронная работа с БД или OCR.
- **Одна модель OCR на процесс.** `get_ocr()` создаёт её лениво при первом распознавании. PaddleOCR не потокобезопасен, поэтому распознавание идёт под `_ocr_run_lock` — строго по одному.
- **Бизнес-логика запросов к БД — только в `crud.py`.** Эндпоинт проверяет права и вызывает функцию из `crud`.
- **Фронт получает данные только через API**, к базе не обращается.

---

## 5. Карта файлов

| Файл | Что внутри |
|---|---|
| [server.py](server.py) | Приложение FastAPI: middleware, все маршруты, сохранение загрузок, проверка пути к фото, очистка фото, блокировка OCR |
| [app/config.py](app/config.py) | Настройки из окружения (раздел 2) |
| [app/database.py](app/database.py) | Engine и сессии SQLAlchemy, `get_db()`, `init_db()` с Alembic. Для SQLite включает внешние ключи и подменяет `lower()` на версию, понимающую кириллицу (иначе `ilike` не найдёт «магнум» в «Магнум») |
| [app/models.py](app/models.py) | Таблицы: `User`, `Receipt`, `ReceiptItem`, `Budget` |
| [app/schemas.py](app/schemas.py) | Pydantic-схемы запросов и ответов, проверка email, пароля, лимитов, нормализация дат |
| [app/crud.py](app/crud.py) | Пользователи, чеки (фильтры, поиск, пагинация), аналитика, бюджеты |
| [app/auth.py](app/auth.py) | bcrypt, выпуск и проверка JWT, зависимость `get_current_user` |
| [app/rate_limit.py](app/rate_limit.py) | Ограничитель частоты запросов в памяти процесса, определение IP |
| [app/dates.py](app/dates.py) | Разбор дат вида `29.07.2025 16:36`, `13.05.19`, `2025-07-29` → `YYYY-MM-DD` и `HH:MM` |
| [app/ocr.py](app/ocr.py) | PaddleOCR + выбор парсера (Qwen или regex) |
| [app/parser.py](app/parser.py) | Запасной парсер чека на регулярных выражениях |
| [app/tasks.py](app/tasks.py) | Очередь фонового распознавания для `/api/analyze/async` (в памяти процесса) |
| [ml/app/agent_parser.py](ml/app/agent_parser.py) | Промпт и вызов Qwen, очистка служебных строк, самокоррекция суммы. Зона ML-разработчика |
| [migrations/](migrations/) | Миграции Alembic |
| [tests/](tests/) | Автотесты API |

Папка `ml/` целиком — зона ML-разработчика. Бэкенд использует из неё только `ml/app/agent_parser.py` (через `app/ocr.py`). `ml/server.py`, `ml/app/ocr.py` и `ml/app/parser.py` — отдельные копии для экспериментов ML, сервер их не использует.

---

## 6. Модель данных

```
users ──< receipts ──< receipt_items
  │
  └──< budgets
```

| Таблица | Поля | Заметки |
|---|---|---|
| `users` | `id`, `email` (уникальный, в нижнем регистре), `hashed_password`, `full_name`, `created_at` | Удаление пользователя каскадно удаляет его чеки и бюджеты |
| `receipts` | `id`, `user_id`, `store`, `date`, `time`, `category`, `total`, `image_path`, `created_at` | `category` — категория чека: та, на которую в нём потрачено больше всего (её считает фронт) |
| `receipt_items` | `id`, `receipt_id`, `name`, `quantity`, `price_per_unit`, `total_price`, `category` | При редактировании чека позиции заменяются целиком |
| `budgets` | `id`, `user_id`, `category`, `limit`, `created_at`, `updated_at` | `category = NULL` — общий лимит на месяц. Лимиты повторяются каждый месяц |

Форматы, которые нельзя нарушать:

- **`receipts.date`** — строка `YYYY-MM-DD` или `NULL`. **`receipts.time`** — `HH:MM` или `NULL`. Строки в таком виде правильно сортируются и сравниваются прямо в SQL (фильтры `from`/`to`, месяц в бюджетах через `substr(date, 1, 7)`). Любую входящую дату прогоняйте через `app.dates.normalize_date_time` — схемы `ReceiptCreate`/`ReceiptUpdate` уже делают это.
- **`created_at`** — UTC без часового пояса (`models.utcnow()`). Фронт сам добавляет `Z` при показе.
- **`image_path`** — URL вида `/data/receipts/<32 hex>.<ext>`. Файл на диске называется `<32 hex>.<ext>`. Путь от клиента всегда проверяется `_resolve_image_path` — иначе можно сослаться на чужой или несуществующий файл. Старый формат `/data/receipts/<uuid>_<имя>.jpg` поддерживается только для уже сохранённых чеков.
- **Категории** — список фронта: `Продукты, Одежда, Дом и быт, Транспорт, Кафе, Здоровье, Развлечения, Другое`. Сервер список не проверяет и принимает любую строку до 100 символов.
- **Деньги** — `Float`, тенге.

---

## 7. Авторизация

Реализация — [app/auth.py](app/auth.py).

- Пароли — bcrypt. Длина пароля: от 8 символов и не больше 72 байт (bcrypt игнорирует всё дальше).
- **Access-токен** — JWT на 60 минут, передаётся в заголовке `Authorization: Bearer <token>`.
- **Refresh-токен** — JWT на 30 дней, обменивается на новую пару в `POST /api/auth/refresh`.
- В оба токена пишется `type` (`access` / `refresh` / `password_reset`) — токен одного типа нельзя использовать вместо другого.
- В токены пишется `pwd` — отпечаток хеша пароля. **После смены пароля все выданные токены перестают действовать** — так «выход со всех устройств» работает без таблицы сессий. `PATCH /api/auth/me` возвращает новую пару токенов, фронт её сохраняет.
- `get_current_user` — зависимость для защищённых эндпоинтов: `401`, если токена нет или он недействителен. `get_optional_current_user` — для эндпоинтов, доступных и гостям.
- Сервер не хранит сессии: выход — это удаление токенов на фронте.

Ошибки входа и смены данных — `400` с понятным текстом (`"Неверный email или пароль"`, `"Неверный текущий пароль"`), не `401`: на `401` фронт считает, что сессия истекла, и выкидывает пользователя.

---

## 8. Распознавание чеков

```
POST /api/analyze (фото)
  → _save_upload: проверка типа и размера, файл <uuid>.<ext> в UPLOADS_DIR
  → _run_ocr (под _ocr_run_lock):
       ocr_image: PaddleOCR → строки (уверенность ≥ 0.2)
       USE_AI_PARSER != 0 → ml.app.agent_parser.parse_receipt_with_correction (Qwen)
       ошибка или пустой ответ → app.parser.parse_receipt (regex)
  → normalize_date_time
  → ответ {success, data: {store, date, time, items[], total, raw_lines, image_path}}
```

- Распознавание доступно гостям. Сохраняет чек уже авторизованный пользователь через `POST /api/receipts/save`, передавая `image_path` из ответа распознавания.
- Нет текста на фото — `422`. Ошибка OCR — `500` с общим текстом, подробности только в логе сервера.
- Если фото распознали, но чек не сохранили, файл удалит фоновая очистка через сутки.
- `POST /api/analyze/async` кладёт задачу в очередь [app/tasks.py](app/tasks.py) и сразу отвечает `202` с `task_id`; статус — `GET /api/tasks/{task_id}`. Задачи хранятся в памяти процесса и пропадают при перезапуске. Фронт этот режим пока не использует.
- Демо: `POST /api/analyze-test` распознаёт `images.jpeg` из корня проекта.

Модель Qwen, промпт и качество распознавания — зона ML-разработчика. Бэкенду важен формат ответа: фронт ждёт поля, перечисленные выше, и сам приводит числа (`quantity`, `price_per_unit`, `total_price`) к числам.

---

## 9. Список эндпоинтов

🔒 — нужен access-токен. Подробные тела запросов и ответов — в Swagger (`/docs`) и в [README.md](README.md#спецификация-api).

| Метод и путь | | Что делает |
|---|---|---|
| `GET /` | | Страница интерфейса |
| `GET /api/health` | | Состояние БД, OCR и Ollama. `503`, если недоступна БД |
| `POST /api/auth/register` | | Регистрация → пара токенов и пользователь |
| `POST /api/auth/login` | | Вход → пара токенов и пользователь |
| `POST /api/auth/refresh` | | Новая пара токенов по refresh-токену |
| `GET /api/auth/me` | 🔒 | Текущий пользователь |
| `PATCH /api/auth/me` | 🔒 | Смена имени, email, пароля (для email и пароля нужен текущий пароль) → новая пара токенов |
| `DELETE /api/auth/me` | 🔒 | Удаление аккаунта с паролем, вместе с чеками, фото и бюджетами |
| `POST /api/auth/forgot-password` | | Запрос сброса пароля (см. раздел 16) |
| `POST /api/auth/reset-password` | | Новый пароль по токену сброса |
| `POST /api/analyze` | | Распознать фото (multipart, поле `file`) |
| `POST /api/analyze/async` | | То же через очередь → `task_id` |
| `GET /api/tasks/{task_id}` | | Статус задачи распознавания |
| `POST /api/analyze-test` | | Распознать демо-чек |
| `POST /api/receipts/save` | 🔒 | Сохранить чек |
| `GET /api/receipts` | 🔒 | Свои чеки: `skip`, `limit` (≤ 500), `from`, `to` (`YYYY-MM-DD`), `category`, `q` |
| `GET /api/receipts/{id}` | 🔒 | Один свой чек |
| `PUT /api/receipts/{id}` | 🔒 | Заменить данные чека, позиции целиком |
| `DELETE /api/receipts/{id}` | 🔒 | Удалить чек и его фото (если на фото больше никто не ссылается) |
| `GET /api/analytics` | 🔒 | Сводка расходов: по категориям, магазинам, месяцам |
| `GET /api/budgets` | 🔒 | Лимиты: `[{category, limit}]`, без лимитов `[]` |
| `PUT /api/budgets` | 🔒 | Заменить все лимиты: `{"budgets": [...]}` |
| `GET /api/budgets/status?month=YYYY-MM` | 🔒 | Потрачено по лимитам за месяц |

Лимиты частоты запросов (на IP, в памяти процесса, `RATE_LIMIT_RULES` в [server.py](server.py)): вход и регистрация — 10 в минуту, сброс пароля — 5 в минуту, распознавание — 15 в минуту. При превышении — `429` с заголовком `Retry-After`. При запуске нескольких воркеров uvicorn у каждого свой счётчик.

---

## 10. Правила и соглашения

**Доступ к данным**

- Каждый запрос к чекам и бюджетам фильтруется по `user_id` текущего пользователя — внутри функций `crud`, а не после выборки.
- Чужой объект для пользователя «не существует»: отвечаем **`404`, а не `403`**, чтобы нельзя было перебором узнать, какие id заняты.

**Ошибки**

- Тексты ошибок — **на русском и для пользователя**: фронт показывает `detail` как есть. Технические подробности — только в лог (`logger.exception`).
- Формат: `HTTPException(status_code=..., detail="Текст")`. Ошибки валидации Pydantic превращаются в `422` со списком в `detail`; приставка `Value error,` убирается обработчиком в `server.py`. В валидаторах пишите `raise ValueError("Понятный текст")`.
- Коды: `400` — неверные данные, которые нельзя проверить схемой (неверный пароль, занятый email); `401` — нет или истёк токен; `404` — нет объекта или он чужой; `413` — файл больше лимита; `415` — не изображение; `422` — не прошла валидация; `429` — лимит запросов.

**Код**

- Запросы к БД — в `crud.py`, эндпоинты — в `server.py`, схемы — в `schemas.py`.
- Схемы ответа указывайте в `response_model` — так в ответ не попадут лишние поля (например, `hashed_password`).
- Новые настройки — в `app/config.py` через `os.getenv` с разумным значением по умолчанию, плюс строка в `.env.example` и в `docker-compose.yml`.
- Комментарии и docstring — на русском, объясняют «почему», а не пересказывают код.
- Зависимости закрепляйте точной версией в `requirements.txt`.

---

## 11. Как добавить эндпоинт

Пример: заметка к чеку `PATCH /api/receipts/{id}/note`.

1. **Модель и миграция** — если нужно новое поле или таблица: поле в [app/models.py](app/models.py) и миграция (раздел 12).
2. **Схема** в [app/schemas.py](app/schemas.py):
   ```python
   class ReceiptNoteUpdate(BaseModel):
       note: Optional[str] = Field(default=None, max_length=1000)
   ```
3. **Функция в** [app/crud.py](app/crud.py):
   ```python
   def set_receipt_note(db: Session, receipt: models.Receipt, note: Optional[str]) -> models.Receipt:
       receipt.note = note.strip() if note else None
       db.commit()
       db.refresh(receipt)
       return receipt
   ```
4. **Маршрут в** [server.py](server.py) — в блок с соседними эндпоинтами:
   ```python
   @app.patch("/api/receipts/{receipt_id}/note", response_model=schemas.ReceiptOut, tags=["Receipts"])
   def update_receipt_note(
       receipt_id: int,
       data: schemas.ReceiptNoteUpdate,
       db: Session = Depends(database.get_db),
       current_user: models.User = Depends(auth.get_current_user),
   ):
       """Заметка к своему чеку."""
       receipt = crud.get_receipt_by_id(db, receipt_id=receipt_id, user_id=current_user.id)
       if receipt is None:
           raise HTTPException(status_code=404, detail="Чек не найден")
       return crud.set_receipt_note(db, receipt, data.note)
   ```
5. **Лимит частоты** — если эндпоинт тяжёлый или его можно использовать для перебора: строка в `RATE_LIMIT_RULES`.
6. **Тест** в `tests/` (раздел 13): успех, `401` без токена, `404` для чужого чека, `422` для неверных данных.
7. **Документация** — раздел «Спецификация API» в [README.md](README.md) и отметка в [ROADMAP.md](ROADMAP.md).
8. Если фронту нужен новый эндпоинт — договоритесь о формате до реализации и запишите его в README.

---

## 12. Миграции базы данных

Схема меняется **только миграциями Alembic** ([migrations/versions/](migrations/versions/)). `init_db()` применяет их при старте сервера, руками запускать не нужно.

Создать миграцию (из корня проекта, с активированным `.venv`):

```powershell
alembic revision --autogenerate -m "заметка к чеку" --rev-id 0004_receipt_note
```

Затем **обязательно откройте файл и проверьте его**: autogenerate часто ошибается с индексами и типами. Правила, которым следуют существующие миграции:

- `revision` — порядковый номер и короткое имя: `0004_receipt_note`. `down_revision` — предыдущая миграция.
- Миграция должна переживать повторный запуск на базе, где изменение уже есть: перед `add_column`/`create_table`/`create_index` проверяйте через `sa.inspect(bind)` (пример — [0003_budgets.py](migrations/versions/0003_budgets.py)).
- SQLite почти не умеет `ALTER TABLE`: для изменения и удаления колонок используйте `with op.batch_alter_table(...)` (batch-режим для SQLite уже включён в `migrations/env.py`).
- Если меняется формат существующих данных — переводите и старые строки в той же миграции (пример — даты в [0002_item_category_iso_dates.py](migrations/versions/0002_item_category_iso_dates.py)).
- Пишите `downgrade()`.

Полезные команды:

```powershell
alembic current           # текущая ревизия базы
alembic history           # список миграций
alembic upgrade head      # применить все
alembic downgrade -1      # откатить последнюю
```

Перед PR проверьте миграцию на **копии старой базы**: скопируйте свой `data/checkai.db` в `data/migrate_test.db` и запустите сервер с `DATABASE_URL=sqlite:///data/migrate_test.db` и отдельным `UPLOADS_DIR` (раздел 3).

---

## 13. Тесты

Тесты — `unittest` + `fastapi.testclient.TestClient`, лежат в [tests/](tests/). Пакет `pytest` не обязателен, но запускает их так же.

**Всегда запускайте тесты на отдельной базе.** Текущие тесты не переопределяют `DATABASE_URL` и иначе пишут в рабочую `data/checkai.db`:

```powershell
$env:DATABASE_URL = "sqlite:///data/test.db"
$env:UPLOADS_DIR  = "data/test_receipts"
python -m unittest discover -s tests -v
```

```bash
# Linux/macOS
DATABASE_URL=sqlite:///data/test.db UPLOADS_DIR=data/test_receipts python -m unittest discover -s tests -v
```

Как писать тест:

- Задайте `SECRET_KEY` и другие переменные **до** `from server import app` — настройки читаются при импорте.
- `TestClient(app)` без `with` не запускает фоновую очистку фото — так и нужно в тестах.
- Пользователей создавайте прямо через `models.User` и `auth.issue_tokens(user)`, как в [tests/test_budgets.py](tests/test_budgets.py) — быстрее, чем регистрация через API.
- На каждый эндпоинт: успешный сценарий, `401` без токена, изоляция пользователей (пользователь B получает `404` на объекты A), ошибки валидации (`422`).
- OCR в тестах не запускайте — это долго. Если нужно проверить сохранение распознанного чека, отправляйте готовый JSON в `/api/receipts/save`.

---

## 14. PostgreSQL и Docker

По умолчанию — SQLite. Для PostgreSQL в `docker-compose.yml` есть сервис `db` (профили `postgres` и `full`):

```powershell
# в .env:
# DATABASE_URL=postgresql://checkai:checkai_secret@db:5432/checkai
docker compose --profile postgres up -d
```

Локально без Docker: `DATABASE_URL=postgresql://checkai:checkai_secret@localhost:5432/checkai` при поднятом `docker compose --profile postgres up db`.

Отличия, о которых стоит помнить:

- Подмена `lower()` для кириллицы делается только для SQLite. В PostgreSQL `ilike` и так работает с кириллицей.
- В PostgreSQL строже типы: строка не сравнится с числом, `NULL` в `NOT NULL` упадёт сразу.
- Данные из SQLite в PostgreSQL автоматически не переносятся.

Команды Docker:

```powershell
docker compose up -d                 # только web (SQLite)
docker compose --profile full up -d  # web + Postgres + Ollama
docker compose logs web -f           # логи сервера
docker compose restart web           # после правок бэкенда
docker compose up -d --build         # после изменения requirements.txt или Dockerfile
docker compose down                  # остановить
```

---

## 15. Что ждёт фронтенд

Фронт — [frontend/static/js/app.js](frontend/static/js/app.js). Что важно не сломать:

- **Формат ошибок.** Фронт показывает `detail` как есть, если это строка. Если `detail` — список (ошибки валидации `422`), он берёт из каждого элемента `msg` (русский текст) или строит сообщение по полю `loc`.
- **`401` = сессия истекла.** Получив `401`, фронт один раз пробует `POST /api/auth/refresh` и повторяет запрос, при неудаче — переходит в гостевой режим. Не отвечайте `401` на ошибки, не связанные с токеном.
- **`404`/`405` = «сервер этого ещё не умеет».** По этим кодам фронт включает запасное поведение (например, бюджеты в `localStorage`). Новые эндпоинты коллекций должны отвечать `200` и `[]`, а не `404`, когда данных нет.
- **История грузится целиком** порциями по 200 (`GET /api/receipts?skip=&limit=200`) — аналитику, категории и бюджеты фронт считает сам по этим данным. Не уменьшайте максимальный `limit` ниже 200.
- **Поля чека**, которые читает фронт: `id, store, date, time, category, total, image_path, created_at, items[{name, quantity, price_per_unit, total_price, category}]`. Переименование любого из них ломает интерфейс.
- Эндпоинты, которые фронт вызывает: `/api/auth/register|login|refresh|me`, `/api/analyze`, `/api/analyze-test`, `/api/receipts…`, `/api/budgets`. Остальные (`/api/analytics`, `/api/budgets/status`, `/api/analyze/async`, сброс пароля, `/api/health`) фронт пока не использует.

---

## 16. Известные проблемы

Найдены при аудите 23.10.2026. Перед правкой проверьте, не закрыта ли уже задача в [ROADMAP.md](ROADMAP.md).

| Проблема | Где | Статус |
|---|---|---|
| `forgot-password` возвращает токен сброса в ответе: зная чужой email, можно сменить пароль и войти в чужой аккаунт. Нужна отправка ссылки на почту, а токен в ответе — убрать | [server.py](server.py), `forgot_password` | Отложено. **Не выкатывайте на публичный сервер, пока не исправлено** |
| Запрос к Qwen (до 120 с, с самокоррекцией — до двух запросов) идёт под общей блокировкой OCR — пока один чек анализируется, остальные ждут. Очередь `/api/analyze/async` это не решает: один поток и та же блокировка | `_run_ocr` в [server.py](server.py), [app/tasks.py](app/tasks.py) | Открыто |
| Тесты пишут в рабочую базу, если не задать `DATABASE_URL` | [tests/test_budgets.py](tests/test_budgets.py) | Открыто, пока запускайте по разделу 13 |
| Сервер принимает отрицательные суммы и количество в чеке; `NaN` в лимите бюджета даёт `500` вместо `422` | `ItemSchema`, `ReceiptCreate`, `BudgetItem` в [app/schemas.py](app/schemas.py) | Открыто |
| Категория по умолчанию: фронт использует «Другое», сервер — «Прочее»; чек без категории сервер записывает в «Продукты» | [app/crud.py](app/crud.py) | Открыто |
| `/api/budgets/status` считает иначе, чем фронт: не учитывает чеки без даты (фронт относит их к месяцу сохранения), месяц по умолчанию берёт по UTC | `get_budget_status` в [app/crud.py](app/crud.py) | Открыто |
| `/api/analyze-test` не ограничен по частоте, хотя запускает OCR | `RATE_LIMIT_RULES` в [server.py](server.py) | Открыто |
| Задачи очереди старше часа удаляются, даже если ещё не выполнены | `cleanup_old_tasks` в [app/tasks.py](app/tasks.py) | Открыто |
| В git лежат личные фото чеков в `data/receipts/` и `images_result.json` | — | Открыто |
| В README указан Python 3.10 и сказано, что без Ollama распознавание не работает — устарело | [README.md](README.md) | Открыто |

---

## 17. Git и порядок работы

- Основная ветка — `main`. Работайте в отдельной ветке: `backend-<что делаете>` или `feat/<что делаете>`.
- Перед PR: подтянуть `main` (`git fetch origin && git merge origin/main`), прогнать тесты на тестовой базе, проверить сервер вручную через `/docs`.
- Сообщения коммитов — на русском или английском, с префиксом типа: `feat(backend): …`, `fix: …`, `docs: …`, `test: …`.
- Изменения в API — вместе с обновлением README в том же PR. Новые миграции — с проверкой на копии старой базы.
- **Не коммитьте**: `.env`, `data/*` (база, фото чеков), `*_result.json`, `.venv`. Если файл уже попал в git, `.gitignore` его не уберёт — нужен `git rm --cached <файл>`.
- Зависимости между задачами ролей — в конце [ROADMAP.md](ROADMAP.md).
