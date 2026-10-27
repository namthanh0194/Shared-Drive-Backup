import sqlite3
from pathlib import Path

from sdb import PLUGIN


class Panel:
    def __init__(self, root='/www/server/panel'):
        self.root = Path(root)

    def connect(self):
        connection = sqlite3.connect('file:' + str(self.root / 'data/default.db') + '?mode=ro', uri=True, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def inventory(self):
        with self.connect() as connection:
            sites = [dict(row) for row in connection.execute('SELECT name,path FROM sites ORDER BY name')]
            databases = [dict(row) for row in connection.execute("SELECT name,type FROM databases WHERE LOWER(type)='mysql' AND (sid IS NULL OR sid=0) ORDER BY name")]
        return {'sites': sites, 'databases': databases}

    def tasks(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT id,name,sType,sName,type,where1,where_hour,where_minute,status,save,backupTo,echo FROM crontab WHERE backupTo=? ORDER BY id DESC', (PLUGIN,))]

    def source(self, kind, target):
        with self.connect() as connection:
            if kind == 'site':
                row = connection.execute('SELECT path FROM sites WHERE name=?', (target,)).fetchone()
                if row is None:
                    raise ValueError('Website không tồn tại trong aaPanel.')
                return row['path']
            if kind == 'database':
                row = connection.execute("SELECT name FROM databases WHERE name=? AND LOWER(type)='mysql' AND (sid IS NULL OR sid=0)", (target,)).fetchone()
                if row is None:
                    raise ValueError('Database MySQL local không tồn tại trong aaPanel. Chưa hỗ trợ database trên máy chủ từ xa.')
                return row['name']
        if kind == 'path':
            return target
        raise ValueError('Loại backup không hợp lệ.')

    def mysql_password(self):
        with self.connect() as connection:
            row = connection.execute('SELECT mysql_root FROM config WHERE id=1').fetchone()
        if row is None or not row['mysql_root']:
            raise ValueError('Không tìm thấy thông tin MySQL do aaPanel quản lý.')
        return row['mysql_root']

    def cron_details(self, echo):
        if not echo:
            return None
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM crontab WHERE echo=? AND backupTo=?', (echo, PLUGIN)).fetchone()
        return dict(row) if row else None
