# Shared Drive Backup

Approved scope: independent aaPanel plugin, Shared drives folder picker, website/database/directory backups, native Cron Backup to target, history, verified downloads, versioned GitHub releases and safe one-click updates. Vietnamese compact UI, dark/light compatible. Do not modify Google Drive or OLS.

Development: /root/DEV/aapanel-shared-drive-backup. Production: /www/server/panel/plugin/shared_drive_backup. All tests, fixtures, screenshots and release artifacts live under tmp/ and are excluded from Git and releases. Runtime secrets live in production data/ with root-only permissions.

Use Google service-account or authorized-user credentials obtained on a trusted machine; never reuse aaPanel's credentials. No OAuth callback on the panel's plain HTTP IP. Authenticate Google credentials only against allowlisted Google endpoints. Shared-drive picker supports pagination, breadcrumbs, folder creation, root selection and server-side parent/drive validation. Store an explicitly confirmed destination; navigating alone never changes it.

Use an independent worker with a nonblocking process lock. Read aaPanel site/database inventory without modifying it. Use argument-vector subprocesses for tar and mysqldump, no user-controlled shell. Validate sources, disk capacity, final archive size and upload checksum. Split remote directories by VPS and task. New backups must upload and verify before any retention. Trash only app-tagged files in the current destination/server/task namespace, and only when pruning is explicitly enabled. Failed upload preserves its local archive for diagnosis.

Native Cron remains the only scheduler. Register legacy libList metadata and narrowly patch the modern CLOUD_STORAGE list and GetShell dispatch because this aaPanel build hardcodes cloud providers. Keep byte-for-byte originals and checksums, verify context before patching, and fail closed on unknown code. Do not patch minified JS. Native schedules call this plugin's worker with safely quoted arguments and preserve nonzero exit status.

Default GitHub repository is private. No repository credential is available to the shell initially; report publishing as blocked if GitHub's connected tools cannot create a repo. Never claim a release exists unless verified. Updates use a pinned GitHub repository, explicit release version, detached Ed25519 signature, size limits and strict ZIP paths. Preserve runtime state, prevent concurrent updates/backups, validate code before deployment and retain one rollback copy.

No destructive restore in v1. Download to a root-only verification directory, verify checksum, and expose status/path to the admin. Do not restart OLS or enable real backup schedules during development.
