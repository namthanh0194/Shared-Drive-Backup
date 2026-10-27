import argparse
import base64
import binascii
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sdb import PLUGIN, VERSION
from sdb.release import code_files, validate_archive, validate_code, verify_signature

def build_archive(root=ROOT):
    root = Path(root)
    validate_code(root, VERSION)
    output = root / 'tmp/dist'
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f'{PLUGIN}-{VERSION}.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        for relative, source in sorted(code_files(root).items()):
            entry = zipfile.ZipInfo(PLUGIN + '/' + relative, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o100755 if source.suffix == '.sh' else 0o100644) << 16
            archive.writestr(entry, source.read_bytes())
    with zipfile.ZipFile(archive_path) as archive:
        validate_archive(archive)
    return archive_path

def verify_release_metadata(payload, metadata, public):
    if not isinstance(metadata, dict) or metadata.get('version') != VERSION or metadata.get('sha256') != hashlib.sha256(payload).hexdigest():
        raise ValueError('Version hoặc SHA-256 của tag không khớp gói phát hành.')
    try:
        signature = base64.b64decode(metadata['signature'], validate=True)
    except (KeyError, ValueError, TypeError, binascii.Error):
        raise ValueError('Tag thiếu chữ ký hợp lệ.') from None
    verify_signature(payload, signature, public)
    return signature

def sign_archive(archive_path, key_path):
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key

    private = load_pem_private_key(Path(key_path).read_bytes(), password=None)
    public = bytes.fromhex((ROOT / 'sdb/release_key.pub').read_text().strip())
    if private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != public:
        raise ValueError('Private key không trùng khóa công khai đã ghim.')
    payload = archive_path.read_bytes()
    metadata = {'version': VERSION, 'sha256': hashlib.sha256(payload).hexdigest(), 'signature': base64.b64encode(private.sign(payload)).decode()}
    signature = verify_release_metadata(payload, metadata, public)
    Path(str(archive_path) + '.sig').write_bytes(signature)
    return metadata

def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--key', help='Ed25519 private PEM nằm ngoài gói cài và Git')
    mode.add_argument('--verify-tag', help='Tái tạo gói và xác minh chữ ký công khai trong annotated tag')
    arguments = parser.parse_args()
    archive_path = build_archive()
    if arguments.key:
        sign_archive(archive_path, arguments.key)
    else:
        tag = arguments.verify_tag
        if tag != 'v' + VERSION:
            raise SystemExit('Tag không khớp version trong mã nguồn.')
        reference = 'refs/tags/' + tag
        if subprocess.check_output(['git', 'cat-file', '-t', reference], cwd=ROOT, text=True).strip() != 'tag':
            raise SystemExit('Release cần annotated tag có chữ ký.')
        commit = subprocess.check_output(['git', 'rev-parse', reference + '^{commit}'], cwd=ROOT, text=True).strip()
        if commit != subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip():
            raise SystemExit('Checkout không trùng commit đã ký.')
        metadata = json.loads(subprocess.check_output(['git', 'for-each-ref', '--format=%(contents)', reference], cwd=ROOT, text=True))
        public = bytes.fromhex((ROOT / 'sdb/release_key.pub').read_text().strip())
        signature = verify_release_metadata(archive_path.read_bytes(), metadata, public)
        Path(str(archive_path) + '.sig').write_bytes(signature)
    print(json.dumps({'version': VERSION, 'archive': str(archive_path), 'signature': str(archive_path) + '.sig', 'files': len(code_files(ROOT))}))

if __name__ == '__main__':
    main()
