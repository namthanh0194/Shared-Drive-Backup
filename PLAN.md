# Implementation plan

Binding spec: DESIGN.md. Execute inline; no extra worktrees for this new, explicitly requested DEV repository.

1. Storage and Google adapter: write failing unittest cases for permissions, path validation, shared-drive parameters, pagination, membership validation, uploads and owned-only retention. Implement; rerun.
2. Worker and native integration: test archive/source validation, process locking, Cron patch/unpatch on fixtures and generated command quoting. Implement worker, installer and reversible integration. No live Cron enabled.
3. Panel backend and folder-picker UI: implement public-method allowlist, credentials/settings, paginated shared drives/folders, breadcrumbs, folder creation/confirmation, inventory, history, manual run and verified download. Browser-test with mock Google data and smoke-test real backend without credentials.
4. Releases: test archive traversal and signature rejection; implement signed packaging, private GitHub configuration, check/update/rollback and state-preserving deployment. Add .gitignore/.ignore and setup docs.
5. Verify: run all unit tests, Python/JS syntax checks, browser checks; fresh-context review and focused fixes. Install production only after these gates, verify native provider endpoint and plugin discovery. Never claim live Drive backup/update without credentials/published release.

Review focus: credential endpoint SSRF, folder ancestry escape, malicious ZIP/symlink updates, retention across VPS/destinations, races between worker/update, error status in native Cron, failed-upload local data preservation.
