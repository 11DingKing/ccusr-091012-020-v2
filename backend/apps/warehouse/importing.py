"""
品种目录暂存导入服务。

流程：上传解析 -> 逐行校验入暂存表 -> 按行修订/删除重复行 -> 发布前影响摘要
-> 原子发布写入正式表。正式表 Variety 在发布成功前不会被写入任何数据，
发布过程中任一步失败整体回滚。
"""
import hashlib
import io
from datetime import timedelta

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.utils import timezone
from openpyxl import load_workbook

from .models import Category, ImportBatch, ImportRow, Variety

# 超过此时长仍停留在 publishing 的批次视为进程崩溃留下的僵死锁，可被重新抢占
STALE_PUBLISH_TIMEOUT = timedelta(minutes=10)

# 模板表头，顺序与列位置固定
EXPECTED_HEADERS = ('品种', '品类', '单位')
MAX_IMPORT_ROWS = 10000


class StagingError(Exception):
    """导入流程的业务异常，view 层据此返回对应 HTTP 状态码。"""

    def __init__(self, message, code=400, data=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


def parse_workbook(content):
    """解析 Excel 内容，返回 [{row_no, variety_name, category_name, unit_name}]。

    只做读取与字符串规整，不做业务校验；三列全空的行直接跳过。
    """
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise StagingError('文件格式错误，请上传由模板填写的 Excel 文件')

    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    try:
        header = next(rows_iter)
    except StopIteration:
        raise StagingError('文件内容为空，请使用导入模板填写后上传')

    headers = tuple(str(c).strip() if c is not None else '' for c in (header + (None,) * 3)[:3])
    if headers != EXPECTED_HEADERS:
        raise StagingError(
            f'文件表头不符合模板，应为：{"、".join(EXPECTED_HEADERS)}，'
            f'实际为：{"、".join(headers) if any(headers) else "空"}'
        )

    parsed = []
    for row_no, values in enumerate(rows_iter, start=2):
        cells = list(values[:3]) + [None] * max(0, 3 - len(values[:3]))

        def _clean(value):
            if value is None:
                return ''
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value).strip()

        variety_name, category_name, unit_name = (_clean(c) for c in cells[:3])
        if not any((variety_name, category_name, unit_name)):
            continue
        parsed.append({
            'row_no': row_no,
            'variety_name': variety_name,
            'category_name': category_name,
            'unit_name': unit_name,
        })
        if len(parsed) > MAX_IMPORT_ROWS:
            raise StagingError(f'单次导入不能超过 {MAX_IMPORT_ROWS} 行')

    wb.close()
    return parsed


def create_batch(uploaded_file, user):
    """上传文件并创建暂存批次。重复文件（未废弃）直接拒绝。"""
    content = uploaded_file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    existing = (
        ImportBatch.objects
        .filter(file_hash=file_hash)
        .exclude(status=ImportBatch.STATUS_DISCARDED)
        .first()
    )
    if existing:
        raise StagingError(
            '该文件已上传，请勿重复提交',
            code=409,
            data={'batch_id': existing.id, 'status': existing.status},
        )

    parsed_rows = parse_workbook(content)
    if not parsed_rows:
        raise StagingError('文件中没有可导入的数据行')

    try:
        with transaction.atomic():
            batch = ImportBatch.objects.create(
                file_name=uploaded_file.name[:255],
                file_hash=file_hash,
                created_by=user,
                total_count=len(parsed_rows),
            )
            ImportRow.objects.bulk_create([
                ImportRow(
                    batch=batch,
                    row_no=item['row_no'],
                    variety_name=item['variety_name'],
                    category_name=item['category_name'],
                    unit_name=item['unit_name'],
                )
                for item in parsed_rows
            ])
    except IntegrityError:
        # 并发上传同一文件时由唯一约束兜底
        raise StagingError('该文件已上传，请勿重复提交', code=409)

    validate_batch(batch)
    return batch


def _apply_validation(batch, rows, raise_if_blocked=False):
    """对批次内所有行执行逐行校验并落库，同时刷新批次计数。

    校验维度：字段必填/长度、品类是否存在且启用、单位与品类是否匹配、
    批次内同一业务键（品类 + 品种名称）多行冲突、与正式表比对判定新增/更新。
    发布前会再执行一次，以消除“校验后、发布前”的数据漂移。
    """
    categories = {
        c.name: c
        for c in Category.objects.filter(is_active=True).select_related('unit')
    }

    # 批次内业务键分组，识别重复行
    grouped = {}
    for row in rows:
        if row.variety_name and row.category_name:
            grouped.setdefault((row.category_name, row.variety_name), []).append(row)
    conflict_keys = {key for key, group in grouped.items() if len(group) > 1}

    referenced_category_ids = {
        category.id for name, category in categories.items()
        if name in {row.category_name for row in rows}
    }
    existing_varieties = {
        (variety.category.name, variety.name): variety
        for variety in (
            Variety.objects.filter(category_id__in=referenced_category_ids)
            .select_related('category')
        )
    }

    for row in rows:
        errors = []
        name = row.variety_name
        category_name = row.category_name
        unit_name = row.unit_name

        if not name:
            errors.append('品种名称不能为空')
        elif len(name) > 20:
            errors.append('品种名称最多20个字')

        category = categories.get(category_name) if category_name else None
        if not category_name:
            errors.append('品类不能为空')
        elif category is None:
            errors.append(f'品类“{category_name}”不存在或已停用')

        if not unit_name:
            errors.append('单位不能为空')
        elif category is not None and category.unit.name != unit_name:
            errors.append(f'单位与品类不匹配，应为“{category.unit.name}”')

        row.errors = errors
        row.target_variety = None

        if errors:
            row.status = ImportRow.ERROR
        elif (category_name, name) in conflict_keys:
            row.status = ImportRow.CONFLICT
            row.errors = ['同一业务键（品类 + 品种）在文件中出现多行，请删除重复行或修订其中一行']
        else:
            target = existing_varieties.get((category_name, name))
            if target is None:
                row.status = ImportRow.CREATE
            else:
                row.status = ImportRow.UPDATE
                row.target_variety = target

    ImportRow.objects.bulk_update(rows, ['status', 'errors', 'target_variety_id'])
    _refresh_counters(batch, rows)

    if raise_if_blocked and (batch.error_count or batch.conflict_count):
        raise StagingError(
            '存在校验失败或冲突的行，无法发布，请修订后重试',
            code=409,
            data=_summary(batch),
        )
    if raise_if_blocked and batch.total_count == 0:
        raise StagingError('批次中没有可发布的数据行', code=400)
    return batch


def _refresh_counters(batch, rows):
    batch.total_count = len(rows)
    batch.create_count = sum(1 for r in rows if r.status == ImportRow.CREATE)
    batch.update_count = sum(1 for r in rows if r.status == ImportRow.UPDATE)
    batch.conflict_count = sum(1 for r in rows if r.status == ImportRow.CONFLICT)
    batch.error_count = sum(1 for r in rows if r.status == ImportRow.ERROR)
    batch.save(update_fields=[
        'total_count', 'create_count', 'update_count',
        'conflict_count', 'error_count', 'updated_at',
    ])


def _summary(batch):
    return {
        'total_count': batch.total_count,
        'create_count': batch.create_count,
        'update_count': batch.update_count,
        'conflict_count': batch.conflict_count,
        'error_count': batch.error_count,
        'can_publish': batch.total_count > 0
        and batch.error_count == 0
        and batch.conflict_count == 0,
    }


def validate_batch(batch):
    """重新校验整个批次（修订行或外部数据可能改变结果）。"""
    _ensure_draft(batch)
    rows = list(batch.rows.all())
    return _apply_validation(batch, rows)


_FIELD_MAX_LENGTH = {
    'variety_name': ImportRow._meta.get_field('variety_name').max_length,
    'category_name': ImportRow._meta.get_field('category_name').max_length,
    'unit_name': ImportRow._meta.get_field('unit_name').max_length,
}


def revise_row(batch, row, payload):
    """修订单行暂存数据，随后全量重新校验（单行变化可能解除或产生冲突）。"""
    _ensure_draft(batch)
    updates = {}
    for field, max_length in _FIELD_MAX_LENGTH.items():
        if field not in payload or payload[field] is None:
            continue
        value = str(payload[field]).strip()
        if len(value) > max_length:
            label = {'variety_name': '品种名称', 'category_name': '品类名称', 'unit_name': '单位名称'}[field]
            raise StagingError(f'{label}最多{max_length}个字')
        updates[field] = value
    if not updates:
        raise StagingError('没有需要修订的字段')
    for field, value in updates.items():
        setattr(row, field, value)
    row.save(update_fields=[*updates.keys(), 'updated_at'])
    batch = validate_batch(batch)
    # validate_batch 重新查库并更新状态，这里刷新为带最新校验结果的同一行
    row.refresh_from_db()
    return batch, row


def delete_row(batch, row):
    """删除一行暂存记录（用于删除批次内重复行），随后全量重新校验。"""
    _ensure_draft(batch)
    row.delete()
    return validate_batch(batch)


def discard_batch(batch):
    """放弃暂存批次，不触碰正式表。"""
    _ensure_draft(batch)
    batch.status = ImportBatch.STATUS_DISCARDED
    batch.save(update_fields=['status', 'updated_at'])
    return batch


def _ensure_draft(batch):
    if batch.status == ImportBatch.STATUS_PUBLISHED:
        raise StagingError('该批次已发布，不能修改', code=409)
    if batch.status == ImportBatch.STATUS_DISCARDED:
        raise StagingError('该批次已废弃，不能修改', code=409)
    if batch.status == ImportBatch.STATUS_PUBLISHING:
        raise StagingError('批次正在发布中，请稍候', code=409)


def _create_variety(*, name, category, created_by):
    """单独封装以便测试模拟发布中途失败。"""
    return Variety.objects.create(name=name, category=category, created_by=created_by)


def _apply_variety_update(variety):
    """更新语义：业务键未变，重新导入视为确认并重新启用该品种。"""
    variety.is_active = True
    variety.save(update_fields=['is_active', 'updated_at'])


def publish_batch(batch_id, user):
    """原子发布。

    并发安全：进入事务后第一条语句即条件 UPDATE 抢占批次（draft -> publishing），
    未抢到者直接失败；事务内再次逐行校验以消除数据漂移；任一步失败整体回滚，
    批次状态随事务回到 draft，正式表不会留下部分数据。
    """
    # 事务前的状态预检给出友好提示（SQLite 下读也可能撞上正在发布的写锁）
    try:
        batch = ImportBatch.objects.filter(pk=batch_id).first()
    except OperationalError:
        raise StagingError('另一个发布正在进行中，请稍后刷新查看', code=409)
    if batch is None:
        raise StagingError('导入批次不存在', code=404)
    if batch.status == ImportBatch.STATUS_PUBLISHED:
        raise StagingError('该批次已发布', code=409, data=_summary(batch))
    if batch.status == ImportBatch.STATUS_DISCARDED:
        raise StagingError('该批次已废弃，不能发布', code=409)
    if batch.status == ImportBatch.STATUS_PUBLISHING:
        stale_before = timezone.now() - STALE_PUBLISH_TIMEOUT
        if batch.publishing_at is None or batch.publishing_at >= stale_before:
            raise StagingError('批次正在发布中，请稍候', code=409)
        # 超过超时阈值仍在 publishing：判定为进程崩溃遗留，允许重新抢占

    published_at = timezone.now()
    try:
        with transaction.atomic():
            # 第一条语句即抢占式更新：SQLite/Postgres 下均为原子写操作，
            # 并发发布会在此被串行化，只有一个事务能匹配到 draft 行；
            # 超时的僵死 publishing 锁也可由此重新接管。
            stale_before = published_at - STALE_PUBLISH_TIMEOUT
            claimed = (
                ImportBatch.objects
                .filter(
                    Q(pk=batch_id)
                    & (
                        Q(status=ImportBatch.STATUS_DRAFT)
                        | Q(status=ImportBatch.STATUS_PUBLISHING, publishing_at__lt=stale_before)
                    ),
                )
                .update(status=ImportBatch.STATUS_PUBLISHING, publishing_at=published_at)
            )
            if claimed != 1:
                raise StagingError('批次正在被其他操作发布，请稍后刷新查看', code=409)

            batch = ImportBatch.objects.get(pk=batch_id)
            rows = list(batch.rows.all())

            # 发布前复检：上传校验之后正式表可能已被改动
            _apply_validation(batch, rows, raise_if_blocked=True)

            categories = {c.name: c for c in Category.objects.filter(is_active=True)}
            try:
                for row in rows:
                    if row.status == ImportRow.CREATE:
                        variety = _create_variety(
                            name=row.variety_name,
                            category=categories[row.category_name],
                            created_by=batch.created_by,
                        )
                        row.target_variety = variety
                    elif row.status == ImportRow.UPDATE:
                        # 复检之后目标仍可能被并发删除，按主键再取一次
                        target = Variety.objects.filter(pk=row.target_variety_id).first()
                        if target is None:
                            raise StagingError(
                                '发布期间正式数据发生变化，请重新校验后再发布', code=409
                            )
                        _apply_variety_update(target)
                    row.published = True
            except IntegrityError:
                # 复检之后仍撞上并发写入（如唯一约束），整体回滚
                raise StagingError(
                    '发布期间正式数据发生变化，请重新校验后再发布', code=409
                )

            ImportRow.objects.bulk_update(rows, ['target_variety_id', 'published'])

            batch.status = ImportBatch.STATUS_PUBLISHED
            batch.published_by = user
            batch.published_at = published_at
            batch.save(update_fields=[
                'status', 'published_by', 'published_at', 'updated_at',
            ])
    except StagingError:
        raise
    except OperationalError:
        # 事务体内撞写锁（SQLite 直接报错），事务已回滚，不产生部分数据
        raise StagingError('另一个发布正在进行中，请稍后刷新查看', code=409)
    except Exception as exc:  # noqa: BLE001 - 发布中途任何失败都不能留下半成品
        # 事务已随异常回滚，批次恢复为 draft；行级 published 标记一并回滚
        raise StagingError(f'发布失败，已全部回滚：{exc}', code=500)

    return batch
