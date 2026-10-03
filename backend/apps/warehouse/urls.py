"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView,
    DashboardView, GoodsListView, StockInListView, StockOutListView,
    WarningListView, ApprovalListView
)
from .import_views import (
    VarietyImportUploadView,
    VarietyImportListView,
    VarietyImportDetailView,
    VarietyImportRowFixView,
    VarietyImportRowDeleteView,
    VarietyImportRevalidateView,
    VarietyImportPublishView,
    VarietyImportDiscardView,
    VarietyImportResultExportView,
)

urlpatterns = [
    # 仪表盘
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 品种目录导入：暂存 → 逐行校验 → 修订 → 原子发布
    path('varieties/import/', VarietyImportUploadView.as_view(), name='variety-import-upload'),
    path('varieties/import/batches/', VarietyImportListView.as_view(), name='variety-import-list'),
    path('varieties/import/batches/<int:pk>/', VarietyImportDetailView.as_view(), name='variety-import-detail'),
    path('varieties/import/batches/<int:pk>/revalidate/', VarietyImportRevalidateView.as_view(), name='variety-import-revalidate'),
    path('varieties/import/batches/<int:pk>/publish/', VarietyImportPublishView.as_view(), name='variety-import-publish'),
    path('varieties/import/batches/<int:pk>/discard/', VarietyImportDiscardView.as_view(), name='variety-import-discard'),
    path('varieties/import/batches/<int:pk>/result/', VarietyImportResultExportView.as_view(), name='variety-import-result'),
    path('varieties/import/batches/<int:pk>/rows/<int:row_pk>/', VarietyImportRowFixView.as_view(), name='variety-import-row-fix'),
    path('varieties/import/batches/<int:pk>/rows/<int:row_pk>/delete/', VarietyImportRowDeleteView.as_view(), name='variety-import-row-delete'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库管理
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批管理
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),
]
