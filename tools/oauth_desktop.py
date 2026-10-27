import argparse
import json
import os
from pathlib import Path


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    parser = argparse.ArgumentParser(description='Chạy trên máy cá nhân có trình duyệt, không dùng callback HTTP của VPS.')
    parser.add_argument('client_json', help='OAuth Desktop client JSON do bạn tạo trong Google Cloud')
    parser.add_argument('output', help='Đường dẫn ghi authorized_user JSON; không đưa vào Git')
    arguments = parser.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(arguments.client_json, ['https://www.googleapis.com/auth/drive'])
    credentials = flow.run_local_server(host='127.0.0.1', port=0, access_type='offline', prompt='consent')
    document = json.loads(credentials.to_json())
    document['type'] = 'authorized_user'
    output = Path(arguments.output)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump(document, target)
    print('Đã lưu authorized_user JSON. Chuyển an toàn lên VPS; không gửi qua HTTP hoặc commit vào Git.')


if __name__ == '__main__':
    main()
