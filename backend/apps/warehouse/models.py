"""
库房管理模型
"""
from django.db import models
from apps.authentication.models import User

class Unit(models.Model):
    """单位模型"""
    name = models.CharField('单位名称', max_length=5, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_units', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_unit'
        verbose_name = '单位'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品类"""
        return self.categories.exists()


class Category(models.Model):
    """品类模型"""
    name = models.CharField('品类名称', max_length=10, unique=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT,
        related_name='categories', verbose_name='单位'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_categories', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_category'
        verbose_name = '品类'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品种"""
        return self.varieties.exists()


class Variety(models.Model):
    """品种模型"""
    name = models.CharField('品种名称', max_length=20)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='varieties', verbose_name='所属品类'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_varieties', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_variety'
        verbose_name = '品种'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        unique_together = ['category', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"
    
    @property
    def is_in_stock(self):
        """是否已入库"""
        return self.goods.exists()
    
    @property
    def unit_name(self):
        """获取单位名称"""
        return self.category.unit.name if self.category and self.category.unit else ''


class Goods(models.Model):
    """货物模型"""
    variety = models.ForeignKey(
        Variety, on_delete=models.CASCADE,
        related_name='goods', verbose_name='所属品种'
    )
    name = models.CharField('货物名称', max_length=200)
    code = models.CharField('货物编码', max_length=50, unique=True)
    specification = models.CharField('规格型号', max_length=200, blank=True)
    quantity = models.DecimalField('库存数量', max_digits=12, decimal_places=2, default=0)
    warning_threshold = models.DecimalField('预警阈值', max_digits=12, decimal_places=2, default=10)
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_goods'
        verbose_name = '货物'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_warning(self):
        """是否预警"""
        return self.quantity <= self.warning_threshold


class StockIn(models.Model):
    """入库记录模型"""
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_ins', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_in_operations', verbose_name='操作人'
    )
    quantity = models.DecimalField('入库数量', max_digits=12, decimal_places=2)
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    supplier = models.CharField('供应商', max_length=200, blank=True)
    stock_in_time = models.DateTimeField('入库时间', auto_now_add=True)
    remark = models.TextField('备注', blank=True)
    
    class Meta:
        db_table = 'wh_stock_in'
        verbose_name = '入库记录'
        verbose_name_plural = verbose_name
        ordering = ['-stock_in_time']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class StockOut(models.Model):
    """出库记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
        ('completed', '已完成'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class Warning(models.Model):
    """预警记录模型"""
    TYPE_CHOICES = [
        ('low_stock', '库存不足'),
        ('expiring', '即将过期'),
        ('expired', '已过期'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='warnings', verbose_name='货物'
    )
    type = models.CharField('预警类型', max_length=20, choices=TYPE_CHOICES)
    message = models.TextField('预警信息')
    is_read = models.BooleanField('是否已读', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_warning'
        verbose_name = '预警记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.get_type_display()}"


class Approval(models.Model):
    """审批记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]
    
    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='approvals', verbose_name='出库记录'
    )
    approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='approvals', verbose_name='审批人'
    )
    status = models.CharField('审批状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    remark = models.TextField('审批意见', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_approval'
        verbose_name = '审批记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.stock_out} - {self.get_status_display()}"


# ==================== 品种目录导入（暂存/发布） ====================

class ImportBatch(models.Model):
    """品种目录导入批次（导入版本）

    文件上传后仅写入暂存表，经逐行校验、修订后再原子发布；
    批次状态贯穿全过程，发布结果可逐行追溯。
    """
    STATUS_DRAFT = 'draft'        # 已暂存，存在错误行，待修订
    STATUS_READY = 'ready'        # 全部行校验通过，可发布
    STATUS_PUBLISHING = 'publishing'  # 发布事务进行中（崩溃残留可被超时回收）
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        (STATUS_DRAFT, '待修订'),
        (STATUS_READY, '待发布'),
        (STATUS_PUBLISHING, '发布中'),
        (STATUS_PUBLISHED, '已发布'),
    ]

    filename = models.CharField('原始文件名', max_length=255)
    file_hash = models.CharField('文件SHA256', max_length=64, unique=True)
    status = models.CharField('批次状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='import_batches', verbose_name='上传人'
    )
    published_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='published_import_batches', verbose_name='发布人'
    )
    total_count = models.PositiveIntegerField('总行数', default=0)
    create_count = models.PositiveIntegerField('新增数', default=0)
    update_count = models.PositiveIntegerField('更新数', default=0)
    conflict_count = models.PositiveIntegerField('冲突数', default=0)
    error_count = models.PositiveIntegerField('错误行数', default=0)
    publish_error = models.TextField('发布失败原因', blank=True)
    publish_started_at = models.DateTimeField('发布开始时间', null=True, blank=True)
    uploaded_at = models.DateTimeField('上传时间', auto_now_add=True)
    validated_at = models.DateTimeField('最近校验时间', null=True, blank=True)
    published_at = models.DateTimeField('发布时间', null=True, blank=True)

    class Meta:
        db_table = 'wh_import_batch'
        verbose_name = '品种导入批次'
        verbose_name_plural = verbose_name
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"导入批次#{self.id}({self.get_status_display()})"

    @property
    def is_open(self):
        """批次是否仍可修订/发布"""
        return self.status in (self.STATUS_DRAFT, self.STATUS_READY)

    def recalc_counters(self):
        """按各行结果重算汇总计数与批次状态（不落库）"""
        rows = self.rows.all()
        self.total_count = len(rows)
        self.create_count = sum(1 for r in rows if r.result == ImportRow.RESULT_CREATE)
        self.update_count = sum(1 for r in rows if r.result == ImportRow.RESULT_UPDATE)
        self.conflict_count = sum(1 for r in rows if r.result == ImportRow.RESULT_CONFLICT)
        self.error_count = sum(1 for r in rows if r.result == ImportRow.RESULT_ERROR)
        self.status = self.STATUS_READY if self.error_count == 0 and self.total_count > 0 else self.STATUS_DRAFT
        return self


class ImportRow(models.Model):
    """导入批次暂存行：保存原始内容、修订内容、逐行校验结果与发布回执"""
    RESULT_ERROR = 'error'
    RESULT_CREATE = 'create'      # 正式表中不存在，发布时新增
    RESULT_UPDATE = 'update'      # 命中正式表，发布时更新
    RESULT_CONFLICT = 'conflict'  # 同文件内业务键重复等冲突，必须修订
    RESULT_CREATED = 'created'    # 已发布：新增完成
    RESULT_UPDATED = 'updated'    # 已发布：更新完成
    RESULT_CHOICES = [
        (RESULT_ERROR, '错误'),
        (RESULT_CREATE, '新增'),
        (RESULT_UPDATE, '更新'),
        (RESULT_CONFLICT, '冲突'),
        (RESULT_CREATED, '已新增'),
        (RESULT_UPDATED, '已更新'),
    ]

    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE,
        related_name='rows', verbose_name='所属批次'
    )
    sheet_row = models.PositiveIntegerField('Excel行号')
    variety_name = models.CharField('品种', max_length=20, blank=True)
    category_name = models.CharField('品类', max_length=10, blank=True)
    unit_name = models.CharField('单位', max_length=5, blank=True)
    result = models.CharField('校验结果', max_length=20, choices=RESULT_CHOICES, default=RESULT_ERROR)
    error_message = models.CharField('错误原因', max_length=255, blank=True)
    variety = models.ForeignKey(
        Variety, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='import_rows', verbose_name='命中的正式品种'
    )
    published_variety = models.ForeignKey(
        Variety, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='published_import_rows', verbose_name='发布写入的品种'
    )

    class Meta:
        db_table = 'wh_import_row'
        verbose_name = '品种导入暂存行'
        verbose_name_plural = verbose_name
        ordering = ['sheet_row']
        unique_together = [['batch', 'sheet_row']]

    @property
    def is_error(self):
        return self.result in (self.RESULT_ERROR, self.RESULT_CONFLICT)

    @property
    def is_published(self):
        return self.result in (self.RESULT_CREATED, self.RESULT_UPDATED)

