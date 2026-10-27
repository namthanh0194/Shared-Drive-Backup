import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sdb.drive import make_client
from sdb.panel import Panel
from sdb.storage import file_lock, safe_error, validate_source


def task_key(kind, target, folder_id, identity='manual'):
    digest = hashlib.sha256(json.dumps([kind, target, folder_id, identity], ensure_ascii=False).encode()).hexdigest()[:20]
    return kind + '-' + digest


def validate_exclusions(patterns):
    if not isinstance(patterns, list) or len(patterns) > 200:
        raise ValueError('Danh sách loại trừ không hợp lệ.')
    for pattern in patterns:
        if not isinstance(pattern, str) or len(pattern) > 500 or pattern.startswith('-') or any(ord(character) < 32 for character in pattern):
            raise ValueError('Quy tắc loại trừ không hợp lệ.')
    return [pattern for pattern in patterns if pattern]


def directory_size(source, exclusions):
    total = 0
    for current, directories, filenames in os.walk(source, followlinks=False):
        relative = str(Path(current).relative_to(source))
        directories[:] = [name for name in directories if not any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(str(Path(relative) / name), pattern) for pattern in exclusions)]
        for filename in filenames:
            path = Path(current) / filename
            relative_file = str(path.relative_to(source))
            if not any(fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(relative_file, pattern) for pattern in exclusions):
                if not path.is_symlink():
                    total += path.stat().st_size
    return total


def monitor(processes, output, max_bytes, reserve_bytes, timeout=7200):
    deadline = time.monotonic() + timeout
    try:
        while any(process.poll() is None for process in processes):
            if time.monotonic() > deadline:
                raise RuntimeError('Tác vụ nén vượt quá thời gian cho phép.')
            if output.exists() and output.stat().st_size > max_bytes:
                raise ValueError('Archive vượt giới hạn dung lượng đã cấu hình.')
            if shutil.disk_usage(output.parent).free < reserve_bytes:
                raise ValueError('Dừng backup vì dung lượng đĩa còn lại dưới ngưỡng an toàn.')
            time.sleep(0.25)
        for process in processes:
            if process.returncode != 0:
                raise RuntimeError(f'Lệnh tạo backup thất bại, exit code {process.returncode}.')
        if shutil.disk_usage(output.parent).free < reserve_bytes:
            raise ValueError('Backup hoàn tất nén nhưng đĩa trống thấp hơn ngưỡng an toàn.')
        if not output.exists() or not output.stat().st_size or output.stat().st_size > max_bytes:
            raise ValueError('Archive rỗng hoặc vượt giới hạn dung lượng.')
    except BaseException:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise


def archive_directory(source, output, exclusions, max_bytes, reserve_bytes):
    source, output = Path(source), Path(output)
    exclusions = validate_exclusions(exclusions)
    estimated = directory_size(source, exclusions)
    if estimated * 1.05 + reserve_bytes > shutil.disk_usage(output.parent).free:
        raise ValueError('Không đủ đĩa trống để tạo archive an toàn. Chọn nguồn nhỏ hơn hoặc tăng dung lượng.')
    arguments = ['tar', '--create', '--gzip', '--file', str(output), '--one-file-system']
    for pattern in exclusions:
        arguments.append('--exclude=' + pattern)
    arguments.extend(['--directory', str(source.parent), '--', source.name])
    error_path = output.with_suffix(output.suffix + '.stderr')
    try:
        with open(error_path, 'wb') as errors:
            process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=errors)
            monitor([process], output, max_bytes, reserve_bytes)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        error_path.unlink(missing_ok=True)


def mysql_archive(panel, database, output, max_bytes, reserve_bytes):
    if not re.fullmatch(r'[A-Za-z0-9_$-]{1,64}', database):
        raise ValueError('Tên database không hợp lệ.')
    binary = Path('/www/server/mysql/bin/mysqldump')
    if not binary.is_file():
        raise ValueError('Không tìm thấy mysqldump của aaPanel.')
    password = panel.mysql_password()
    escaped = password.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
    descriptor, options = tempfile.mkstemp(dir=output.parent, prefix='.mysql-', suffix='.cnf')
    error_path = output.with_suffix('.stderr')
    processes = []
    try:
        with os.fdopen(descriptor, 'w') as configuration:
            os.fchmod(configuration.fileno(), 0o600)
            configuration.write('[client]\nuser=root\npassword="' + escaped + '"\n')
        with open(error_path, 'wb') as errors, open(output, 'xb') as archive:
            dump = subprocess.Popen([str(binary), '--defaults-extra-file=' + options, '--single-transaction', '--quick', '--routines', '--events', '--triggers', '--hex-blob', '--databases', database], stdout=subprocess.PIPE, stderr=errors)
            processes.append(dump)
            compress = subprocess.Popen(['gzip', '-c'], stdin=dump.stdout, stdout=archive, stderr=errors)
            processes.append(compress)
            dump.stdout.close()
            monitor(processes, output, max_bytes, reserve_bytes)
    except BaseException:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        output.unlink(missing_ok=True)
        raise
    finally:
        Path(options).unlink(missing_ok=True)
        error_path.unlink(missing_ok=True)


def ensure_ready(store, client=None):
    if (store.data / 'restore-recovery.json').exists():
        raise ValueError('Khôi phục website chưa hoàn tất. Kiểm tra journal restore trước khi chạy tiếp.')
    if (store.data / 'deployment-recovery.json').exists():
        raise ValueError('Deployment chưa phục hồi. Chạy tools/recover_deployment.py trước khi backup.')
    configuration = store.config()
    if not configuration.get('configured') or not configuration.get('folder_id') or not store.secret('credentials'):
        raise ValueError('Chưa kết nối Google và xác nhận thư mục lưu. Không tạo backup.')
    client = client or make_client(store)
    client.folder(configuration['drive_id'], configuration['folder_id'], writable=True)
    reserve = int(configuration['min_free_mb']) * 1024 * 1024
    if shutil.disk_usage(store.tmp).free <= reserve:
        raise ValueError('Không đủ dung lượng đĩa dự phòng.')
    return configuration, client


def run_backup(store, job_id, panel=None):
    panel = panel or Panel()
    archive = None
    verified_upload = False
    try:
        with file_lock(store.data / 'operation.lock'):
            job = store.job(job_id)
            details = job['details']
            store.update_job(job_id, 'running', phase='preflight', progress=0)
            configuration, client = ensure_ready(store)
            if details.get('destination') and details['destination'] != configuration['folder_id']:
                raise ValueError('Nơi lưu đã thay đổi sau khi tạo tác vụ. Vui lòng chạy lại.')
            kind, target = details['kind'], details['target']
            source = panel.source(kind, target)
            if kind == 'site':
                from sdb.native import site_by_name
                snapshot = site_by_name(panel, target)
                if str(snapshot['path']) != str(source):
                    raise ValueError('Đường dẫn website đã thay đổi trước khi backup.')
                store.update_job(job_id, 'running', site=snapshot, archive_root=Path(source).name)
            identity = 'cron:' + details['cron_echo'] if details.get('origin') == 'cron' else 'manual'
            task = task_key(kind, target, configuration['folder_id'], identity)
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', Path(target).name)[:70] or kind
            suffix = '.sql.gz' if kind == 'database' else '.tar.gz'
            archive = store.tmp / (f"{kind}-{name}-{time.strftime('%Y%m%d-%H%M%S')}-{job_id[:10]}" + suffix)
            max_bytes = int(configuration['max_archive_gb']) * 1024 ** 3
            reserve = int(configuration['min_free_mb']) * 1024 ** 2
            exclusions = validate_exclusions(details.get('exclusions', []))
            store.update_job(job_id, 'running', phase='archive', archive=str(archive), task=task)
            if kind == 'database':
                mysql_archive(panel, source, archive, max_bytes, reserve)
            else:
                source = validate_source(source, store.root)
                if source == archive.parent or source in archive.parents:
                    raise ValueError('Nguồn backup không được chứa thư mục output.')
                archive_directory(source, archive, exclusions, max_bytes, reserve)
            archive.chmod(0o600)
            store.update_job(job_id, 'running', phase='upload', size=archive.stat().st_size)
            metadata = client.upload(archive, configuration, task, lambda percent: store.update_job(job_id, 'running', progress=percent))
            verified_upload = True
            store.update_job(job_id, 'running', phase='retention', remote=metadata, progress=100)
            warning = ''
            try:
                removed = client.prune(configuration, task, metadata['parent_id'], int(details.get('keep', 3)), metadata['id'])
            except Exception as error:
                warning = 'Backup đã xác minh nhưng dọn bản cũ thất bại: ' + safe_error(error)
                removed = 0
            if not configuration['keep_local'] and not details.get('keep_local'):
                archive.unlink()
            store.update_job(job_id, 'success', phase='complete', remote=metadata, pruned=removed, warning=warning, local_retained=archive.exists(), verified=True)
            if kind == 'site' and str(snapshot['project_type']).upper() == 'PHP':
                try:
                    from sdb.native import register_backup
                    native_id = register_backup(store, panel, store.job(job_id))
                    store.update_job(job_id, 'success', native_id=native_id)
                except Exception as error:
                    store.update_job(job_id, 'success', warning=(warning + ' Đã backup nhưng chưa ghi danh sách aaPanel: ' + safe_error(error)).strip())
            print(f'Backup thành công: {archive.name}; checksum đã xác minh.', flush=True)
            if warning:
                print(warning, flush=True)
            return True
    except Exception as error:
        message = safe_error(error)
        store.update_job(job_id, 'failed', error=message, local_retained=bool(archive and archive.exists()), verified=verified_upload)
        print('Backup thất bại: ' + message, flush=True)
        return False


def run_download(store, job_id):
    try:
        with file_lock(store.data / 'operation.lock'):
            job = store.job(job_id)
            original = store.job(job['details']['backup_id'])
            remote = original['details'].get('remote', {})
            configuration, client = ensure_ready(store)
            if original['details'].get('destination') != configuration['folder_id']:
                raise ValueError('Chọn lại nơi lưu gốc trước khi tải bản backup này.')
            size = int(remote['size'])
            if size + int(configuration['min_free_mb']) * 1024 ** 2 > shutil.disk_usage(store.data).free:
                raise ValueError('Không đủ đĩa trống để tải bản backup.')
            destination = store.data / 'downloads' / (job_id + '-' + Path(remote['name']).name)
            store.update_job(job_id, 'running', phase='download')
            client.download(remote['id'], destination, remote['md5Checksum'], remote['sha256'], configuration, original['details']['task'], remote['parent_id'])
            destination.chmod(0o600)
            store.update_job(job_id, 'success', phase='complete', verified=True, download_path=str(destination), size=size)
            print('Đã tải và xác minh: ' + str(destination), flush=True)
            return True
    except Exception as error:
        store.update_job(job_id, 'failed', error=safe_error(error))
        print('Tải xuống thất bại: ' + safe_error(error), flush=True)
        return False
