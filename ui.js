(function () {
  'use strict';
  const root = document.getElementById('sdb-root');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
  const find = id => root.querySelector('#sdb-' + id);
  const state = { config: {}, inventory: { sites: [], databases: [] }, jobs: [], release: null, updateChecking: false, nextUpdateCheck: 0, picker: { drive: '', folder: '', next: '', driveNext: '', sequence: 0 }, pending: 0 };
  const secure = location.protocol === 'https:';
  const labels = { queued: 'Chờ chạy', running: 'Đang chạy', success: 'Thành công', failed: 'Thất bại' };
  const phases = { preflight: 'kiểm tra', archive: 'nén', upload: 'upload', retention: 'giữ bản', download: 'tải về', complete: 'hoàn tất' };
  const kinds = { site: 'Website', database: 'MySQL', path: 'Thư mục', download: 'Tải kiểm tra' };
  function text(id, value) { find(id).textContent = value == null ? '—' : String(value); }
  function message(value, error) { const area = find('message'); area.hidden = !value; area.textContent = value || ''; area.dataset.error = String(Boolean(error)); }
  function pickerError(error) { find('picker-error').hidden = !error; find('picker-error').textContent = error ? error.message || String(error) : ''; }
  function formatSize(value) { if (value == null) return '—'; let amount = Number(value); const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let unit = 0; while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; } return amount.toLocaleString('vi-VN', { maximumFractionDigits: unit ? 1 : 0 }) + ' ' + units[unit]; }
  function date(value) { return new Date(Number(value) * 1000).toLocaleString('vi-VN', { hour12: false }); }
  function element(tag, value, className) { const node = document.createElement(tag); if (value != null) node.textContent = String(value); if (className) node.className = className; return node; }
  function button(value, callback) { const node = element('button', value, 'sdb-link'); node.type = 'button'; node.addEventListener('click', callback); return node; }
  async function api(method, args) {
    const data = Object.assign({}, args || {});
    if (window.$ && window.$.ajax) {
      return new Promise((resolve, reject) => window.$.ajax({ type: 'POST', url: '/plugin?action=a&s=' + encodeURIComponent(method) + '&name=shared_drive_backup', data: data, dataType: 'json', timeout: 190000, success(response) { if (!response || response.status !== true) { reject(new Error(response && response.msg || 'Backend trả về lỗi.')); return; } resolve(response.data); }, error(xhr) { reject(new Error('Không gọi được plugin, HTTP ' + xhr.status + '. Kiểm tra đăng nhập aaPanel.')); } }));
    }
    const response = await fetch('/plugin?action=a&s=' + encodeURIComponent(method) + '&name=shared_drive_backup', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(data) });
    const result = await response.json();
    if (!response.ok || result.status !== true) throw new Error(result.msg || 'Không gọi được plugin.');
    return result.data;
  }
  async function action(control, operation, success) { const disabled = control.disabled; control.disabled = true; control.setAttribute('aria-busy', 'true'); try { const result = await operation(); if (success) message(success, false); return result; } catch (error) { message(error.message, true); throw error; } finally { control.disabled = (control.id === 'sdb-update' || control.id === 'sdb-header-update') ? !state.release || !state.release.available : disabled; control.removeAttribute('aria-busy'); } }
  function bind(id, callback) { find(id).addEventListener('click', event => { Promise.resolve(callback(event)).catch(() => {}); }); }
  function showTab(name) {
    root.querySelectorAll('[role=tab]').forEach(tab => { const selected = tab.dataset.tab === name; tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; });
    root.querySelectorAll('[role=tabpanel]').forEach(panel => { panel.hidden = panel.id !== 'sdb-' + name; });
    adjustModalPosition();
    if (name === 'settings') checkUpdates();
    if (name === 'history') loadHistory().catch(error => message(error.message, true));
    if (name === 'cron') loadTasks().catch(error => message(error.message, true));
    if (name === 'backup') loadInventory().catch(error => message(error.message, true));
  }
  function statusNode(job) { const node = element('span', labels[job.status] || job.status, 'sdb-state sdb-state-' + job.status); if (job.status === 'running') node.textContent += ' · ' + (phases[job.details.phase] || job.details.phase || '') + (job.details.phase === 'upload' ? ' ' + (job.details.progress || 0) + '%' : ''); return node; }
  function renderJobs(target, jobs, detailed) {
    target.replaceChildren();
    if (!jobs.length) { const row = element('tr'); const cell = element('td', 'Chưa có tác vụ. Chọn nơi lưu và chạy bản backup thử đầu tiên.', 'sdb-empty'); cell.colSpan = detailed ? 5 : 4; row.append(cell); target.append(row); return; }
    jobs.forEach(job => { const row = element('tr'); const source = element('td'); source.append(element('div', job.details.target || '—')); source.append(element('small', (kinds[job.details.kind] || job.details.kind) + ' · ' + (job.details.origin === 'cron' ? 'Cron aaPanel' : 'Thủ công'))); if (detailed && (job.details.error || job.details.warning || job.details.download_path)) source.append(element('small', job.details.error || job.details.warning || job.details.download_path)); if (detailed && job.details.local_retained) source.append(element('small', 'Archive local được giữ: ' + (job.details.archive || ''))); row.append(source, element('td', date(job.created))); const status = element('td'); status.append(statusNode(job)); row.append(status, element('td', formatSize(job.details.size), 'sdb-number')); if (detailed) { const actions = element('td'); actions.append(button('Xem log', async () => { try { const result = await api('read_log', { job_id: job.id }); find('job-log').hidden = false; text('job-log', result.log); } catch (error) { message(error.message, true); } })); if (job.status === 'success' && job.details.remote) { actions.append(button('Tải kiểm tra', async () => { if (!confirm('Tải bản backup về VPS và xác minh checksum? Không giải nén hoặc ghi đè dữ liệu production.')) return; try { const result = await api('start_download', { job_id: job.id }); message('Đã tạo tác vụ tải kiểm tra: ' + result.job_id, false); await loadHistory(); } catch (error) { message(error.message, true); } })); const remoteId = job.details.remote.id; if (/^[A-Za-z0-9_-]+$/.test(remoteId)) { const link = element('a', 'Mở Drive', 'sdb-link'); link.href = 'https://drive.google.com/file/d/' + encodeURIComponent(remoteId) + '/view'; link.target = '_blank'; link.rel = 'noopener'; actions.append(link); } } row.append(actions); } target.append(row); });
  }
  async function refresh() {
    const result = await api('status'); state.config = result.config;
    if (result.recovery_required) message('Cập nhật trước chưa hoàn tất. Không chạy backup; dùng công cụ recover_deployment.py theo README.', true);
    text('version', 'v' + result.version); text('current-version', result.version); text('repository', result.repository);
    text('server-summary', result.config.server_name + ' · VPS ID ' + result.config.server_id.slice(0, 12)); text('route-source', result.config.server_name); text('route-drive', result.config.drive_name || 'Chưa chọn Shared drive'); text('route-folder', result.config.folder_name || 'Chưa chọn thư mục');
    text('google-state', result.config.configured && result.credentials.present ? 'Đã cấu hình' : 'Chưa cấu hình'); text('worker-state', result.busy ? 'Đang xử lý' : 'Sẵn sàng'); text('disk-free', formatSize(result.disk.free)); text('disk-reserve', 'Dự phòng tối thiểu ' + formatSize(result.config.min_free_mb * 1024 * 1024));
    text('credential-state', result.credentials.present ? 'Đã lưu ' + result.credentials.type + (result.credentials.email ? ' · ' + result.credentials.email : '') : 'Chưa nhập credentials.'); text('saved-drive', result.config.drive_name || 'Chưa chọn'); text('saved-folder', result.config.folder_name || 'Chưa chọn'); text('saved-id', result.config.folder_id || '—');
    text('integration-state', result.integration.registered ? 'Adapter Cron đã đăng ký. Nếu chưa thấy lựa chọn, cần tải lại panel; xem README về nạp lại backend.' : 'Chưa đăng ký hoặc adapter không còn khớp phiên bản aaPanel. Không bật lịch thật.');
    ['server-name', 'min-free', 'max-archive'].forEach((id, index) => { if (document.activeElement !== find(id)) find(id).value = result.config[['server_name', 'min_free_mb', 'max_archive_gb'][index]]; });
    if (document.activeElement !== find('keep-local')) find('keep-local').checked = result.config.keep_local;
    if (document.activeElement !== find('prune')) find('prune').checked = result.config.prune_enabled;
    find('rollback').disabled = !result.rollback_available;
    renderJobs(find('recent-rows'), result.history, false);
    return result;
  }
  async function loadHistory() { const result = await api('history'); state.jobs = result.jobs; renderJobs(find('history-rows'), result.jobs, true); }
  async function loadInventory() { state.inventory = await api('inventory'); renderTargets(); }
  function renderTargets() { const kind = find('backup-kind').value; find('target-select-wrap').hidden = kind === 'path'; find('target-path-wrap').hidden = kind !== 'path'; find('backup-exclusions').disabled = kind === 'database'; const select = find('backup-target'); const previous = select.value; select.replaceChildren(element('option', 'Chọn nguồn backup…')); select.firstChild.value = ''; (state.inventory[kind === 'site' ? 'sites' : 'databases'] || []).forEach(item => { const option = element('option', item.name); option.value = item.name; select.append(option); }); if (Array.from(select.options).some(option => option.value === previous)) select.value = previous; }
  async function loadTasks() { const result = await api('tasks'); const rows = find('cron-rows'); rows.replaceChildren(); if (!result.tasks.length) { const row = element('tr'); const cell = element('td', 'Chưa có lịch dùng Shared Drive Backup. Tạo lịch tại Cron Job aaPanel.', 'sdb-empty'); cell.colSpan = 4; row.append(cell); rows.append(row); } result.tasks.forEach(task => { const row = element('tr'); row.append(element('td', task.name), element('td', (kinds[task.sType] || task.sType) + ' · ' + task.sName), element('td', task.type + ' · ' + String(task.where_hour).padStart(2, '0') + ':' + String(task.where_minute).padStart(2, '0')), element('td', Number(task.status) === 1 ? 'Đang bật' : 'Đang tắt')); rows.append(row); }); }
  async function loadDrives(append) { const result = await api('list_drives', { page_token: append ? state.picker.driveNext : '' }); const select = find('drive-select'); if (!append) { select.replaceChildren(element('option', 'Chọn bộ nhớ…')); select.firstChild.value = ''; } (result.drives || []).forEach(drive => { const option = element('option', drive.name); option.value = drive.id; select.append(option); }); state.picker.driveNext = result.nextPageToken || ''; find('more-drives').hidden = !state.picker.driveNext; if (state.picker.drive) select.value = state.picker.drive; }
  async function browse(driveId, folderId, append) {
    const sequence = ++state.picker.sequence; pickerError(null); find('confirm-folder').disabled = true; find('create-folder').disabled = true; find('more-folders').disabled = true;
    if (!append) find('folder-list').replaceChildren(element('p', 'Đang tải thư mục…', 'sdb-empty'));
    try {
      const result = await api('browse_folders', { drive_id: driveId, folder_id: folderId || driveId, page_token: append ? state.picker.next : '' });
      if (sequence !== state.picker.sequence || !find('folder-dialog').open) return;
      state.picker.drive = driveId; state.picker.folder = result.folder.id; state.picker.next = result.nextPageToken || ''; state.picker.folderName = result.folder.name;
      find('drive-id').value = driveId; find('folder-id').value = result.folder.id; find('drive-select').value = driveId;
      const crumbs = find('breadcrumbs'); crumbs.replaceChildren(); result.breadcrumbs.forEach((crumb, index) => { if (index) crumbs.append(element('span', '›', 'sdb-muted')); crumbs.append(button(crumb.name, () => browse(driveId, crumb.id, false))); });
      const list = find('folder-list'); if (!append) list.replaceChildren();
      (result.files || []).forEach(folder => { const row = element('button', null, 'sdb-folder-row'); row.type = 'button'; row.append(element('span', folder.name), element('small', 'Mở ›')); row.addEventListener('click', () => browse(driveId, folder.id, false)); list.append(row); });
      if (!list.children.length) list.append(element('p', 'Không có thư mục con. Bạn có thể lưu tại đây hoặc tạo thư mục mới.', 'sdb-empty'));
      const writable = Boolean(result.folder.capabilities && result.folder.capabilities.canAddChildren);
      text('picker-current', result.breadcrumbs.map(crumb => crumb.name).join(' / ')); text('picker-current-id', result.folder.id + (writable ? ' · Có quyền tạo file' : ' · Chỉ đọc, không thể chọn làm nơi lưu'));
      find('confirm-folder').disabled = !writable; find('create-folder').disabled = !writable; find('more-folders').hidden = !state.picker.next;
    } catch (error) { if (sequence === state.picker.sequence) { state.picker.folder = ''; state.picker.next = ''; text('picker-current', 'Chưa chọn thư mục hợp lệ'); pickerError(error); if (!append) find('folder-list').replaceChildren(element('p', 'Không đọc được thư mục. Kiểm tra ID, quyền và thử lại.', 'sdb-empty')); } } finally { if (sequence === state.picker.sequence) find('more-folders').disabled = false; }
  }
  async function openPicker() { const dialog = find('folder-dialog'); if (!dialog.open) dialog.showModal(); pickerError(null); state.picker.drive = state.config.drive_id || ''; state.picker.folder = ''; state.picker.next = ''; find('confirm-folder').disabled = true; find('create-folder').disabled = true; text('picker-current', 'Chưa chọn thư mục'); text('picker-current-id', 'Điều hướng chưa thay đổi cấu hình đã lưu.'); try { await loadDrives(false); if (state.config.drive_id) await browse(state.config.drive_id, state.config.folder_id || state.config.drive_id, false); } catch (error) { pickerError(error); } }
  root.querySelectorAll('[data-tab]').forEach(tab => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
  root.querySelector('[role=tablist]').addEventListener('keydown', event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; const tabs = Array.from(root.querySelectorAll('[role=tab]')); const index = tabs.indexOf(document.activeElement); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; event.preventDefault(); showTab(tabs[next].dataset.tab); tabs[next].focus(); });
  root.querySelectorAll('[data-go]').forEach(control => control.addEventListener('click', () => showTab(control.dataset.go)));
  root.querySelectorAll('[data-open-picker]').forEach(control => control.addEventListener('click', () => { openPicker().catch(pickerError); }));
  bind('picker-close', () => { state.picker.sequence += 1; find('folder-dialog').close(); });
  find('folder-dialog').addEventListener('cancel', () => { state.picker.sequence += 1; });
  bind('refresh', event => action(event.currentTarget, refresh, 'Đã làm mới trạng thái.'));
  bind('refresh-history', event => action(event.currentTarget, loadHistory));
  bind('check-connection', event => action(event.currentTarget, async () => { await api('connection_check'); text('google-state', 'Đã kiểm tra OK'); }, 'Google xác nhận có quyền tạo file tại thư mục đã chọn.'));
  find('credential-file').addEventListener('change', () => { text('credential-filename', find('credential-file').files[0] ? find('credential-file').files[0].name : 'Chưa chọn file.'); });
  bind('save-credentials', event => action(event.currentTarget, async () => { const file = find('credential-file').files[0]; if (!file || file.size > 65536) throw new Error('Chọn file JSON xác thực, tối đa 64 KiB.'); const documentText = await file.text(); JSON.parse(documentText); await api('save_credentials', { document: documentText }); find('credential-file').value = ''; text('credential-filename', 'Đã nhập file; không giữ nội dung trong giao diện.'); await refresh(); }, 'Đã lưu xác thực. Tiếp theo, chọn và xác nhận thư mục lưu.'));
  find('drive-select').addEventListener('change', event => { if (event.target.value) browse(event.target.value, event.target.value, false); });
  bind('open-drive', () => browse(find('drive-id').value.trim(), find('drive-id').value.trim(), false));
  bind('open-folder', () => browse(state.picker.drive || find('drive-id').value.trim(), find('folder-id').value.trim(), false));
  bind('more-drives', event => action(event.currentTarget, () => loadDrives(true)).catch(pickerError));
  bind('more-folders', () => browse(state.picker.drive, state.picker.folder, true));
  find('new-folder-form').addEventListener('submit', async event => { event.preventDefault(); try { await action(find('create-folder'), async () => { const result = await api('create_folder', { drive_id: state.picker.drive, parent_id: state.picker.folder, folder_name: find('new-folder-name').value }); find('new-folder-name').value = ''; await browse(state.picker.drive, result.id, false); }); } catch (error) { pickerError(error); } });
  bind('confirm-folder', async event => { try { await action(event.currentTarget, async () => { if (!state.picker.folder) throw new Error('Chưa chọn thư mục hợp lệ.'); await api('set_destination', { drive_id: state.picker.drive, folder_id: state.picker.folder }); find('folder-dialog').close(); await refresh(); }, 'Đã xác nhận thư mục lưu. Cron và backup thủ công sẽ dùng nơi nhận này.'); } catch (error) { pickerError(error); } });
  find('backup-kind').addEventListener('change', renderTargets);
  find('backup-form').addEventListener('submit', async event => { event.preventDefault(); const submit = event.currentTarget.querySelector('[type=submit]'); try { await action(submit, async () => { const kind = find('backup-kind').value; const target = kind === 'path' ? find('backup-path').value.trim() : find('backup-target').value; if (!target) throw new Error('Chọn nguồn backup trước.'); if (!confirm('Backup ' + target + ' lên thư mục đã xác nhận? Plugin sẽ tạo archive trên đĩa VPS.')) return; const result = await api('start_backup', { kind, target, keep: find('backup-keep').value, exclusions: kind === 'database' ? '' : find('backup-exclusions').value }); message('Đã tạo tác vụ ' + result.job_id + '. Theo dõi tiến độ trong Lịch sử.', false); showTab('history'); }); } catch (error) {} });
  find('settings-form').addEventListener('submit', async event => { event.preventDefault(); if (find('prune').checked && !state.config.prune_enabled && !confirm('Cho phép đưa các bản backup cũ do plugin quản lý vào thùng rác Drive, theo số bản giữ lại của từng nguồn?')) { find('prune').checked = false; return; } try { await action(event.currentTarget.querySelector('[type=submit]'), async () => { await api('save_settings', { server_name: find('server-name').value, min_free_mb: find('min-free').value, max_archive_gb: find('max-archive').value, keep_local: find('keep-local').checked ? '1' : '0', prune_enabled: find('prune').checked ? '1' : '0' }); await refresh(); }, 'Đã lưu cài đặt.'); } catch (error) {} });
  function renderUpdate(result) {
    state.release = result;
    state.nextUpdateCheck = result.next_check_at;
    const headerUpdate = find('header-update');
    if (headerUpdate) {
      headerUpdate.hidden = !result.available || Boolean(result.error);
      headerUpdate.disabled = !result.available || Boolean(result.error);
      headerUpdate.textContent = result.available ? 'Cập nhật lên v' + result.version : 'Update';
    }
    text('update-status', result.error || (result.available ? 'Có phiên bản mới: v' + result.version : 'Bạn đang dùng bản mới nhất: v' + result.installed_version));
    text('update-checked', result.checked_at ? 'Lần kiểm tra: ' + date(result.checked_at) : 'Chưa kiểm tra được phiên bản.');
    find('update-banner').hidden = !result.available || Boolean(result.error);
    text('update-banner-text', result.available ? 'Đã có Shared Drive Backup v' + result.version + '.' : '');
    find('release-notes').hidden = !result.body;
    text('release-notes', result.body || '');
  }
  async function checkUpdates(force = false) {
    if (state.updateChecking || (!force && Date.now() / 1000 < state.nextUpdateCheck)) return;
    state.updateChecking = true;
    const headerUpdate = find('header-update');
    if (headerUpdate) headerUpdate.disabled = true;
    text('update-status', 'Đang kiểm tra phiên bản…');
    try {
      renderUpdate(await api('check_update', { force: force ? '1' : '0' }));
    } catch (error) {
      renderUpdate({ available: false, error: error.message, checked_at: null, next_check_at: Date.now() / 1000 + 900 });
    } finally {
      state.updateChecking = false;
    }
  }
  bind('check-update', event => action(event.currentTarget, () => checkUpdates(true)));
  async function triggerUpdate(control) {
    if (!state.release || !state.release.available || !confirm('Cập nhật lên ' + state.release.version + '? Giữ cấu hình và secrets, chặn cập nhật khi đang backup.')) return;
    await action(control, async () => {
      await api('update', { version: state.release.version });
      state.release = null;
      const headerUpdate = find('header-update');
      if (headerUpdate) {
        headerUpdate.disabled = true;
        headerUpdate.textContent = 'Đã cập nhật';
      }
      find('update-banner').hidden = true;
      text('update-status', 'Đã cập nhật. Đóng và mở lại plugin để nạp giao diện mới.');
      await refresh();
    }, 'Đã cập nhật. Đóng và mở lại plugin.');
  }
  bind('header-update', event => triggerUpdate(event.currentTarget));
  bind('rollback', async event => { if (!confirm('Khôi phục mã phiên bản trước, giữ dữ liệu và cấu hình?')) return; await action(event.currentTarget, async () => { await api('rollback'); await refresh(); }, 'Đã rollback. Đóng và mở lại plugin.'); });
  bind('repair-integration', event => action(event.currentTarget, async () => { const result = await api('repair_integration'); await refresh(); if (result.restart_panel_required) message('Adapter đã sửa. Cần nạp lại dịch vụ panel để backend nhận thay đổi; không cần restart OLS.', false); }));
  function syncTheme() {
    try {
      let isDark = false;
      const docEl = document.documentElement;
      const themeMode = docEl.getAttribute('theme-mode');
      const dataTheme = docEl.getAttribute('data-theme');
      if (themeMode === 'dark' || dataTheme === 'dark' || docEl.classList.contains('dark')) {
        isDark = true;
      } else if (themeMode === 'light' || dataTheme === 'light' || docEl.classList.contains('light')) {
        isDark = false;
      } else {
        const rawStore = localStorage.getItem('ThemeStore');
        if (rawStore) {
          const parsed = JSON.parse(rawStore);
          const ref = parsed && (parsed.themeRef || (parsed.theme && parsed.theme.value));
          if (ref === 'dark') isDark = true;
          else if (ref === 'light') isDark = false;
          else isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        } else {
          const bg = getComputedStyle(document.body).backgroundColor;
          const rgb = bg.match(/\d+/g);
          isDark = rgb && rgb.length >= 3 ? (Number(rgb[0]) + Number(rgb[1]) + Number(rgb[2]) <= 480) : false;
        }
      }
      root.classList.toggle('sdb-light', !isDark);
    } catch (e) {}
  }
  syncTheme();
  if (window.MutationObserver) {
    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['theme-mode', 'data-theme', 'class'] });
  }
  window.addEventListener('storage', event => { if (event.key === 'ThemeStore') syncTheme(); });
  bind('theme', () => { root.classList.toggle('sdb-light'); });
  find('http-warning').hidden = secure;
  function adjustModalPosition() {
    if (!window.$) return;
    const modal = window.$(root).closest('.layui-layer');
    if (!modal.length) return;
    const targetWidth = Math.min(1060, Math.max(360, window.innerWidth - 32));
    const targetLeft = Math.max(16, Math.round((window.innerWidth - targetWidth) / 2));
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 800;
    const targetTop = 20;
    const maxHeight = Math.max(300, viewportHeight - (targetTop * 2));
    modal.css({
      width: targetWidth + 'px',
      height: maxHeight + 'px',
      left: targetLeft + 'px',
      top: targetTop + 'px',
      transform: 'none',
      margin: '0',
      overflow: 'hidden'
    });
    const content = modal.find('.layui-layer-content');
    if (content.length) {
      const titleHeight = modal.find('.layui-layer-title').outerHeight() || 42;
      content.css({
        height: (maxHeight - titleHeight) + 'px',
        maxHeight: (maxHeight - titleHeight) + 'px',
        overflowY: 'auto',
        overflowX: 'hidden',
        boxSizing: 'border-box'
      });
    }
    const index = modal.attr('times');
    if (window.layer && index && typeof window.layer.style === 'function') {
      try { window.layer.style(index, { width: targetWidth + 'px', height: maxHeight + 'px', left: targetLeft + 'px', top: targetTop + 'px' }); } catch (e) {}
    }
  }
  adjustModalPosition();
  window.addEventListener('resize', adjustModalPosition);
  showTab('overview'); refresh().catch(error => message(error.message, true)); checkUpdates();
  const updateTimer = setInterval(() => { if (!root.isConnected) { clearInterval(updateTimer); return; } if (!document.hidden) checkUpdates(); }, 60000);
  const timer = setInterval(() => { if (!root.isConnected) { clearInterval(timer); return; } if (document.hidden || find('folder-dialog').open) return; const selected = root.querySelector('[role=tab][aria-selected=true]'); if (selected && selected.dataset.tab === 'overview') refresh().catch(() => {}); if (selected && selected.dataset.tab === 'history') loadHistory().catch(() => {}); }, 7000);
})();
