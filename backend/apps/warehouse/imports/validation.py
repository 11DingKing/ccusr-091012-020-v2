"""逐行校验：把暂存行判定为 新增 / 更新 / 冲突 / 错误。"""
from django.utils import timezone

from ..models import Category, ImportRow, Variety

VARIETY_NAME_MAX = 20


def _validate_fields(name, category_name, unit_name, categories):
    """单行字段级校验，返回错误信息（None 表示通过）。"""
    if not name:
        return '品种名称不能为空'
    if len(name) > VARIETY_NAME_MAX:
        return f'品种名称最多{VARIETY_NAME_MAX}个字'
    if not category_name:
        return '品类不能为空'
    category = categories.get(category_name)
    if category is None:
        return f'品类“{category_name}”不存在'
    if not unit_name:
        return '单位不能为空'
    if category.unit.name != unit_name:
        return f'单位与品类不匹配，应为“{category.unit.name}”'
    return None


def validate_batch(batch):
    """对批次的全部暂存行重新执行逐行校验，并刷新汇总与批次状态。

    判定规则：
    - 字段不合法（含品类不存在、单位不匹配）→ error；
    - 同一批次内出现相同业务键（品类+品种）多行 → 除首行外均为 conflict；
    - 命中正式表同名品种 → update；
    - 其余 → create。

    校验在单个事务内完成，调用方无需自行加锁。
    """
    rows = list(batch.rows.order_by('sheet_row'))
    categories = {
        c.name: c
        for c in Category.objects.filter(is_active=True).select_related('unit')
    }
    # 一次性取出版本内涉及的正式品种，避免逐行查库
    names = {r.variety_name for r in rows if r.variety_name}
    existing = {
        (v.name, v.category.name): v
        for v in Variety.objects.filter(name__in=names).select_related('category')
    }

    seen_keys = {}
    for row in rows:
        name = row.variety_name
        category_name = row.category_name
        unit_name = row.unit_name

        error = _validate_fields(name, category_name, unit_name, categories)
        if error:
            row.result = ImportRow.RESULT_ERROR
            row.error_message = error
            row.variety = None
            continue

        key = (name, category_name)
        if key in seen_keys:
            row.result = ImportRow.RESULT_CONFLICT
            row.error_message = (
                f'与第{seen_keys[key]}行业务键重复（品类“{category_name}”+品种“{name}”）'
            )
            row.variety = None
            continue
        seen_keys[key] = row.sheet_row

        variety = existing.get(key)
        if variety is not None:
            row.result = ImportRow.RESULT_UPDATE
            row.error_message = ''
            row.variety = variety
        else:
            row.result = ImportRow.RESULT_CREATE
            row.error_message = ''
            row.variety = None

    ImportRow.objects.bulk_update(
        rows, ['result', 'error_message', 'variety']
    )

    batch.recalc_counters()
    batch.validated_at = timezone.now()
    batch.publish_error = ''
    batch.save(update_fields=[
        'status', 'total_count', 'create_count', 'update_count',
        'conflict_count', 'error_count', 'validated_at', 'publish_error',
    ])
    return batch


def impact_summary(batch):
    """影响摘要：新增/更新/冲突/错误的数量与样例行。

    已发布批次中 created/updated 是 create/update 的终态，计入同一口径，
    因此发布前后摘要口径一致、可对照。
    """
    def sample(results):
        return [
            {
                'sheet_row': r.sheet_row,
                'variety': r.variety_name,
                'category': r.category_name,
                'unit': r.unit_name,
                'error_message': r.error_message,
            }
            for r in batch.rows.filter(result__in=results)[:5]
        ]

    create_groups = [ImportRow.RESULT_CREATE, ImportRow.RESULT_CREATED]
    update_groups = [ImportRow.RESULT_UPDATE, ImportRow.RESULT_UPDATED]
    rows = batch.rows.all()
    create_count = sum(1 for r in rows if r.result in create_groups)
    update_count = sum(1 for r in rows if r.result in update_groups)

    return {
        'total': batch.total_count,
        'create_count': create_count,
        'update_count': update_count,
        'conflict_count': batch.conflict_count,
        'error_count': batch.error_count,
        'creates': sample(create_groups),
        'updates': sample(update_groups),
        'conflicts': sample([ImportRow.RESULT_CONFLICT]),
        'errors': sample([ImportRow.RESULT_ERROR]),
    }
