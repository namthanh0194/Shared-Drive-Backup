import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sdb import PLUGIN, REPOSITORY, VERSION
from sdb.drive import identifier, make_client, validate_credentials
from sdb.integration import register, status as integration_status
from sdb.panel import Panel
from sdb.release import latest_release, rollback, update
from sdb.storage import file_lock, validate_source
from sdb.worker import ensure_ready, validate_exclusions

METHODS = {
    'status', 'save_credentials', 'list_drives', 'browse_folders', 'create_folder', 'set_destination',
    'connection_check', 'save_settings', 'inventory', 'history', 'start_backup', 'start_download',
    'tasks', 'save_github_token', 'check_update', 'update', 'rollback', 'repair_integration', 'read_log',
}


def boolean(value):
    if value in (True, 1, '1', 'true'):
        return True
    if value in (False, 0, '0', 'false'):
        return False
    raise ValueError('Giá trị boolean không hợp lệ.')


class API:
    def __init__(self, store):
        self.store = store

    def dispatch(self, method, arguments):
        if method not in METHODS or not isinstance(arguments, dict):
            raise ValueError('Thao tác không được phép.')
        return getattr(self, method)(arguments)

    def status(self, arguments):
        configuration = self.store.config()
        secret = self.store.secret('credentials') or {}
        disk = shutil.disk_usage(self.store.tmp)
        return {
            'version': VERSION, 'repository': REPOSITORY, 'config': configuration,
            'credentials': {'present': bool(secret), 'type': secret.get('type', ''), 'email': secret.get('client_email', '')},
            'github_configured': bool(self.store.secret('github_token')), 'busy': self.store.busy(),
            'disk': {'free': disk.free, 'total': disk.total}, 'history': self.store.history(5),
            'integration': integration_status(self.store, '/www/server/panel'),
            'rollback_available': (self.store.data / 'releases/previous/info.json').exists(),
            'recovery_required': (self.store.data / 'deployment-recovery.json').exists(),
        }

    def mutation_lock(self):
        return file_lock(self.store.data / 'operation.lock')

    def save_credentials(self, arguments):
        document = arguments.get('document', '')
        if not isinstance(document, str) or len(document) > 65536:
            raise ValueError('JSON xác thực không hợp lệ hoặc quá lớn.')
        try:
            document = json.loads(document)
        except json.JSONDecodeError:
            raise ValueError('Không đọc được JSON xác thực.') from None
        validate_credentials(document)
        with self.mutation_lock():
            self.store.save_secret('credentials', document)
            self.store.save_config({'configured': False})
        return {'saved': True, 'type': document['type']}

    def list_drives(self, arguments):
        return make_client(self.store).drives(str(arguments.get('page_token', ''))[:2048])

    def browse_folders(self, arguments):
        client = make_client(self.store)
        drive_id = identifier(arguments.get('drive_id', ''))
        folder_id = identifier(arguments.get('folder_id') or drive_id)
        folder = client.folder(drive_id, folder_id)
        folders = client.folders(drive_id, folder_id, str(arguments.get('page_token', ''))[:2048])
        return {'folder': folder, 'breadcrumbs': client.breadcrumbs(drive_id, folder_id), **folders}

    def create_folder(self, arguments):
        with self.mutation_lock():
            return make_client(self.store).create_folder(arguments.get('drive_id', ''), arguments.get('parent_id', ''), arguments.get('name', ''))

    def set_destination(self, arguments):
        with self.mutation_lock():
            client = make_client(self.store)
            drive_id = identifier(arguments.get('drive_id', ''))
            folder_id = identifier(arguments.get('folder_id', ''))
            folder = client.folder(drive_id, folder_id, writable=True)
            drive = client.service.drives().get(driveId=drive_id, fields='id,name').execute(num_retries=3)
            configuration = self.store.save_config({'drive_id': drive_id, 'drive_name': drive['name'], 'folder_id': folder_id, 'folder_name': folder['name'], 'configured': True})
        return {'config': configuration}

    def connection_check(self, arguments):
        configuration, client = ensure_ready(self.store)
        return {'connected': True, 'folder': client.folder(configuration['drive_id'], configuration['folder_id'], writable=True)}

    def save_settings(self, arguments):
        allowed = {'server_name', 'prune_enabled', 'min_free_mb', 'max_archive_gb', 'keep_local'}
        payload = {key: value for key, value in arguments.items() if key != '_secure_transport'}
        if not set(payload).issubset(allowed):
            raise ValueError('Thiết lập không được phép.')
        for key in ['prune_enabled', 'keep_local']:
            if key in payload:
                payload[key] = boolean(payload[key])
        for key, low, high in [('min_free_mb', 256, 1048576), ('max_archive_gb', 1, 2048)]:
            if key in payload:
                payload[key] = int(payload[key])
                if not low <= payload[key] <= high:
                    raise ValueError('Giới hạn dung lượng không hợp lệ.')
        if 'server_name' in payload:
            payload['server_name'] = str(payload['server_name']).strip()[:80]
            if not payload['server_name']:
                raise ValueError('Tên VPS không được trống.')
        with self.mutation_lock():
            configuration = self.store.save_config(payload)
        return {'config': configuration}

    def inventory(self, arguments):
        return Panel().inventory()

    def tasks(self, arguments):
        return {'tasks': Panel().tasks()}

    def history(self, arguments):
        return {'jobs': self.store.history()}

    def launch(self, details, operation):
        with file_lock(self.store.data / 'launch.lock'):
            if self.store.busy() or any(row['status'] == 'queued' and time.time() - row['created'] < 120 for row in self.store.history(10)):
                raise ValueError('Đang có tác vụ chạy hoặc chờ chạy.')
            job_id = self.store.new_job(details)
            try:
                path = self.store.logs / (job_id + '.log')
                with open(path, 'xb') as log:
                    os.chmod(path, 0o600)
                    subprocess.Popen([sys.executable, '-u', str(self.store.root / 'cli.py'), operation, job_id], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            except Exception:
                self.store.update_job(job_id, 'failed', error='Không khởi động được worker.')
                raise
        return {'job_id': job_id, 'queued': True}

    def start_backup(self, arguments):
        kind, target = arguments.get('kind', ''), str(arguments.get('target', '')).strip()
        if kind not in {'site', 'database', 'path'} or not target or len(target) > 4096:
            raise ValueError('Nguồn backup không hợp lệ.')
        keep = int(arguments.get('keep', 3))
        if not 1 <= keep <= 10000:
            raise ValueError('Số bản giữ lại phải từ 1 đến 10000.')
        exclusions = arguments.get('exclusions', [])
        if isinstance(exclusions, str):
            exclusions = exclusions.splitlines()
        exclusions = validate_exclusions(exclusions)
        source = Panel().source(kind, target)
        if kind != 'database':
            validate_source(source, self.store.root)
        configuration = self.store.config()
        if not configuration['configured']:
            raise ValueError('Chưa xác nhận thư mục lưu trên Shared drive.')
        return self.launch({'kind': kind, 'target': target, 'keep': keep, 'exclusions': exclusions, 'destination': configuration['folder_id'], 'origin': 'manual'}, 'backup')

    def start_download(self, arguments):
        original = self.store.job(str(arguments.get('job_id', '')))
        if original['status'] != 'success' or not original['details'].get('remote') or not original['details'].get('verified'):
            raise ValueError('Chỉ tải lại bản backup đã upload và xác minh thành công.')
        return self.launch({'kind': 'download', 'target': original['details'].get('target', ''), 'backup_id': original['id']}, 'download')

    def read_log(self, arguments):
        job_id = str(arguments.get('job_id', ''))
        self.store.job(job_id)
        path = self.store.logs / (job_id + '.log')
        if not path.exists():
            return {'log': 'Tác vụ Cron ghi log trong aaPanel Cron Job; xem chi tiết ở lịch sử plugin.'}
        with open(path, 'rb') as log:
            log.seek(max(0, path.stat().st_size - 32768))
            text = log.read().decode('utf-8', errors='replace')
        return {'log': text}

    def save_github_token(self, arguments):
        token = str(arguments.get('token', '')).strip()
        if not re.fullmatch(r'[A-Za-z0-9_]{20,255}', token):
            raise ValueError('GitHub token không hợp lệ.')
        with self.mutation_lock():
            self.store.save_secret('github_token', token)
        return {'saved': True}

    def check_update(self, arguments):
        release = latest_release(self.store)
        return {key: value for key, value in release.items() if key != 'assets'}

    def update(self, arguments):
        return update(self.store, str(arguments.get('version', '')))

    def rollback(self, arguments):
        return rollback(self.store)

    def repair_integration(self, arguments):
        with self.mutation_lock():
            return register(self.store, '/www/server/panel')
