"""Операции с базой данных по разделам. Роутеры вызывают их как crud.<функция> — имена реэкспортируются отсюда.

Все операции с чеками требуют user_id: анонимы с историей не работают (эндпоинты защищены через auth.get_current_user).
"""
from app.categories import get_user_category  # noqa: F401 — раньше было доступно как app.crud.get_user_category
from app.crud.users import (
    create_user,
    delete_user,
    get_user_by_email,
    get_user_by_id,
)
from app.crud.receipts import (
    attach_base_rates,
    backfill_user_rates,
    count_receipts_with_image,
    create_receipt,
    delete_receipt,
    find_duplicate,
    get_all_receipts,
    get_receipt_by_id,
    receipt_day,
    update_receipt,
)
from app.crud.analytics import (
    get_analytics,
)
from app.crud.profile import (
    ACTIVITY_DAYS,
    get_profile_stats,
)
from app.crud.budgets import (
    get_budget_status,
    get_user_budgets,
    set_user_budgets,
)
from app.crud.categories import (
    create_category,
    delete_category,
    delete_rule,
    get_categories,
    get_rules,
    move_items,
    reorder_categories,
    update_category,
)
from app.crud.insights import (
    INSIGHTS_KEEP,
    SMALL_PURCHASE_KZT,
    SNAPSHOT_MONTHS,
    SNAPSHOT_NAME_MAX,
    SNAPSHOT_PREV_MONTHS,
    SNAPSHOT_SMALL_GROUPS,
    SNAPSHOT_TOP_STORES,
    build_finance_snapshot,
    get_latest_insight,
    save_insight,
    snapshot_hash,
)
