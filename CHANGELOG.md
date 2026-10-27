# Changelog

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
