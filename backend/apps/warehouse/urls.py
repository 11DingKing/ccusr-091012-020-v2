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
    ImportBatchListUploadView, ImportBatchDetailView,
    ImportRowListView, ImportRowDetailView,
    ImportBatchRevalidateView, ImportBatchPublishView, ImportBatchDiscardView,
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

    # 品种目录暂存导入：上传暂存 -> 逐行校验/修订 -> 原子发布
    path('varieties/imports/', ImportBatchListUploadView.as_view(), name='variety-import-batch-list'),
    path('varieties/imports/<int:pk>/', ImportBatchDetailView.as_view(), name='variety-import-batch-detail'),
    path('varieties/imports/<int:pk>/rows/', ImportRowListView.as_view(), name='variety-import-row-list'),
    path('varieties/imports/<int:pk>/rows/<int:row_pk>/', ImportRowDetailView.as_view(), name='variety-import-row-detail'),
    path('varieties/imports/<int:pk>/revalidate/', ImportBatchRevalidateView.as_view(), name='variety-import-revalidate'),
    path('varieties/imports/<int:pk>/publish/', ImportBatchPublishView.as_view(), name='variety-import-publish'),
    path('varieties/imports/<int:pk>/discard/', ImportBatchDiscardView.as_view(), name='variety-import-discard'),

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
