import importlib.util
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from sdb import PLUGIN
from sdb.integration import register, replace_file
from sdb.release import deploy, validate_code
from sdb.storage import Store, assert_recovery_clear, file_lock

def install_in_place(store, panel, lock=True):
    with file_lock(store.data / 'operation.lock') if lock else nullcontext():
        assert_recovery_clear(store)
        validate_code(store.root)
        index = store.root / 'index.html'
        body = index.read_bytes().replace(b'/* SHARED_DRIVE_STYLE */', (store.root / 'ui.css').read_bytes()).replace(b'/* SHARED_DRIVE_SCRIPT */', (store.root / 'ui.js').read_bytes())
        replace_file(index, body)
        return register(store, panel)


def main():
    os.umask(0o077)
    if os.geteuid() != 0:
        raise SystemExit('Chạy cài đặt bằng root.')
    panel = Path('/www/server/panel')
    destination = panel / 'plugin' / PLUGIN
    if not (panel / 'class_v2/crontab_v2.py').is_file():
        raise SystemExit('Phiên bản aaPanel chưa được adapter này hỗ trợ.')
    store = Store(destination)
    vendor = destination / 'data/vendor'
    sys.path.insert(0, str(vendor))
    missing = [name for name in ['googleapiclient', 'google_auth_httplib2', 'cryptography'] if importlib.util.find_spec(name) is None]
    with file_lock(store.data / 'operation.lock'):
        assert_recovery_clear(store)
        if missing:
            vendor.mkdir(mode=0o700, exist_ok=True)
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(vendor), '-r', str(SOURCE / 'requirements.txt')], check=True)
        if SOURCE == destination:
            result = install_in_place(store, panel, lock=False)
        else:
            result = deploy(SOURCE, destination, str(panel), lock=False)
    print(json.dumps({'installed': True, 'path': str(destination), **result}, ensure_ascii=False))
    if result.get('restart_panel_required'):
        print('Cần nạp lại dịch vụ aaPanel để nhận adapter Cron. Không restart OLS hoặc website.')


if __name__ == '__main__':
    main()
