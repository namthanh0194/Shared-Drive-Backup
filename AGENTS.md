# Shared Drive Backup — hướng dẫn dự án

## Phạm vi và tài liệu tham chiếu
- Plugin aaPanel độc lập: backup website, MySQL cục bộ và thư mục lên Google Shared drives.
- Repo chính thức: `namthanh0194/Shared-Drive-Backup` (public).
- DEV: `/root/DEV/aapanel-shared-drive-backup`; production: `/www/server/panel/plugin/shared_drive_backup`.
- Tham chiếu nền tảng: [aaPanel Linux Panel Plug-in development documentation v1.1](https://www.aapanel.com/pkg/aaPanel_Linux_Panel_Plug-in_development_documentation_v1.1.pdf), đặc biệt trang 1–2 về cấu trúc/giao tiếp và trang 8–10 về routing/static/template.
- PDF là quy ước nền tảng, không phải cam kết tương thích với mọi phiên bản aaPanel. Trước khi dùng API/module nội bộ, kiểm tra mã và chữ ký thực tế trên phiên bản panel mục tiêu; không chép nguyên ví dụ cũ hoặc suy đoán API.
- Giữ kiến trúc và các ràng buộc an toàn của dự án. Không đổi plugin Python sang PHP, thêm framework hoặc chuyển sang cấu trúc demo chỉ để giống tài liệu.

## Cấu trúc plugin và metadata
- Tên định danh là `shared_drive_backup`: thư mục cài đặt và `info.json.name` phải trùng nhau; backend là `shared_drive_backup_main.py`, class là `shared_drive_backup_main`.
- `index.html` là giao diện plugin; `ui.css` và `ui.js` là source riêng. Installer nhúng chúng vào HTML production; không sửa riêng bản HTML production hoặc đổi sang tải asset ngoài khi chưa được yêu cầu.
- `info.json` phải là JSON hợp lệ, không có comment; giữ các trường `title`, `name`, `ps`, `versions`, `checks`, `author`, `home` và metadata bổ sung hiện có mà panel sử dụng.
- `checks` trỏ tới file nhận diện cài đặt của dự án: `/www/server/panel/plugin/shared_drive_backup/shared_drive_backup_main.py`. Không nhầm `versions` của aaPanel với khóa `version` tự đặt.
- `icon.png` là icon plugin; chỉ đăng ký/cập nhật icon thuộc plugin này. Không ghi đè icon hoặc tạo phụ thuộc vào plugin `gdrive`.
- `install.sh` là entry cài đặt và hỗ trợ đối số `uninstall`; `uninstall.sh` là entry gỡ đăng ký riêng của dự án. Giữ việc gọi installer/backend hiện có, không nhân đôi logic triển khai trong shell.
- `cli.py`, `sdb/`, `tools/` là phần triển khai riêng, không phải cấu trúc bắt buộc của aaPanel. Không di chuyển hoặc đổi tên nếu không phục vụ trực tiếp yêu cầu.

## Backend và giao tiếp với aaPanel
- Phương thức được panel gọi nhận đối tượng tham số theo giao diện `self, args` (mã hiện tại dùng tên `arguments`); tương thích `get_items()` của panel và dict dùng trong kiểm thử.
- `shared_drive_backup_main.py`: adapter aaPanel, allowlist tham số và subprocess backend mới mỗi request.
- Endpoint mới phải được khai báo rõ và bổ sung allowlist đúng trường cần thiết; không dispatch tùy ý tên hàm hoặc chuyển toàn bộ request xuống backend. Không vô tình đưa helper nội bộ thành endpoint.
- Dùng Python của panel tại `/www/server/panel/pyenv/bin/python`. Giữ dữ liệu truyền qua JSON và `subprocess` dạng danh sách đối số; không ghép dữ liệu người dùng vào chuỗi shell hoặc `public.ExecShell`.
- Phản hồi thông thường trả dữ liệu JSON-serializable, không trả object Python tùy ý hoặc serialize lặp. Giữ contract hiện có: thành công `status: true` kèm `data`, thất bại `status: false` kèm `msg`; không tự đổi sang định dạng khác chỉ vì ví dụ dùng `public.ReturnMsg`.
- Tác vụ backup/restore dài chạy qua worker và cơ chế trạng thái hiện có, không chặn request panel. Không thay worker bằng `panelTask` chỉ vì tài liệu có ví dụ hàng đợi.
- Chỉ dùng `public`, `public.M`, `panelTask` hoặc module core khi thực sự cần và đã kiểm tra tương thích. Không dùng helper tiện ích để bỏ qua ownership, kiểm tra đường dẫn, checksum hoặc xác thực của dự án.

## Frontend, routing và quyền truy cập
- Frontend gọi plugin bằng POST qua route panel hiện có: `/plugin?action=a&s=<method>&name=shared_drive_backup`; giữ session cùng origin, URL phân biệt hoa/thường và encode tham số định tuyến.
- `action`, `s`, `name`, `a` là tên dành cho định tuyến, không dùng làm trường nghiệp vụ trong POST body. Ví dụ tạo thư mục gửi `folder_name`; adapter mới đổi thành `name` nội bộ.
- Dynamic routing `/<plugin>/<method>[.json|.html]` không tự kế thừa quyền session panel theo tài liệu v1.1. Không tự thêm đường truy cập này; nếu được yêu cầu, phải kiểm tra cơ chế `_check` trên panel mục tiêu và kiểm thử từ chối truy cập trái phép trước khi dùng.
- Không coi root-only filesystem, POST hoặc kiểm tra HTTPS là thay thế cho xác thực/phân quyền request. Giữ kiểm tra transport và không mở endpoint credentials, backup, restore hay cập nhật ra công khai.
- Nếu cần `static/`, chỉ chứa asset công khai; không đặt credentials, log, backup hoặc dữ liệu runtime trong đó. Không tạo `static/` hay `templates/` nếu cách nhúng giao diện hiện tại đáp ứng yêu cầu.
- Nếu dùng template dynamic routing, đối chiếu quy tắc Jinja2 của panel: `templates/<method>.html`, dữ liệu qua biến `data`; tài liệu v1.1 không hỗ trợ `extends`. Không mặc định `index.html` hiện tại cần chuyển sang cơ chế này.
- CSS/JS phải được giới hạn trong giao diện plugin, không tác động popup, theme hoặc handler của plugin khác. Giữ xử lý lỗi mạng/session và không đưa dữ liệu người dùng vào HTML chưa escape.

## Kiến trúc nghiệp vụ và tích hợp core
- `sdb/databases.py`: liên kết website/database, manifest theo đợt, retention bảo vệ thành viên, restore cùng tên có snapshot trước DROP/import và journal. `tools/recover_database.py` chỉ phục hồi/gỡ journal khi quản trị viên xác nhận rõ; không tự chạy lại import lỗi hoặc đổi cấu hình MySQL toàn cục.
- `sdb/api.py`, `sdb/storage.py`: API và trạng thái root-only; `sdb/drive.py`, `sdb/worker.py`: upload, xác minh, retention và worker.
- `sdb/native.py`: liên kết website và bản backup remote theo schema core; không sửa core/schema hoặc giả thành backup local.
- `sdb/restore.py`: danh sách đã xác minh, giải nén an toàn, thay website đã dừng và journal phục hồi. Journal chưa xử lý phải chặn tác vụ mới/cập nhật; không gọi shell restore thiếu kiểm tra của core.
- `sdb/integration.py`: adapter Cron có kiểm tra ngữ cảnh, ownership và khả năng khôi phục; không chỉnh JS minified của aaPanel.
- Không sửa trực tiếp core/schema để thêm tính năng; thay đổi tích hợp đã được duyệt phải qua adapter có kiểm tra phiên bản/ngữ cảnh và khả năng khôi phục. Không sửa core thủ công hoặc cập nhật module core hàng loạt.
- `sdb/release.py`: repo đã ghim, cache kiểm tra version, xác minh chữ ký Ed25519, deploy/rollback có journal.

## Quy tắc thay đổi
- Đọc yêu cầu và skill liên quan trước khi thực hiện; nêu skill đã dùng và vai trò thực tế. Với yêu cầu phức tạp, lượt đầu chỉ phân tích/đề xuất phạm vi, rủi ro và cách xác minh; chờ người dùng trả lời `ok` mới thực thi.
- Yêu cầu đơn giản, rõ phạm vi và không có rủi ro đáng kể được thực hiện ngay. Không tạo spec/plan hoặc hỏi xác nhận không cần thiết; đề xuất mới ngoài phạm vi đã duyệt phải chờ một phản hồi `ok` mới.
- Nêu rõ giả định; nếu chưa rõ điểm ảnh hưởng đến an toàn hoặc thiết kế thì hỏi trước. Chỉ sửa nguyên nhân và lượng mã tối thiểu, không thêm chức năng suy đoán, tái cấu trúc hoặc dọn mã không liên quan.
- Chỉ sửa trong phạm vi yêu cầu; không chỉnh plugin `gdrive`, OLS, website, database hoặc Cron khác.
- Không tạo nhánh hay commit/push nếu chưa được người dùng yêu cầu. Không force-push hoặc di chuyển tag đã phát hành.
- Dùng `apply_patch`; giữ phong cách hiện tại, không thêm dependency/framework hoặc inline comment không cần thiết.
- Không đổi cơ chế phân tách VPS/nguồn/Cron/nơi lưu. Chỉ dọn backup đúng ownership và đã xác minh checksum.
- Không chạy backup, thử xóa dữ liệu Drive, đổi quyền repo hay thay lịch Cron thật để kiểm thử nếu chưa được cho phép.

## Bí mật và dữ liệu runtime
- Giữ quyền root-only cho runtime và secrets; không nới quyền để frontend hoặc static route đọc được. Không thay cấu hình, credentials, đích lưu hay lịch Cron khi cài lại, cập nhật hoặc gỡ đăng ký nếu chưa được phép.
- Không in hoặc đọc toàn bộ credentials/token/private key vào terminal, log hoặc chat. Chỉ kiểm tra sự tồn tại, quyền file và thông tin tối thiểu.
- Không đưa `data/`, `logs/`, `tmp/`, credentials, SSH keys hoặc khóa ký riêng vào Git/release.
- Khóa ký riêng ở DEV `data/`; chỉ `sdb/release_key.pub` được phát hành. Không tự thay khóa tin cậy.
- Public updater không cần token; quyền push/phát hành của maintainer tách biệt với quyền tải trên VPS.
- Không coi `.gitignore` là bằng chứng bí mật chưa từng vào lịch sử; rà soát trước khi xuất bản.

## Kiểm thử và tiêu chí hoàn thành
- Mọi test, fixture, script thử nghiệm, screenshot, build output và artifact kỹ thuật đặt trong `tmp/`; không commit/deploy chúng.
- Viết regression tái hiện lỗi trước khi sửa. Không bỏ assertion chỉ để test qua; không tuyên bố kết quả chưa kiểm tra.
- Khi thay contract plugin, kiểm tra tên thư mục/file/class, metadata, POST body không đè trường định tuyến, phản hồi JSON, session/quyền truy cập và allowlist endpoint. Dùng fixture/mock; không chạy tác vụ thật để chứng minh route hoạt động.
- Python: `TMPDIR="$PWD/tmp" /www/server/panel/pyenv/bin/python -B -W ignore::DeprecationWarning -m unittest discover -s tmp -p 'test_*.py' -q`.
- UI: `node --check ui.js`; Playwright: `PLAYWRIGHT_BROWSERS_PATH="$PWD/tmp/pw-browsers" node tmp/test_browser.cjs`.
- Test UI cần phân biệt dữ liệu giả với Google thật; kiểm tra popup aaPanel, tab dài, scroll, theme và mobile khi thay layout.
- Khi thay installer/integration, kiểm tra cài mới, cài lại, gỡ đăng ký, lỗi giữa chừng và khôi phục bằng fixture; bảo toàn runtime và chỉ thay tài nguyên thuộc plugin.
- Updater: kiểm tra có/không có bản mới, cache/force, lỗi mạng/rate-limit, chữ ký sai, gói thiếu file và bảo toàn runtime khi update/rollback.
- Trước khi kết luận, chạy kiểm tra phù hợp phạm vi, `git diff --check` và kiểm tra diff; báo rõ lệnh/kết quả thực tế, phần chưa xác minh và dữ liệu giả hay môi trường thật. Thay đổi chỉ tài liệu không bắt buộc chạy toàn bộ test backend/UI.

## Version, deploy và phát hành
- Đồng bộ version ở `sdb/__init__.py`, `info.json`, nhãn dự phòng trong `index.html` và `CHANGELOG.md`.
- Đóng gói chỉ theo allowlist; ZIP và chữ ký trong `tmp/dist/`. Release phải có cả hai, không chỉ tag Git.
- Khóa ký ở lại VPS maintainer. Quy trình phát hành phải xác minh đúng commit, version, repo, digest và chữ ký trước khi publish.
- Chạy kiểm thử, kiểm tra diff và bí mật trước khi deploy. Không thay cấu hình, credentials, đích lưu hay lịch Cron khi nâng cấp.
- Không reload panel khi chỉ đổi UI/backend subprocess; chỉ reload khi adapter core thay đổi và installer báo cần thiết.
- Cập nhật `LEDGER.md` với bằng chứng thực tế; báo rõ những bước còn cần quyền hoặc người dùng thực hiện.
