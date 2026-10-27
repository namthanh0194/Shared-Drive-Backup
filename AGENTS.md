# Shared Drive Backup — hướng dẫn dự án

## Mục đích và kiến trúc
- `sdb/native.py`: liên kết website và bản backup remote theo schema core; không sửa core/schema hoặc giả thành backup local.
- `sdb/restore.py`: danh sách đã xác minh, giải nén an toàn, thay website đã dừng và journal phục hồi. Journal chưa xử lý phải chặn tác vụ mới/cập nhật; không gọi shell restore thiếu kiểm tra của core.
- Plugin aaPanel độc lập: backup website, MySQL cục bộ và thư mục lên Google Shared drives.
- Repo chính thức: `namthanh0194/Shared-Drive-Backup` (public).
- DEV: `/root/DEV/aapanel-shared-drive-backup`; production: `/www/server/panel/plugin/shared_drive_backup`.
- `shared_drive_backup_main.py`: adapter aaPanel, allowlist tham số và subprocess backend mới mỗi request.
- `sdb/api.py`, `sdb/storage.py`: API và trạng thái root-only; `sdb/drive.py`, `sdb/worker.py`: upload, xác minh, retention và worker.
- `sdb/integration.py`: adapter Cron có kiểm tra ngữ cảnh, ownership và khả năng khôi phục; không chỉnh JS minified của aaPanel.
- `sdb/release.py`: repo đã ghim, cache kiểm tra version, xác minh chữ ký Ed25519, deploy/rollback có journal.
- `index.html` là template; installer nhúng `ui.css` và `ui.js` vào production. Không sửa riêng bản HTML production.

## Quy tắc thay đổi
- Đọc yêu cầu và skill liên quan trước khi thực hiện; yêu cầu phức tạp mới cần đề xuất và xác nhận trước.
- Chỉ sửa trong phạm vi yêu cầu; không chỉnh plugin `gdrive`, OLS, website, database hoặc Cron khác.
- Không tạo nhánh hay commit/push nếu chưa được người dùng yêu cầu. Không force-push hoặc di chuyển tag đã phát hành.
- Dùng `apply_patch`; giữ phong cách hiện tại, không thêm dependency/framework hoặc inline comment không cần thiết.
- Không đổi cơ chế phân tách VPS/nguồn/Cron/nơi lưu. Chỉ dọn backup đúng ownership và đã xác minh checksum.
- Không chạy backup, thử xóa dữ liệu Drive, đổi quyền repo hay thay lịch Cron thật để kiểm thử nếu chưa được cho phép.

## Bí mật và dữ liệu runtime
- Không in hoặc đọc toàn bộ credentials/token/private key vào terminal, log hoặc chat. Chỉ kiểm tra sự tồn tại, quyền file và thông tin tối thiểu.
- Không đưa `data/`, `logs/`, `tmp/`, credentials, SSH keys hoặc khóa ký riêng vào Git/release.
- Khóa ký riêng ở DEV `data/`; chỉ `sdb/release_key.pub` được phát hành. Không tự thay khóa tin cậy.
- Public updater không cần token; quyền push/phát hành của maintainer tách biệt với quyền tải trên VPS.
- Không coi `.gitignore` là bằng chứng bí mật chưa từng vào lịch sử; rà soát trước khi xuất bản.

## Kiểm thử và tiêu chí hoàn thành
- Mọi test, fixture, script thử nghiệm, screenshot, build output và artifact kỹ thuật đặt trong `tmp/`; không commit/deploy chúng.
- Viết regression tái hiện lỗi trước khi sửa. Không bỏ assertion chỉ để test qua; không tuyên bố kết quả chưa kiểm tra.
- Python: `TMPDIR="$PWD/tmp" /www/server/panel/pyenv/bin/python -B -W ignore::DeprecationWarning -m unittest discover -s tmp -p 'test_*.py' -q`.
- UI: `node --check ui.js`; Playwright: `PLAYWRIGHT_BROWSERS_PATH="$PWD/tmp/pw-browsers" node tmp/test_browser.cjs`.
- Test UI cần phân biệt dữ liệu giả với Google thật; kiểm tra popup aaPanel, tab dài, scroll, theme và mobile khi thay layout.
- Updater: kiểm tra có/không có bản mới, cache/force, lỗi mạng/rate-limit, chữ ký sai, gói thiếu file và bảo toàn runtime khi update/rollback.

## Version, deploy và phát hành
- Đồng bộ version ở `sdb/__init__.py`, `info.json`, nhãn dự phòng trong `index.html` và `CHANGELOG.md`.
- Đóng gói chỉ theo allowlist; ZIP và chữ ký trong `tmp/dist/`. Release phải có cả hai, không chỉ tag Git.
- Khóa ký ở lại VPS maintainer. Quy trình phát hành phải xác minh đúng commit, version, repo, digest và chữ ký trước khi publish.
- Chạy kiểm thử, kiểm tra diff và bí mật trước khi deploy. Không thay cấu hình, credentials, đích lưu hay lịch Cron khi nâng cấp.
- Không reload panel khi chỉ đổi UI/backend subprocess; chỉ reload khi adapter core thay đổi và installer báo cần thiết.
- Cập nhật `LEDGER.md` với bằng chứng thực tế; báo rõ những bước còn cần quyền hoặc người dùng thực hiện.
