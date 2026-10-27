import os
import pwd
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from sdb.drive import BackupMissing, digests, make_client
from sdb.native import check_identity, register_backup, remove_backup, site
from sdb.panel import Panel
from sdb.storage import assert_recovery_clear, atomic_json, file_lock, safe_error, sync_directory, validate_source
from sdb.worker import archive_directory


def backup_for_site(store, panel, backup_id, site_id, mapped=True):
    original = store.job(str(backup_id))
    details = original['details']
    current = site(panel, site_id)
    if original['status'] != 'success' or details.get('kind') != 'site' or not details.get('verified') or not details.get('remote') or details.get('target') != current['name']:
        raise ValueError('Backup không phải bản website đã xác minh của website này.')
    if details.get('site') or mapped:
        check_identity(current, details.get('site'))
    configuration = store.config()
    if not configuration.get('configured') or details.get('destination') != configuration['folder_id']:
        raise ValueError('Nơi lưu đã thay đổi; chọn lại đúng nơi nhận của bản backup trước.')
    return original, current


def map_backup(store, panel, backup_id, site_id, confirmation):
    with file_lock(store.data / 'operation.lock'):
        assert_recovery_clear(store)
        original, current = backup_for_site(store, panel, backup_id, site_id, mapped=False)
        if confirmation != current['name'] or original['details'].get('site'):
            raise ValueError('Nhập đúng tên website để liên kết bản backup cũ chưa có ID.')
        store.update_job(original['id'], 'success', site=current, archive_root=Path(current['path']).name, legacy_mapping=True)
    return {'mapped': True}


def catalog(store, panel, site_id, offset=0, client=None):
    current = site(panel, site_id)
    offset = int(offset)
    if offset < 0 or offset > 1000000:
        raise ValueError('Trang lịch sử không hợp lệ.')
    jobs = store.site_backups(current['name'], offset, 21)
    configuration = store.config()
    client = client or make_client(store)
    rows = []
    for job in jobs[:20]:
        details = job['details']
        row = {'id': job['id'], 'created': job['created'], 'name': details['remote'].get('name', ''), 'size': details['remote'].get('size', 0), 'origin': details.get('origin', 'manual'), 'mapped': bool(details.get('site')), 'partial': bool(details.get('exclusions')), 'availability': 'unavailable'}
        try:
            backup_for_site(store, panel, job['id'], current['id'], mapped=False)
            client.backup_metadata(configuration, details['remote'], details['task'])
            row['availability'] = 'available'
        except BackupMissing as error:
            row.update(availability='missing', error=safe_error(error))
        except Exception as error:
            row['error'] = safe_error(error)
        rows.append(row)
    return {'site': current, 'backups': rows, 'offset': offset, 'next_offset': offset + 20 if len(jobs) > 20 else None}


def sync_native(store, panel, site_id, offset=0, client=None):
    with file_lock(store.data / 'operation.lock'):
        assert_recovery_clear(store)
        result = catalog(store, panel, site_id, offset, client)
        added = removed = 0
        for row in result['backups']:
            if row['availability'] == 'available' and row['mapped']:
                register_backup(store, panel, store.job(row['id']))
                added += 1
            elif row['availability'] == 'missing':
                remove_backup(store, panel, row['id'])
                removed += 1
        return {'registered': added, 'missing_removed': removed, 'next_offset': result['next_offset']}


def target_directory(store, panel, current, replace=False):
    raw = Path(current['path'])
    source = validate_source(str(raw), store.root)
    forbidden = ['/etc', '/usr', '/proc', '/sys', '/dev', '/run', '/boot', '/var/lib', '/var/log', '/root/.ssh', '/root/.codex']
    if source in [Path(value) for value in ['/', '/www', '/www/wwwroot', '/root', '/home', '/var', '/var/www', '/tmp']] or any(source == Path(value) or Path(value) in source.parents for value in forbidden):
        raise ValueError('Không khôi phục vào đường dẫn hệ thống.')
    panel_root = panel.root.resolve()
    if source == panel_root or panel_root in source.parents or source in panel_root.parents:
        raise ValueError('Đường dẫn website chứa hoặc nằm trong core aaPanel.')
    with panel.connect() as connection:
        others = connection.execute('SELECT id,path FROM sites WHERE id<>?', (current['id'],)).fetchall()
    for other in others:
        other_path = Path(other['path']).resolve()
        if source == other_path or source in other_path.parents or other_path in source.parents:
            raise ValueError('Website dùng chung hoặc lồng thư mục website khác; chỉ khôi phục thủ công.')
    if replace and (raw != source or os.path.ismount(source) or str(current['status']) != '0'):
        raise ValueError('Cần dừng website trong aaPanel; không thay thế webroot symlink hoặc mount point.')
    if replace:
        for parent in source.parents:
            metadata = parent.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise ValueError('Thư mục cha webroot phải thuộc root và không cho group/other ghi; dùng giải nén kiểm tra trước.')
        device = source.stat().st_dev
        for folder, directories, filenames in os.walk(source, followlinks=False):
            for name in directories:
                path = Path(folder) / name
                if not path.is_symlink() and (os.path.ismount(path) or path.stat().st_dev != device):
                    raise ValueError('Webroot chứa mount point; không thay thế toàn bộ website tự động.')
    return source


def prepare_restore(store, panel, arguments):
    if (store.data / 'restore-recovery.json').exists():
        raise ValueError('Có journal khôi phục chưa hoàn tất; kiểm tra trước khi chạy tiếp.')
    mode = arguments.get('mode', 'preview')
    if mode not in {'preview', 'replace'}:
        raise ValueError('Chế độ khôi phục không hợp lệ.')
    original, current = backup_for_site(store, panel, arguments.get('backup_id', ''), arguments.get('site_id', ''))
    source = target_directory(store, panel, current, replace=mode == 'replace')
    identity = source.stat()
    if mode == 'replace' and (arguments.get('confirmation') != current['name'] or original['details'].get('exclusions')):
        raise ValueError('Thay thế cần nhập đúng tên website và backup không có exclusions.')
    return {'kind': 'restore', 'target': current['name'], 'site': current, 'source_identity': [identity.st_dev, identity.st_ino], 'backup_id': original['id'], 'mode': mode, 'confirmation': arguments.get('confirmation', ''), 'destination': original['details']['destination']}


def inspect_archive(filename, root_name, max_bytes):
    if not root_name or '/' in root_name or '\\' in root_name or root_name in {'.', '..'}:
        raise ValueError('Thư mục gốc archive không hợp lệ.')
    entries = []
    paths = {}
    expanded = 0
    try:
        with tarfile.open(filename, 'r:gz') as archive:
            for entry in archive:
                name = entry.name.rstrip('/')
                parts = PurePosixPath(name).parts
                if not parts or parts[0] != root_name or name.startswith('/') or '\\' in name or any(part in {'.', '..', ''} for part in name.split('/')) or any(ord(character) < 32 for character in name) or name in paths:
                    raise ValueError('Archive chứa đường dẫn không an toàn, trùng lặp hoặc sai website.')
                if not (entry.isfile() or entry.isdir()) or entry.issparse() or entry.size < 0 or len(name) > 4096:
                    raise ValueError('Archive chứa link, thiết bị hoặc kiểu file không được phép.')
                if len(parts) == 1 and not entry.isdir():
                    raise ValueError('Gốc archive phải là thư mục.')
                paths[name] = entry.isdir()
                expanded += entry.size
                entries.append(entry)
                if expanded > max_bytes or len(entries) > 100000:
                    raise ValueError('Nội dung giải nén vượt giới hạn dung lượng/số file.')
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError('Không đọc được archive tar.gz: ' + safe_error(error)) from None
    if not entries or not any(entry.isfile() for entry in entries):
        raise ValueError('Archive rỗng hoặc không có file website.')
    for name in paths:
        for parent in PurePosixPath(name).parents:
            if str(parent) in paths and not paths[str(parent)]:
                raise ValueError('File trong archive bị dùng làm thư mục.')
    return entries, expanded


def extract_archive(filename, destination, root_name, max_bytes, reserve_bytes, progress=None):
    entries, expanded = inspect_archive(filename, root_name, max_bytes)
    destination = Path(destination)
    if expanded + len(entries) * 4096 + reserve_bytes > shutil.disk_usage(destination.parent).free:
        raise ValueError('Không đủ đĩa trống để giải nén an toàn.')
    destination.mkdir(mode=0o700)
    try:
        with tarfile.open(filename, 'r:gz') as archive:
            for index, entry in enumerate(entries):
                target = destination.joinpath(*PurePosixPath(entry.name.rstrip('/')).parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if entry.isdir():
                    target.mkdir(exist_ok=True, mode=0o700)
                else:
                    with archive.extractfile(entry) as source, open(target, 'xb') as output:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            if shutil.disk_usage(destination).free < reserve_bytes + len(chunk):
                                raise ValueError('Dừng giải nén để giữ dung lượng đĩa dự phòng.')
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if target.stat().st_size != entry.size:
                        raise ValueError('Archive bị cắt ngắn trong khi giải nén.')
                    target.chmod(0o600)
                if progress and (index % 100 == 0 or index == len(entries) - 1):
                    progress(round((index + 1) * 100 / len(entries)))
        return destination / root_name
    except BaseException:
        shutil.rmtree(destination)
        raise


def activate(store, panel, job_id, current, source, staged, work, identity):
    previous = staged.parent.parent / 'previous'
    journal_path = store.data / 'restore-recovery.json'
    journal = {'job_id': job_id, 'site': current, 'source': str(source), 'staged': str(staged), 'previous': str(previous), 'phase': 'prepared'}
    account = pwd.getpwnam('www')
    for folder, directories, filenames in os.walk(staged):
        os.chown(folder, account.pw_uid, account.pw_gid)
        os.chmod(folder, 0o755)
        for filename in filenames:
            path = Path(folder) / filename
            os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o644)
    fresh = site(panel, current['id'])
    check_identity(fresh, current)
    target_directory(store, panel, fresh, replace=True)
    snapshot = work / 'before.tar.gz'
    configuration = store.config()
    archive_directory(source, snapshot, [], int(configuration['max_archive_gb']) * 1024 ** 3, int(configuration['min_free_mb']) * 1024 ** 2)
    snapshot.chmod(0o600)
    _, snapshot_sha256 = digests(snapshot)
    store.update_job(job_id, 'running', phase='activate', snapshot=str(snapshot), snapshot_sha256=snapshot_sha256, previous_path=str(previous))
    check_identity(site(panel, current['id']), current)
    target_directory(store, panel, site(panel, current['id']), replace=True)
    fresh_stat = source.stat()
    if [fresh_stat.st_dev, fresh_stat.st_ino] != identity:
        raise ValueError('Webroot đã được thay thế trong khi khôi phục; không ghi đè.')
    atomic_json(journal_path, journal)
    moved = False
    try:
        os.replace(source, previous)
        moved = True
        sync_directory(source.parent)
        sync_directory(previous.parent)
        journal['phase'] = 'original_moved'
        atomic_json(journal_path, journal)
        os.replace(staged, source)
        sync_directory(source.parent)
        sync_directory(staged.parent)
        journal['phase'] = 'installed'
        atomic_json(journal_path, journal)
    except Exception:
        if moved and not source.exists():
            os.replace(previous, source)
            sync_directory(source.parent)
        if source.exists() and not previous.exists():
            journal_path.unlink(missing_ok=True)
        raise
    return previous


def run_restore(store, job_id, panel=None):
    panel = panel or Panel()
    started = False
    initial = store.job(job_id)
    if initial['status'] != 'queued' or initial['details'].get('kind') != 'restore':
        return False
    try:
        with file_lock(store.data / 'operation.lock'):
            assert_recovery_clear(store)
            job = store.job(job_id)
            if job['status'] != 'queued' or job['details'].get('kind') != 'restore':
                return False
            details = job['details']
            request = prepare_restore(store, panel, {**details, 'site_id': details['site']['id']})
            check_identity(request['site'], details['site'])
            if request['source_identity'] != details.get('source_identity'):
                raise ValueError('Webroot đã được thay thế sau khi tạo tác vụ; không ghi đè.')
            if (store.data / 'deployment-recovery.json').exists():
                raise ValueError('Deployment chưa phục hồi; không chạy restore.')
            configuration = store.config()
            original, current = backup_for_site(store, panel, details['backup_id'], details['site']['id'])
            backup = original['details']
            remote = backup['remote']
            source = target_directory(store, panel, current, replace=details['mode'] == 'replace')
            root_name = backup.get('archive_root')
            if root_name != source.name:
                raise ValueError('Gốc archive không khớp đường dẫn website hiện tại.')
            store.update_job(job_id, 'running', phase='preflight', progress=0)
            started = True
            client = make_client(store)
            metadata = client.backup_metadata(configuration, remote, backup['task'])
            limit = int(configuration['max_archive_gb']) * 1024 ** 3
            reserve = int(configuration['min_free_mb']) * 1024 ** 2
            size = int(metadata['size'])
            if size <= 0 or size > limit or size + reserve > shutil.disk_usage(store.data).free:
                raise ValueError('Archive vượt giới hạn hoặc thiếu đĩa trống để tải.')
            parent = store.data / 'restores'
            parent.mkdir(mode=0o700, exist_ok=True)
            work = parent / job_id
            work.mkdir(mode=0o700)
            download = work / 'download.tar.gz'
            store.update_job(job_id, 'running', phase='download', download_path=str(download), size=size)
            client.download(remote['id'], download, remote['md5Checksum'], remote['sha256'], configuration, backup['task'], remote['parent_id'], max_bytes=limit, reserve_bytes=reserve, progress=lambda value: store.update_job(job_id, 'running', progress=value))
            download.chmod(0o600)
            if download.stat().st_size != size or digests(download) != (remote['md5Checksum'], remote['sha256']):
                raise ValueError('Checksum/kích thước bản tải về không khớp.')
            store.update_job(job_id, 'running', phase='validate', progress=0)
            inspect_archive(download, root_name, limit)
            if details['mode'] == 'replace':
                staging = source.parent / ('.sdb-restore-' + job_id)
                staging.mkdir(mode=0o700)
            else:
                staging = work
            store.update_job(job_id, 'running', phase='extract', progress=0)
            staged = extract_archive(download, staging / 'files', root_name, limit, reserve, lambda value: store.update_job(job_id, 'running', progress=value))
            if details['mode'] == 'replace':
                store.update_job(job_id, 'running', phase='snapshot', progress=0)
                activate(store, panel, job_id, current, source, staged, work, details['source_identity'])
                restored = source
            else:
                restored = staged
            store.update_job(job_id, 'success', phase='complete', progress=100, restore_path=str(restored), verified=True)
            if details['mode'] == 'replace':
                (store.data / 'restore-recovery.json').unlink()
                sync_directory(store.data)
            print('Khôi phục hoàn tất: ' + str(restored), flush=True)
            return True
    except Exception as error:
        if not started and store.job(job_id)['status'] != 'queued':
            return False
        store.update_job(job_id, 'failed', error=safe_error(error), recovery_required=(store.data / 'restore-recovery.json').exists())
        print('Khôi phục thất bại: ' + safe_error(error), flush=True)
        return False
