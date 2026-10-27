import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'data/vendor'))

from sdb.api import API
from sdb.drive import validate_credentials
from sdb.integration import register, unregister
from sdb.panel import Panel
from sdb.storage import Store, file_lock, safe_error
from sdb.worker import ensure_ready, run_backup, run_download, validate_exclusions
from sdb.restore import run_restore


def cron_backup(store, kind, target, keep, echo=''):
    if kind not in {'site', 'path', 'database'} or not 1 <= int(keep) <= 10000:
        raise ValueError('Tham số Cron không hợp lệ.')
    panel = Panel()
    cron = panel.cron_details(echo)
    if echo and cron is None:
        raise ValueError('Không tìm thấy Cron thuộc plugin này.')
    if cron and (cron.get('second') or (kind == 'database' and str(cron.get('db_type') or 'mysql').lower() != 'mysql')):
        raise ValueError('Chưa hỗ trợ lịch theo giây hoặc database không phải MySQL local.')
    if cron and kind == 'database' and (cron.get('split_type') or str(cron.get('table_list') or '') not in {'', '[]', 'null'}):
        raise ValueError('Chưa hỗ trợ split backup hoặc chọn từng bảng. Hãy chọn backup toàn database MySQL local.')
    exclusions = validate_exclusions((cron.get('sBody', '') or '').splitlines() if cron else [])
    if cron and (cron['sType'] != kind or cron['sName'] != target):
        raise ValueError('Thông số không trùng tác vụ Cron đã lưu.')
    configuration, client = ensure_ready(store)
    targets = [target]
    if target == 'ALL' and kind in {'site', 'database'}:
        inventory = panel.inventory()
        targets = [item['name'] for item in inventory['sites' if kind == 'site' else 'databases']]
    elif kind in {'site', 'database'} and ',' in target:
        targets = [name for name in target.split(',') if name]
    if not targets:
        raise ValueError('Không có nguồn phù hợp cho tác vụ Cron.')
    success = True
    for item in targets:
        details = {'kind': kind, 'target': item, 'keep': int(keep), 'exclusions': exclusions, 'destination': configuration['folder_id'], 'origin': 'cron', 'cron_echo': echo, 'keep_local': bool(cron and int(cron.get('save_local') or 0))}
        job_id = store.new_job(details)
        success = run_backup(store, job_id, panel) and success
    return success


def main():
    os.umask(0o077)
    store = Store(ROOT)
    operation = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if operation == 'api':
            payload = sys.stdin.buffer.read(128 * 1024 + 1)
            if len(payload) > 128 * 1024:
                raise ValueError('Yêu cầu quá lớn.')
            request = json.loads(payload)
            data = API(store).dispatch(request['method'], request.get('arguments', {}))
            print(json.dumps({'status': True, 'data': data}, ensure_ascii=False))
            return 0
        if operation in {'backup', 'download'}:
            return 0 if (run_backup if operation == 'backup' else run_download)(store, sys.argv[2]) else 1
        if operation == 'restore':
            return 0 if run_restore(store, sys.argv[2]) else 1
        if operation in {'site', 'path', 'database'}:
            return 0 if cron_backup(store, operation, sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else '') else 1
        if operation in {'register', 'unregister'}:
            result = (register if operation == 'register' else unregister)(store, sys.argv[2] if len(sys.argv) > 2 else '/www/server/panel')
            print(json.dumps(result))
            return 0
        if operation == 'import-credentials':
            document = json.loads(Path(sys.argv[2]).read_text())
            validate_credentials(document)
            with file_lock(store.data / 'operation.lock'):
                store.save_secret('credentials', document)
                store.save_config({'configured': False})
            print('Đã nhập credentials an toàn. Mở plugin để chọn Shared drive và thư mục.')
            return 0
        if operation == 'import-github-token':
            token = Path(sys.argv[2]).read_text().strip()
            if not re.fullmatch(r'[A-Za-z0-9_]{20,255}', token):
                raise ValueError('GitHub token không hợp lệ.')
            with file_lock(store.data / 'operation.lock'):
                store.save_secret('github_token', token)
            print('Đã lưu GitHub token. Không hiển thị lại secret.')
            return 0
        raise ValueError('Lệnh không hợp lệ.')
    except Exception as error:
        result = {'status': False, 'msg': safe_error(error)}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if operation == 'api' else 1


if __name__ == '__main__':
    sys.exit(main())
