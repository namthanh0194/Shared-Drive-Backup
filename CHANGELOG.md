# Changelog

## 1.0.8 — 2026-10-27

- Thay hộp xác nhận mặc định của trình duyệt bằng popup đồng bộ giao diện aaPanel, tự theo chế độ sáng/tối.
- Popup cập nhật hiển thị phiên bản đang cài, phiên bản mới, ngày phát hành và nội dung thay đổi; chỉ chạy khi chọn Cập nhật ngay.
- Giữ tiêu đề, nút đóng và thanh Huỷ / Xác nhận luôn trong màn hình; nội dung dài cuộn riêng, hỗ trợ bàn phím và màn hình nhỏ.
- Chặn bấm cập nhật lặp lại hoặc đóng popup khi đang xử lý; hiển thị lỗi trong popup để thử lại.
- Đồng bộ popup xác nhận cho Backup ngay, Tải kiểm tra, bật dọn dẹp Drive và Rollback; không đổi quy tắc backup, retention hoặc cập nhật.

## 1.0.7 — 2026-10-27

- Sửa thứ tự khởi tạo popup: căn bố cục sau khi aaPanel hoàn tất mở layer, tránh vị trí bị ghi lại thành top:20%.
- Tách thanh tiêu đề cố định và vùng nội dung cuộn; đặt toàn bộ nút đóng vào trong thanh tiêu đề.
- Đồng bộ màu thanh tiêu đề và nút đóng theo giao diện plugin, kể cả khi đổi sáng/tối.
- Thay icon bằng đúng ảnh Google Drive của plugin gdrive; đồng bộ icon riêng của Shared Drive Backup khi cài hoặc cập nhật.

## 1.0.6 — 2026-10-27

- UI: Dùng setProperty !important ép cứng chiều cao và overflow cho .layui-layer và .layui-layer-content, ngăn aaPanel ghi đè style khiến popup bị tràn chân màn hình.

## 1.0.5 — 2026-10-27

- UI: Đồng bộ màu thanh tiêu đề layer theo chế độ Dark Mode của aaPanel (thay nền trắng bằng màu tối).
- UI: Căn chỉnh lại nút đóng (close 'x') của layer popup hiển thị rõ ràng, không bị che mất.
- UI: Tính toán chiều cao linh hoạt theo màn hình để form không bị cắt ở dưới và cuộn mượt mà.

## 1.0.4 — 2026-10-27

- Workflow: Thêm bước fetch tags tường minh để build release trên runner.
- UI: Ẩn nút Update khi đang ở bản mới nhất.

## 1.0.3 — 2026-10-27

- UI: Nút Cập nhật trên header mặc định ẩn hoàn toàn khi đang ở bản mới nhất, chỉ xuất hiện khi phát hiện có phiên bản mới trên GitHub.
- Workflow: Bật fetch-tags để xác thực chữ ký tag trên GitHub Actions runner.

## 1.0.2 — 2026-10-27

- UI: Thêm nút Cập nhật ở góc trên bên phải header, tự động xuất hiện nổi bật khi có phiên bản mới trên GitHub.
- Đồng bộ hành động cập nhật trên header và tab Cài đặt & phiên bản.

## 1.0.1 — 2026-10-27

- Ghim đúng repo public `namthanh0194/Shared-Drive-Backup`; bỏ yêu cầu token khỏi UI và không gửi token trong luồng tải cập nhật.
- Tự kiểm tra khi mở plugin, cache 6 giờ, retry lỗi sau 15 phút; có kiểm tra ngay, trạng thái lỗi, changelog và thông báo bản mới.
- Chỉ cài khi người dùng xác nhận; giữ nguyên chữ ký Ed25519, xác minh gói, khóa tác vụ và rollback bảo toàn runtime.
- Bổ sung AGENTS.md mô tả kiến trúc, quy tắc code, bảo mật, test và phát hành.
- Đóng gói tái lập và workflow xác minh chữ ký từ annotated tag trước khi publish; khóa ký luôn ở VPS maintainer.

## 1.0.0 — 2026-10-26

- Plugin riêng cho Google Shared drives; không sửa plugin Google Drive hoặc OLS.
- Chọn bộ nhớ/thư mục bằng UI, breadcrumbs, phân trang, mở ID và tạo thư mục.
- Backup website, MySQL và thư mục; upload resumable, kiểm tra MD5/size, SHA-256 cho tải kiểm tra.
- Tích hợp Backup to trong Cron aaPanel bằng adapter có kiểm tra ngữ cảnh và rollback.
- Tách namespace theo VPS, nguồn và nơi lưu; mặc định tắt dọn bản cũ.
- Giữ archive khi upload thất bại, kiểm tra đĩa, chống chạy trùng.
- Update một click từ repo GitHub private đã ghim với chữ ký Ed25519; giữ runtime state.
- Không nhận secrets qua panel HTTP; hỗ trợ nhập qua SSH.

Deployment note: live Google backup requires user-supplied credentials and Shared-drive access. GitHub publication requires a GitHub credential with repository-creation/push/release permission. These are separate verification gates, not implied by local test success.
