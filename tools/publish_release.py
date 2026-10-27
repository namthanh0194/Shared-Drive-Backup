import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdb import REPOSITORY, VERSION
from tools.package_release import build_archive, sign_archive

def git(*arguments):
    return subprocess.check_output(['git', *arguments], cwd=ROOT, text=True).strip()

def main():
    parser = argparse.ArgumentParser(description='Ký trên VPS, push annotated tag bằng SSH; GitHub Actions xác minh và publish bằng GITHUB_TOKEN của workflow.')
    parser.add_argument('--key', default=str(ROOT / 'data/release-signing-key.pem'))
    parser.add_argument('--publish', action='store_true', help='Push main và tag; không tạo commit hoặc force-push')
    arguments = parser.parse_args()
    if git('status', '--porcelain'):
        raise SystemExit('Working tree chưa sạch. Kiểm tra, kiểm thử và commit trước khi phát hành.')
    if git('branch', '--show-current') != 'main':
        raise SystemExit('Chỉ phát hành từ nhánh main đã kiểm tra.')
    allowed = {'https://github.com/' + REPOSITORY + '.git', 'git@github.com:' + REPOSITORY + '.git', 'git@github-shared-drive:' + REPOSITORY + '.git'}
    if git('remote', 'get-url', 'origin') not in allowed:
        raise SystemExit('origin không trùng repo chính thức.')
    tag = 'v' + VERSION
    if git('tag', '--list', tag):
        raise SystemExit('Tag đã tồn tại. Không thay tag; tăng version trước khi phát hành lại.')
    metadata = sign_archive(build_archive(), arguments.key)
    annotation = ROOT / 'tmp/dist' / (tag + '-metadata.json')
    annotation.write_text(json.dumps(metadata))
    if not arguments.publish:
        print('Đã chuẩn bị gói và chữ ký trong tmp/dist; chưa push tag hoặc phát hành.')
        return
    subprocess.run(['git', 'tag', '-a', tag, '-F', str(annotation)], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', '--atomic', 'origin', 'main', 'refs/tags/' + tag], cwd=ROOT, check=True)
    print('Đã push tag ' + tag + '. Kiểm tra GitHub Actions; chưa khẳng định Release thành công cho đến khi workflow hoàn tất.')

if __name__ == '__main__':
    main()
