import contextlib
import fcntl
import json
import os
import re
import socket
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(descriptor, 'w') as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def file_lock(path, blocking=False, timeout=None, on_wait=None):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking and deadline is None else fcntl.LOCK_NB))
                break
            except BlockingIOError:
                if deadline is None:
                    raise RuntimeError('Một tác vụ backup/cập nhật đang chạy. Vui lòng chờ.') from None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'Hết thời gian chờ khóa backup ({timeout:g} giây); chưa bắt đầu tác vụ.') from None
                if on_wait:
                    on_wait()
                    on_wait = None
                time.sleep(min(1, remaining))
        yield
    finally:
        os.close(descriptor)

def assert_recovery_clear(store):
    for name in ['restore-recovery.json', 'deployment-recovery.json', 'database-recovery.json']:
        if (store.data / name).exists():
            raise ValueError('Có journal ' + name.split('-')[0] + ' chưa hoàn tất: ' + name + '. Kiểm tra phục hồi trước khi chạy tiếp.')


def validate_source(value, plugin_root):
    try:
        source = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError('Nguồn backup không tồn tại hoặc không truy cập được.') from None
    if not source.is_dir():
        raise ValueError('Nguồn backup phải là thư mục tồn tại.')
    forbidden = [Path(name) for name in ['/proc', '/sys', '/dev', '/run', '/boot']]
    own_root = Path(plugin_root).resolve()
    if source == Path('/') or any(source == entry or entry in source.parents for entry in forbidden):
        raise ValueError('Không cho phép backup đường dẫn hệ thống này.')
    if source == own_root or own_root in source.parents or source in own_root.parents:
        raise ValueError('Nguồn backup không được chứa mã hoặc secrets của plugin.')
    return source


def safe_error(error):
    message = str(error)
    message = re.sub(r'(?i)(Bearer\s+|ya29\.|gh[pousr]_|github_pat_)[A-Za-z0-9_.-]+', '[REDACTED]', message)
    message = re.sub(r'(?i)(access_token|refresh_token|client_secret|private_key|password)([\s\"\x27:=]+)[^\s,}]+', r'\1=[REDACTED]', message)
    if '<HttpError' in message or 'https://www.googleapis.com' in message:
        status = getattr(getattr(error, 'resp', None), 'status', 'unknown')
        return f'Google Drive API trả về HTTP {status}. Kiểm tra quyền, quota và kết nối.'
    return message[:1200]


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = self.root / 'data'
        self.tmp = self.root / 'tmp'
        self.logs = self.root / 'logs'
        for directory in [self.data, self.tmp, self.logs, self.data / 'downloads', self.data / 'releases']:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        with file_lock(self.data / 'config.lock', blocking=True):
            if not (self.data / 'config.json').exists():
                atomic_json(self.data / 'config.json', {
                    'server_id': uuid.uuid4().hex,
                    'server_name': socket.gethostname()[:80],
                    'drive_id': '', 'drive_name': '', 'folder_id': '', 'folder_name': '',
                    'prune_enabled': False, 'min_free_mb': 1024, 'max_archive_gb': 16,
                    'keep_local': False, 'configured': False,
                })
        database = self.data / 'history.sqlite'
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT, details TEXT)')
        database.chmod(0o600)

    def connect(self):
        connection = sqlite3.connect(self.data / 'history.sqlite', timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def config(self):
        return json.loads((self.data / 'config.json').read_text())

    def save_config(self, changes):
        allowed = {'server_name', 'drive_id', 'drive_name', 'folder_id', 'folder_name', 'prune_enabled', 'min_free_mb', 'max_archive_gb', 'keep_local', 'configured'}
        if not set(changes).issubset(allowed):
            raise ValueError('Thiết lập không hợp lệ.')
        with file_lock(self.data / 'config.lock', blocking=True):
            configuration = self.config()
            configuration.update(changes)
            atomic_json(self.data / 'config.json', configuration)
        marker = self.root / 'config.conf'
        if configuration['configured']:
            atomic_json(marker, {'configured': True})
        elif marker.exists():
            marker.unlink()
        return configuration

    def save_secret(self, name, value):
        if name not in {'credentials', 'github_token'}:
            raise ValueError('Secret name không hợp lệ.')
        atomic_json(self.data / (name + '.json'), value)

    def secret(self, name):
        if name not in {'credentials', 'github_token'}:
            raise ValueError('Secret name không hợp lệ.')
        path = self.data / (name + '.json')
        return json.loads(path.read_text()) if path.exists() else None

    def job(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Mã tác vụ không hợp lệ.')
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise ValueError('Không tìm thấy tác vụ.')
        return {**dict(row), 'details': json.loads(row['details'])}

    def history(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
        return [{**dict(row), 'details': json.loads(row['details'])} for row in rows]

    def new_job(self, details):
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.connect() as connection:
            connection.execute('INSERT INTO jobs (id,created,updated,status,details) VALUES (?,?,?,?,?)', (job_id, now, now, 'queued', json.dumps(details)))
        return job_id

    def site_backups(self, site_name, offset=0, limit=21):
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM jobs WHERE status='success' AND json_extract(details,'$.kind')='site' AND json_extract(details,'$.target')=? AND json_extract(details,'$.verified')=1 AND json_type(details,'$.remote')='object' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?", (site_name, limit, offset)).fetchall()
        return [{**dict(row), 'details': json.loads(row['details'])} for row in rows]

    def update_job(self, job_id, status, **details):
        with self.connect() as connection:
            row = connection.execute('SELECT details FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('Không tìm thấy tác vụ.')
            document = json.loads(row['details'])
            document.update(details)
            connection.execute('UPDATE jobs SET status=?,updated=?,details=? WHERE id=?', (status, time.time(), json.dumps(document), job_id))

    def busy(self):
        if any((self.data / name).exists() for name in ['restore-recovery.json', 'deployment-recovery.json', 'database-recovery.json']):
            return True
        try:
            with file_lock(self.data / 'operation.lock'):
                return False
        except RuntimeError:
            return True
