# Shared Drive Backup · aaPanel

Plugin độc lập backup website, database MySQL và thư mục lên **Bộ nhớ dùng chung Google Drive**. Có UI duyệt thư mục thật, tích hợp native Cron Job và cập nhật bằng release có chữ ký.

## Thư mục

- DEV: `/root/DEV/aapanel-shared-drive-backup`.
- Production: `/www/server/panel/plugin/shared_drive_backup`.
- `data/`: root-only config, credentials, history, verified downloads, integration snapshots, rollback code.
- `tmp/`: tests, browser tooling, generated release archives and temporary backup archives. Never committed or shipped.
- `logs/`: worker logs, root-only. `.gitignore` excludes runtime data and secrets; `.ignore` records package exclusions. Packaging additionally uses a strict file allowlist.

## Cài đặt

Requires root, Linux aaPanel with `class_v2/crontab_v2.py`, Python >=3.10 in aaPanel pyenv, GNU tar/gzip and aaPanel MySQL mysqldump for database jobs. The adapter is tested against this VPS's aaPanel 8.0.7 code; other releases are accepted only when the expected source contexts match exactly. Unknown versions fail closed.

```sh
bash /root/DEV/aapanel-shared-drive-backup/install.sh install
```

Other VPS: import the signed release ZIP via aaPanel Third-party Plug-ins → Import plugins, or extract the `shared_drive_backup/` directory and run `bash install.sh install` as root. Verify the initial package/signing-key fingerprint through a trusted channel. Missing Python libraries install into plugin `data/vendor/`, not into aaPanel's global environment. Do not import the entire DEV tree.

The modern panel hardcodes its list of backup providers. Installer records original Cron source and hashes under private `data/`, adds a provider to `data/libList.conf`, and narrowly patches provider discovery and dispatch. It does **not** edit minified JS or the old Google Drive plugin. Reload/restart **aaPanel only** when installer reports `restart_panel_required`; do not restart OLS/MySQL/websites. After a panel upgrade, open the plugin's Cron tab and use the repair button; incompatible source is rejected instead of guessed.

## Google authentication

Enable Google Drive API in your Google Cloud project. Never share a private key/token in chat, Git or screenshots.

### Service account (recommended for unattended Shared-drive backups)

Create a service account in your project and grant its email access to the intended Shared drive/folder, subject to Workspace external-sharing policy. Contributor permissions suffice for creating backups; trashing old files requires additional permissions. The plugin checks capabilities when selecting a destination and defaults retention deletion OFF.

On HTTPS aaPanel, select the service-account JSON in **Kết nối & nơi lưu** and save. On the current plain-HTTP panel, import by SSH instead:

```sh
chmod 600 /safe/path/credentials.json
/www/server/panel/pyenv/bin/python -B \
  /www/server/panel/plugin/shared_drive_backup/cli.py \
  import-credentials /safe/path/credentials.json
```

No automatic reuse of credentials from the existing Google Drive plugin.

### OAuth user account

Use your own Google OAuth **Desktop** client. On your trusted personal computer with a browser and google-auth-oauthlib installed:

```sh
python tools/oauth_desktop.py /safe/path/desktop-client.json /safe/path/authorized-user.json
```

Transfer the generated `authorized_user` file securely and import as above. This uses loopback OAuth on the personal computer, not a public HTTP-IP callback. Scope is `https://www.googleapis.com/auth/drive` to browse existing Shared-drive folders; this grants broad Drive access for the authenticated user. Consider a dedicated backup account/service account. Workspace admin policies or OAuth app testing/token expiration can prevent long-term unattended access.

## Choose the destination

1. Open **Kết nối & nơi lưu → Duyệt & chọn thư mục**.
2. Select the Shared drive (or enter its ID), open subfolders, use breadcrumbs or Folder ID, optionally create a new folder.
3. Click **Lưu vào thư mục này**. Browsing/canceling does not change the saved destination. The server verifies drive membership, folder type and `canAddChildren`.
4. Run connection check, then a small manual backup and **Tải kiểm tra** to verify end-to-end.

Remote layout: selected folder → `VPS-<stable server ID>` → `<kind>-<source/destination/task hash>` → timestamped archive. Each native Cron schedule has its own namespace; manual runs are separate from Cron retention. Each VPS generates its own ID. Never clone production `data/` onto another VPS, or both could share retention ownership.

## Website restore and native Backup / Restore (DEV, unreleased)

This feature is in DEV only; installed v1.0.8 stays unchanged until a separately approved release/update. It restores **PHP website files/code**, not databases, OLS settings, SSL certificates or DNS.

### aaPanel integration

- Uses the existing `sites` and `backup` tables in `data/default.db`, without schema migrations, core/frontend patches or impersonating `gdrive`.
- New verified PHP-site backups register the real site ID (`pid`), `type=0` and the core remote filename format `local_path|shared_drive_backup|remote_file_id`. The local-path part is metadata, not a claim that a local archive exists.
- The native remote-row action opens plugin settings. Select **Khôi phục** there; this implementation does not inject native local Restore/Download buttons or a provider-filter card into aaPanel.
- For existing history, choose the website and explicitly **Đồng bộ trang này vào Backup / Restore aaPanel**. Sync handles the current page and is idempotent. Legacy backups without a recorded site ID first require typing the website name to link them.
- Only verified site backups from this VPS's plugin history are listed, not arbitrary files on Drive. Site ID/name/path and the configured destination must match; cross-VPS imports are not implemented.
- Native rows reflect the last registration/sync. Only confirmed trashed files remove plugin-owned rows; permission errors, HTTP 404 and network failures retain them. Sync does not touch other providers' rows. Removing a native row does not delete the Drive archive.

### Restore workflow

1. Select the PHP website and an accessible verified backup. **Tải về VPS** uses the existing download-only flow.
2. Prefer **Giải nén kiểm tra**: the worker downloads, verifies size/checksums and extracts into private `data/restores/<job_id>/files/<archive_root>`, without changing the website.
3. For **Khôi phục website**, stop the site in aaPanel and separately stop processes/Cron jobs writing its files. Type the exact website name in the confirmation dialog. The destination comes from aaPanel, never a client-supplied path.
4. The worker rechecks site identity/webroot inode, extracts privately on the same filesystem, snapshots the current webroot, renames the old tree aside and activates the new tree. It uses core-style `www` ownership, files `0644` and directories `0755`.
5. Follow progress and output paths in **Lịch sử**. The website remains stopped. Validate files and restore the matching database separately if needed before starting the site yourself.

Partial backups with exclusions support inspection only. Archives containing links, devices, sparse files, traversal, duplicate paths or multiple roots are rejected. Replacement rejects symlink/mounted/overlapping webroots, nested mounts and ancestor directories not root-owned or writable by group/others. File-count, expanded-size and free-space limits apply; not every uploaded backup is eligible for automatic restore.

### Recovery and retained files

- Download and pre-restore `before.tar.gz` remain in private `data/restores/<job_id>/`; History records the snapshot checksum. Staging/original tree stay in private `<webroot-parent>/.sdb-restore-<job_id>/`; History shows the exact `previous_path`. These files are not governed by Drive retention.
- Snapshot/extraction failure leaves the website untouched. Failed activation attempts to restore the original tree.
- Interrupted activation or failed rollback retains `data/restore-recovery.json`, blocking new work and update/rollback. An administrator must inspect the journal, History, webroot, staged and previous paths to determine the complete tree to recover. **Do not blindly delete the journal or restart the site.** Automatic crash recovery is not implemented.
- Keep snapshot and previous tree until validation succeeds. Cleanup is manual; never remove data needed by an unfinished restore.

## Cron Job

aaPanel → Cron Job → Backup Site / Backup Database / Backup Directory → **Backup to: Shared Drive Backup**. Set source, schedule, exclusion rules and retain count there. The worker reads the saved Cron row by ID, validates target/type, uses the plugin's destination, and exits nonzero on failure. `ALL`/multi-source Site and MySQL jobs run sources sequentially. There is no second scheduler in the plugin. The plugin's Cron tab lists only its own native schedules and links to the panel's scheduler.

Regular daily/weekly/monthly schedules and the panel's existing time gates are preserved. Only local MySQL databases are listed; remote servers are not backed up using the local root credential. Second-level schedules, full-server backups, PostgreSQL/Redis/MongoDB and split-table database modes are not supported in v1; do not select them for this provider. Cron notification configuration remains visible in aaPanel, but this independent worker does not implement the panel's per-channel notification callbacks; use native execution logs/status until notification support is explicitly added.

Backup exclusions are tar patterns, not shell commands. The source must be a real directory, not `/`, virtual filesystems, or a tree containing the plugin/runtime output. Tar does not follow symlinks or cross filesystem boundaries. MySQL dumps use a temporary 0600 defaults file and `--single-transaction`; nontransactional tables may still require maintenance/locking for consistent snapshots. No database password is passed in argv or written into `/etc/my.cnf`.

## Safety and restore

- One operation per VPS; no backup/update/download overlap.
- Preflight checks disk space; ongoing archive creation checks free space and output size. Estimates are conservative.
- Resumable upload with bounded retries; a fresh remote metadata read checks MD5, size and parent before success.
- Retention disabled by default. When enabled, only matching app-owned files in the current VPS/source/destination/schedule namespace are moved to Trash, after a new verified upload. A file receives its verified marker only after checksum checks; unverified uploads do not consume retention slots. Metadata is read again before trashing. Unrelated Drive files are never pruned. Unverified remote orphans are left for manual review.
- Upload failures keep completed local archives in production `tmp/`. Review disk usage and remove failed archives only after diagnosis; there is no silent deletion of the only local copy.
- **Tải kiểm tra** downloads into private `data/downloads/`, verifies MD5 and SHA-256, and displays the server path. It never extracts or restores into production. Verified downloads consume local disk until manually removed.
- History/logs persist across code updates. Review growth periodically; no automatic history purge in v1.

## Versioning / updates / GitHub

Pinned public repository: `namthanh0194/Shared-Drive-Backup`. VPS installations need **no GitHub token or SSH key** to check/download releases. Previously stored GitHub tokens are not sent by the public updater; existing secret files are preserved for compatibility/rollback.

The UI automatically checks on opening the plugin and rechecks when the six-hour server cache expires while the plugin remains open. It does not install a background system Cron or check while the UI is closed. A failed check is shown as an error, not "up to date", and is retried after 15 minutes. **Kiểm tra ngay** bypasses the cache. The interface displays the installed/latest version, last check, safe-text changelog and a new-version notice across tabs. Installation happens only after the user clicks **Cập nhật lên v…** and confirms.

From v1.0.8, confirmation uses a theme-aware popup styled to match aaPanel, not a browser-native alert/confirm. The update popup shows the installed/latest version, publication date when available, release notes, and **Huỷ / Cập nhật ngay**. Long content scrolls separately from the title and action buttons. During installation the popup cannot be dismissed or submitted again; failures remain visible for retry. Backup, verification downloads, enabling Drive pruning, and rollback use the same confirmation style. When upgrading from an older version, that first confirmation still uses the old UI; close and reopen the plugin after installation to load the new interface.

The updater accepts only stable semver releases from the pinned repository with both `shared_drive_backup-X.Y.Z.zip` and `.zip.sig`, verifies Ed25519 against bundled `sdb/release_key.pub`, validates archive paths/sizes and Python syntax, preserves data/logs/config, and keeps the previous code for rollback. A release cannot replace its own trusted key. Close/reopen the plugin after updating to refresh UI; backend API runs in a fresh subprocess per request. Release metadata is fetched again without cache before installation.

A Git tag alone is not an installable release. Until signed assets are published, the UI explicitly says there is no public GitHub Release. Installations of the original 1.0.0 that pinned the old repository need a one-time import/install of the trusted 1.0.1 package; they cannot discover the new repository through the old URL.

Before changing live files, deployment prepares a durable code snapshot and records a private recovery journal. Handled errors and SIGINT restore code before clearing it. SIGKILL/power loss cannot run a Python rollback handler; snapshots/journal remain, and backup/new updates are blocked pending recovery. Run the standalone stdlib-only helper from the trusted DEV checkout (or the installed tool if intact):

```sh
/www/server/panel/pyenv/bin/python -B \
  /root/DEV/aapanel-shared-drive-backup/tools/recover_deployment.py
```

This restores the recorded previous code/adapter without touching credentials or backup data. Preserve snapshots until checked. A first installation interrupted before a previous code snapshot exists requires checking/unregistering the recorded adapter and rerunning the trusted installer; do not delete its recovery journal blindly.

For maintainers: follow `AGENTS.md`, bump `sdb/__init__.py`, `info.json` and HTML fallback labels together, update CHANGELOG and run checks before deployment or publication. Losing the protected Ed25519 signing key prevents future updates under the old trust root. Back it up separately and never commit/publish it.

```sh
/www/server/panel/pyenv/bin/python -B tools/package_release.py --key /safe/path/signing-key.pem
```

Artifacts live only in `tmp/dist/`. Packages are reproducible (sorted paths, fixed ZIP metadata and no compression), so GitHub Actions can rebuild exactly the locally signed bytes without receiving the private signing key. After the user authorizes committing/pushing a tested clean `main`, publish using the maintainer's existing SSH write access:

```sh
/www/server/panel/pyenv/bin/python -B tools/publish_release.py --publish
```

The tool checks the clean main branch and pinned remote, signs locally, then atomically pushes main and a new annotated version tag containing only public SHA-256/signature metadata. It does not create commits or replace existing tags. `.github/workflows/release.yml` rebuilds that exact tag, verifies the digest and Ed25519 signature and publishes the ZIP/signature using the workflow's built-in `GITHUB_TOKEN` with `contents: write`. No personal token or signing-key secret is required on GitHub. GitHub Actions must be enabled for the repository. Check workflow success and anonymous asset downloads before claiming publication; pushing a tag/local packaging is not proof of a Release. Runtime data, tests and build outputs are never committed.

## Validation

Tests live only in `tmp/` per project policy and are not committed or packaged. On the development VPS:

```sh
TMPDIR="$PWD/tmp" /www/server/panel/pyenv/bin/python -B -m unittest discover -s tmp -p 'test_*.py' -v
node --check ui.js
```

Use a small disposable dataset for the first real backup/restore verification. UI tests with mocked Google responses are not evidence of actual Workspace permissions or live Google upload. Never enable production schedules before that check.

## Uninstall

Disable/retarget the plugin's own Cron jobs first. Back up credentials/config/history separately if required. `bash uninstall.sh` restores only the exact recorded core version and removes only this provider's metadata; it refuses to overwrite unrelated panel edits. aaPanel itself may delete the plugin directory when uninstalling through its app manager. No other provider or OLS configuration is changed.
