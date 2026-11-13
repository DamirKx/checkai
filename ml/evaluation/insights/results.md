# Оценка качества модуля ИИ-рекомендаций (Advisor)

- **Дата запуска:** 2026-11-13 16:34:41
- **Всего тестов:** 12
- **Пройдено успешно:** 12/12 (100.0%)

## Результаты по синтетическим сценариям

| ID сценария | Описание | Факты | Чисел нет лишних | Формат | Статус |
|---|---|---|---|---|---|
| `case_01_beginner` | Новичок: всего 3 чека за месяц, без лимитов и истории | нет | Да | Да | ✅ PASS |
| `case_02_over_budget_single` | Перерасход по категории Кафе: 35 000 из 25 000 ₸ | budget_forecast, category_growth, over_budget | Да | Да | ✅ PASS |
| `case_03_over_budget_total` | Превышен общий лимит на месяц: 310 000 при лимите 300 000 ₸ | budget_forecast, category_growth, over_budget, budget_suggestion | Да | Да | ✅ PASS |
| `case_04_budget_forecast` | Высокий темп трат: лимит 100 000 ₸ на Продукты закончится к 20 числу | budget_forecast | Да | Да | ✅ PASS |
| `case_05_category_growth` | Резкий рост трат на Развлечения: 45 000 ₸ vs обычно 15 000 ₸ | category_growth, budget_suggestion | Да | Да | ✅ PASS |
| `case_06_frequent_small` | Частые мелкие покупки: 14 раз кофе в Coffee Boom на 21 000 ₸ | category_growth, budget_suggestion, frequent_small | Да | Да | ✅ PASS |
| `case_07_recurring_subscription` | Регулярная ежемесячная подписка на сервис | recurring, budget_suggestion | Да | Да | ✅ PASS |
| `case_08_budget_suggestion` | Стабильные траты на Транспорт без лимита: предложить 25 000 ₸ | budget_suggestion | Да | Да | ✅ PASS |
| `case_09_uncategorized_high` | Высокая доля неразобранных трат: 25% без категории | budget_forecast, uncategorized | Да | Да | ✅ PASS |
| `case_10_unconverted_currency` | Чеки в иностранных валютах без курса: 3 чека в USD и EUR | budget_forecast, unconverted | Да | Да | ✅ PASS |
| `case_11_balanced_saver` | Сбалансированный бюджет: все траты строго в рамках лимитов | нет | Да | Да | ✅ PASS |
| `case_12_multi_currency_stable` | Пользователь с валютой RUB и стабильным транспортом | budget_suggestion | Да | Да | ✅ PASS |

## Выводы

1. Все детекторы (перерасход, прогноз, рост категорий, мелкие покупки, регулярные платежи, предложения лимитов) стабильно извлекают факты из сводки.
2. Отсутствуют выдуманные числа: валидатор строго отсекает любые суммы, которых не было в фактах.
3. Формат ответа строго соответствует схеме `InsightsOut` для бесшовной интеграции с бэкендом и фронтендом.
