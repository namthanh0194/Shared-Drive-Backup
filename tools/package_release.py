import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sdb import PLUGIN, VERSION
from sdb.release import code_files, validate_archive, validate_code, verify_signature


def main():
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key

    parser = argparse.ArgumentParser()
    parser.add_argument('--key', required=True, help='Ed25519 private PEM nằm ngoài gói cài và Git')
    arguments = parser.parse_args()
    validate_code(ROOT)
    private = load_pem_private_key(Path(arguments.key).read_bytes(), password=None)
    public = bytes.fromhex((ROOT / 'sdb/release_key.pub').read_text().strip())
    if private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != public:
        raise SystemExit('Private key không trùng khóa công khai đã ghim.')
    output = ROOT / 'tmp/dist'
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f'{PLUGIN}-{VERSION}.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, source in sorted(code_files(ROOT).items()):
            archive.write(source, PLUGIN + '/' + relative)
    with zipfile.ZipFile(archive_path) as archive:
        validate_archive(archive)
    payload = archive_path.read_bytes()
    signature = private.sign(payload)
    signature_path = Path(str(archive_path) + '.sig')
    signature_path.write_bytes(signature)
    verify_signature(payload, signature, public)
    print(json.dumps({'version': VERSION, 'archive': str(archive_path), 'signature': str(signature_path), 'files': len(code_files(ROOT))}))


if __name__ == '__main__':
    main()
