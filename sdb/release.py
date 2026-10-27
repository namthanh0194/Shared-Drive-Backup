import ast
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from sdb import PLUGIN, REPOSITORY, VERSION
from sdb.integration import replace_file
from sdb.storage import atomic_json, file_lock, sync_directory

TOP_FILES = {
    'info.json', 'shared_drive_backup_main.py', 'cli.py', 'index.html', 'ui.js', 'ui.css',
    'icon.png', 'icon.svg', 'install.sh', 'upgrade.sh', 'repair.sh', 'uninstall.sh',
    'README.md', 'CHANGELOG.md', 'requirements.txt', '.ignore',
}
MAX_ARCHIVE = 30 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
UPDATE_INTERVAL = 6 * 60 * 60
UPDATE_RETRY_INTERVAL = 15 * 60
REQUIRED_FILES = {
    'info.json', 'shared_drive_backup_main.py', 'cli.py', 'index.html', 'ui.js', 'ui.css',
    'install.sh', 'upgrade.sh', 'repair.sh', 'uninstall.sh', 'requirements.txt', 'tools/install_plugin.py', 'tools/recover_deployment.py',
    'sdb/__init__.py', 'sdb/api.py', 'sdb/drive.py', 'sdb/integration.py', 'sdb/panel.py',
    'sdb/release.py', 'sdb/storage.py', 'sdb/worker.py', 'sdb/release_key.pub',
}


def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Version không hợp lệ.')
    return tuple(int(part) for part in value.split('.'))


def managed_path(value):
    parts = PurePosixPath(value).parts
    if not parts or any(part in {'', '.', '..'} for part in parts) or '\\' in value or value.startswith('/'):
        return False
    if len(parts) == 1:
        return parts[0] in TOP_FILES
    if parts[0] == 'sdb':
        return len(parts) == 2 and (parts[1].endswith('.py') or parts[1] == 'release_key.pub')
    if parts[0] == 'tools':
        return len(parts) == 2 and parts[1].endswith('.py')
    return False


def validate_archive(archive):
    entries = archive.infolist()
    if len(entries) > 150:
        raise ValueError('Gói cập nhật có quá nhiều file.')
    total = 0
    paths = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if entry.is_dir():
            if path.as_posix().rstrip('/') not in {PLUGIN, PLUGIN + '/sdb', PLUGIN + '/tools'}:
                raise ValueError('Thư mục không được phép trong release.')
            continue
        parts = path.parts
        if not parts or parts[0] != PLUGIN or len(parts) < 2 or not managed_path('/'.join(parts[1:])):
            raise ValueError('Đường dẫn không được phép trong release.')
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
            raise ValueError('Không chấp nhận symlink hoặc file đặc biệt trong release.')
        if entry.filename in paths:
            raise ValueError('Release có đường dẫn trùng nhau.')
        paths.add(entry.filename)
        total += entry.file_size
        if total > MAX_EXPANDED or entry.flag_bits & 1:
            raise ValueError('Release quá lớn hoặc được mã hóa.')
    required = {PLUGIN + '/' + filename for filename in REQUIRED_FILES}
    if not required.issubset(paths):
        raise ValueError('Release thiếu file bắt buộc.')


def verify_signature(payload, signature, public_key):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError):
        raise ValueError('Chữ ký release không hợp lệ. Không cài đặt.') from None


def allowed_url(url):
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in {None, 443} and (host in {'api.github.com', 'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com'} or host.endswith('.githubusercontent.com'))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not allowed_url(new_url):
            raise ValueError('GitHub chuyển hướng tới host không được phép.')
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected and urllib.parse.urlparse(request.full_url).hostname != urllib.parse.urlparse(new_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


def github_get(url, token=None, binary=False, limit=MAX_ARCHIVE):
    if not allowed_url(url):
        raise ValueError('URL GitHub không được phép.')
    headers = {'User-Agent': 'aaPanel-SharedDriveBackup/' + VERSION, 'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token and urllib.parse.urlparse(url).hostname == 'api.github.com':
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=45) as response:
        if int(response.headers.get('Content-Length', '0')) > limit:
            raise ValueError('Phản hồi GitHub vượt giới hạn dung lượng.')
        payload = response.read(limit + 1)
        if len(payload) > limit:
            raise ValueError('Phản hồi GitHub vượt giới hạn dung lượng.')
        return payload


def latest_release(store):
    try:
        document = json.loads(github_get(f'https://api.github.com/repos/{REPOSITORY}/releases/latest', limit=1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError('Chưa có GitHub Release công khai. Tag Git chưa đủ để cập nhật.') from None
        if error.code in {403, 429}:
            raise ValueError('GitHub đang giới hạn lượt truy cập. Plugin sẽ thử lại sau.') from None
        raise ValueError(f'GitHub API trả về HTTP {error.code}.') from None
    version = document.get('tag_name', '').removeprefix('v')
    version_tuple(version)
    if document.get('draft') or document.get('prerelease'):
        raise ValueError('Không dùng release draft/prerelease.')
    assets = {item['name']: item for item in document.get('assets', [])}
    name = f'{PLUGIN}-{version}.zip'
    if name not in assets or name + '.sig' not in assets:
        raise ValueError('Release thiếu gói cài hoặc chữ ký.')
    return {'version': version, 'available': version_tuple(version) > version_tuple(VERSION), 'body': str(document.get('body', ''))[:5000], 'url': document.get('html_url', ''), 'assets': assets, 'asset_name': name}

def check_for_updates(store, force=False):
    path = store.data / 'update-cache.json'
    with file_lock(store.data / 'update-check.lock', blocking=True):
        now = int(time.time())
        try:
            cached = json.loads(path.read_text())
            valid = isinstance(cached, dict) and cached.get('repository') == REPOSITORY and 0 <= now - cached['checked_at'] < (UPDATE_RETRY_INTERVAL if cached.get('error') else UPDATE_INTERVAL)
            if valid and cached.get('version') is not None:
                version_tuple(cached['version'])
        except (OSError, ValueError, KeyError, TypeError):
            cached, valid = None, False
        if force or not valid:
            cached = {'repository': REPOSITORY, 'checked_at': now, 'version': None, 'body': '', 'url': '', 'error': None}
            try:
                release = latest_release(store)
                cached.update({key: release[key] for key in ('version', 'body', 'url')})
            except ValueError as error:
                cached['error'] = str(error)
            except (urllib.error.URLError, OSError):
                cached['error'] = 'Không kết nối được GitHub. Kiểm tra mạng; plugin sẽ thử lại sau.'
            atomic_json(path, cached)
        interval = UPDATE_RETRY_INTERVAL if cached.get('error') else UPDATE_INTERVAL
        return {**cached, 'installed_version': VERSION, 'available': not cached.get('error') and cached.get('version') is not None and version_tuple(cached['version']) > version_tuple(VERSION), 'next_check_at': cached['checked_at'] + interval}


def code_files(root):
    root = Path(root)
    return {str(path.relative_to(root)): path for path in root.rglob('*') if path.is_file() and not path.is_symlink() and managed_path(str(path.relative_to(root)))}


def validate_code(root, expected_version=None):
    root = Path(root)
    if not REQUIRED_FILES.issubset(code_files(root)):
        raise ValueError('Gói cài thiếu module runtime/installer bắt buộc.')
    for relative, path in code_files(root).items():
        if path.suffix == '.py':
            compile(path.read_text(), relative, 'exec')
    metadata = json.loads((root / 'info.json').read_text())
    if metadata.get('name') != PLUGIN:
        raise ValueError('Gói cài không đúng plugin.')
    version_tuple(metadata['versions'])
    if expected_version and metadata['versions'] != expected_version:
        raise ValueError('Version trong gói cài không trùng release.')
    syntax = ast.parse((root / 'sdb/__init__.py').read_text())
    versions = [node.value.value for node in syntax.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets) and isinstance(node.value, ast.Constant)]
    if versions != [metadata['versions']]:
        raise ValueError('Version metadata và mã nguồn không khớp.')


def deploy(source, destination, panel_path='/www/server/panel', lock=True):
    from contextlib import nullcontext
    from sdb.storage import Store

    source, destination = Path(source).resolve(), Path(destination).resolve()
    validate_code(source)
    store = Store(destination)
    with file_lock(store.data / 'operation.lock') if lock else nullcontext():
        recovery_path = store.data / 'deployment-recovery.json'
        if recovery_path.exists():
            raise ValueError('Có deployment chưa phục hồi. Chạy tools/recover_deployment.py trước khi cập nhật tiếp.')
        incoming = code_files(source)
        previous = code_files(destination)
        rollback = store.data / 'releases' / 'previous'
        staging = Path(tempfile.mkdtemp(dir=store.tmp, prefix='deploy-'))
        snapshot = Path(tempfile.mkdtemp(dir=store.tmp, prefix='rollback-'))
        prepared_rollback = store.tmp / ('prepared-' + uuid.uuid4().hex)
        prior_rollback = store.data / 'releases' / ('prior-' + uuid.uuid4().hex)
        mutations_started = False
        restored = False
        committed = False
        try:
            for relative, path in incoming.items():
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            for relative, path in previous.items():
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            if previous:
                shutil.copytree(snapshot, prepared_rollback)
                for file in prepared_rollback.rglob('*'):
                    if file.is_file():
                        with open(file, 'rb') as saved:
                            os.fsync(saved.fileno())
                for directory in sorted([path for path in prepared_rollback.rglob('*') if path.is_dir()], reverse=True):
                    sync_directory(directory)
                sync_directory(prepared_rollback)
                sync_directory(prepared_rollback.parent)
            try:
                atomic_json(recovery_path, {'snapshot': str(snapshot), 'previous_files': list(previous), 'incoming_files': list(incoming), 'prior_rollback': str(prior_rollback), 'panel_path': str(panel_path)})
                mutations_started = True
                for relative in incoming:
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    payload = (staging / relative).read_bytes()
                    if relative == 'index.html':
                        payload = payload.replace(b'/* SHARED_DRIVE_STYLE */', (staging / 'ui.css').read_bytes()).replace(b'/* SHARED_DRIVE_SCRIPT */', (staging / 'ui.js').read_bytes())
                    replace_file(target, payload)
                    target.chmod(0o755 if target.suffix == '.sh' else 0o644)
                for relative in set(previous) - set(incoming):
                    (destination / relative).unlink()
                validate_code(destination)
                result = subprocess.run([sys.executable, '-B', str(destination / 'cli.py'), 'register', str(panel_path)], check=True, capture_output=True, text=True, timeout=60)
                integration = json.loads(result.stdout)
                if previous:
                    if rollback.exists():
                        os.replace(rollback, prior_rollback)
                    os.replace(prepared_rollback, rollback)
                    sync_directory(rollback.parent)
                committed = True
            except BaseException:
                if not previous and (store.data / 'integration.json').exists():
                    from sdb.integration import unregister
                    unregister(store, panel_path)
                for relative in set(incoming) - set(previous):
                    (destination / relative).unlink(missing_ok=True)
                for relative in previous:
                    replace_file(destination / relative, (snapshot / relative).read_bytes())
                if prior_rollback.exists():
                    if rollback.exists():
                        shutil.rmtree(rollback)
                    os.replace(prior_rollback, rollback)
                if previous:
                    subprocess.run([sys.executable, '-B', str(destination / 'cli.py'), 'register', str(panel_path)], check=True, capture_output=True, text=True, timeout=60)
                restored = True
                raise
            if prior_rollback.exists():
                shutil.rmtree(prior_rollback, ignore_errors=True)
            return {'version': json.loads((destination / 'info.json').read_text())['versions'], **integration}
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if not mutations_started or committed or restored:
                recovery_path.unlink(missing_ok=True)
                shutil.rmtree(snapshot, ignore_errors=True)
                shutil.rmtree(prepared_rollback, ignore_errors=True)


def update(store, expected_version):
    with file_lock(store.data / 'operation.lock'):
        release = latest_release(store)
        if release['version'] != expected_version or not release['available']:
            raise ValueError('Release đã thay đổi hoặc không mới hơn. Hãy kiểm tra lại.')
        name = release['asset_name']
        asset = release['assets'][name]
        signature_asset = release['assets'][name + '.sig']
        prefix = f'https://api.github.com/repos/{REPOSITORY}/releases/assets/'
        if not str(asset.get('url', '')).startswith(prefix) or not str(signature_asset.get('url', '')).startswith(prefix):
            raise ValueError('Asset không thuộc repo đã ghim.')
        payload = github_get(asset['url'], binary=True)
        signature = github_get(signature_asset['url'], binary=True, limit=1024)
        trusted_key = bytes.fromhex((store.root / 'sdb/release_key.pub').read_text().strip())
        verify_signature(payload, signature, trusted_key)
        stage = Path(tempfile.mkdtemp(dir=store.tmp, prefix='release-'))
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                validate_archive(archive)
                archive.extractall(stage)
            source = stage / PLUGIN
            validate_code(source, expected_version)
            if bytes.fromhex((source / 'sdb/release_key.pub').read_text().strip()) != trusted_key:
                raise ValueError('Release không được tự thay khóa ký tin cậy.')
            return deploy(source, store.root, lock=False)
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def rollback(store):
    source = store.data / 'releases' / 'previous'
    if not (source / 'info.json').exists():
        raise ValueError('Chưa có phiên bản trước để rollback.')
    stage = Path(tempfile.mkdtemp(dir=store.tmp, prefix='restore-'))
    try:
        shutil.copytree(source, stage / PLUGIN)
        return deploy(stage / PLUGIN, store.root)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
