import re
import sqlite3
import time
from pathlib import Path

from sdb import PLUGIN


def sites(panel):
    with panel.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT id,name,path,status,project_type FROM sites WHERE UPPER(project_type)='PHP' ORDER BY name")]


def site_by_name(panel, name):
    with panel.connect() as connection:
        rows = connection.execute('SELECT id,name,path,status,project_type FROM sites WHERE name=?', (name,)).fetchall()
    if len(rows) != 1:
        raise ValueError('Không xác định được duy nhất website trong aaPanel.')
    return dict(rows[0])


def site(panel, site_id):
    if isinstance(site_id, bool) or not str(site_id).isdigit() or int(site_id) < 1:
        raise ValueError('ID website không hợp lệ.')
    with panel.connect() as connection:
        row = connection.execute('SELECT id,name,path,status,project_type FROM sites WHERE id=?', (int(site_id),)).fetchone()
    if row is None or str(row['project_type']).upper() != 'PHP':
        raise ValueError('Chỉ hỗ trợ website PHP hiện có trong aaPanel.')
    return dict(row)


def check_identity(current, saved):
    if not isinstance(saved, dict) or any(str(current.get(key)) != str(saved.get(key)) for key in ('id', 'name', 'path')):
        raise ValueError('Website đã đổi ID/tên/đường dẫn hoặc backup chưa được liên kết. Không tự chọn đích khôi phục.')


def backup_connection(panel):
    connection = sqlite3.connect('file:' + str(panel.root / 'data/default.db') + '?mode=rw', uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(backup)')}
    if not {'id', 'type', 'name', 'pid', 'filename', 'size', 'addtime', 'ps', 'backup_type'}.issubset(columns):
        connection.close()
        raise ValueError('Schema backup aaPanel không tương thích. Không sửa database/core.')
    return connection


def marker(store, job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('ID backup không hợp lệ.')
    return PLUGIN + ':' + store.config()['server_id'] + ':' + job_id


def register_backup(store, panel, job):
    details = job['details']
    kind = details.get('kind')
    saved = details.get('site' if kind == 'site' else 'database')
    if job['status'] != 'success' or kind not in {'site', 'database'} or not details.get('verified') or not saved:
        raise ValueError('Chỉ đăng ký bản backup website/database đã xác minh và liên kết.')
    remote = details['remote']
    extension = r'\.tar\.gz' if kind == 'site' else r'\.sql\.gz'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+' + extension, remote.get('name', '')) or not re.fullmatch(r'[A-Za-z0-9_-]{3,200}', remote.get('id', '')) or int(remote.get('size', 0)) <= 0:
        raise ValueError('Metadata backup không hợp lệ.')
    if kind == 'site':
        current = site(panel, saved['id'])
        check_identity(current, saved)
    else:
        from sdb.databases import database_name
        current = panel.database_identity(database_name(details['target']))
        if current != saved:
            raise ValueError('Database đã đổi ID/tên; không đăng ký vào database khác.')
        configuration = store.config()
        if not configuration.get('configured') or details.get('destination') != configuration['folder_id']:
            raise ValueError('Chọn lại đúng nơi lưu gốc trước khi đồng bộ database.')
    backup_type = 0 if kind == 'site' else 1
    ownership = marker(store, job['id'])
    legacy_filename = str(store.tmp / remote['name']) + '|' + PLUGIN + '|' + remote['id']
    filename = legacy_filename if kind == 'site' else PLUGIN + '|' + remote['name']
    connection = backup_connection(panel)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            if kind == 'site':
                row = connection.execute('SELECT id,name,path FROM sites WHERE id=?', (current['id'],)).fetchone()
                check_identity(dict(row) if row else {}, saved)
            else:
                row = connection.execute("SELECT id,name FROM databases WHERE id=? AND LOWER(type)='mysql' AND (sid IS NULL OR sid=0)", (current['id'],)).fetchone()
                if row is None or dict(row) != saved:
                    raise ValueError('Database đã đổi ID/tên; không đăng ký vào database khác.')
            existing = connection.execute('SELECT id,filename FROM backup WHERE type=? AND pid=? AND ps=?', (backup_type, current['id'], ownership)).fetchall()
            if len(existing) > 1 or any((entry['filename'].split('|')[1:2] != [PLUGIN] if kind == 'site' else entry['filename'] not in {filename, legacy_filename}) for entry in existing):
                raise ValueError('Bản ghi native bị thay đổi; không ghi đè.')
            values = (remote['name'], filename, int(remote['size']), time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['created'])))
            if existing:
                connection.execute('UPDATE backup SET name=?,filename=?,size=?,addtime=? WHERE id=?', (*values, existing[0]['id']))
                return existing[0]['id']
            return connection.execute('INSERT INTO backup (type,pid,name,filename,size,addtime,ps,backup_type) VALUES (?,?,?,?,?,?,?,0)', (backup_type, current['id'], *values, ownership)).lastrowid
    finally:
        connection.close()


def remove_backup(store, panel, job_id, kind='site'):
    if kind not in {'site', 'database'}:
        raise ValueError('Loại backup native không hợp lệ.')
    if kind == 'database':
        job = store.job(job_id)
        if job['details'].get('kind') != 'database':
            raise ValueError('Tác vụ không phải backup database.')
        remote = job['details']['remote']
        filenames = {PLUGIN + '|' + remote['name'], str(store.tmp / remote['name']) + '|' + PLUGIN + '|' + remote['id']}
    connection = backup_connection(panel)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('SELECT id,filename FROM backup WHERE type=? AND ps=?', (0 if kind == 'site' else 1, marker(store, job_id))).fetchall()
            for row in rows:
                if (row['filename'].split('|')[1:2] == [PLUGIN] if kind == 'site' else row['filename'] in filenames):
                    connection.execute('DELETE FROM backup WHERE id=?', (row['id'],))
    finally:
        connection.close()
