# Оценка качества модуля ИИ-рекомендаций (Advisor, i18n)

- **Дата запуска:** 2026-11-15 22:35:15
- **Проверенные языки:** ru, kk, en

## Сводная таблица по языкам (12 синтетических сценариев)

| Язык | Провайдер | Пройдено | Без выдуманных чисел | Корректный язык | Формат OK |
|---|---|---|---|---|---|
| `ru` | `rules` | 12/12 (100.0%) | 12/12 | 12/12 | 12/12 |
| `kk` | `rules` | 12/12 (100.0%) | 12/12 | 12/12 | 12/12 |
| `en` | `rules` | 12/12 (100.0%) | 12/12 | 12/12 | 12/12 |

## Поведение локальной модели Ollama (`qwen2.5:3b`)

Локальная модель `ollama` (`qwen2.5:3b`) проверена на сценарии `case_03_over_budget_total` (`language='kk'`): ответ модели не прошёл строгую проверку казахского языка/чисел, поэтому система штатно выполнила fallback на казахские шаблоны `rules` (пример заголовка: «Жалпы бюджет: лимит 10 000 ₸ асып кетті»).

## Примеры советов для вычитки (`kk` и `en`)

### Примеры на языке `kk`

1. **[case_02_over_budget_single] Дәмхана: лимит 10 000 ₸ асып кетті** (`rules`)
   - 25 000 ₸ лимит кезінде 35 000 ₸ жұмсалды.
2. **[case_02_over_budget_single] Дәмхана: әдеттегіден 67% көп** (`rules`)
   - Осы айда — 35 000 ₸, әдетте айына шамамен 21 000 ₸.
3. **[case_02_over_budget_single] Азық-түлік: лимит 19.11 таусылуы мүмкін** (`rules`)
   - 80 000 ₸ лимиттің 65 000 ₸ жұмсалды. Осы қарқынмен ай соңына дейін жетпейді.

### Примеры на языке `en`

1. **[case_02_over_budget_single] Cafe: over limit by 10,000 ₸** (`rules`)
   - Spent 35,000 ₸ out of 25,000 ₸ limit.
2. **[case_02_over_budget_single] Cafe: 67% higher than usual** (`rules`)
   - This month is 35,000 ₸, usually around 21,000 ₸ per month.
3. **[case_02_over_budget_single] Groceries: limit may run out on 19.11** (`rules`)
   - Already spent 65,000 ₸ of 80,000 ₸. At this pace the limit will run out before month end.

## Детали по сценариям

| Язык | Сценарий | Описание | Модель | Факты | Без выдуманных чисел | Язык | Статус |
|---|---|---|---|---|---|---|---|
| `ru` | `case_01_beginner` | Новичок: всего 3 чека за месяц, без лимитов и истории | `rules` | нет | Да | Да | ✅ PASS |
| `ru` | `case_02_over_budget_single` | Перерасход по категории Кафе: 35 000 из 25 000 ₸ | `rules` | category_growth, budget_forecast, over_budget | Да | Да | ✅ PASS |
| `ru` | `case_03_over_budget_total` | Превышен общий лимит на месяц: 310 000 при лимите 300 000 ₸ | `rules` | budget_forecast, budget_suggestion, over_budget, category_growth | Да | Да | ✅ PASS |
| `ru` | `case_04_budget_forecast` | Высокий темп трат: лимит 100 000 ₸ на Продукты закончится к 20 числу | `rules` | budget_forecast | Да | Да | ✅ PASS |
| `ru` | `case_05_category_growth` | Резкий рост трат на Развлечения: 45 000 ₸ vs обычно 15 000 ₸ | `rules` | category_growth, budget_suggestion | Да | Да | ✅ PASS |
| `ru` | `case_06_frequent_small` | Частые мелкие покупки: 14 раз кофе в Coffee Boom на 21 000 ₸ | `rules` | category_growth, budget_suggestion, frequent_small | Да | Да | ✅ PASS |
| `ru` | `case_07_recurring_subscription` | Регулярная ежемесячная подписка на сервис | `rules` | budget_suggestion, recurring | Да | Да | ✅ PASS |
| `ru` | `case_08_budget_suggestion` | Стабильные траты на Транспорт без лимита: предложить 25 000 ₸ | `rules` | budget_suggestion | Да | Да | ✅ PASS |
| `ru` | `case_09_uncategorized_high` | Высокая доля неразобранных трат: 25% без категории | `rules` | budget_forecast, uncategorized | Да | Да | ✅ PASS |
| `ru` | `case_10_unconverted_currency` | Чеки в иностранных валютах без курса: 3 чека в USD и EUR | `rules` | budget_forecast, unconverted | Да | Да | ✅ PASS |
| `ru` | `case_11_balanced_saver` | Сбалансированный бюджет: все траты строго в рамках лимитов | `rules` | нет | Да | Да | ✅ PASS |
| `ru` | `case_12_multi_currency_stable` | Пользователь с валютой RUB и стабильным транспортом | `rules` | budget_suggestion | Да | Да | ✅ PASS |
| `kk` | `case_01_beginner` | Новичок: всего 3 чека за месяц, без лимитов и истории | `rules` | нет | Да | Да | ✅ PASS |
| `kk` | `case_02_over_budget_single` | Перерасход по категории Кафе: 35 000 из 25 000 ₸ | `rules` | category_growth, budget_forecast, over_budget | Да | Да | ✅ PASS |
| `kk` | `case_03_over_budget_total` | Превышен общий лимит на месяц: 310 000 при лимите 300 000 ₸ | `rules` | budget_forecast, budget_suggestion, over_budget, category_growth | Да | Да | ✅ PASS |
| `kk` | `case_04_budget_forecast` | Высокий темп трат: лимит 100 000 ₸ на Продукты закончится к 20 числу | `rules` | budget_forecast | Да | Да | ✅ PASS |
| `kk` | `case_05_category_growth` | Резкий рост трат на Развлечения: 45 000 ₸ vs обычно 15 000 ₸ | `rules` | category_growth, budget_suggestion | Да | Да | ✅ PASS |
| `kk` | `case_06_frequent_small` | Частые мелкие покупки: 14 раз кофе в Coffee Boom на 21 000 ₸ | `rules` | category_growth, budget_suggestion, frequent_small | Да | Да | ✅ PASS |
| `kk` | `case_07_recurring_subscription` | Регулярная ежемесячная подписка на сервис | `rules` | budget_suggestion, recurring | Да | Да | ✅ PASS |
| `kk` | `case_08_budget_suggestion` | Стабильные траты на Транспорт без лимита: предложить 25 000 ₸ | `rules` | budget_suggestion | Да | Да | ✅ PASS |
| `kk` | `case_09_uncategorized_high` | Высокая доля неразобранных трат: 25% без категории | `rules` | budget_forecast, uncategorized | Да | Да | ✅ PASS |
| `kk` | `case_10_unconverted_currency` | Чеки в иностранных валютах без курса: 3 чека в USD и EUR | `rules` | budget_forecast, unconverted | Да | Да | ✅ PASS |
| `kk` | `case_11_balanced_saver` | Сбалансированный бюджет: все траты строго в рамках лимитов | `rules` | нет | Да | Да | ✅ PASS |
| `kk` | `case_12_multi_currency_stable` | Пользователь с валютой RUB и стабильным транспортом | `rules` | budget_suggestion | Да | Да | ✅ PASS |
| `en` | `case_01_beginner` | Новичок: всего 3 чека за месяц, без лимитов и истории | `rules` | нет | Да | Да | ✅ PASS |
| `en` | `case_02_over_budget_single` | Перерасход по категории Кафе: 35 000 из 25 000 ₸ | `rules` | category_growth, budget_forecast, over_budget | Да | Да | ✅ PASS |
| `en` | `case_03_over_budget_total` | Превышен общий лимит на месяц: 310 000 при лимите 300 000 ₸ | `rules` | budget_forecast, budget_suggestion, over_budget, category_growth | Да | Да | ✅ PASS |
| `en` | `case_04_budget_forecast` | Высокий темп трат: лимит 100 000 ₸ на Продукты закончится к 20 числу | `rules` | budget_forecast | Да | Да | ✅ PASS |
| `en` | `case_05_category_growth` | Резкий рост трат на Развлечения: 45 000 ₸ vs обычно 15 000 ₸ | `rules` | category_growth, budget_suggestion | Да | Да | ✅ PASS |
| `en` | `case_06_frequent_small` | Частые мелкие покупки: 14 раз кофе в Coffee Boom на 21 000 ₸ | `rules` | category_growth, budget_suggestion, frequent_small | Да | Да | ✅ PASS |
| `en` | `case_07_recurring_subscription` | Регулярная ежемесячная подписка на сервис | `rules` | budget_suggestion, recurring | Да | Да | ✅ PASS |
| `en` | `case_08_budget_suggestion` | Стабильные траты на Транспорт без лимита: предложить 25 000 ₸ | `rules` | budget_suggestion | Да | Да | ✅ PASS |
| `en` | `case_09_uncategorized_high` | Высокая доля неразобранных трат: 25% без категории | `rules` | budget_forecast, uncategorized | Да | Да | ✅ PASS |
| `en` | `case_10_unconverted_currency` | Чеки в иностранных валютах без курса: 3 чека в USD и EUR | `rules` | budget_forecast, unconverted | Да | Да | ✅ PASS |
| `en` | `case_11_balanced_saver` | Сбалансированный бюджет: все траты строго в рамках лимитов | `rules` | нет | Да | Да | ✅ PASS |
| `en` | `case_12_multi_currency_stable` | Пользователь с валютой RUB и стабильным транспортом | `rules` | budget_suggestion | Да | Да | ✅ PASS |

## Выводы

1. Все 12 сценариев успешно проходят на `ru`, `kk` и `en` с сохранением языконезависимых полей (`id`, `type`, `category_id`, `action.kind`).
2. Проверка чисел (`_extract_numbers_from_text`) корректно распознаёт оба числовых формата (`12 500 ₸` и `12,500 ₸`, с копейками и без).
3. Фильтр языка (`_looks_like_language`) отсекает ответы LLM на неверном языке и безопасно переключает ответ на шаблоны целевого языка.
