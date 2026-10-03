"""品种目录暂存导入：暂存、逐行校验、修订、原子发布、并发与去重测试。"""
import io
import threading
import time
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient, APITestCase

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from . import importing
from .models import Category, ImportBatch, ImportRow, Unit, Variety


def build_xlsx(rows):
    """rows: [(品种, 品类, 单位), ...]，生成与模板同表头的 Excel 字节。"""
    wb = Workbook()
    ws = wb.active
    ws.append(['品种', '品类', '单位'])
    for row in rows:
        ws.append(list(row))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def upload_file(client, rows, name='varieties.xlsx'):
    content = build_xlsx(rows)
    return client.post(
        '/api/varieties/imports/',
        {'file': SimpleUploadedFile(name, content,
         content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
        format='multipart',
    )


class ImportFixtureMixin:
    def setUp(self):
        self.user = User.objects.create_user('importer', 'testpass123', role='admin')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit_jian = Unit.objects.create(name='件', created_by=self.user)
        self.unit_tai = Unit.objects.create(name='台', created_by=self.user)
        self.cat_a = Category.objects.create(name='器材甲', unit=self.unit_jian, created_by=self.user)
        self.cat_b = Category.objects.create(name='器材乙', unit=self.unit_tai, created_by=self.user)


class StagingUploadTest(ImportFixtureMixin, APITestCase):
    def test_upload_only_stages_and_never_writes_formal_table(self):
        before = Variety.objects.count()
        resp = upload_file(self.client, [
            ('新终端', '器材甲', '件'),
            ('记录仪', '器材乙', '台'),
        ])
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(Variety.objects.count(), before)  # 正式表零变化

        batch = ImportBatch.objects.get(pk=data['id'])
        self.assertEqual(batch.status, ImportBatch.STATUS_DRAFT)
        self.assertEqual(batch.total_count, 2)
        self.assertEqual(batch.create_count, 2)
        self.assertTrue(data['can_publish'])

    def test_upload_marks_update_create_conflict_and_error_per_row(self):
        Variety.objects.create(name='已有终端', category=self.cat_a, created_by=self.user)
        resp = upload_file(self.client, [
            ('新终端', '器材甲', '件'),        # create
            ('已有终端', '器材甲', '件'),      # update
            ('重复品种', '器材甲', '件'),      # conflict
            ('重复品种', '器材甲', '件'),      # conflict
            ('未知品种', '不存在品类', '件'),  # error: 品类不存在
            ('单位错配', '器材甲', '台'),      # error: 单位不匹配
            ('', '器材甲', '件'),              # error: 名称为空
        ])
        self.assertEqual(resp.status_code, 200, resp.content)
        batch_id = resp.json()['data']['id']
        statuses = {
            r['row_no']: (r['status'], bool(r['errors']))
            for r in ImportRow.objects.filter(batch_id=batch_id).values('row_no', 'status', 'errors')
        }
        self.assertEqual(statuses[2][0], ImportRow.CREATE)
        self.assertEqual(statuses[3][0], ImportRow.UPDATE)
        self.assertEqual(statuses[4][0], ImportRow.CONFLICT)
        self.assertEqual(statuses[5][0], ImportRow.CONFLICT)
        self.assertEqual(statuses[6][0], ImportRow.ERROR)
        self.assertEqual(statuses[7][0], ImportRow.ERROR)
        self.assertEqual(statuses[8][0], ImportRow.ERROR)

        detail = self.client.get(f'/api/varieties/imports/{batch_id}/').json()['data']
        # 发布前影响摘要
        self.assertEqual(detail['create_count'], 1)
        self.assertEqual(detail['update_count'], 1)
        self.assertEqual(detail['conflict_count'], 2)
        self.assertEqual(detail['error_count'], 3)
        self.assertFalse(detail['can_publish'])

    def test_bad_file_is_rejected_without_batch(self):
        resp = self.client.post(
            '/api/varieties/imports/',
            {'file': SimpleUploadedFile('bad.xlsx', b'not an excel', content_type='text/plain')},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_bad_header_is_rejected(self):
        wb = Workbook()
        ws = wb.active
        ws.append(['名称', '分类', '单位'])
        buffer = io.BytesIO()
        wb.save(buffer)
        resp = self.client.post(
            '/api/varieties/imports/',
            {'file': SimpleUploadedFile('wrong.xlsx', buffer.getvalue())},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('表头', resp.json()['message'])


class DuplicateFileTest(ImportFixtureMixin, APITestCase):
    def _upload(self):
        return upload_file(self.client, [('新终端', '器材甲', '件')])

    def test_duplicate_draft_file_rejected(self):
        first = self._upload()
        self.assertEqual(first.status_code, 200)
        second = self._upload()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['data']['batch_id'], first.json()['data']['id'])

    def test_duplicate_after_publish_still_rejected(self):
        batch_id = self._upload().json()['data']['id']
        self.assertEqual(self.client.post(f'/api/varieties/imports/{batch_id}/publish/').status_code, 200)
        self.assertEqual(self._upload().status_code, 409)

    def test_reupload_after_discard_allowed(self):
        batch_id = self._upload().json()['data']['id']
        self.assertEqual(self.client.post(f'/api/varieties/imports/{batch_id}/discard/').status_code, 200)
        again = self._upload()
        self.assertEqual(again.status_code, 200)
        self.assertNotEqual(again.json()['data']['id'], batch_id)


class RowRevisionTest(ImportFixtureMixin, APITestCase):
    def test_fix_only_error_row_without_reupload(self):
        resp = upload_file(self.client, [
            ('好品种', '器材甲', '件'),
            ('错品类品种', '不存在品类', '件'),
        ])
        batch_id = resp.json()['data']['id']
        bad_row = ImportRow.objects.get(batch_id=batch_id, status=ImportRow.ERROR)

        fixed = self.client.patch(
            f'/api/varieties/imports/{batch_id}/rows/{bad_row.id}/',
            {'category_name': '器材乙', 'unit_name': '台'},
            format='json',
        )
        self.assertEqual(fixed.status_code, 200, fixed.content)
        self.assertEqual(fixed.json()['data']['row']['status'], ImportRow.CREATE)
        self.assertEqual(fixed.json()['data']['summary']['error_count'], 0)
        self.assertEqual(Variety.objects.count(), 0)  # 修订期间正式表仍不变

    def test_delete_one_duplicate_resolves_conflict(self):
        resp = upload_file(self.client, [
            ('重复品种', '器材甲', '件'),
            ('重复品种', '器材甲', '件'),
        ])
        batch_id = resp.json()['data']['id']
        rows = list(ImportRow.objects.filter(batch_id=batch_id).order_by('row_no'))
        deleted = self.client.delete(f'/api/varieties/imports/{batch_id}/rows/{rows[0].id}/')
        self.assertEqual(deleted.status_code, 200)
        remaining = ImportRow.objects.get(batch_id=batch_id)
        self.assertEqual(remaining.status, ImportRow.CREATE)

    def test_rows_endpoint_filters_errors(self):
        resp = upload_file(self.client, [
            ('好品种', '器材甲', '件'),
            ('坏品种', '不存在', '件'),
        ])
        batch_id = resp.json()['data']['id']
        rows = self.client.get(f'/api/varieties/imports/{batch_id}/rows/?status=error').json()['data']
        self.assertEqual(rows['total'], 1)
        self.assertEqual(rows['list'][0]['variety_name'], '坏品种')

    def test_revise_published_batch_rejected(self):
        batch_id = upload_file(self.client, [('新终端', '器材甲', '件')]).json()['data']['id']
        row_id = ImportRow.objects.get(batch_id=batch_id).id
        self.client.post(f'/api/varieties/imports/{batch_id}/publish/')
        resp = self.client.patch(
            f'/api/varieties/imports/{batch_id}/rows/{row_id}/',
            {'variety_name': '改名终端'}, format='json',
        )
        self.assertEqual(resp.status_code, 409)


class PublishTest(ImportFixtureMixin, APITestCase):
    def test_publish_applies_creates_and_updates_in_one_version(self):
        existing = Variety.objects.create(name='已有终端', category=self.cat_a, created_by=self.user)
        existing.is_active = False
        existing.save(update_fields=['is_active'])

        batch_id = upload_file(self.client, [
            ('新终端', '器材甲', '件'),
            ('已有终端', '器材甲', '件'),
        ]).json()['data']['id']
        resp = self.client.post(f'/api/varieties/imports/{batch_id}/publish/')
        self.assertEqual(resp.status_code, 200, resp.content)

        existing.refresh_from_db()
        self.assertTrue(existing.is_active)  # update 生效
        created = Variety.objects.get(name='新终端', category=self.cat_a)
        self.assertEqual(created.created_by, self.user)

        batch = ImportBatch.objects.get(pk=batch_id)
        self.assertEqual(batch.status, ImportBatch.STATUS_PUBLISHED)
        self.assertEqual(batch.published_by, self.user)
        self.assertIsNotNone(batch.published_at)

        # 每个暂存行都能追溯到结果与目标品种
        rows = {r.row_no: r for r in ImportRow.objects.filter(batch_id=batch_id)}
        self.assertTrue(all(r.published for r in rows.values()))
        self.assertEqual(rows[2].status, ImportRow.CREATE)
        self.assertEqual(rows[2].target_variety_id, created.id)
        self.assertEqual(rows[3].status, ImportRow.UPDATE)
        self.assertEqual(rows[3].target_variety_id, existing.id)

    def test_publish_blocked_by_errors_touches_nothing(self):
        existing = Variety.objects.create(name='已有终端', category=self.cat_a, created_by=self.user)
        existing.is_active = False
        existing.save(update_fields=['is_active'])

        batch_id = upload_file(self.client, [
            ('已有终端', '器材甲', '件'),
            ('坏品种', '不存在', '件'),
        ]).json()['data']['id']
        resp = self.client.post(f'/api/varieties/imports/{batch_id}/publish/')
        self.assertEqual(resp.status_code, 409)
        existing.refresh_from_db()
        self.assertFalse(existing.is_active)  # 连“更新”行也没有生效
        self.assertEqual(ImportBatch.objects.get(pk=batch_id).status, ImportBatch.STATUS_DRAFT)

    def test_publish_revalidates_formal_table_drift(self):
        # 更新行指向甲类既有品种；新增行指向乙类。发布前停用乙类 -> 必须拦截且不产生任何效果
        existing = Variety.objects.create(name='已有终端', category=self.cat_a, created_by=self.user)
        existing.is_active = False
        existing.save(update_fields=['is_active'])

        batch_id = upload_file(self.client, [
            ('已有终端', '器材甲', '件'),
            ('新终端', '器材乙', '台'),
        ]).json()['data']['id']
        self.cat_b.is_active = False
        self.cat_b.save(update_fields=['is_active'])

        resp = self.client.post(f'/api/varieties/imports/{batch_id}/publish/')
        self.assertEqual(resp.status_code, 409)
        existing.refresh_from_db()
        self.assertFalse(existing.is_active)
        self.assertFalse(Variety.objects.filter(name='新终端').exists())
        self.assertEqual(ImportBatch.objects.get(pk=batch_id).status, ImportBatch.STATUS_DRAFT)

    def test_mid_publish_failure_rolls_back_everything(self):
        batch_id = upload_file(self.client, [
            ('终端一', '器材甲', '件'),
            ('终端二', '器材甲', '件'),
            ('终端三', '器材甲', '件'),
        ]).json()['data']['id']

        calls = {'n': 0}
        real_create = importing._create_variety

        def flaky_create(**kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('模拟发布中途存储故障')
            return real_create(**kwargs)

        with mock.patch.object(importing, '_create_variety', side_effect=flaky_create):
            with self.assertRaises(importing.StagingError) as ctx:
                importing.publish_batch(batch_id, self.user)
        self.assertEqual(ctx.exception.code, 500)

        # 正式表没有任何部分数据；批次回到暂存，可修复后重新发布
        self.assertEqual(Variety.objects.count(), 0)
        batch = ImportBatch.objects.get(pk=batch_id)
        self.assertEqual(batch.status, ImportBatch.STATUS_DRAFT)
        self.assertFalse(ImportRow.objects.filter(batch_id=batch_id, published=True).exists())

        # 重新发布成功，证明回滚干净、版本可继续使用
        batch = importing.publish_batch(batch_id, self.user)
        self.assertEqual(batch.status, ImportBatch.STATUS_PUBLISHED)
        self.assertEqual(Variety.objects.count(), 3)

    def test_publish_idempotent_guard(self):
        batch_id = upload_file(self.client, [('终端一', '器材甲', '件')]).json()['data']['id']
        self.assertEqual(self.client.post(f'/api/varieties/imports/{batch_id}/publish/').status_code, 200)
        again = self.client.post(f'/api/varieties/imports/{batch_id}/publish/')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(Variety.objects.count(), 1)


class ConcurrentPublishTest(ImportFixtureMixin, TransactionTestCase):
    """真正的跨线程并发发布：只能有一个成功，另一个失败且无重复数据。"""

    def test_two_concurrent_publishes_only_one_wins(self):
        user = User.objects.create_user('importer2', 'testpass123', role='admin')
        unit = Unit.objects.create(name='箱', created_by=user)
        Category.objects.create(name='并发器材', unit=unit, created_by=user)

        content = build_xlsx([('并发终端', '并发器材', '箱'), ('并发终端二', '并发器材', '箱')])
        with io.BytesIO(content) as fh:
            batch = importing.create_batch(
                SimpleUploadedFile('c.xlsx', fh.read()), user,
            )
        batch_id = batch.id

        started = threading.Event()
        real_create = importing._create_variety

        def slow_create(**kwargs):
            started.set()
            time.sleep(1.0)  # 持写锁，确保另一个线程的发布与之真正并发
            return real_create(**kwargs)

        errors = []

        def winner():
            try:
                with mock.patch.object(importing, '_create_variety', side_effect=slow_create):
                    importing.publish_batch(batch_id, user)
            except Exception as exc:  # noqa: BLE001
                errors.append(('winner', exc))

        def loser():
            started.wait(5)
            time.sleep(0.3)
            try:
                importing.publish_batch(batch_id, user)
            except importing.StagingError as exc:
                errors.append(('loser', exc))
            except Exception as exc:  # noqa: BLE001
                errors.append(('loser', exc))

        t1 = threading.Thread(target=winner)
        t2 = threading.Thread(target=loser)
        t1.start()
        t2.start()
        t1.join(20)
        t2.join(20)
        connections.close_all()

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(len(errors), 1)
        loser_name, loser_exc = errors[0]
        self.assertEqual(loser_name, 'loser')
        self.assertEqual(loser_exc.code, 409)

        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.STATUS_PUBLISHED)
        self.assertEqual(Variety.objects.count(), 2)  # 无重复、无部分数据
        self.assertEqual(ImportRow.objects.filter(batch_id=batch_id, published=True).count(), 2)


class StalePublishingGuardTest(ImportFixtureMixin, APITestCase):
    def test_recent_publishing_status_is_rejected(self):
        batch_id = upload_file(self.client, [('终端一', '器材甲', '件')]).json()['data']['id']
        ImportBatch.objects.filter(pk=batch_id).update(status=ImportBatch.STATUS_PUBLISHING)
        with self.assertRaises(importing.StagingError):
            importing.publish_batch(batch_id, self.user)
        self.assertEqual(Variety.objects.count(), 0)

    def test_stale_publishing_lock_after_crash_is_reclaimable(self):
        batch_id = upload_file(self.client, [('终端一', '器材甲', '件')]).json()['data']['id']
        # 模拟进程在发布中途崩溃：状态停留在 publishing 且时间戳远超超时阈值
        ImportBatch.objects.filter(pk=batch_id).update(
            status=ImportBatch.STATUS_PUBLISHING,
            publishing_at=timezone.now() - timedelta(minutes=30),
        )
        batch = importing.publish_batch(batch_id, self.user)
        self.assertEqual(batch.status, ImportBatch.STATUS_PUBLISHED)
        self.assertEqual(Variety.objects.count(), 1)

    def test_old_drafts_remain_listable_and_traceable(self):
        old = upload_file(self.client, [('终端一', '器材甲', '件')]).json()['data']['id']
        self.client.post(f'/api/varieties/imports/{old}/publish/')
        time.sleep(0.01)
        new = upload_file(self.client, [
            ('终端二', '器材乙', '台'),
        ])
        # 不同文件允许各自成版本
        self.assertEqual(new.status_code, 200)
        listing = self.client.get('/api/varieties/imports/').json()['data']
        self.assertEqual(listing['total'], 2)
        newest = listing['list'][0]
        self.assertEqual(newest['id'], new.json()['data']['id'])


class PublishTimestampTest(ImportFixtureMixin, APITestCase):
    def test_published_at_recorded(self):
        before = timezone.now() - timedelta(seconds=1)
        batch_id = upload_file(self.client, [('终端一', '器材甲', '件')]).json()['data']['id']
        importing.publish_batch(batch_id, self.user)
        batch = ImportBatch.objects.get(pk=batch_id)
        self.assertGreaterEqual(batch.published_at, before)
