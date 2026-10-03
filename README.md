# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```

## 品种目录批量导入（暂存 → 校验 → 修订 → 原子发布）

合作单位批量提交品种目录时，文件上传后只写入暂存表，逐行校验为 **新增 / 更新 / 冲突 / 错误**，
错误行可单独修订而无需重传整份文件；确认影响摘要后一次性原子发布，任何失败整体回滚，
正式表不会出现部分写入。

| 接口 | 说明 |
| --- | --- |
| `POST /api/varieties/import/` | 上传 Excel（列：品种、品类、单位），仅暂存并逐行校验；重复文件（SHA256）返回 409 与已有批次 ID |
| `GET /api/varieties/import/batches/` | 导入版本列表（可按 `status` 过滤、分页） |
| `GET /api/varieties/import/batches/{id}/` | 版本详情：新增/更新/冲突/错误影响摘要与每一行结果 |
| `PATCH /api/varieties/import/batches/{id}/rows/{rowId}/` | 修订单条错误/冲突行，修订后整批自动重新校验 |
| `DELETE /api/varieties/import/batches/{id}/rows/{rowId}/delete/` | 删除单条暂存行（如文件内业务键重复） |
| `POST /api/varieties/import/batches/{id}/revalidate/` | 按当前正式表重新校验 |
| `POST /api/varieties/import/batches/{id}/publish/` | 原子发布；并发发布与发布中途失败均整体回滚 |
| `POST /api/varieties/import/batches/{id}/discard/` | 丢弃未发布版本（仅删除暂存数据，正式表不受影响） |
| `GET /api/varieties/import/batches/{id}/result/` | 导出逐行结果（含终态与正式品种 ID，可逐行追溯） |

批次状态：`draft`（待修订）→ `ready`（待发布）→ `publishing`（发布中）→ `published`（已发布）。
同一业务键（品类+品种）在文件内出现多行会被标记为冲突；发布时会持锁按正式表重新校验，
并以正式表唯一约束兜底，防止并发发布产生重复数据。
