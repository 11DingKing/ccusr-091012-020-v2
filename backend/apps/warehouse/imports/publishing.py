"""原子发布：把校验通过的暂存行写入正式表。

保证：
- 发布在单个数据库事务内完成，任何一步失败整体回滚，正式表绝不残留部分数据；
- 进入事务的第一条语句即“认领”批次（draft/ready → publishing 的原子 UPDATE），
  并发发布在写锁上串行，先到者发布成功，后到者被明确拒绝；
- 进程在发布中途崩溃会残留 publishing 状态，超过 PUBLISH_LOCK_TIMEOUT_SECONDS
  后视为僵死事务，下次发布可重新认领（原事务的数据库改动已随连接断开回滚）；
- 持锁后按当前正式表重新校验，上传后被其他发布/手工改动抢先写入的业务键
  会被识别为冲突并中止发布；
- 正式表 (category, name) 唯一约束是最后防线，并发发布触发唯一冲突时同样整体回滚。
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Category, ImportBatch, ImportRow, Variety
from .validation import _validate_fields

logger = logging.getLogger('apps')

# publishing 状态超过该时长视为崩溃残留，可被重新认领
PUBLISH_LOCK_TIMEOUT_SECONDS = 60


class PublishError(Exception):
    """发布被拒绝或失败；message 可直接返回给用户。"""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _revalidate_locked(rows, categories, existing):
    """持锁状态下按当前正式表重新判定每一行。

    返回 blockers（仍处于错误/冲突的行）与 matched（{row_id: variety} 更新命中）。
    """
    blockers = []
    matched = {}
    seen_keys = {}
    for row in rows:
        error = _validate_fields(
            row.variety_name, row.category_name, row.unit_name, categories
        )
        if error:
            row.result = ImportRow.RESULT_ERROR
            row.error_message = error
            row.variety = None
            blockers.append(row)
            continue

        key = (row.variety_name, row.category_name)
        if key in seen_keys:
            row.result = ImportRow.RESULT_CONFLICT
            row.error_message = (
                f'与第{seen_keys[key]}行业务键重复'
                f'（品类“{row.category_name}”+品种“{row.variety_name}”）'
            )
            row.variety = None
            blockers.append(row)
            continue
        seen_keys[key] = row.sheet_row

        variety = existing.get(key)
        if variety is not None:
            row.result = ImportRow.RESULT_UPDATE
            row.error_message = ''
            row.variety = variety
            matched[row.id] = variety
        else:
            row.result = ImportRow.RESULT_CREATE
            row.error_message = ''
            row.variety = None
    return blockers, matched


def publish_batch(batch_id, user):
    """原子发布一个批次。成功返回已发布的批次，失败抛 PublishError。"""
    held_claim = False
    try:
        with transaction.atomic():
            now = timezone.now()
            stale_cutoff = now - timedelta(seconds=PUBLISH_LOCK_TIMEOUT_SECONDS)

            # 第一条语句即认领：draft/ready 可直接认领；超时残留的 publishing 可回收。
            # 该 UPDATE 立即获取行/库写锁，并发发布在此串行。
            claim_filter = Q(pk=batch_id) & (
                Q(status__in=[ImportBatch.STATUS_DRAFT, ImportBatch.STATUS_READY])
                | Q(status=ImportBatch.STATUS_PUBLISHING, publish_started_at__lt=stale_cutoff)
            )
            claimed = (
                ImportBatch.objects.filter(claim_filter)
                .update(status=ImportBatch.STATUS_PUBLISHING, publish_started_at=now)
            )
            if claimed == 0:
                current = ImportBatch.objects.filter(pk=batch_id).first()
                if current is None:
                    raise PublishError('导入批次不存在', status_code=404)
                if current.status == ImportBatch.STATUS_PUBLISHED:
                    raise PublishError('该导入批次已发布，请勿重复发布')
                raise PublishError('该批次正在发布中，请勿并发提交', status_code=409)
            held_claim = True

            batch = ImportBatch.objects.get(pk=batch_id)
            rows = list(batch.rows.order_by('sheet_row'))
            if not rows:
                raise PublishError('批次中没有可发布的数据行')

            # 持锁后按当前正式表重新校验，捕获上传后的正式数据漂移
            categories = {
                c.name: c
                for c in Category.objects.filter(is_active=True).select_related('unit')
            }
            names = {r.variety_name for r in rows if r.variety_name}
            existing_varieties = list(
                Variety.objects.select_for_update()
                .filter(name__in=names)
                .select_related('category')
            )
            existing = {(v.name, v.category.name): v for v in existing_varieties}

            blockers, matched = _revalidate_locked(rows, categories, existing)
            if blockers:
                raise PublishError('存在错误或冲突行，请修订后重新发布')

            to_create = [r for r in rows if r.result == ImportRow.RESULT_CREATE]
            to_update = [r for r in rows if r.result == ImportRow.RESULT_UPDATE]

            # 新增：唯一约束撞车（并发发布）会抛 IntegrityError，整个事务回滚
            new_by_row = {
                r.id: Variety(
                    name=r.variety_name,
                    category=categories[r.category_name],
                    created_by=user,
                )
                for r in to_create
            }
            created_list = list(new_by_row.values())
            try:
                Variety.objects.bulk_create(created_list)
            except IntegrityError as exc:
                raise PublishError(
                    '发布失败：检测到并发发布或正式数据已变更，本次写入已全部回滚，'
                    '请重新校验后再发布'
                ) from exc

            # 更新：重新启用曾被停用的品种，并刷新更新时间
            for row in to_update:
                variety = matched[row.id]
                if not variety.is_active:
                    variety.is_active = True
                    variety.save(update_fields=['is_active', 'updated_at'])
                else:
                    variety.save(update_fields=['updated_at'])

            for row in to_create:
                row.result = ImportRow.RESULT_CREATED
                row.published_variety = new_by_row[row.id]
            for row in to_update:
                row.result = ImportRow.RESULT_UPDATED
                row.published_variety = matched[row.id]
            ImportRow.objects.bulk_update(
                rows, ['result', 'error_message', 'variety', 'published_variety']
            )

            batch.status = ImportBatch.STATUS_PUBLISHED
            batch.published_by = user
            batch.published_at = now
            batch.publish_error = ''
            batch.total_count = len(rows)
            batch.create_count = len(to_create)
            batch.update_count = len(to_update)
            batch.conflict_count = 0
            batch.error_count = 0
            batch.save(update_fields=[
                'status', 'total_count', 'create_count', 'update_count',
                'conflict_count', 'error_count', 'published_by',
                'published_at', 'publish_error',
            ])
            return batch
    except PublishError:
        if held_claim:
            _persist_failure(batch_id)
        raise
    except OperationalError as exc:
        # SQLite 等待写锁超时等：事务已回滚，没有任何正式数据残留
        logger.warning('Import batch %s publish busy: %s', batch_id, exc)
        if held_claim:
            _persist_failure(batch_id, note='另一个发布正在进行，本次发布已取消，请稍后重试')
        raise PublishError('另一个发布正在进行，请稍后重试', status_code=409) from exc
    except IntegrityError as exc:
        logger.warning('Import batch %s publish conflict: %s', batch_id, exc)
        if held_claim:
            _persist_failure(batch_id, note='并发写入冲突，已全部回滚，请重新校验后发布')
        raise PublishError(
            '发布失败：检测到并发发布或正式数据已变更，本次写入已全部回滚，'
            '请重新校验后再发布'
        ) from exc
    except Exception as exc:  # 发布中途任何异常都不允许留下部分正式数据
        logger.exception('Import batch %s publish failed', batch_id)
        if held_claim:
            _persist_failure(batch_id, note=f'发布异常，已全部回滚：{exc}')
        raise PublishError('发布失败，所有写入已回滚，请检查数据后重试') from exc


def _persist_failure(batch_id, note=None):
    """事务回滚后，重新校验并落库批次当前状态，记录失败原因用于追溯。

    发布成功的批次不处理；publishing 崩溃残留会被 validate_batch 重置为 draft/ready。
    若另一个写事务仍持锁（SQLite 共享缓存会立即抛 locked 而非等待），
    本次状态落库可以放弃——失败事务本身已回滚，正式数据无残留，稍后可重新校验。
    """
    from .validation import validate_batch

    try:
        batch = ImportBatch.objects.filter(pk=batch_id).first()
        if batch is None or batch.status == ImportBatch.STATUS_PUBLISHED:
            return
        validate_batch(batch)
        batch.publish_started_at = None
        if note:
            batch.publish_error = note[:1000]
            batch.save(update_fields=['publish_error', 'publish_started_at'])
        else:
            batch.save(update_fields=['publish_started_at'])
    except OperationalError as exc:
        logger.info(
            'Skip persisting publish failure for batch %s (lock busy): %s',
            batch_id, exc,
        )
