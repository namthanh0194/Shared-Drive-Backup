import json
import os
import subprocess
import sys
from pathlib import Path


class shared_drive_backup_main:
    def _invoke(self, method, arguments):
        from flask import request
        allowed = {
            'status', 'save_credentials', 'list_drives', 'browse_folders', 'create_folder', 'set_destination',
            'connection_check', 'save_settings', 'inventory', 'history', 'start_backup', 'start_download',
            'tasks', 'save_github_token', 'check_update', 'update', 'rollback', 'repair_integration', 'read_log',
        }
        if method not in allowed:
            return {'status': False, 'msg': 'Thao tác không được phép.'}
        fields = {
            'save_credentials': {'document'}, 'list_drives': {'page_token'},
            'browse_folders': {'drive_id', 'folder_id', 'page_token'},
            'create_folder': {'drive_id', 'parent_id', 'folder_name'},
            'set_destination': {'drive_id', 'folder_id'},
            'save_settings': {'server_name', 'prune_enabled', 'min_free_mb', 'max_archive_gb', 'keep_local'},
            'start_backup': {'kind', 'target', 'keep', 'exclusions'},
            'start_download': {'job_id'}, 'read_log': {'job_id'},
            'save_github_token': {'token'}, 'update': {'version'},
        }
        source = arguments.get_items() if hasattr(arguments, 'get_items') else (arguments or {})
        payload = {key: source[key] for key in fields.get(method, set()) if key in source}
        if method == 'create_folder' and 'folder_name' in payload:
            payload['name'] = payload.pop('folder_name')
        payload['_secure_transport'] = bool(request.is_secure)
        root = Path(__file__).resolve().parent
        python = '/www/server/panel/pyenv/bin/python'
        try:
            result = subprocess.run([python, '-B', str(root / 'cli.py'), 'api'], input=json.dumps({'method': method, 'arguments': payload}), text=True, capture_output=True, timeout=180, cwd=str(root))
            if result.returncode != 0:
                return {'status': False, 'msg': 'Backend lỗi. Kiểm tra cài đặt Python và dependencies.'}
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            return {'status': False, 'msg': 'Thao tác vượt thời gian chờ. Kiểm tra trạng thái trước khi thử lại.'}
        except Exception:
            return {'status': False, 'msg': 'Không đọc được kết quả backend.'}


def endpoint(method):
    def action(self, arguments):
        return self._invoke(method, arguments)
    return action


for method_name in [
    'status', 'save_credentials', 'list_drives', 'browse_folders', 'create_folder', 'set_destination',
    'connection_check', 'save_settings', 'inventory', 'history', 'start_backup', 'start_download',
    'tasks', 'save_github_token', 'check_update', 'update', 'rollback', 'repair_integration', 'read_log',
]:
    setattr(shared_drive_backup_main, method_name, endpoint(method_name))


if __name__ == '__main__':
    os.umask(0o077)
    from cli import main
    sys.exit(main())
