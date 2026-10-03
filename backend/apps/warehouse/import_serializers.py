"""品种目录导入相关序列化器。"""
from rest_framework import serializers

from .models import ImportBatch, ImportRow


class ImportRowSerializer(serializers.ModelSerializer):
    """暂存行（含逐行校验/发布结果）"""
    result_display = serializers.CharField(source='get_result_display', read_only=True)
    is_error = serializers.BooleanField(read_only=True)
    is_published = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImportRow
        fields = [
            'id', 'sheet_row', 'variety_name', 'category_name', 'unit_name',
            'result', 'result_display', 'error_message', 'is_error',
            'is_published', 'variety', 'published_variety',
        ]
        read_only_fields = fields


class ImportRowFixSerializer(serializers.Serializer):
    """修订单条错误行"""
    variety_name = serializers.CharField(
        min_length=1, max_length=20, required=True,
        error_messages={
            'required': '请输入品种名称', 'blank': '品种名称不能为空',
            'min_length': '品种名称至少1个字', 'max_length': '品种名称最多20个字',
        },
    )
    category_name = serializers.CharField(
        min_length=1, max_length=10, required=True,
        error_messages={
            'required': '请输入品类名称', 'blank': '品类名称不能为空',
            'max_length': '品类名称最多10个字',
        },
    )
    unit_name = serializers.CharField(
        min_length=1, max_length=5, required=True,
        error_messages={
            'required': '请输入单位', 'blank': '单位不能为空',
            'max_length': '单位最多5个字',
        },
    )


class ImportBatchListSerializer(serializers.ModelSerializer):
    """导入批次列表项"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uploaded_by_name = serializers.CharField(source='uploaded_by.username', read_only=True)
    published_by_name = serializers.CharField(source='published_by.username', read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            'id', 'filename', 'file_hash', 'status', 'status_display', 'is_open',
            'total_count', 'create_count', 'update_count', 'conflict_count',
            'error_count', 'uploaded_by', 'uploaded_by_name',
            'published_by', 'published_by_name',
            'uploaded_at', 'validated_at', 'published_at', 'publish_error',
        ]
        read_only_fields = fields


class ImportBatchDetailSerializer(ImportBatchListSerializer):
    """导入批次详情：含影响摘要与全部暂存行"""
    rows = ImportRowSerializer(many=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta(ImportBatchListSerializer.Meta):
        fields = ImportBatchListSerializer.Meta.fields + ['rows', 'summary']

    def get_summary(self, obj):
        from .imports.validation import impact_summary
        return impact_summary(obj)
