import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or not path.parts or path.parts[0] in {'data', 'logs', 'tmp', '.git', 'config.conf'}:
        raise ValueError('Đường dẫn trong recovery journal không an toàn.')
    if len(path.parts) > 2 or len(path.parts) == 2 and path.parts[0] not in {'sdb', 'tools'}:
        raise ValueError('Đường dẫn recovery không thuộc mã plugin.')
    return Path(*path.parts)


def restore_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix='.sdb-recover-')
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o755 if target.suffix == '.sh' else 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def recover(root):
    root = Path(root).resolve()
    journal_path = root / 'data/deployment-recovery.json'
    if not journal_path.is_file():
        raise ValueError('Không có deployment cần phục hồi.')
    descriptor = os.open(root / 'data/operation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = json.loads(journal_path.read_text())
        snapshot = Path(journal['snapshot']).resolve()
        if snapshot.parent != (root / 'tmp').resolve() or not snapshot.name.startswith('rollback-') or not snapshot.is_dir():
            raise ValueError('Snapshot recovery không nằm trong private tmp của plugin.')
        previous = [safe_relative(value) for value in journal['previous_files']]
        incoming = [safe_relative(value) for value in journal['incoming_files']]
        if not previous:
            raise ValueError('Cài đặt đầu tiên chưa hoàn tất; giữ snapshot và chạy lại installer từ DEV sau khi kiểm tra core adapter.')
        for relative in previous:
            source = snapshot / relative
            if not source.is_file() or source.is_symlink():
                raise ValueError('Snapshot recovery thiếu file hoặc có symlink.')
        for relative in previous:
            restore_file(snapshot / relative, root / relative)
        for relative in set(incoming) - set(previous):
            (root / relative).unlink(missing_ok=True)
        prior = Path(journal['prior_rollback']).resolve()
        release_directory = (root / 'data/releases').resolve()
        if prior.parent != release_directory or not prior.name.startswith('prior-'):
            raise ValueError('Đường dẫn previous rollback không hợp lệ.')
        if prior.exists():
            rollback = release_directory / 'previous'
            if rollback.exists():
                shutil.move(str(rollback), str(root / 'tmp' / ('displaced-' + prior.name)))
            os.replace(prior, rollback)
        subprocess.run([sys.executable, '-B', str(root / 'cli.py'), 'register', journal['panel_path']], check=True)
        journal_path.unlink()
        print('Đã phục hồi mã và adapter. Giữ snapshot để kiểm tra; không đổi credentials hoặc dữ liệu backup.')
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Phục hồi thủ công sau mất điện/SIGKILL trong cập nhật; không import mã runtime có thể bị dở dang.')
    parser.add_argument('--plugin-root', default='/www/server/panel/plugin/shared_drive_backup')
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Chạy bằng root.')
    recover(arguments.plugin_root)
