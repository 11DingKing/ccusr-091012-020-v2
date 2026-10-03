"""
品种目录暂存导入视图。

上传只入暂存表，正式表仅在“发布”成功的一个事务内变更：
- POST   /varieties/imports/                     上传文件创建暂存批次
- GET    /varieties/imports/                     批次列表
- GET    /varieties/imports/<pk>/                批次详情（影响摘要）
- GET    /varieties/imports/<pk>/rows/           逐行校验结果（可按 status 过滤）
- PATCH  /varieties/imports/<pk>/rows/<row_pk>/  修订单行
- DELETE /varieties/imports/<pk>/rows/<row_pk>/  删除单行（如批次内重复行）
- POST   /varieties/imports/<pk>/revalidate/     重新逐行校验
- POST   /varieties/imports/<pk>/publish/        原子发布
- POST   /varieties/imports/<pk>/discard/        放弃批次
"""
import logging

from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.response import error_response, success_response

from . import importing
from .models import ImportBatch, ImportRow
from .serializers import ImportBatchSerializer, ImportRowSerializer

logger = logging.getLogger('apps')


def _staging_error_response(exc):
    return error_response(message=exc.message, code=exc.code, data=exc.data)


def _get_batch(pk):
    return ImportBatch.objects.filter(pk=pk).first()


def _get_row(batch, row_pk):
    return batch.rows.filter(pk=row_pk).first()


class ImportBatchListUploadView(APIView):
    """暂存批次列表 / 上传文件创建批次"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        queryset = ImportBatch.objects.all().order_by('-created_at')

        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = max(int(request.query_params.get('page_size', 10)), 1)
        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        batches = queryset[(page - 1) * page_size:page * page_size]
        return success_response(data={
            'list': ImportBatchSerializer(batches, many=True).data,
            'total': total,
            'page': page,
            'page_size': page_size,
        })

    def post(self, request):
        if 'file' not in request.FILES:
            return error_response(message='请上传文件')
        try:
            batch = importing.create_batch(request.FILES['file'], request.user)
        except importing.StagingError as exc:
            return _staging_error_response(exc)

        logger.info(
            "User %s staged import batch %s (%s rows, %s errors)",
            request.user.username, batch.id, batch.total_count, batch.error_count,
        )
        return success_response(
            data=ImportBatchSerializer(batch).data,
            message='文件已暂存，请逐行核对后发布',
        )


class ImportBatchDetailView(APIView):
    """批次详情：发布前的新增/更新/冲突/错误影响摘要"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        batch = _get_batch(pk)
        if batch is None:
            return error_response(message='导入批次不存在', code=404)
        data = ImportBatchSerializer(batch).data
        data['rows_url'] = f'/api/varieties/imports/{batch.id}/rows/'
        return success_response(data=data)


class ImportRowListView(APIView):
    """批次逐行结果，支持按状态过滤，用于只定位错误行/冲突行"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        batch = _get_batch(pk)
        if batch is None:
            return error_response(message='导入批次不存在', code=404)

        rows = batch.rows.all()
        status = request.query_params.get('status')
        if status:
            if status not in dict(ImportRow.STATUS_CHOICES):
                return error_response(message='无效的状态过滤条件')
            rows = rows.filter(status=status)

        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = max(int(request.query_params.get('page_size', 20)), 1)
        total = rows.count()
        page_rows = rows[(page - 1) * page_size:page * page_size]
        return success_response(data={
            'list': ImportRowSerializer(page_rows, many=True).data,
            'total': total,
            'page': page,
            'page_size': page_size,
            'summary': ImportBatchSerializer(batch).data,
        })


class ImportRowDetailView(APIView):
    """修订单行 / 删除单行（修复错误行或去掉重复行，无需重传整份文件）"""
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, row_pk):
        batch = _get_batch(pk)
        if batch is None:
            return error_response(message='导入批次不存在', code=404)
        row = _get_row(batch, row_pk)
        if row is None:
            return error_response(message='该行不存在于当前批次', code=404)

        payload = {
            key: request.data.get(key)
            for key in ('variety_name', 'category_name', 'unit_name')
            if key in request.data
        }
        try:
            batch, row = importing.revise_row(batch, row, payload)
        except importing.StagingError as exc:
            return _staging_error_response(exc)

        return success_response(
            data={
                'row': ImportRowSerializer(row).data,
                'summary': ImportBatchSerializer(batch).data,
            },
            message='修订成功，已重新校验整份批次',
        )

    def delete(self, request, pk, row_pk):
        batch = _get_batch(pk)
        if batch is None:
            return error_response(message='导入批次不存在', code=404)
        row = _get_row(batch, row_pk)
        if row is None:
            return error_response(message='该行不存在于当前批次', code=404)

        try:
            batch = importing.delete_row(batch, row)
        except importing.StagingError as exc:
            return _staging_error_response(exc)

        return success_response(
            data=ImportBatchSerializer(batch).data,
            message='该行已删除，已重新校验整份批次',
        )


class ImportBatchRevalidateView(APIView):
    """重新逐行校验（正式表数据变化后可刷新影响摘要）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        batch = _get_batch(pk)
        if batch is None:
            return error_response(message='导入批次不存在', code=404)
        try:
            batch = importing.validate_batch(batch)
        except importing.StagingError as exc:
            return _staging_error_response(exc)
        return success_response(data=ImportBatchSerializer(batch).data, message='已重新校验')


class ImportBatchPublishView(APIView):
    """原子发布：全部成功才写正式表，失败整体回滚"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            batch = importing.publish_batch(pk, request.user)
        except importing.StagingError as exc:
            return _staging_error_response(exc)

        logger.info(
            "User %s published import batch %s: %s created, %s updated",
            request.user.username, batch.id, batch.create_count, batch.update_count,
        )
        return success_response(
            data=ImportBatchSerializer(batch).data,
            message=f'发布成功：新增 {batch.create_count} 个，更新 {batch.update_count} 个',
        )


class ImportBatchDiscardView(APIView):
    """放弃暂存批次"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        batch = _get_batch(pk)
        if batch is None:
            return error_response(message='导入批次不存在', code=404)
        try:
            importing.discard_batch(batch)
        except importing.StagingError as exc:
            return _staging_error_response(exc)
        return success_response(message='批次已废弃')
