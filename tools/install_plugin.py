import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from sdb import PLUGIN
from sdb.integration import register, replace_file
from sdb.release import deploy, validate_code
from sdb.storage import Store


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
    if missing:
        vendor.mkdir(mode=0o700, exist_ok=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(vendor), '-r', str(SOURCE / 'requirements.txt')], check=True)
    if SOURCE == destination:
        validate_code(destination)
        index = destination / 'index.html'
        body = index.read_bytes().replace(b'/* SHARED_DRIVE_STYLE */', (destination / 'ui.css').read_bytes()).replace(b'/* SHARED_DRIVE_SCRIPT */', (destination / 'ui.js').read_bytes())
        replace_file(index, body)
        result = register(store, panel)
    else:
        result = deploy(SOURCE, destination, str(panel))
    print(json.dumps({'installed': True, 'path': str(destination), **result}, ensure_ascii=False))
    if result.get('restart_panel_required'):
        print('Cần nạp lại dịch vụ aaPanel để nhận adapter Cron. Không restart OLS hoặc website.')


if __name__ == '__main__':
    main()
