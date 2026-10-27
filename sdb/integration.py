import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from sdb import PLUGIN
from sdb.storage import atomic_json, file_lock, sync_directory

LIST_ANCHOR = '        cloud_storage_info = [x for x in CLOUD_STORAGE]'
LIST_PATCH = LIST_ANCHOR + " + [item for item in libs if item.get('opt') == 'shared_drive_backup']"
TITLE_ANCHOR = "'localhost':'Local Disk'\n                }"
TITLE_PATCH = "'localhost':'Local Disk',\n                'shared_drive_backup':'Shared Drive Backup'\n                }"
DISPATCH_ANCHOR = '            try:\n                shell = wheres[type]'
DISPATCH_PATCH = '''            if param.get('backupTo') == 'shared_drive_backup':
                if type not in ('site', 'path', 'database'):
                    raise ValueError('Shared Drive Backup supports only Site, MySQL Database and Directory backups.')
                import shlex
                shared_drive_command = [public.get_python_bin(), '-u', os.path.join(public.get_panel_path(), 'plugin', 'shared_drive_backup', 'shared_drive_backup_main.py'), type, str(param['sName']), str(param['save']), str(cronName)]
                shared_drive_head = re.sub(r'^export BT_EXCLUDE=.*$', '', head, flags=re.MULTILINE)
                shared_drive_cleanup = 'rm -f -- ' + shlex.quote(cronFile)
                wheres[type] = shared_drive_head + 'trap ' + shlex.quote(shared_drive_cleanup) + ' EXIT\\n' + ' '.join(shlex.quote(argument) for argument in shared_drive_command) + '\\nshared_drive_status=$?\\nif [ "$shared_drive_status" -ne 0 ]; then exit "$shared_drive_status"; fi\\n'
            try:
                shell = wheres[type]'''


def digest(body):
    return hashlib.sha256(body).hexdigest()


def replace_file(path, body):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.sdb-')
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        else:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def patched_cron(source, panel_path):
    if LIST_PATCH in source and DISPATCH_PATCH in source and TITLE_PATCH in source:
        return source
    if source.count(LIST_ANCHOR) != 1 or source.count(DISPATCH_ANCHOR) != 1 or source.count(TITLE_ANCHOR) != 1:
        raise ValueError('Phiên bản Cron aaPanel chưa tương thích. Không sửa core; cần kiểm tra adapter.')
    result = source.replace(LIST_ANCHOR, LIST_PATCH, 1).replace(TITLE_ANCHOR, TITLE_PATCH, 1).replace(DISPATCH_ANCHOR, DISPATCH_PATCH, 1)
    compile(result, str(Path(panel_path) / 'class_v2/crontab_v2.py'), 'exec')
    return result


def provider_entry(panel_path):
    return {
        'name': 'Shared Drive Backup', 'type': 'Cron job', 'ps': 'Backup to Google Shared drives',
        'status': False, 'opt': PLUGIN, 'module': 'os', 'script': PLUGIN,
        'help': '', 'key': '', 'secret': '', 'bucket': '', 'domain': '',
        'check': [str(Path(panel_path) / 'plugin' / PLUGIN / (PLUGIN + '_main.py'))],
    }


def register(store, panel_path):
    panel = Path(panel_path)
    source = panel / 'class_v2/crontab_v2.py'
    registry = panel / 'data/libList.conf'
    state_path = store.data / 'integration.json'
    with file_lock(panel / 'data/shared_drive_backup.integration.lock', blocking=True):
        body = source.read_bytes()
        previous_state = state_path.read_bytes() if state_path.exists() else None
        previous_registry = registry.read_bytes() if registry.exists() else None
        state = json.loads(previous_state) if previous_state is not None else None
        baseline = body
        if state and digest(body) == state.get('patched_hash') and not (LIST_PATCH in body.decode('utf-8') and DISPATCH_PATCH in body.decode('utf-8')):
            baseline = (store.data / state['backup']).read_bytes()
            if digest(baseline) != state['original_hash']:
                raise ValueError('Bản sao Cron gốc không khớp checksum; không nâng adapter.')
        result = patched_cron(baseline.decode('utf-8'), str(panel)).encode('utf-8')
        libraries = json.loads(registry.read_text()) if registry.exists() else []
        if not isinstance(libraries, list):
            raise ValueError('Danh sách cloud aaPanel không hợp lệ.')
        if body != result:
            backup = store.data / 'cron-original.py'
            if state and body != (store.data / state['backup']).read_bytes():
                if state.get('patched_hash') != digest(body):
                    backup = store.data / ('cron-original-' + digest(body)[:12] + '.py')
            replace_file(backup, baseline)
            backup.chmod(0o600)
            state = {'backup': backup.name, 'original_hash': digest(baseline), 'patched_hash': digest(result)}
        elif not state:
            raise ValueError('Cron đã có adapter nhưng thiếu bản sao gốc. Không ghi đè.')
        try:
            if body != result:
                replace_file(source, result)
            libraries = [item for item in libraries if item.get('opt') != PLUGIN]
            libraries.append(provider_entry(panel))
            atomic_json(registry, libraries)
            if body != result:
                atomic_json(state_path, state)
        except BaseException:
            if body != result:
                replace_file(source, body)
            if previous_registry is not None:
                replace_file(registry, previous_registry)
            else:
                registry.unlink(missing_ok=True)
            if previous_state is not None:
                replace_file(state_path, previous_state)
            else:
                state_path.unlink(missing_ok=True)
            raise
    return {'registered': True, 'restart_panel_required': body != result, 'core_hash': digest(result)}


def unregister(store, panel_path):
    panel = Path(panel_path)
    source = panel / 'class_v2/crontab_v2.py'
    state_path = store.data / 'integration.json'
    with file_lock(panel / 'data/shared_drive_backup.integration.lock', blocking=True):
        if state_path.exists():
            state = json.loads(state_path.read_text())
            body = source.read_bytes()
            if digest(body) != state['patched_hash']:
                raise ValueError('Cron đã thay đổi sau cài đặt. Từ chối khôi phục đè thay đổi của panel.')
            original = (store.data / state['backup']).read_bytes()
            if digest(original) != state['original_hash']:
                raise ValueError('Bản sao Cron gốc không khớp checksum.')
            replace_file(source, original)
            state_path.unlink()
        registry = panel / 'data/libList.conf'
        libraries = json.loads(registry.read_text())
        atomic_json(registry, [item for item in libraries if item.get('opt') != PLUGIN])
    return {'registered': False, 'restart_panel_required': True}


def status(store, panel_path):
    source = Path(panel_path) / 'class_v2/crontab_v2.py'
    body = source.read_text()
    libraries = json.loads((Path(panel_path) / 'data/libList.conf').read_text())
    return {'registered': LIST_PATCH in body and DISPATCH_PATCH in body and any(item.get('opt') == PLUGIN for item in libraries)}
