"""暂存与修订：上传文件只落暂存表；错误行可单独修订，无需重传整份文件。"""
import logging

from django.db import IntegrityError, transaction

from ..models import ImportBatch, ImportRow
from .parser import parse_rows, read_uploaded_file
from .validation import validate_batch

logger = logging.getLogger('apps')


class StagingError(Exception):
    """暂存被拒绝（重复文件、空文件等）。"""

    def __init__(self, message, status_code=400, existing_batch=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.existing_batch = existing_batch


def find_duplicate(file_hash):
    """查找同指纹的未发布/已发布批次；批次被丢弃时记录已物理删除，故不会命中。"""
    return (
        ImportBatch.objects
        .filter(file_hash=file_hash)
        .order_by('-uploaded_at')
        .first()
    )


def create_batch(uploaded_file, user):
    """解析上传文件并写入暂存表，随后逐行校验。

    整个过程在单事务内完成：解析/写暂存失败不会留下任何批次残留。
    返回 (batch, duplicate)；重复文件时不创建新批次，返回已有批次。
    """
    content, file_hash = read_uploaded_file(uploaded_file)

    existing = find_duplicate(file_hash)
    if existing is not None:
        logger.info(
            'Duplicate import file rejected for user %s, existing batch %s',
            getattr(user, 'id', None), existing.id,
        )
        raise StagingError(
            '该文件已上传过（重复文件），请直接修订或发布原有导入批次，无需重复上传',
            status_code=409,
            existing_batch=existing,
        )

    rows_data = parse_rows(content)

    try:
        with transaction.atomic():
            batch = ImportBatch.objects.create(
                filename=(uploaded_file.name or 'import.xlsx')[:255],
                file_hash=file_hash,
                uploaded_by=user,
                status=ImportBatch.STATUS_DRAFT,
                total_count=len(rows_data),
            )
            ImportRow.objects.bulk_create([
                ImportRow(
                    batch=batch,
                    sheet_row=data['sheet_row'],
                    variety_name=data['variety'],
                    category_name=data['category'],
                    unit_name=data['unit'],
                )
                for data in rows_data
            ])
            validate_batch(batch)
    except IntegrityError as exc:
        # 并发上传同一文件：唯一约束兜底，不产生重复批次
        existing = find_duplicate(file_hash)
        logger.info(
            'Concurrent duplicate upload of hash %s blocked, existing batch %s',
            file_hash, getattr(existing, 'id', None),
        )
        raise StagingError(
            '该文件已上传过（重复文件），请直接修订或发布原有导入批次，无需重复上传',
            status_code=409,
            existing_batch=existing,
        ) from exc

    logger.info(
        'User %s staged import batch %s with %s rows',
        getattr(user, 'id', None), batch.id, batch.total_count,
    )
    return batch


def _require_open_batch(batch):
    if batch.status == ImportBatch.STATUS_PUBLISHED:
        raise StagingError('该导入批次已发布，不能再修订')
    if batch.status == ImportBatch.STATUS_PUBLISHING:
        raise StagingError('批次正在发布中，请稍后再试', status_code=409)
    return batch


def fix_row(batch, row, variety, category, unit):
    """修订单条暂存行后对整批重新校验（业务键重复是跨行问题）。"""
    _require_open_batch(batch)
    with transaction.atomic():
        # 锁定批次，避免与发布并发
        ImportBatch.objects.select_for_update().filter(pk=batch.pk).exists()
        row.variety_name = variety
        row.category_name = category
        row.unit_name = unit
        row.save(update_fields=['variety_name', 'category_name', 'unit_name'])
        validate_batch(batch)
        row.refresh_from_db()
    return row


def delete_row(batch, row):
    """删除单条暂存行（例如业务键冲突时删掉重复的一行）。"""
    _require_open_batch(batch)
    with transaction.atomic():
        ImportBatch.objects.select_for_update().filter(pk=batch.pk).exists()
        row.delete()
        validate_batch(batch)


def discard_batch(batch):
    """丢弃未发布的批次：物理删除批次及暂存行，正式表不受任何影响。"""
    _require_open_batch(batch)
    with transaction.atomic():
        batch.delete()
    return None
