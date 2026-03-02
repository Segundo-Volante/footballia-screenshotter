// ===== STATE =====
const state = {
    currentView: 'home',
    project: null,
    matches: [],
    selectedMatch: null,
    targets: {},
    startTime: '00:00',
    ws: null,
    captureStatus: 'idle',
    progress: {},
    activityLog: [],
    latestFrame: null,
    cameraTypes: [],
    cameraDescriptions: {},
    defaultTargets: {},
    costPerFrame: 0.00007,
    quickCaptureMode: false,
    editingUrlMatchId: null,
    // Part 2 additions
    tasks: [],
    providers: [],
    selectedTask: null,
    selectedProvider: 'openai',
    selectedPreset: null,
    // Part 3 additions
    galleryFrames: [],
    galleryIndex: 0,
    galleryMode: 'manual',
    galleryCaptureId: null,
    galleryHistory: [],
    captureMode: 'full_match',
    scrapedGoals: [],
    scrapedData: null,
    activeCaptureId: null,
};

// ===== VIEW SWITCHING =====
function showView(viewName) {
    document.querySelectorAll('.modal').forEach(m => m.style.display = 'none');

    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const el = document.getElementById(`view-${viewName}`);
    if (el) el.classList.add('active');
    state.currentView = viewName;

    if (viewName === 'dashboard') {
        connectWebSocket();
    }

    if (viewName === 'library') {
        loadMatches();
    }

    if (viewName === 'home') {
        updateHomeView();
    }

    if (viewName === 'config') {
        loadConfigView();
    }

    // Clean up gallery keyboard listener when leaving
    if (viewName !== 'gallery') {
        document.removeEventListener('keydown', galleryKeyHandler);
    }
}

// ===== INITIALIZATION =====
async function init() {
    try {
        const projectRes = await fetch('/api/project').then(r => r.json());

        if (projectRes.needs_setup) {
            showView('setup');
            return;
        }

        state.project = projectRes.project;

        const configRes = await fetch('/api/config').then(r => r.json());
        state.cameraTypes = configRes.camera_types || [];
        state.cameraDescriptions = configRes.camera_descriptions || {};
        state.defaultTargets = configRes.defaults?.targets || {};

        const statusRes = await fetch('/api/capture/status').then(r => r.json());
        if (statusRes.status === 'capturing' || statusRes.status === 'paused') {
            showView('dashboard');
            return;
        }

        showView('home');
    } catch (e) {
        console.error('Init error:', e);
        showView('setup');
    }
}

// ===== HOME VIEW =====
function updateHomeView() {
    if (state.project) {
        const sub = `${state.project.team_name} · ${state.project.season}`;
        document.getElementById('home-subtitle').textContent = sub;
    }
    fetch('/api/matches').then(r => r.json()).then(matches => {
        state.matches = matches;
        const countEl = document.getElementById('home-match-count');
        if (countEl) countEl.textContent = `${matches.length} matches`;
    }).catch(() => {});
}

// ===== SETUP =====
async function completeSetup() {
    const teamName = document.getElementById('setup-team').value.trim();
    const season = document.getElementById('setup-season').value.trim();
    const comps = document.getElementById('setup-competitions').value.trim();
    const excelPath = document.getElementById('setup-excel-path').value.trim();

    if (!teamName) {
        alert('Please enter your team name.');
        return;
    }

    const competitions = comps ? comps.split(',').map(s => s.trim()).filter(Boolean) : [];

    try {
        await fetch('/api/setup', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({team_name: teamName, season, competitions}),
        });

        if (excelPath) {
            try {
                const importRes = await fetch('/api/matches/import-excel', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({filepath: excelPath, competition: competitions[0] || ''}),
                });
                const importData = await importRes.json();
                if (importData.imported > 0) {
                    console.log(`Imported ${importData.imported} matches`);
                }
            } catch (e) {
                console.warn('Excel import failed:', e);
            }
        }

        const configRes = await fetch('/api/config').then(r => r.json());
        state.cameraTypes = configRes.camera_types || [];
        state.cameraDescriptions = configRes.camera_descriptions || {};
        state.defaultTargets = configRes.defaults?.targets || {};

        state.project = {team_name: teamName, season, competitions};
        showView('home');
    } catch (e) {
        console.error('Setup error:', e);
        alert('Setup failed. Check console for details.');
    }
}

// ===== MATCH LIBRARY =====
async function loadMatches() {
    try {
        const matches = await fetch('/api/matches').then(r => r.json());
        state.matches = matches;
        renderMatchTable(matches);
    } catch (e) {
        console.error('Load matches error:', e);
    }
}

function renderMatchTable(matches) {
    const tbody = document.getElementById('match-tbody');
    tbody.innerHTML = '';

    matches.forEach(m => {
        const tr = document.createElement('tr');
        const hasUrl = m.footballia_url && m.footballia_url.length > 0;

        if (hasUrl) {
            tr.className = 'clickable';
            tr.onclick = (e) => {
                if (e.target.closest('.url-cell')) return;
                selectMatch(m);
            };
        } else {
            tr.className = 'no-url-row';
            tr.onclick = (e) => {
                if (e.target.closest('.url-cell')) return;
            };
        }

        const resultClass = m.result ? `result-${m.result.charAt(0).toUpperCase()}` : '';
        const urlCell = hasUrl
            ? `<td class="url-cell" onclick="showUrlEdit(${m.id}, '${(m.footballia_url || '').replace(/'/g, "\\'")}')"><span class="url-yes">&#10003;</span></td>`
            : `<td class="url-cell" onclick="showUrlEdit(${m.id}, '')"><span class="url-no">+ add</span></td>`;

        tr.innerHTML = `
            <td class="col-md">${m.md || m.match_day || ''}</td>
            <td>${m.date || ''}</td>
            <td>${m.home_away || ''}</td>
            <td>${m.opponent || ''}</td>
            <td class="col-score">${m.score || ''}</td>
            <td class="${resultClass}">${m.result || ''}</td>
            ${urlCell}
        `;

        tbody.appendChild(tr);
    });
}

// Search filter
document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('match-search');
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            const q = searchInput.value.toLowerCase();
            const filtered = state.matches.filter(m =>
                (m.opponent || '').toLowerCase().includes(q) ||
                (m.date || '').includes(q) ||
                String(m.md || m.match_day || '').includes(q)
            );
            renderMatchTable(filtered);
        });
    }

    document.querySelectorAll('input[name="start-pos"]').forEach(radio => {
        radio.addEventListener('change', () => {
            const timeInput = document.getElementById('start-time-input');
            timeInput.disabled = radio.value !== 'custom';
            if (radio.value === 'beginning') {
                timeInput.value = '';
            }
        });
    });

    init();
});

// ===== QUICK CAPTURE =====
function showQuickCapture() {
    document.getElementById('quick-capture-modal').style.display = 'flex';
    document.getElementById('quick-url').value = '';
    document.getElementById('quick-opponent').value = '';
    document.getElementById('quick-date').value = '';
    document.getElementById('quick-url').focus();
}

function hideQuickCapture() {
    document.getElementById('quick-capture-modal').style.display = 'none';
}

async function startQuickCapture() {
    const url = document.getElementById('quick-url').value.trim();
    if (!url) {
        alert('Please paste a Footballia URL.');
        return;
    }

    const opponent = document.getElementById('quick-opponent').value.trim() || 'Unknown';
    const date = document.getElementById('quick-date').value.trim() || '';

    const targets = {};
    state.cameraTypes.forEach(type => {
        targets[type] = state.defaultTargets[type] || 0;
    });

    state.quickCaptureMode = true;
    state.selectedMatch = {
        opponent,
        date,
        home_away: '',
        md: 0,
        match_day: 0,
        score: '',
        footballia_url: url,
    };
    state.targets = targets;

    hideQuickCapture();

    document.getElementById('config-match-title').textContent = `Quick Capture \u00b7 ${opponent}`;
    document.getElementById('config-match-date').textContent = date || 'Footballia URL';
    document.getElementById('config-back-btn').onclick = () => showView('home');

    showView('config');
}

// ===== URL EDITING =====
function showUrlEdit(matchId, currentUrl) {
    state.editingUrlMatchId = matchId;
    document.getElementById('url-edit-input').value = currentUrl || '';
    const match = state.matches.find(m => m.id === matchId);
    document.getElementById('url-edit-title').textContent =
        match ? `Edit URL \u00b7 ${match.opponent}` : 'Edit Footballia URL';
    document.getElementById('url-edit-modal').style.display = 'flex';
    document.getElementById('url-edit-input').focus();
}

function hideUrlEdit() {
    document.getElementById('url-edit-modal').style.display = 'none';
    state.editingUrlMatchId = null;
}

async function saveUrl() {
    const url = document.getElementById('url-edit-input').value.trim();
    const matchId = state.editingUrlMatchId;
    if (!matchId) return;

    try {
        await fetch(`/api/matches/${matchId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({footballia_url: url}),
        });
        hideUrlEdit();
        loadMatches();
    } catch (e) {
        console.error('Save URL error:', e);
        alert('Failed to save URL.');
    }
}

// ===== ADD MATCH =====
function showAddMatchDialog() {
    document.getElementById('add-match-modal').style.display = 'flex';
    document.getElementById('add-md').value = '';
    document.getElementById('add-date').value = '';
    document.getElementById('add-ha').value = 'H';
    document.getElementById('add-opponent').value = '';
    document.getElementById('add-score').value = '';
    document.getElementById('add-url').value = '';
}

function hideAddMatch() {
    document.getElementById('add-match-modal').style.display = 'none';
}

async function saveNewMatch() {
    const opponent = document.getElementById('add-opponent').value.trim();
    if (!opponent) {
        alert('Please enter the opponent name.');
        return;
    }

    const score = document.getElementById('add-score').value.trim();
    let result = '';
    if (score) {
        const parts = score.split('-').map(s => parseInt(s.trim()));
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
            const ha = document.getElementById('add-ha').value;
            const [a, b] = ha === 'H' ? [parts[0], parts[1]] : [parts[1], parts[0]];
            if (a > b) result = 'W';
            else if (a < b) result = 'L';
            else result = 'D';
        }
    }

    const body = {
        match_day: parseInt(document.getElementById('add-md').value) || 0,
        date: document.getElementById('add-date').value || '',
        home_away: document.getElementById('add-ha').value,
        opponent,
        score,
        result,
        footballia_url: document.getElementById('add-url').value.trim(),
        competition: state.project?.competitions?.[0] || '',
        season: state.project?.season || '',
        team_name: state.project?.team_name || '',
    };

    try {
        await fetch('/api/matches', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        hideAddMatch();
        loadMatches();
    } catch (e) {
        console.error('Add match error:', e);
        alert('Failed to add match.');
    }
}

// ===== IMPORT EXCEL =====
function showImportDialog() {
    document.getElementById('import-modal').style.display = 'flex';
    document.getElementById('import-path').value = '';
    document.getElementById('import-competition').value = '';
}

function hideImportDialog() {
    document.getElementById('import-modal').style.display = 'none';
}

async function doImportExcel() {
    const filepath = document.getElementById('import-path').value.trim();
    if (!filepath) {
        alert('Please enter the path to your Excel file.');
        return;
    }

    const competition = document.getElementById('import-competition').value.trim();

    try {
        const res = await fetch('/api/matches/import-excel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({filepath, competition}),
        });
        const data = await res.json();
        if (data.status === 'ok') {
            alert(`Imported ${data.imported} matches.`);
            hideImportDialog();
            loadMatches();
        } else {
            alert(data.message || 'Import failed.');
        }
    } catch (e) {
        console.error('Import error:', e);
        alert('Import failed. Check the file path and try again.');
    }
}

// ===== VIEW 3: CONFIGURATION =====
function selectMatch(match) {
    state.selectedMatch = match;
    state.quickCaptureMode = false;

    const ha = match.home_away === 'H' ? '(H)' : match.home_away === 'A' ? '(A)' : '';
    const md = match.md || match.match_day || '';
    const mdStr = md ? `MD${md} \u00b7 ` : '';
    document.getElementById('config-match-title').textContent =
        `${mdStr}${match.opponent} ${ha} \u00b7 ${match.score || ''}`;
    document.getElementById('config-match-date').textContent = match.date || '';
    document.getElementById('config-back-btn').onclick = () => showView('library');

    if (match.id) {
        loadScrapedData(match.id);
    }

    showView('config');
}

async function loadConfigView() {
    // Load tasks and providers in parallel
    try {
        const [tasksRes, providersRes] = await Promise.all([
            fetch('/api/tasks').then(r => r.json()),
            fetch('/api/providers').then(r => r.json()),
        ]);
        state.tasks = tasksRes;
        state.providers = providersRes;

        // Default selections
        if (!state.selectedTask && state.tasks.length > 0) {
            state.selectedTask = state.tasks[0].id;
        }

        renderTaskCards();
        renderProviderCards();
        await loadTaskDetails();
    } catch (e) {
        console.error('Config load error:', e);
        // Fallback to existing behavior
        renderTargetInputs();
        updateSummary();
    }
}

function renderTaskCards() {
    const container = document.getElementById('task-cards');
    container.innerHTML = '';

    state.tasks.forEach(task => {
        const card = document.createElement('div');
        card.className = `task-card${task.id === state.selectedTask ? ' selected' : ''}`;
        card.onclick = () => selectTask(task.id);

        const catCount = task.category_count || 0;
        card.innerHTML = `
            <div class="task-card-name">${task.name}</div>
            <div class="task-card-meta">${catCount} categories</div>
        `;
        container.appendChild(card);
    });
}

async function selectTask(taskId) {
    state.selectedTask = taskId;
    state.selectedPreset = null;
    renderTaskCards();
    await loadTaskDetails();
}

async function loadTaskDetails() {
    if (!state.selectedTask) return;

    try {
        const task = await fetch(`/api/tasks/${state.selectedTask}`).then(r => r.json());

        // Update categories from task
        state.cameraTypes = (task.categories || []).map(c => c.value);
        state.cameraDescriptions = {};
        (task.categories || []).forEach(c => {
            state.cameraDescriptions[c.value] = c.label || c.value;
        });

        // Set suggested targets as defaults
        state.defaultTargets = task.suggested_targets || {};

        // Render presets
        renderPresetBar(task.presets || []);
        renderTargetInputs();
        updateSummary();
    } catch (e) {
        console.error('Load task error:', e);
    }
}

function renderPresetBar(presets) {
    const bar = document.getElementById('preset-bar');
    bar.innerHTML = '';

    presets.forEach(preset => {
        const btn = document.createElement('button');
        btn.className = `preset-btn${preset.id === state.selectedPreset ? ' selected' : ''}`;
        btn.textContent = preset.name;
        btn.onclick = () => selectPreset(preset);
        bar.appendChild(btn);
    });

    // Custom option
    const customBtn = document.createElement('button');
    customBtn.className = `preset-btn${state.selectedPreset === null ? ' selected' : ''}`;
    customBtn.textContent = 'Custom';
    customBtn.onclick = () => {
        state.selectedPreset = null;
        renderPresetBar(presets);
    };
    bar.appendChild(customBtn);
}

async function selectPreset(preset) {
    state.selectedPreset = preset.id;

    try {
        const res = await fetch(`/api/tasks/${state.selectedTask}/presets/${preset.id}`).then(r => r.json());
        if (res.targets) {
            state.targets = {};
            state.cameraTypes.forEach(type => {
                state.targets[type] = res.targets[type] || 0;
            });

            // Update input fields
            state.cameraTypes.forEach(type => {
                const input = document.querySelector(`.target-input[data-type="${type}"]`);
                if (input) input.value = state.targets[type] || 0;
            });

            updateSummary();
        }
    } catch (e) {
        console.error('Preset load error:', e);
    }

    // Re-render preset bar to update selection
    const taskRes = await fetch(`/api/tasks/${state.selectedTask}`).then(r => r.json());
    renderPresetBar(taskRes.presets || []);
}

function renderProviderCards() {
    const container = document.getElementById('provider-cards');
    container.innerHTML = '';

    state.providers.forEach(provider => {
        const card = document.createElement('div');
        const isSelected = provider.id === state.selectedProvider;
        const isAvailable = provider.available;
        card.className = `provider-card${isSelected ? ' selected' : ''}${!isAvailable ? ' unavailable' : ''}`;
        card.onclick = () => {
            if (isAvailable) selectProvider(provider.id);
        };

        const costStr = provider.cost_per_frame > 0
            ? `$${(provider.cost_per_frame * 1000).toFixed(2)}/1K frames`
            : 'Free';
        const statusStr = isAvailable ? costStr : 'No API key';

        card.innerHTML = `
            <div class="provider-card-name">${provider.name}</div>
            <div class="provider-card-desc">${provider.description}</div>
            <div class="provider-card-cost">${statusStr}</div>
        `;
        container.appendChild(card);
    });
}

function selectProvider(providerId) {
    state.selectedProvider = providerId;
    const provider = state.providers.find(p => p.id === providerId);
    if (provider) {
        state.costPerFrame = provider.cost_per_frame || 0;
    }
    renderProviderCards();
    updateSummary();
}

function renderTargetInputs() {
    const container = document.getElementById('target-inputs');
    container.innerHTML = '';

    state.cameraTypes.forEach(type => {
        const defaultVal = state.targets[type] !== undefined ? state.targets[type] : (state.defaultTargets[type] || 0);
        state.targets[type] = defaultVal;

        const desc = state.cameraDescriptions[type] || '';

        const row = document.createElement('div');
        row.className = 'target-row';
        row.innerHTML = `
            <div class="target-info">
                <span class="target-name">${formatCategoryLabel(type)}</span>
                <span class="target-hint">${desc}</span>
            </div>
            <input type="number" class="target-input" data-type="${type}"
                   value="${defaultVal}" min="0" max="500" />
        `;
        container.appendChild(row);
    });

    container.querySelectorAll('.target-input').forEach(input => {
        input.addEventListener('input', () => {
            state.targets[input.dataset.type] = parseInt(input.value) || 0;
            updateSummary();
        });
    });
}

function formatCategoryLabel(type) {
    return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
               .replace('Closeup', 'Close-up');
}

function updateSummary() {
    const total = Object.values(state.targets).reduce((a, b) => a + b, 0);
    const cost = total * state.costPerFrame;

    document.getElementById('total-count').textContent = total;
    document.getElementById('est-cost').textContent = state.costPerFrame > 0 ? `$${cost.toFixed(3)}` : 'Free';
    document.getElementById('summary-provider').textContent = state.selectedProvider;
}

// ===== START CAPTURE =====
async function startCapture() {
    const match = state.selectedMatch;
    if (!match) return;

    const startPosRadio = document.querySelector('input[name="start-pos"]:checked');
    let startTime = '00:00';
    if (startPosRadio && startPosRadio.value === 'custom') {
        const timeInput = document.getElementById('start-time-input');
        startTime = timeInput.value || '00:00';
    }

    const body = {
        match_id: match.id || null,
        footballia_url: match.footballia_url,
        targets: state.targets,
        start_time: startTime,
        match_data: match,
        source_type: 'footballia',
        provider: state.selectedProvider,
        task_id: state.selectedTask || 'camera_angle',
        capture_mode: state.captureMode,
        goal_times: state.scrapedGoals,
        goal_window: parseInt(document.getElementById('goal-window')?.value || '30'),
    };

    try {
        const res = await fetch('/api/capture/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await res.json();

        if (data.status === 'started') {
            state.activeCaptureId = data.capture_id;
            setupDashboard(match);
            showView('dashboard');
        } else {
            alert(data.message || 'Failed to start capture');
        }
    } catch (e) {
        console.error('Start capture error:', e);
        alert('Failed to start capture');
    }
}

// ===== VIEW 4: DASHBOARD =====
function setupDashboard(match) {
    const ha = match.home_away === 'H' ? '(H)' : match.home_away === 'A' ? '(A)' : '';
    const md = match.md || match.match_day || '';
    const mdStr = md ? `MD${md} \u00b7 ` : '';
    const title = `${mdStr}${match.opponent || 'Quick Capture'} ${ha} \u00b7 ${match.score || ''}`;

    document.getElementById('dash-match-title').textContent = title;
    document.getElementById('dash-provider').textContent = state.selectedProvider;
    document.getElementById('completed-match-title').textContent =
        `${title} \u00b7 ${match.date || ''}`;

    const tbody = document.getElementById('progress-tbody');
    tbody.innerHTML = '';

    state.cameraTypes.forEach(type => {
        const target = state.targets[type] || 0;
        if (target <= 0) return;

        const tr = document.createElement('tr');
        tr.id = `prog-row-${type}`;
        tr.innerHTML = `
            <td>${formatCategoryLabel(type)}</td>
            <td class="col-num">${target}</td>
            <td class="col-num" data-count="${type}">0</td>
            <td class="col-bar">
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" data-bar="${type}"></div>
                </div>
            </td>
        `;
        tbody.appendChild(tr);
    });

    // Reset state
    state.activityLog = [];
    state.latestFrame = null;
    document.getElementById('activity-log').innerHTML = '';
    document.getElementById('latest-thumbnail').src = '';
    document.getElementById('latest-info').textContent = 'Waiting for first frame...';
    document.getElementById('dashboard-capturing').style.display = 'block';
    document.getElementById('dashboard-completed').style.display = 'none';
    document.getElementById('pause-overlay').classList.remove('active');

    // Reset filter stats
    document.getElementById('fs-total').textContent = '0';
    document.getElementById('fs-passed').textContent = '0';
    document.getElementById('fs-black').textContent = '0';
    document.getElementById('fs-dup').textContent = '0';
    document.getElementById('fs-scene').textContent = '0';
}

// ===== WEBSOCKET =====
function connectWebSocket() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) return;

    state.ws = new WebSocket(`ws://${location.host}/ws`);

    state.ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
            case 'progress':
                updateProgressUI(msg);
                break;
            case 'frame_classified':
                addToActivityLog(msg);
                updateLatestFrame(msg);
                break;
            case 'frame_skipped':
                addToActivityLog({...msg, skipped: true});
                break;
            case 'frame_filtered':
                // Silently count — stats shown in filter-stats bar
                break;
            case 'status':
                handleStatusChange(msg);
                break;
            case 'completed':
                showCompletionSummary(msg.summary);
                document.getElementById('completion-actions').style.display = '';
                showCompletionActions(msg.summary);
                break;
            case 'error':
                showError(msg.message);
                break;
        }
    };

    state.ws.onclose = () => {
        setTimeout(() => {
            if (state.currentView === 'dashboard') {
                connectWebSocket();
            }
        }, 2000);
    };

    state.ws.onerror = () => {};
}

function sendAction(action) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({action}));
    } else {
        fetch(`/api/capture/${action}`, {method: 'POST'});
    }
}

// ===== UI UPDATE FUNCTIONS =====
function updateProgressUI(msg) {
    document.getElementById('video-time').textContent = formatTime(msg.video_time);
    document.getElementById('video-duration').textContent = formatTime(msg.video_duration);
    document.getElementById('video-part').textContent =
        `Part ${msg.video_part} of ${msg.total_parts}`;

    if (msg.provider) {
        document.getElementById('dash-provider').textContent = msg.provider;
    }

    // Update pre-filter stats
    if (msg.pre_filter_stats) {
        const pf = msg.pre_filter_stats;
        document.getElementById('fs-total').textContent = pf.total || 0;
        document.getElementById('fs-passed').textContent = pf.passed || 0;
        document.getElementById('fs-black').textContent = pf.black || 0;
        document.getElementById('fs-dup').textContent = pf.duplicate || 0;
        document.getElementById('fs-scene').textContent = pf.scene_changes || 0;
    }

    if (msg.counts) {
        for (const [type, data] of Object.entries(msg.counts)) {
            const pct = data.target > 0 ? Math.min(100, (data.captured / data.target) * 100) : 0;
            const bar = document.querySelector(`[data-bar="${type}"]`);
            const countEl = document.querySelector(`[data-count="${type}"]`);
            const row = document.getElementById(`prog-row-${type}`);

            if (bar) {
                bar.style.width = `${pct}%`;
                if (data.captured >= data.target && data.target > 0) {
                    bar.classList.add('complete');
                    if (row) row.classList.add('row-complete');
                }
            }
            if (countEl) countEl.textContent = data.captured;
        }
    }

    document.getElementById('total-progress').textContent =
        `${msg.total_captured} / ${msg.total_target}`;
    document.getElementById('api-cost').textContent =
        `$${(msg.api_cost || 0).toFixed(4)}`;

    const overallPct = msg.total_target > 0
        ? Math.min(100, (msg.total_captured / msg.total_target) * 100) : 0;
    document.getElementById('overall-bar').style.width = `${overallPct}%`;
    document.getElementById('overall-pct').textContent = `${Math.round(overallPct)}%`;
}

function addToActivityLog(msg) {
    state.activityLog.unshift(msg);
    if (state.activityLog.length > 50) state.activityLog.pop();
    renderActivityLog();
}

function renderActivityLog() {
    const container = document.getElementById('activity-log');
    container.innerHTML = '';

    state.activityLog.forEach(entry => {
        const div = document.createElement('div');
        div.className = 'log-entry';
        if (entry.anomaly) div.className += ' log-anomaly';

        const now = new Date();
        const timeStr = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
        const videoTimeStr = formatTime(entry.video_time || 0);
        const classifiedAs = entry.classified_as || entry.camera_type || '';

        if (entry.skipped) {
            div.innerHTML = `
                <span class="log-time">${timeStr}</span>
                <span class="log-file">${videoTimeStr}</span>
                <span class="log-type">${classifiedAs}</span>
                <span class="log-skip">\u2192skip</span>
            `;
        } else {
            const conf = entry.confidence ? `${Math.round(entry.confidence * 100)}%` : '';
            const anomalyTag = entry.anomaly ? `<span class="log-anomaly-tag">!</span>` : '';
            div.innerHTML = `
                <span class="log-time">${timeStr}</span>
                <span class="log-file">${entry.filename || videoTimeStr}</span>
                <span class="log-type">${classifiedAs}${anomalyTag}</span>
                <span class="log-conf">conf: ${conf}</span>
            `;
        }

        container.appendChild(div);
    });
}

function updateLatestFrame(msg) {
    if (msg.thumbnail_b64) {
        state.latestFrame = msg;
        const img = document.getElementById('latest-thumbnail');
        img.src = `data:image/jpeg;base64,${msg.thumbnail_b64}`;

        const classifiedAs = msg.classified_as || msg.camera_type || '';
        const confText = msg.confidence ? `${Math.round(msg.confidence * 100)}%` : '';
        document.getElementById('latest-info').textContent =
            `${formatTime(msg.video_time)} \u00b7 ${classifiedAs} \u00b7 ${confText} confidence`;
    }
}

function handleStatusChange(msg) {
    state.captureStatus = msg.status;

    if (msg.status === 'paused') {
        showPauseOverlay(msg.pause_time || '00:00');
    } else if (msg.status === 'capturing') {
        hidePauseOverlay();
    }
}

function showPauseOverlay(pauseTime) {
    const overlay = document.getElementById('pause-overlay');
    overlay.classList.add('active');
    document.getElementById('pause-time-display').textContent = pauseTime;

    const totalCaptured = state.activityLog.filter(e => !e.skipped).length;
    document.getElementById('pause-info').textContent =
        `Progress saved. ${totalCaptured} screenshots captured so far.`;
}

function hidePauseOverlay() {
    document.getElementById('pause-overlay').classList.remove('active');
}

function copyPauseTime() {
    const time = document.getElementById('pause-time-display').textContent;
    navigator.clipboard.writeText(time).then(() => {
        const btn = document.getElementById('copy-time-btn');
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = 'Copy', 1500);
    });
}

function showCompletionSummary(summary) {
    document.getElementById('dashboard-capturing').style.display = 'none';
    document.getElementById('pause-overlay').classList.remove('active');
    document.getElementById('dashboard-completed').style.display = 'block';

    const tbody = document.getElementById('completed-tbody');
    tbody.innerHTML = '';

    let totalTarget = 0;
    let totalCaptured = 0;

    if (summary.counts) {
        for (const [type, data] of Object.entries(summary.counts)) {
            if (data.target <= 0) continue;
            totalTarget += data.target;
            totalCaptured += data.captured;

            const tr = document.createElement('tr');
            let statusText, statusClass;

            if (data.captured >= data.target) {
                statusText = '\u2713 Complete';
                statusClass = 'status-complete';
            } else if (data.captured === 0) {
                statusText = 'Rare angle';
                statusClass = 'status-short';
            } else {
                statusText = 'Video ended';
                statusClass = 'status-short';
            }

            tr.innerHTML = `
                <td>${formatCategoryLabel(type)}</td>
                <td class="col-num">${data.target}</td>
                <td class="col-num">${data.captured}</td>
                <td class="${statusClass}">${statusText}</td>
            `;
            tbody.appendChild(tr);
        }
    }

    const totalTr = document.createElement('tr');
    totalTr.style.fontWeight = '600';
    totalTr.innerHTML = `
        <td>Total</td>
        <td class="col-num">${totalTarget}</td>
        <td class="col-num">${totalCaptured}</td>
        <td></td>
    `;
    tbody.appendChild(totalTr);

    document.getElementById('completed-duration').textContent =
        `${summary.duration_minutes || 0}m`;
    document.getElementById('completed-cost').textContent =
        `$${(summary.api_cost || 0).toFixed(4)}`;
    document.getElementById('completed-provider').textContent =
        summary.provider || '';
    document.getElementById('completed-output-dir').textContent =
        summary.output_dir || '';
}

function showError(message) {
    addToActivityLog({
        classified_as: 'ERROR',
        video_time: 0,
        filename: message,
        skipped: false,
        confidence: 0,
    });
    console.error('Pipeline error:', message);
}

// ===== GALLERY / REVIEW =====

async function openGallery(captureId, mode) {
    state.galleryCaptureId = captureId;
    state.galleryMode = mode;
    state.galleryIndex = 0;
    state.galleryHistory = [];

    let url = `/api/captures/${captureId}/frames`;
    if (mode === 'manual') {
        url += '?only_pending=true';
    } else {
        const threshold = document.getElementById('confidence-slider')?.value || 85;
        url += `?max_confidence=${threshold / 100}&only_unreviewed=true`;
    }

    const res = await fetch(url).then(r => r.json());
    state.galleryFrames = res.frames || [];

    if (state.galleryFrames.length === 0) {
        alert(mode === 'manual'
            ? 'No pending frames to classify.'
            : 'No frames need review — all classifications look good!');
        return;
    }

    document.getElementById('gallery-title').textContent =
        mode === 'manual' ? 'Manual Classification' : 'Review Classifications';
    document.getElementById('review-controls').style.display =
        mode === 'review' ? '' : 'none';

    renderGalleryButtons();
    renderGalleryFrame(0);
    renderFilmstrip();
    updateGalleryStats();

    showView('gallery');
    document.addEventListener('keydown', galleryKeyHandler);
}

function exitGallery() {
    document.removeEventListener('keydown', galleryKeyHandler);
    showView('home');
}

function renderGalleryFrame(index) {
    if (index < 0 || index >= state.galleryFrames.length) return;
    state.galleryIndex = index;

    const frame = state.galleryFrames[index];

    document.getElementById('gallery-image').src = `/api/frames/${frame.id}/image`;
    document.getElementById('gallery-filename').textContent = frame.filename || '';
    const mins = Math.floor((frame.video_time || 0) / 60);
    const secs = Math.floor((frame.video_time || 0) % 60);
    document.getElementById('gallery-time').textContent = `${mins}:${secs.toString().padStart(2, '0')}`;

    // AI label (review mode only)
    const aiLabel = document.getElementById('gallery-ai-label');
    if (state.galleryMode === 'review' && frame.camera_type !== 'PENDING') {
        aiLabel.style.display = '';
        document.getElementById('gallery-ai-type').textContent = frame.camera_type;
        const conf = Math.round((frame.confidence || 0) * 100);
        document.getElementById('gallery-ai-confidence').textContent = `${conf}%`;
        try {
            const raw = JSON.parse(frame.raw_response || '{}');
            document.getElementById('gallery-ai-reasoning').textContent = raw.reasoning || '';
        } catch {
            document.getElementById('gallery-ai-reasoning').textContent = '';
        }
    } else {
        aiLabel.style.display = 'none';
    }

    // Anomaly warning
    const anomalyEl = document.getElementById('gallery-anomaly');
    if (frame.anomaly) {
        anomalyEl.style.display = '';
        document.getElementById('gallery-anomaly-text').textContent =
            frame.consistency_note || 'Possible misclassification';
    } else {
        anomalyEl.style.display = 'none';
    }

    // Update progress
    const reviewed = state.galleryFrames.filter(f => f.is_reviewed).length;
    document.getElementById('gallery-progress-text').textContent =
        `${reviewed} / ${state.galleryFrames.length} reviewed`;
    const pct = state.galleryFrames.length > 0
        ? (reviewed / state.galleryFrames.length) * 100 : 0;
    document.getElementById('gallery-progress-fill').style.width = `${pct}%`;

    renderFilmstrip();
}

function renderGalleryButtons() {
    const task = state.tasks.find(t => t.id === (state.selectedTask || 'camera_angle'));
    const categories = task ? task.categories.map(c => c.value || c) : state.cameraTypes;

    const container = document.getElementById('gallery-buttons');
    container.innerHTML = categories.map((cat, i) => {
        const shortLabel = cat.replace(/_/g, ' ').replace('WIDE CENTER', 'W. Center')
            .replace('WIDE LEFT', 'W. Left').replace('WIDE RIGHT', 'W. Right')
            .replace('BEHIND GOAL', 'Behind G.').replace('CLOSEUP', 'Close-up');
        const keyNum = i + 1;
        return `
            <button class="gallery-cat-btn" data-category="${cat}"
                    onclick="classifyCurrentFrame('${cat}')">
                <span class="cat-key">${keyNum <= 9 ? keyNum : ''}</span>
                <span class="cat-label">${shortLabel}</span>
            </button>
        `;
    }).join('');
}

async function classifyCurrentFrame(category) {
    const frame = state.galleryFrames[state.galleryIndex];
    if (!frame) return;

    const oldType = frame.camera_type;

    await fetch(`/api/frames/${frame.id}/review`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({classified_as: category}),
    });

    frame.camera_type = category;
    frame.reviewed_type = category;
    frame.is_reviewed = 1;

    state.galleryHistory.push({frameId: frame.id, oldType, newType: category});

    const btn = document.querySelector(`.gallery-cat-btn[data-category="${category}"]`);
    if (btn) {
        btn.classList.add('flash');
        setTimeout(() => btn.classList.remove('flash'), 200);
    }

    advanceToNextUnreviewed();
    updateGalleryStats();
}

function advanceToNextUnreviewed() {
    for (let i = state.galleryIndex + 1; i < state.galleryFrames.length; i++) {
        if (!state.galleryFrames[i].is_reviewed) {
            renderGalleryFrame(i);
            return;
        }
    }
    for (let i = 0; i < state.galleryIndex; i++) {
        if (!state.galleryFrames[i].is_reviewed) {
            renderGalleryFrame(i);
            return;
        }
    }
    const allDone = state.galleryFrames.every(f => f.is_reviewed);
    if (allDone) {
        alert('All frames have been classified! You can close this view.');
    }
}

function galleryKeyHandler(e) {
    if (state.currentView !== 'gallery') return;

    if (e.key >= '1' && e.key <= '9') {
        e.preventDefault();
        const task = state.tasks.find(t => t.id === (state.selectedTask || 'camera_angle'));
        const categories = task ? task.categories.map(c => c.value || c) : state.cameraTypes;
        const index = parseInt(e.key) - 1;
        if (index < categories.length) {
            classifyCurrentFrame(categories[index]);
        }
        return;
    }

    if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (state.galleryIndex < state.galleryFrames.length - 1) {
            renderGalleryFrame(state.galleryIndex + 1);
        }
        return;
    }
    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (state.galleryIndex > 0) {
            renderGalleryFrame(state.galleryIndex - 1);
        }
        return;
    }

    if (e.key === 's' || e.key === 'S') {
        e.preventDefault();
        classifyCurrentFrame('OTHER');
        return;
    }

    if (e.ctrlKey && e.key === 'z') {
        e.preventDefault();
        undoLastClassification();
        return;
    }
}

async function undoLastClassification() {
    if (state.galleryHistory.length === 0) return;

    const last = state.galleryHistory.pop();

    await fetch(`/api/frames/${last.frameId}/review`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({classified_as: last.oldType}),
    });

    const frame = state.galleryFrames.find(f => f.id === last.frameId);
    if (frame) {
        frame.camera_type = last.oldType;
        frame.is_reviewed = last.oldType === 'PENDING' ? 0 : 1;

        const idx = state.galleryFrames.indexOf(frame);
        if (idx >= 0) renderGalleryFrame(idx);
    }

    updateGalleryStats();
}

function renderFilmstrip() {
    const container = document.getElementById('gallery-filmstrip');
    const windowSize = 15;
    const start = Math.max(0, state.galleryIndex - Math.floor(windowSize / 2));
    const end = Math.min(state.galleryFrames.length, start + windowSize);

    container.innerHTML = '';
    for (let i = start; i < end; i++) {
        const frame = state.galleryFrames[i];
        const thumb = document.createElement('div');
        thumb.className = `filmstrip-thumb ${i === state.galleryIndex ? 'active' : ''}`;
        if (frame.is_reviewed) thumb.classList.add('reviewed');
        if (frame.anomaly) thumb.classList.add('anomaly');

        thumb.innerHTML = `<img src="/api/frames/${frame.id}/image" loading="lazy" />`;
        thumb.onclick = () => renderGalleryFrame(i);
        container.appendChild(thumb);
    }
}

function updateGalleryStats() {
    const counts = {};
    state.galleryFrames.forEach(f => {
        const type = f.is_reviewed ? (f.reviewed_type || f.camera_type) : f.camera_type;
        counts[type] = (counts[type] || 0) + 1;
    });

    const container = document.getElementById('gallery-stats');
    container.innerHTML = Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([type, count]) => `
            <div class="stat-row">
                <span class="stat-type">${type.replace(/_/g, ' ')}</span>
                <span class="stat-count">${count}</span>
            </div>
        `).join('');
}

async function batchAccept() {
    const threshold = parseInt(document.getElementById('batch-threshold')?.textContent || '90') / 100;
    const res = await fetch(`/api/captures/${state.galleryCaptureId}/batch-accept`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({min_confidence: threshold}),
    });
    const data = await res.json();

    const threshold_pct = parseInt(document.getElementById('confidence-slider')?.value || '85');
    const framesRes = await fetch(
        `/api/captures/${state.galleryCaptureId}/frames?max_confidence=${threshold_pct / 100}&only_unreviewed=true`
    ).then(r => r.json());
    state.galleryFrames = framesRes.frames || [];

    if (state.galleryFrames.length === 0) {
        alert(`Accepted ${data.accepted} frames. All frames are now reviewed!`);
        exitGallery();
    } else {
        state.galleryIndex = 0;
        renderGalleryFrame(0);
        renderFilmstrip();
        updateGalleryStats();
    }
}

function updateConfidenceFilter(value) {
    document.getElementById('confidence-value').textContent = `${value}%`;
}

function showAllFrames() {
    fetch(`/api/captures/${state.galleryCaptureId}/frames`).then(r => r.json()).then(res => {
        state.galleryFrames = res.frames || [];
        state.galleryIndex = 0;
        renderGalleryFrame(0);
        renderFilmstrip();
        updateGalleryStats();
    });
}

// ===== POST-CAPTURE ACTIONS =====

function showCompletionActions(summary) {
    const actionsDiv = document.getElementById('completion-actions');
    if (!actionsDiv) return;

    let html = '';

    if (summary.provider === 'manual') {
        const pendingCount = summary.counts?.PENDING || 0;
        html += `
            <button class="btn-primary" onclick="openGallery(${state.activeCaptureId}, 'manual')">
                Classify ${pendingCount} Frames
            </button>
        `;
    }

    if (summary.provider !== 'manual') {
        const anomalies = summary.anomalies || 0;
        html += `
            <button class="btn-secondary" onclick="openGallery(${state.activeCaptureId}, 'review')">
                Review Classifications${anomalies > 0 ? ` (${anomalies} flagged)` : ''}
            </button>
        `;
    }

    html += `
        <button class="btn-text" onclick="showView('home')">
            Done — Back to Home
        </button>
    `;

    actionsDiv.innerHTML = html;
}

// ===== CAPTURE MODE =====

function selectCaptureMode(mode) {
    state.captureMode = mode;

    document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('selected'));
    event.currentTarget.classList.add('selected');

    document.getElementById('goals-detail').style.display =
        mode === 'goals_only' ? '' : 'none';
    document.getElementById('custom-times-detail').style.display =
        mode === 'custom_times' ? '' : 'none';

    if (mode === 'goals_only' && state.scrapedGoals.length === 0) {
        document.getElementById('goals-detail').innerHTML =
            '<p class="warning">No goal data available. Goals will be detected after the page loads.</p>';
    }
}

async function loadScrapedData(matchId) {
    try {
        const data = await fetch(`/api/matches/${matchId}/scraped`).then(r => r.json());
        state.scrapedData = data;

        state.scrapedGoals = data.goals || [];

        if (state.scrapedGoals.length > 0) {
            const badge = document.getElementById('goals-badge');
            badge.textContent = `${state.scrapedGoals.length} goals`;
            badge.style.display = '';

            document.getElementById('goals-list').innerHTML = state.scrapedGoals
                .map(g => `<div class="goal-item">&#x26BD; ${g.minute}' ${g.scorer}${g.team !== 'unknown' ? ` (${g.team})` : ''}</div>`)
                .join('');
        }

        if (data.home_lineup?.length > 0 || data.away_lineup?.length > 0) {
            const lineupInfo = document.getElementById('config-lineup-info');
            if (lineupInfo) {
                lineupInfo.textContent =
                    `Lineups: ${data.home_lineup?.length || 0} + ${data.away_lineup?.length || 0} players`;
                lineupInfo.style.display = '';
            }
        }

    } catch (e) {
        console.log('No scraped data available (will scrape on capture start)');
    }
}

function addCustomRange() {
    const container = document.getElementById('custom-ranges');
    const row = document.createElement('div');
    row.className = 'custom-range-row';
    row.innerHTML = `
        <input type="text" placeholder="Start (MM:SS)" class="time-input custom-start" />
        <span>to</span>
        <input type="text" placeholder="End (MM:SS)" class="time-input custom-end" />
        <button class="btn-icon" onclick="removeCustomRange(this)">&#x2715;</button>
    `;
    container.appendChild(row);
}

function removeCustomRange(btn) {
    btn.closest('.custom-range-row').remove();
}

// ===== HELPERS =====
function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return '00:00';
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
}
