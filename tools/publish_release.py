import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdb import PLUGIN, REPOSITORY, VERSION


def main():
    parser = argparse.ArgumentParser(description='Cần GH_TOKEN/GITHUB_TOKEN được chủ tài khoản cấp; không đọc token Codex.')
    parser.add_argument('--create-repository', action='store_true')
    parser.add_argument('--publish', action='store_true')
    arguments = parser.parse_args()
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        raise SystemExit('Chưa có GH_TOKEN/GITHUB_TOKEN. Chưa tạo repo hoặc phát hành gì.')

    def request(path, method='GET', document=None, binary=None):
        url = 'https://api.github.com' + path
        body = json.dumps(document).encode() if document is not None else binary
        headers = {'Authorization': 'Bearer ' + token, 'User-Agent': 'SharedDriveBackup-Publisher', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        if body is not None:
            headers['Content-Type'] = 'application/octet-stream' if binary is not None else 'application/json'
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method, headers=headers), timeout=60) as response:
            return json.load(response)

    profile = request('/user')
    if profile['login'].lower() != REPOSITORY.split('/')[0].lower():
        raise SystemExit('Token không thuộc chủ repo đã ghim. Không tạo hoặc push.')
    try:
        repository = request('/repos/' + REPOSITORY)
    except urllib.error.HTTPError as error:
        if error.code != 404 or not arguments.create_repository:
            raise
        repository = request('/user/repos', 'POST', {'name': REPOSITORY.split('/')[1], 'private': True, 'auto_init': False, 'description': 'aaPanel backups to Google Shared drives with native Cron and signed updates'})
    if not repository['private']:
        raise SystemExit('Repo hiện public; cần xác nhận riêng trước khi xuất bản công khai.')
    if not arguments.publish:
        print('Repo private đã kiểm tra: ' + repository['html_url'])
        return
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain']).strip():
        raise SystemExit('Working tree chưa sạch; kiểm tra và commit trước khi publish.')
    askpass = ROOT / 'tmp/github-askpass.sh'
    askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" x-access-token;; *) printf "%s\\n" "$SDB_PUBLISH_TOKEN";; esac\n')
    askpass.chmod(0o700)
    environment = dict(os.environ, GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT='0', SDB_PUBLISH_TOKEN=token)
    try:
        remote = 'https://github.com/' + REPOSITORY + '.git'
        remotes = subprocess.check_output(['git', '-C', str(ROOT), 'remote'], text=True).split()
        if 'origin' not in remotes:
            subprocess.run(['git', '-C', str(ROOT), 'remote', 'add', 'origin', remote], check=True)
        elif subprocess.check_output(['git', '-C', str(ROOT), 'remote', 'get-url', 'origin'], text=True).strip() != remote:
            raise SystemExit('origin không trùng repo đã ghim.')
        subprocess.run(['git', '-C', str(ROOT), 'push', '-u', 'origin', 'main'], env=environment, check=True)
        archive = ROOT / 'tmp/dist' / f'{PLUGIN}-{VERSION}.zip'
        if not archive.is_file() or not Path(str(archive) + '.sig').is_file():
            raise SystemExit('Chưa có gói release và chữ ký. Chạy package_release.py trước.')
        release = request('/repos/' + REPOSITORY + '/releases', 'POST', {'tag_name': 'v' + VERSION, 'target_commitish': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(), 'name': 'Shared Drive Backup ' + VERSION, 'body': (ROOT / 'CHANGELOG.md').read_text(), 'draft': True, 'prerelease': False})
        for file in [archive, Path(str(archive) + '.sig')]:
            upload_url = release['upload_url'].split('{')[0] + '?name=' + file.name
            upload = urllib.request.Request(upload_url, data=file.read_bytes(), method='POST', headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'SharedDriveBackup-Publisher', 'Content-Type': 'application/octet-stream'})
            with urllib.request.urlopen(upload, timeout=90) as response:
                json.load(response)
        release = request('/repos/' + REPOSITORY + '/releases/' + str(release['id']), 'PATCH', {'draft': False})
        print('Đã phát hành: ' + release['html_url'])
    finally:
        askpass.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
