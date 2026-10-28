import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from sdb.drive import BackupMissing, digests, make_client
from sdb.panel import Panel
from sdb.storage import assert_recovery_clear, atomic_json, file_lock, safe_error, sync_directory
from sdb.worker import backup_lock, ensure_ready, mysql_archive, run_backup, task_key


def database_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_$][A-Za-z0-9_$-]{0,63}', value) or value.lower() in {'mysql', 'sys', 'information_schema', 'performance_schema'}:
        raise ValueError('Chỉ hỗ trợ tên database ứng dụng MySQL local, không gồm database hệ thống.')
    return value


def selection(value, allowed):
    if not isinstance(value, list) or not value or len(value) > 500 or any(not isinstance(name, str) for name in value) or len(value) != len(set(value)) or not set(value).issubset(allowed):
        raise ValueError('Danh sách database rỗng, trùng lặp hoặc không thuộc nguồn đã chọn (tối đa 500).')
    return sorted(value)


def prepare_backup(store, panel, names, keep, origin='manual', cron_echo=''):
    assert_recovery_clear(store)
    current = inventory(store, panel)
    allowed = {row['name'] for row in current['databases']}
    names = selection(sorted(allowed) if names == 'ALL' else names, allowed)
    for name in names:
        database_name(name)
    keep = int(keep)
    if not 1 <= keep <= 10000 or current['stale_links']:
        raise ValueError('Số bản giữ hoặc liên kết database không hợp lệ; xác nhận lại liên kết trước.')
    configuration = store.config()
    if not configuration.get('configured'):
        raise ValueError('Chưa xác nhận nơi lưu trên Shared Drive.')
    return {'kind': 'database_set', 'target': 'MySQL (' + str(len(names)) + ')', 'database_names': names, 'database_identities': [panel.database_identity(name) for name in names], 'links': current['links'], 'keep': keep, 'origin': origin, 'cron_echo': cron_echo, 'destination': configuration['folder_id'], 'complete': False, 'retained': True}


def inventory(store, panel):
    result = panel.inventory()
    result['sites'] = [panel.site_identity(row['name']) for row in result['sites']]
    result['databases'] = [panel.database_identity(row['name']) for row in result['databases'] if row['name'].lower() not in {'mysql', 'sys', 'information_schema', 'performance_schema'}]
    path = store.data / 'database-links.json'
    saved = json.loads(path.read_text()) if path.exists() else {}
    current_sites = {row['name']: row for row in result['sites']}
    current_databases = {row['name']: row for row in result['databases']}
    links, stale = {}, []
    for name, link in saved.items():
        if current_sites.get(name) != link['site'] or any(current_databases.get(row['name']) != row for row in link['databases']):
            stale.append(name)
        else:
            links[name] = [row['name'] for row in link['databases']]
    return {**result, 'links': links, 'stale_links': stale}


def linked_sites(store, panel, name):
    if not (store.data / 'database-links.json').exists():
        return []
    current = inventory(store, panel)
    return sorted(site for site, names in current['links'].items() if name in names)


def save_links(store, panel, links):
    if not isinstance(links, dict) or len(links) > 500:
        raise ValueError('Liên kết website/database không hợp lệ.')
    current = inventory(store, panel)
    sites = {row['name']: row for row in current['sites']}
    databases = {row['name']: row for row in current['databases']}
    path = store.data / 'database-links.json'
    document = json.loads(path.read_text()) if path.exists() else {}
    for name, names in links.items():
        if not isinstance(names, list):
            raise ValueError('Danh sách database liên kết không hợp lệ.')
        if not names:
            document.pop(name, None)
            continue
        if name not in sites:
            raise ValueError('Website liên kết không tồn tại trong aaPanel.')
        selected = selection(names, databases) if names else []
        document[name] = {'site': sites[name], 'databases': [databases[database_name(item)] for item in selected]}
    atomic_json(store.data / 'database-links.json', document)
    return inventory(store, panel)


def set_jobs(store):
    with store.connect() as connection:
        rows = connection.execute("SELECT * FROM jobs WHERE json_extract(details,'$.kind')='database_set' ORDER BY created DESC,id DESC").fetchall()
    return [{**dict(row), 'details': json.loads(row['details'])} for row in rows]


def protected_files(store):
    protected = set()
    for batch in set_jobs(store):
        if batch['details'].get('retained', True):
            for child_id in batch['details'].get('children', []):
                child = store.job(child_id)
                if child['details'].get('verified') and child['details'].get('remote'):
                    protected.add(child['details']['remote']['id'])
    return protected


def backup_record(store, panel, backup_id, client=None):
    job = store.job(backup_id)
    details = job['details']
    name = database_name(details.get('target'))
    current = panel.database_identity(name)
    if job['status'] != 'success' or details.get('kind') != 'database' or not details.get('verified') or not details.get('remote'):
        raise ValueError('Chỉ dùng backup database đã upload và xác minh thành công.')
    if details.get('database') and details['database'] != current:
        raise ValueError('Database đã đổi ID/tên trong aaPanel; không tự chọn đích khôi phục.')
    configuration = store.config()
    if not configuration.get('configured') or details.get('destination') != configuration['folder_id']:
        raise ValueError('Chọn lại đúng nơi lưu gốc của backup trước khi khôi phục.')
    if client is not None:
        client.backup_metadata(configuration, details['remote'], details['task'])
    return job


def sync_native(store, panel, offset=0, client=None):
    from sdb.native import register_backup, remove_backup
    offset = int(offset)
    if offset < 0 or offset > 1000000:
        raise ValueError('Trang lịch sử không hợp lệ.')
    with file_lock(store.data / 'operation.lock'):
        assert_recovery_clear(store)
        configuration = store.config()
        if not configuration.get('configured'):
            raise ValueError('Chưa xác nhận nơi lưu trên Shared Drive.')
        with store.connect() as connection:
            rows = connection.execute("SELECT id FROM jobs WHERE status='success' AND json_extract(details,'$.kind')='database' ORDER BY created DESC,id DESC LIMIT 21 OFFSET ?", (offset,)).fetchall()
        registered = removed = skipped = 0
        warnings = []
        for row in rows[:20]:
            try:
                job = backup_record(store, panel, row['id'])
                if not job['details'].get('database'):
                    raise ValueError('Bản backup cũ chưa ghi ID database; không tự liên kết theo tên.')
                client = client or make_client(store)
                try:
                    client.backup_metadata(configuration, job['details']['remote'], job['details']['task'])
                except BackupMissing:
                    remove_backup(store, panel, job['id'], kind='database')
                    removed += 1
                    continue
                native_id = register_backup(store, panel, job)
                store.update_job(job['id'], 'success', native_id=native_id)
                registered += 1
            except Exception as error:
                skipped += 1
                warnings.append({'backup_id': row['id'], 'error': safe_error(error)})
        return {'registered': registered, 'missing_removed': removed, 'skipped': skipped, 'warnings': warnings, 'next_offset': offset + 20 if len(rows) > 20 else None}

def members(store, backup_id):
    original = store.job(backup_id)
    details = original['details']
    if details.get('kind') == 'database':
        return original, [original]
    if details.get('kind') != 'database_set' or details.get('retained') is False:
        raise ValueError('Đợt backup database không tồn tại hoặc đã hết thời gian giữ.')
    children = [store.job(child) for child in details.get('children', [])]
    if any(child['details'].get('batch_id') != original['id'] or child['details'].get('kind') != 'database' for child in children):
        raise ValueError('Thành viên đợt backup không hợp lệ.')
    return original, children


def catalog(store, panel, backup_id='', offset=0):
    current = inventory(store, panel)
    if not backup_id:
        offset = int(offset)
        if offset < 0 or offset > 1000000:
            raise ValueError('Trang lịch sử không hợp lệ.')
        with store.connect() as connection:
            rows = connection.execute("SELECT child.* FROM jobs child LEFT JOIN jobs parent ON parent.id=json_extract(child.details,'$.batch_id') WHERE json_extract(child.details,'$.kind')='database_set' OR (json_extract(child.details,'$.kind')='database' AND child.status='success' AND COALESCE(json_extract(parent.details,'$.complete'),0)!=1) ORDER BY child.created DESC,child.id DESC LIMIT 21 OFFSET ?", (offset,)).fetchall()
        backups = []
        for row in rows[:20]:
            details = json.loads(row['details'])
            backups.append({'id': row['id'], 'created': row['created'], 'status': row['status'], 'kind': details['kind'], 'target': details['target'], 'batch_id': details.get('batch_id', ''), 'complete': details.get('complete', row['status'] == 'success'), 'retained': details.get('retained', True)})
        return {'backups': backups, 'offset': offset, 'next_offset': offset + 20 if len(rows) > 20 else None, 'inventory': current}
    original, children = members(store, backup_id)
    entries = []
    for child in children:
        name = child['details']['target']
        sites = sorted(set(child['details'].get('database_sites', [])) | {site for site, names in current['links'].items() if name in names})
        row = {'backup_id': child['id'], 'name': name, 'sites': sites, 'available': False, 'legacy': not bool(child['details'].get('database')), 'size': child['details'].get('remote', {}).get('size', 0)}
        try:
            backup_record(store, panel, child['id'])
            row['available'] = True
        except Exception as error:
            row['error'] = safe_error(error)
        entries.append(row)
    return {'id': original['id'], 'complete': original['details'].get('complete', original['status'] == 'success'), 'entries': entries, 'inventory': current, 'notice': 'Drive/checksum sẽ được kiểm tra lại trong worker trước mọi thao tác ghi.'}


def prepare_restore(store, panel, backup_id, names, confirmation, writers_stopped, client=None, verify_remote=True):
    assert_recovery_clear(store)
    original, children = members(store, backup_id)
    by_name = {row['details']['target']: row for row in children}
    if len(by_name) != len(children):
        raise ValueError('Đợt backup chứa tên database trùng nhau.')
    selected = selection(names, by_name)
    if original['details']['kind'] == 'database_set' and set(selected) == set(original['details'].get('database_names', [])) and not original['details'].get('complete'):
        raise ValueError('Đợt backup chưa hoàn tất; không được khôi phục tất cả như một đợt đầy đủ.')
    if confirmation != 'RESTORE ' + str(len(selected)) + ' DATABASES' or writers_stopped is not True:
        raise ValueError('Phải xác nhận số database và đã dừng mọi nguồn ghi dữ liệu.')
    current = inventory(store, panel)
    if current['stale_links']:
        raise ValueError('Liên kết website/database đã thay đổi; xác nhận lại liên kết trước khi khôi phục.')
    client = (client or make_client(store)) if verify_remote else None
    configuration = store.config()
    if original['details']['kind'] == 'database_set' and original['details'].get('complete'):
        if not original['details'].get('verified') or original['details'].get('destination') != configuration['folder_id']:
            raise ValueError('Đợt backup không còn đúng nơi lưu đã xác minh.')
        if client is not None:
            client.backup_metadata(configuration, original['details']['remote'], original['details']['task'])
    entries, affected = [], set()
    for name in selected:
        child = backup_record(store, panel, by_name[name]['id'], client)
        sites = {site for site, databases in current['links'].items() if name in databases}
        sites.update(site for site in child['details'].get('database_sites', []) if any(row['name'] == site for row in current['sites']))
        affected.update(sites)
        entries.append({'backup_id': child['id'], 'name': name, 'database': panel.database_identity(name)})
    panel.stopped_sites(sorted(affected))
    return {'kind': 'database_restore', 'target': ', '.join(selected), 'backup_id': backup_id, 'database_names': selected, 'entries': entries, 'affected_sites': sorted(affected), 'confirmation': confirmation, 'writers_stopped': True, 'destination': configuration['folder_id']}


def run_backup_set(store, job_id, panel=None):
    panel = panel or Panel()
    if store.job(job_id)['status'] != 'queued' or store.job(job_id)['details'].get('kind') != 'database_set':
        return False
    started = False
    try:
        with backup_lock(store, job_id):
            if store.job(job_id)['status'] != 'queued':
                return False
            started = True
            configuration, client = ensure_ready(store, make_client(store))
            details = store.job(job_id)['details']
            current = inventory(store, panel)
            names = selection(details['database_names'], {row['name'] for row in current['databases']})
            if current['stale_links'] or details['destination'] != configuration['folder_id'] or details['links'] != current['links'] or details['database_identities'] != [panel.database_identity(name) for name in names]:
                raise ValueError('Nơi lưu hoặc liên kết database đã thay đổi sau khi tạo tác vụ.')
            children = []
            store.update_job(job_id, 'running', phase='archive', children=children, retained=True, complete=False)
            for name in names:
                child_id = store.new_job({'kind': 'database', 'target': name, 'batch_id': job_id, 'database': panel.database_identity(name), 'destination': configuration['folder_id'], 'keep': details['keep'], 'keep_local': details.get('keep_local', False), 'origin': details.get('origin', 'manual'), 'cron_echo': details.get('cron_echo', ''), 'database_sites': sorted(site for site, databases in current['links'].items() if name in databases)})
                children.append(child_id)
                store.update_job(job_id, 'running', children=children, current_database=name)
                if not run_backup(store, child_id, panel, lock=False):
                    raise ValueError('Backup database thất bại: ' + name + '. Đợt chưa hoàn tất; các bản đã xác minh vẫn được giữ.')
            entries = []
            for child_id in children:
                child = backup_record(store, panel, child_id, client)
                entries.append({'backup_id': child_id, **{key: child['details'][key] for key in ['target', 'database', 'database_sites', 'remote', 'task']}})
            manifest = store.tmp / (job_id + '-databases.json')
            atomic_json(manifest, {'format': 1, 'server_id': configuration['server_id'], 'drive_id': configuration['drive_id'], 'folder_id': configuration['folder_id'], 'batch_id': job_id, 'created': store.job(job_id)['created'], 'entries': entries})
            task = task_key('database-set', 'MySQL', configuration['folder_id'], 'cron:' + details['cron_echo'] if details.get('cron_echo') else 'manual')
            remote = client.upload(manifest, configuration, task)
            store.update_job(job_id, 'success', phase='complete', complete=True, verified=True, remote=remote, task=task, current_database='', progress=100)
            manifest.unlink()
            if configuration['prune_enabled']:
                try:
                    client.prune(configuration, task, remote['parent_id'], int(details['keep']), remote['id'])
                    for previous in set_jobs(store):
                        prior = previous['details']
                        if previous['id'] == job_id or not prior.get('complete') or not prior.get('retained', True) or prior.get('task') != task or prior.get('destination') != configuration['folder_id']:
                            continue
                        try:
                            client.backup_metadata(configuration, prior['remote'], task)
                        except BackupMissing:
                            store.update_job(previous['id'], previous['status'], retained=False)
                    pins = protected_files(store)
                    for entry in entries:
                        client.prune(configuration, entry['task'], entry['remote']['parent_id'], int(details['keep']), entry['remote']['id'], protected_ids=pins)
                except Exception as error:
                    store.update_job(job_id, 'success', warning='Đợt đã xác minh; chưa dọn bản cũ: ' + safe_error(error))
            return True
    except Exception as error:
        if not started and store.job(job_id)['status'] != 'queued':
            return False
        store.update_job(job_id, 'failed', complete=False, error=safe_error(error))
        print('Backup database theo đợt thất bại: ' + safe_error(error), flush=True)
        return False


@contextmanager
def mysql_command(panel, directory):
    binary = Path('/www/server/mysql/bin/mysql')
    if not binary.is_file():
        raise ValueError('Không tìm thấy MySQL client của aaPanel.')
    password = panel.mysql_password()
    escaped = password.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
    descriptor, options = tempfile.mkstemp(dir=directory, prefix='.mysql-', suffix='.cnf')
    try:
        with os.fdopen(descriptor, 'w') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write('[client]\nuser=root\npassword="' + escaped + '"\n')
        yield [str(binary), '--defaults-file=' + options, '--host=localhost', '--protocol=socket', '--batch', '--binary-mode=1', '--local-infile=0', '--skip-reconnect']
    finally:
        Path(options).unlink(missing_ok=True)


def mysql_data_directory(panel, directory):
    with mysql_command(panel, directory) as command:
        result = subprocess.run(command + ['--skip-column-names', '--execute=SELECT @@datadir'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    if result.returncode:
        raise ValueError('Không kiểm tra được datadir MySQL; chưa thực hiện import.')
    path = Path(result.stdout.decode().strip())
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('Datadir MySQL không hợp lệ.')
    return path


def assert_events_stopped(panel, directory):
    with mysql_command(panel, directory) as command:
        result = subprocess.run(command + ['--skip-column-names', '--execute=SELECT @@event_scheduler'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    if result.returncode or result.stdout.strip().upper() not in {b'OFF', b'DISABLED'}:
        raise ValueError('Dump có EVENT; quản trị viên phải dừng MySQL event_scheduler trước. Plugin không tự đổi cấu hình toàn cục.')


def unpack_sql(source, destination, database, max_bytes, reserve):
    database_name(database)
    size, uses, creates, events = 0, set(), set(), False
    try:
        with gzip.open(source, 'rb') as compressed, open(destination, 'xb') as output:
            os.fchmod(output.fileno(), 0o600)
            while True:
                line = compressed.readline(1024 * 1024 + 1)
                if not line:
                    break
                size += len(line)
                if size > max_bytes or shutil.disk_usage(destination.parent).free < reserve:
                    raise ValueError('SQL giải nén vượt giới hạn hoặc không đủ dung lượng dự phòng.')
                statement = line.lstrip()
                if re.match(rb'(?:/\*[M]?\!\d+\s*)?CREATE\b', statement, re.I) and re.search(rb'\bEVENT\b', statement, re.I):
                    events = True
                if re.match(rb'(?:/\*!\d+\s*)?(?:SET\s+(?:@@)?GLOBAL|RESET\s+(?:MASTER|BINARY)|CHANGE\s+(?:MASTER|REPLICATION))\b', statement, re.I):
                    raise ValueError('Dump có thay đổi trạng thái MySQL toàn cục; không hỗ trợ tự động restore.')
                if re.match(rb'USE\b', statement, re.I):
                    match = re.fullmatch(rb'USE\s+`([A-Za-z0-9_$-]+)`\s*;\s*', statement, re.I)
                    if not match:
                        raise ValueError('USE trong dump không đúng định dạng backup MySQL được hỗ trợ.')
                    uses.add(match[1].decode('ascii'))
                if re.match(rb'CREATE\s+DATABASE\b', statement, re.I):
                    identifiers = re.findall(rb'`([^`]+)`', statement)
                    if not identifiers or identifiers[0].decode('ascii') != database:
                        raise ValueError('Dump chứa database không trùng tên nguồn.')
                    creates.add(identifiers[0].decode('ascii'))
                output.write(line)
            output.flush()
            os.fsync(output.fileno())
        if uses != {database} or creates != {database}:
            raise ValueError('Dump phải có CREATE DATABASE và USE đúng database nguồn; không tự sửa SQL hoặc đổi tên đích.')
        return events
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def mysql_import(panel, database, source, reserve, datadir):
    database_name(database)
    with mysql_command(panel, source.parent) as command, open(source, 'rb') as sql:
        if any(shutil.disk_usage(path).free < reserve for path in [source.parent, datadir]):
            raise ValueError('Không đủ đĩa dự phòng; chưa xóa database đích.')
        reset = subprocess.run(command + ['--execute=DROP DATABASE IF EXISTS `' + database + '`'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, preexec_fn=pdeathsig_preexec)
        if reset.returncode:
            raise ValueError('Không thay thế được database đích; giữ snapshot/journal để phục hồi.')
        process = subprocess.Popen(command, stdin=sql, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=pdeathsig_preexec)
        try:
            deadline = time.monotonic() + 7200
            while process.poll() is None:
                if time.monotonic() > deadline or any(shutil.disk_usage(path).free < reserve for path in [source.parent, datadir]):
                    raise ValueError('Dừng import vì hết thời gian hoặc dung lượng đĩa dưới ngưỡng an toàn.')
                time.sleep(0.25)
            if process.returncode:
                raise ValueError('MySQL import thất bại (exit ' + str(process.returncode) + '); giữ snapshot và journal để phục hồi.')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def run_restore(store, job_id, panel=None):
    panel = panel or Panel()
    if store.job(job_id)['status'] != 'queued' or store.job(job_id)['details'].get('kind') != 'database_restore':
        return False
    stage = None
    started = False
    journal = store.data / 'database-recovery.json'
    try:
        with file_lock(store.data / 'operation.lock'):
            if store.job(job_id)['status'] != 'queued':
                return False
            started = True
            details = store.job(job_id)['details']
            configuration, client = ensure_ready(store, make_client(store))
            plan = prepare_restore(store, panel, details['backup_id'], details['database_names'], details['confirmation'], details['writers_stopped'], client)
            if details['entries'] != plan['entries'] or details['affected_sites'] != plan['affected_sites'] or details['destination'] != configuration['folder_id']:
                raise ValueError('Nguồn, database hoặc liên kết website đã thay đổi sau khi xác nhận.')
            store.update_job(job_id, 'running', phase='preflight', results=[])
            stage = Path(tempfile.mkdtemp(dir=store.tmp, prefix='database-restore-'))
            stage.chmod(0o700)
            reserve = int(configuration['min_free_mb']) * 1024 ** 2
            maximum = int(configuration['max_archive_gb']) * 1024 ** 3
            datadir = mysql_data_directory(panel, stage)
            prepared = []
            for entry in plan['entries']:
                original = backup_record(store, panel, entry['backup_id'], client)['details']
                remote = original['remote']
                packed = stage / (entry['name'] + '.sql.gz')
                sql = stage / (entry['name'] + '.sql')
                store.update_job(job_id, 'running', phase='download', current_database=entry['name'])
                client.download(remote['id'], packed, remote['md5Checksum'], remote['sha256'], configuration, original['task'], remote['parent_id'], max_bytes=maximum, reserve_bytes=reserve)
                events = unpack_sql(packed, sql, entry['name'], maximum, reserve)
                if events:
                    assert_events_stopped(panel, stage)
                prepared.append({**entry, 'sql': str(sql), 'state': 'pending', 'events': events})
            required = sum(Path(entry['sql']).stat().st_size for entry in prepared) * 2 + reserve
            if required > shutil.disk_usage(datadir).free:
                raise ValueError('Datadir không đủ dung lượng dự phòng cho import.')
            snapshots = store.data / 'database-snapshots' / job_id
            snapshots.mkdir(parents=True, mode=0o700)
            snapshots.parent.chmod(0o700)
            sync_directory(snapshots.parent)
            snapshot_check = stage / 'snapshot-check'
            snapshot_check.mkdir(mode=0o700)
            for entry in prepared:
                store.update_job(job_id, 'running', phase='snapshot', current_database=entry['name'])
                snapshot = snapshots / (entry['name'] + '.sql.gz')
                mysql_archive(panel, entry['name'], snapshot, maximum, reserve)
                snapshot.chmod(0o600)
                with open(snapshot, 'rb') as saved:
                    os.fsync(saved.fileno())
                snapshot_sql = snapshot_check / (entry['name'] + '.sql')
                entry['events'] = unpack_sql(snapshot, snapshot_sql, entry['name'], maximum, reserve) or entry['events']
                if entry['events']:
                    assert_events_stopped(panel, stage)
                snapshot_sql.unlink()
                md5, sha256 = digests(snapshot)
                entry.update(snapshot=str(snapshot), snapshot_md5=md5, snapshot_sha256=sha256)
                store.update_job(job_id, 'running', snapshots=[{key: item[key] for key in ['name', 'snapshot', 'snapshot_sha256']} for item in prepared if 'snapshot' in item])
            sync_directory(snapshots)
            sync_directory(snapshots.parent)
            if required > shutil.disk_usage(datadir).free:
                raise ValueError('Sau khi tạo snapshot, datadir không còn đủ dung lượng import; chưa thay database.')
            record = {'job_id': job_id, 'entries': prepared, 'affected_sites': plan['affected_sites'], 'reserve': reserve, 'maximum': maximum}
            atomic_json(journal, record)
            for index, entry in enumerate(prepared):
                if panel.database_identity(entry['name']) != entry['database']:
                    raise ValueError('Database đích thay đổi trước import.')
                panel.stopped_sites(plan['affected_sites'])
                if any(item['events'] for item in prepared):
                    assert_events_stopped(panel, stage)
                entry['state'] = 'importing'
                atomic_json(journal, record)
                store.update_job(job_id, 'running', phase='import', current_database=entry['name'], progress=round(index * 100 / len(prepared)), results=[{'name': item['name'], 'state': item['state']} for item in prepared])
                mysql_import(panel, entry['name'], Path(entry['sql']), reserve, datadir)
                entry['state'] = 'complete'
                atomic_json(journal, record)
                store.update_job(job_id, 'running', results=[{'name': item['name'], 'state': item['state']} for item in prepared])
            store.update_job(job_id, 'success', phase='complete', current_database='', progress=100, recovery_required=False)
            journal.unlink()
            sync_directory(store.data)
            return True
    except Exception as error:
        if not started and store.job(job_id)['status'] != 'queued':
            return False
        store.update_job(job_id, 'failed', error=safe_error(error), recovery_required=journal.exists())
        print('Khôi phục database thất bại: ' + safe_error(error), flush=True)
        return False
    finally:
        if stage is not None:
            try:
                shutil.rmtree(stage)
                store.update_job(job_id, store.job(job_id)['status'], temporary_files_cleaned=True, cleanup_warning='')
            except Exception as error:
                store.update_job(job_id, store.job(job_id)['status'], temporary_files_cleaned=False, cleanup_warning='Chưa dọn được file tải về/SQL tạm: ' + safe_error(error))


def prepare_snapshot_cleanup(store, job_id, confirmation):
    assert_recovery_clear(store)
    job = store.job(job_id)
    details = job['details']
    snapshots = details.get('snapshots', [])
    results = details.get('results', [])
    if confirmation != 'DELETE ' + job_id:
        raise ValueError('Cần xác nhận DELETE và đúng mã tác vụ khôi phục trước khi xoá snapshot.')
    if job['status'] != 'success' or details.get('kind') != 'database_restore' or details.get('phase') != 'complete' or details.get('recovery_required') or not snapshots:
        raise ValueError('Chỉ dọn snapshot của tác vụ khôi phục database đã hoàn tất thành công, không cần phục hồi.')
    names = [database_name(item['name']) for item in snapshots]
    if len(names) != len(set(names)) or sorted(item['name'] for item in results) != sorted(names) or any(item.get('state') != 'complete' for item in results):
        raise ValueError('Kết quả khôi phục chưa hoàn tất cho toàn bộ snapshot.')
    return {'kind': 'database_snapshot_cleanup', 'target': details.get('target', ', '.join(names)), 'restore_job_id': job_id, 'confirmation': confirmation}

def run_snapshot_cleanup(store, job_id):
    job = store.job(job_id)
    if job['status'] != 'queued' or job['details'].get('kind') != 'database_snapshot_cleanup':
        return False
    try:
        with file_lock(store.data / 'operation.lock'):
            if store.job(job_id)['status'] != 'queued':
                return False
            details = job['details']
            source_id = details['restore_job_id']
            prepare_snapshot_cleanup(store, source_id, details['confirmation'])
            source = store.job(source_id)
            root = store.data / 'database-snapshots'
            folder = root / source_id
            if root.is_symlink() or folder.is_symlink() or root.resolve() != root.absolute() or folder.resolve() != folder.absolute():
                raise ValueError('Thư mục snapshot không an toàn; không xoá.')
            expected = {folder / (database_name(item['name']) + '.sql.gz') for item in source['details']['snapshots']}
            if folder.exists() and (not folder.is_dir() or not set(folder.iterdir()).issubset(expected)):
                raise ValueError('Thư mục snapshot chứa file lạ; không xoá.')
            store.update_job(job_id, 'running', phase='validate')
            files = []
            for item in source['details']['snapshots']:
                path = folder / (database_name(item['name']) + '.sql.gz')
                if Path(item['snapshot']) != path or path.is_symlink():
                    raise ValueError('Đường dẫn snapshot không thuộc tác vụ; không xoá.')
                if path.exists():
                    if not path.is_file() or digests(path)[1] != item.get('snapshot_sha256'):
                        raise ValueError('Checksum snapshot đã thay đổi; không xoá.')
                    files.append((path, path.stat().st_size))
            for path, size in files:
                path.unlink()
            if folder.exists():
                folder.rmdir()
                sync_directory(root)
            store.update_job(source_id, 'success', snapshots_cleaned=True, snapshots_cleaned_at=time.time())
            store.update_job(job_id, 'success', phase='complete', removed_files=len(files), freed_bytes=sum(size for path, size in files))
            print('Đã dọn snapshot local đã nghiệm thu của tác vụ ' + source_id + '; không đổi MySQL hoặc Drive.', flush=True)
            return True
    except Exception as error:
        store.update_job(job_id, 'failed', error=safe_error(error))
        print('Dọn snapshot thất bại: ' + safe_error(error), flush=True)
        return False

def recover(store, job_id, mode, confirmation, writers_stopped=False, panel=None):
    panel = panel or Panel()
    if mode not in {'rollback', 'accept'} or confirmation != mode.upper() + ' ' + job_id:
        raise ValueError('Cần xác nhận rõ hành động và mã tác vụ phục hồi.')
    with file_lock(store.data / 'operation.lock'):
        journal = store.data / 'database-recovery.json'
        record = json.loads(journal.read_text())
        if record['job_id'] != job_id or store.job(job_id)['details'].get('kind') != 'database_restore':
            raise ValueError('Journal không thuộc tác vụ khôi phục được chọn.')
        if mode == 'rollback':
            if writers_stopped is not True:
                raise ValueError('Phải xác nhận đã dừng mọi nguồn ghi trước khi phục hồi snapshot.')
            panel.stopped_sites(record['affected_sites'])
            with tempfile.TemporaryDirectory(dir=store.tmp, prefix='database-recovery-') as temporary:
                stage = Path(temporary)
                datadir = mysql_data_directory(panel, stage)
                prepared = []
                for entry in record['entries']:
                    if entry['state'] == 'pending':
                        continue
                    snapshot = Path(entry['snapshot'])
                    expected = store.data / 'database-snapshots' / job_id / (database_name(entry['name']) + '.sql.gz')
                    if snapshot != expected or snapshot.is_symlink() or snapshot.resolve() != expected.absolute() or digests(snapshot) != (entry['snapshot_md5'], entry['snapshot_sha256']) or panel.database_identity(entry['name']) != entry['database']:
                        raise ValueError('Snapshot hoặc database đích đã thay đổi; không tự phục hồi.')
                    sql = stage / (entry['name'] + '.sql')
                    events = unpack_sql(snapshot, sql, entry['name'], record['maximum'], record['reserve'])
                    if events:
                        assert_events_stopped(panel, stage)
                    prepared.append((entry, sql))
                for entry, sql in prepared:
                    panel.stopped_sites(record['affected_sites'])
                    if entry.get('events'):
                        assert_events_stopped(panel, stage)
                    entry['state'] = 'recovery-importing'
                    atomic_json(journal, record)
                    mysql_import(panel, entry['name'], sql, record['reserve'], datadir)
                    entry['state'] = 'recovered'
                    atomic_json(journal, record)
        store.update_job(job_id, 'failed', recovery_required=False, recovery_resolution=mode)
        journal.unlink()
        sync_directory(store.data)
    return {'resolved': True, 'mode': mode, 'job_id': job_id}
def pdeathsig_preexec():
    import ctypes
    import signal
    PR_SET_PDEATHSIG = 1
    libc = ctypes.CDLL(None)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)

