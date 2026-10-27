import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'data/vendor'))

from sdb.databases import recover
from sdb.storage import Store, safe_error


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Phục hồi database theo snapshot local; không tự chạy lại import bị lỗi.')
    parser.add_argument('mode', choices=['status', 'rollback', 'accept'])
    parser.add_argument('--job', default='')
    parser.add_argument('--confirm', default='')
    parser.add_argument('--writers-stopped', action='store_true')
    arguments = parser.parse_args()
    store = Store(ROOT)
    if arguments.mode == 'status':
        path = store.data / 'database-recovery.json'
        record = json.loads(path.read_text()) if path.exists() else None
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(recover(store, arguments.job, arguments.mode, arguments.confirm, arguments.writers_stopped), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(safe_error(error), file=sys.stderr)
        sys.exit(1)
