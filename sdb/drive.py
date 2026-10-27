import hashlib
import re
from pathlib import Path

from sdb import PLUGIN

FOLDER = 'application/vnd.google-apps.folder'
SCOPES = ['https://www.googleapis.com/auth/drive']


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,200}', value):
        raise ValueError('ID Google Drive không hợp lệ.')
    return value


def query_string(value):
    return str(value).replace('\\', '\\\\').replace("'", "\\'")


def validate_credentials(document):
    if not isinstance(document, dict) or document.get('type') not in {'service_account', 'authorized_user'}:
        raise ValueError('Cần JSON service_account hoặc authorized_user hợp lệ.')
    if document.get('token_uri', 'https://oauth2.googleapis.com/token') not in {
        'https://oauth2.googleapis.com/token', 'https://accounts.google.com/o/oauth2/token',
    }:
        raise ValueError('Chỉ cho phép endpoint xác thực chính thức của Google.')
    if document.get('universe_domain', 'googleapis.com') != 'googleapis.com':
        raise ValueError('Chỉ hỗ trợ Google API public.')
    required = {'client_email', 'private_key', 'token_uri'} if document['type'] == 'service_account' else {'client_id', 'client_secret', 'refresh_token'}
    if not all(isinstance(document.get(key), str) and document[key] for key in required):
        raise ValueError('JSON xác thực thiếu trường bắt buộc.')
    return document


def make_client(store):
    from google.auth.transport.requests import Request
    from google.oauth2 import credentials, service_account
    from googleapiclient.discovery import build
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    document = validate_credentials(store.secret('credentials'))
    if document['type'] == 'service_account':
        authentication = service_account.Credentials.from_service_account_info(document, scopes=SCOPES)
    else:
        authentication = credentials.Credentials.from_authorized_user_info(document, scopes=SCOPES)
    if not authentication.valid:
        authentication.refresh(Request())
    transport = AuthorizedHttp(authentication, http=httplib2.Http(timeout=90))
    return DriveClient(build('drive', 'v3', http=transport, cache_discovery=False))


def digests(filename):
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with open(filename, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


class DriveClient:
    def __init__(self, service):
        self.service = service

    def drives(self, token=''):
        return self.service.drives().list(pageSize=100, pageToken=token or None, fields='nextPageToken,drives(id,name)').execute(num_retries=3)

    def folder(self, drive_id, folder_id, writable=False):
        identifier(drive_id)
        identifier(folder_id)
        metadata = self.service.files().get(
            fileId=folder_id, supportsAllDrives=True,
            fields='id,name,mimeType,driveId,parents,trashed,capabilities(canAddChildren)',
        ).execute(num_retries=3)
        if metadata.get('driveId') != drive_id or metadata.get('mimeType') != FOLDER or metadata.get('trashed'):
            raise ValueError('Thư mục không thuộc Bộ nhớ dùng chung đã chọn hoặc không còn tồn tại.')
        if writable and not metadata.get('capabilities', {}).get('canAddChildren'):
            raise ValueError('Tài khoản không có quyền tạo file trong thư mục này.')
        return metadata

    def folders(self, drive_id, parent_id, token=''):
        self.folder(drive_id, parent_id)
        return self.service.files().list(
            corpora='drive', driveId=drive_id, includeItemsFromAllDrives=True, supportsAllDrives=True,
            q=f"'{identifier(parent_id)}' in parents and trashed=false and mimeType='{FOLDER}'",
            pageSize=100, pageToken=token or None, orderBy='name_natural',
            fields='nextPageToken,files(id,name,parents,capabilities(canAddChildren))',
        ).execute(num_retries=3)

    def breadcrumbs(self, drive_id, folder_id):
        result = []
        visited = set()
        current = folder_id
        for depth in range(64):
            if current in visited:
                raise ValueError('Cấu trúc thư mục không hợp lệ.')
            visited.add(current)
            metadata = self.folder(drive_id, current)
            result.append({'id': metadata['id'], 'name': metadata.get('name', metadata['id'])})
            if current == drive_id:
                return list(reversed(result))
            parents = metadata.get('parents', [])
            if not parents:
                return list(reversed(result))
            current = parents[0]
        raise ValueError('Thư mục quá sâu.')

    def create_folder(self, drive_id, parent_id, name, properties=None):
        self.folder(drive_id, parent_id, writable=True)
        name = str(name).strip()
        if not name or len(name) > 150 or '/' in name or any(ord(character) < 32 for character in name):
            raise ValueError('Tên thư mục không hợp lệ.')
        body = {'name': name, 'mimeType': FOLDER, 'parents': [parent_id]}
        if properties:
            body['appProperties'] = properties
        return self.service.files().create(body=body, supportsAllDrives=True, fields='id,name,parents,driveId').execute(num_retries=3)

    def owned_folder(self, drive_id, parent_id, name, namespace):
        self.folder(drive_id, parent_id, writable=True)
        result = self.service.files().list(
            corpora='drive', driveId=drive_id, includeItemsFromAllDrives=True, supportsAllDrives=True,
            q=f"'{identifier(parent_id)}' in parents and trashed=false and mimeType='{FOLDER}' and appProperties has {{ key='sdb_namespace' and value='{query_string(namespace)}' }}",
            pageSize=100, fields='files(id,name)',
        ).execute(num_retries=3)
        folders = result.get('files', [])
        if folders:
            return folders[0]['id']
        return self.create_folder(drive_id, parent_id, name, {'sdb_plugin': PLUGIN, 'sdb_namespace': namespace})['id']

    def task_folder(self, configuration, task):
        server_id = configuration['server_id']
        drive_id = configuration['drive_id']
        server = self.owned_folder(drive_id, configuration['folder_id'], f"VPS-{server_id[:12]}", server_id)
        return self.owned_folder(drive_id, server, task, server_id + ':' + task)

    def upload(self, filename, configuration, task, progress=None):
        from googleapiclient.http import MediaFileUpload

        parent_id = self.task_folder(configuration, task)
        md5, sha256 = digests(filename)
        size = Path(filename).stat().st_size
        properties = {'sdb_plugin': PLUGIN, 'sdb_server': configuration['server_id'], 'sdb_task': task, 'sdb_sha256': sha256}
        media = MediaFileUpload(str(filename), mimetype='application/octet-stream', chunksize=8 * 1024 * 1024, resumable=True)
        request = self.service.files().create(
            body={'name': Path(filename).name, 'parents': [parent_id], 'appProperties': properties},
            media_body=media, supportsAllDrives=True, fields='id,name,size,md5Checksum,parents,webViewLink',
        )
        response = None
        try:
            while response is None:
                status, response = request.next_chunk(num_retries=5)
                if status and progress:
                    progress(round(status.progress() * 100))
        finally:
            stream = media.stream()
            if stream:
                stream.close()
        metadata = self.service.files().get(
            fileId=response['id'], supportsAllDrives=True, fields='id,name,size,md5Checksum,parents,webViewLink,appProperties,driveId',
        ).execute(num_retries=3)
        if metadata.get('md5Checksum') != md5 or int(metadata.get('size', -1)) != size or parent_id not in metadata.get('parents', []) or metadata.get('driveId') != configuration['drive_id']:
            raise ValueError('Upload chưa được xác minh checksum/kích thước/thư mục. Giữ bản local, không dọn bản cũ.')
        verified_properties = {**properties, 'sdb_verified': 'true', 'sdb_md5': md5}
        self.service.files().update(fileId=metadata['id'], supportsAllDrives=True, body={'appProperties': verified_properties}, fields='id').execute(num_retries=3)
        metadata['appProperties'] = verified_properties
        return {**metadata, 'sha256': sha256, 'parent_id': parent_id}

    def prune(self, configuration, task, parent_id, keep, exclude_id):
        if not configuration['prune_enabled']:
            return 0
        if not 1 <= int(keep) <= 10000:
            raise ValueError('Số bản giữ lại không hợp lệ.')
        files = []
        token = None
        while True:
            result = self.service.files().list(
                corpora='drive', driveId=configuration['drive_id'], supportsAllDrives=True, includeItemsFromAllDrives=True,
                q=f"'{identifier(parent_id)}' in parents and trashed=false and mimeType!='{FOLDER}' and appProperties has {{ key='sdb_plugin' and value='{PLUGIN}' }} and appProperties has {{ key='sdb_server' and value='{query_string(configuration['server_id'])}' }} and appProperties has {{ key='sdb_task' and value='{query_string(task)}' }} and appProperties has {{ key='sdb_verified' and value='true' }}",
                orderBy='createdTime desc', pageSize=100, pageToken=token,
                fields='nextPageToken,files(id,parents,appProperties,driveId,md5Checksum)',
            ).execute(num_retries=3)
            files.extend(result.get('files', []))
            token = result.get('nextPageToken')
            if not token:
                break
        verified_files = []
        for item in files:
            properties = item.get('appProperties', {})
            if properties.get('sdb_verified') != 'true' or not item.get('md5Checksum') or properties.get('sdb_md5') != item['md5Checksum']:
                continue
            if parent_id not in item.get('parents', []) or item.get('driveId') != configuration['drive_id']:
                raise ValueError('Từ chối dọn file ngoài thư mục backup.')
            if properties.get('sdb_plugin') != PLUGIN or properties.get('sdb_server') != configuration['server_id'] or properties.get('sdb_task') != task:
                raise ValueError('Từ chối dọn file không thuộc tác vụ/VPS này.')
            verified_files.append(item)
        removed = 0
        for item in verified_files[int(keep):]:
            if item['id'] == exclude_id:
                continue
            fresh = self.service.files().get(fileId=item['id'], supportsAllDrives=True, fields='id,parents,appProperties,driveId,md5Checksum,trashed').execute(num_retries=3)
            if fresh.get('trashed') or fresh.get('parents') != item.get('parents') or fresh.get('driveId') != item.get('driveId') or fresh.get('appProperties') != item.get('appProperties') or fresh.get('md5Checksum') != item.get('md5Checksum'):
                raise ValueError('Metadata backup đã thay đổi trước khi dọn. Dừng dọn để bảo vệ dữ liệu.')
            self.service.files().update(fileId=item['id'], supportsAllDrives=True, body={'trashed': True}, fields='id,trashed').execute(num_retries=3)
            removed += 1
        return removed

    def download(self, remote_id, destination, expected_md5, expected_sha256, configuration, task, parent_id):
        from googleapiclient.http import MediaIoBaseDownload

        identifier(remote_id)
        metadata = self.service.files().get(fileId=remote_id, supportsAllDrives=True, fields='id,size,md5Checksum,appProperties,parents,driveId,trashed').execute(num_retries=3)
        properties = metadata.get('appProperties', {})
        if metadata.get('trashed') or metadata.get('driveId') != configuration['drive_id'] or parent_id not in metadata.get('parents', []):
            raise ValueError('File không còn ở đúng thư mục backup.')
        if properties.get('sdb_plugin') != PLUGIN or properties.get('sdb_server') != configuration['server_id'] or properties.get('sdb_task') != task:
            raise ValueError('File không thuộc tác vụ/VPS này.')
        if metadata.get('md5Checksum') != expected_md5:
            raise ValueError('File trên Drive đã thay đổi.')
        request = self.service.files().get_media(fileId=remote_id, supportsAllDrives=True)
        try:
            with open(destination, 'xb') as output:
                download = MediaIoBaseDownload(output, request, chunksize=8 * 1024 * 1024)
                done = False
                while not done:
                    status, done = download.next_chunk(num_retries=5)
            md5, sha256 = digests(destination)
            if md5 != expected_md5 or sha256 != expected_sha256:
                raise ValueError('Checksum bản tải về không khớp.')
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise
