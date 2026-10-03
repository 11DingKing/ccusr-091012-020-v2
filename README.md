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

## 物资目录导入（暂存—校验—发布）

合作单位批量提交品种目录时，文件不会直接写入正式表，而是先进入暂存区：

1. `POST /api/varieties/imports/` 上传 Excel（模板：`GET /api/varieties/template/`），逐行解析为暂存行，立即返回新增/更新/冲突/错误的影响摘要；相同文件（SHA-256）重复上传会被拒绝。
2. 错误行可通过 `PATCH /api/varieties/imports/{id}/rows/{row_id}/` 单独修订，批次内重复业务键（品类+品种）可用 `DELETE` 删除其中一行，无需重传整份文件；每次修订自动重新校验整份批次。
3. `POST /api/varieties/imports/{id}/publish/` 原子发布：单个数据库事务内完成抢占批次、发布前复检与全部写入，任一失败整体回滚，正式表不留部分数据；并发发布只有一个请求成功，失败方收到 409。
4. 发布完成后批次保留文件哈希、发布人、发布时间，每个暂存行保留最终状态与目标品种 ID，可逐行追溯。

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```
