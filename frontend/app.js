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
    // Part 4 additions
    selectedSourceType: 'footballia',
    platformInfo: null,
    annotationReadyPath: null,
    lineupAvailable: false,
    // Part 5 additions
    navigatorData: null,
    batchPaused: false,
    navMode: 'person',
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

    if (viewName === 'stats') {
        loadStats();
    }

    if (viewName === 'batch') {
        connectWebSocket(); // Reuse WebSocket for batch updates
        loadBatchState();
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

        // Load platform info
        try {
            const platformRes = await fetch('/api/platform').then(r => r.json());
            state.platformInfo = platformRes.platform;

            // Show DRM note on generic web card if macOS
            if (state.platformInfo?.drm_bypass_warning) {
                const note = document.getElementById('drm-note');
                if (note) note.textContent = 'DRM limited on macOS';
            }
        } catch (e) {
            console.log('Platform info not available');
        }

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

// ===== DELETE PROJECT =====
async function deleteProject() {
    const projectName = state.project?.team_name || 'this project';

    // Step 1: first confirmation
    const confirmed = confirm(
        `Are you sure you want to delete the project "${projectName}" and ALL captured screenshots?\n\nThis will permanently remove:\n• All matches in the library\n• All captured & classified frames\n• All exports\n• The project configuration\n\nThis cannot be undone.`
    );
    if (!confirmed) return;

    // Step 2: type project name to confirm
    const typed = prompt(
        `To confirm deletion, type the project name exactly: "${projectName}"`
    );
    if (typed === null) return; // cancelled
    if (typed.trim() !== projectName) {
        alert(`Project name didn't match. Deletion cancelled.\n\nYou typed: "${typed.trim()}"\nExpected: "${projectName}"`);
        return;
    }

    // Execute deletion
    try {
        const res = await fetch('/api/project', {method: 'DELETE'});
        const data = await res.json();

        if (data.status === 'error') {
            alert(data.message || 'Failed to delete project.');
            return;
        }

        // Clear all local state
        state.project = null;
        state.matches = [];
        state.cameraTypes = [];
        state.cameraDescriptions = {};
        state.defaultTargets = {};

        // Reset setup form fields for fresh start
        const setupTeam = document.getElementById('setup-team');
        const setupSeason = document.getElementById('setup-season');
        const setupComps = document.getElementById('setup-competitions');
        const setupExcel = document.getElementById('setup-excel-path');
        if (setupTeam) setupTeam.value = '';
        if (setupSeason) setupSeason.value = '';
        if (setupComps) setupComps.value = '';
        if (setupExcel) setupExcel.value = '';

        // Show setup view
        showView('setup');
    } catch (e) {
        console.error('Delete project error:', e);
        alert('Failed to delete project. Check console for details.');
    }
}

// ===== MATCH LIBRARY =====
// ===== COLLECTIONS =====
function toggleCollectionFilter() {
    const sel = document.getElementById('lib-collection-filter');
    if (sel.style.display === 'none') {
        loadCollections();
        sel.style.display = '';
    } else {
        sel.style.display = 'none';
    }
}

async function loadCollections() {
    const collections = await fetch('/api/collections').then(r => r.json());
    const sel = document.getElementById('lib-collection-filter');
    // Keep first option ("All matches")
    while (sel.options.length > 1) sel.remove(1);
    collections.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.name} (${c.captured_count}/${c.match_count})`;
        sel.appendChild(opt);
    });
}

async function filterByCollection() {
    const colId = document.getElementById('lib-collection-filter').value;
    if (!colId) {
        renderMatchTable(state.matches);
        return;
    }
    const matches = await fetch(`/api/collections/${colId}/matches`).then(r => r.json());
    renderMatchTable(matches);
}

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
        } else {
            tr.className = 'no-url-row';
        }

        const status = m.captured
            ? `<span class="status-captured">\u2705 ${m.frame_count || ''} frames</span>`
            : hasUrl
                ? '<span class="status-ready">\uD83D\uDD17 Ready</span>'
                : '<span class="status-empty">\u2B1C No URL</span>';

        tr.innerHTML = `
            <td class="batch-select-cell">
                <input type="checkbox" class="lib-batch-cb" data-match-id="${m.id}"
                       onchange="updateLibrarySelection()" />
            </td>
            <td class="col-md">${m.md || m.match_day || ''}</td>
            <td>${m.date || ''}</td>
            <td>${m.home_away || ''}</td>
            <td class="match-opponent-cell" onclick="selectMatch(${JSON.stringify(m).replace(/"/g, '&quot;')})">${m.opponent || ''}</td>
            <td class="col-score">${m.score || ''}</td>
            <td>${status}</td>
            <td class="col-actions">
                ${m.captured ? `<button class="btn-export-small" onclick="event.stopPropagation(); exportForAnnotation(${m.id})" title="Export for Annotation" id="btn-export-annotation-${m.id}">&#128230;</button>` : ''}
                <button class="btn-delete-match" data-match-id="${m.id}" data-opponent="${(m.opponent || '').replace(/"/g, '&quot;')}" title="Delete match">&times;</button>
            </td>
        `;

        tbody.appendChild(tr);
    });
}

async function deleteMatch(matchId, opponentName) {
    const confirmed = confirm(`Delete match vs ${opponentName}?\n\nThis will also delete all captures, frames, and files for this match.`);
    if (!confirmed) return;

    try {
        const res = await fetch(`/api/matches/${matchId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'ok') {
            state.matches = state.matches.filter(m => m.id !== matchId);
            renderMatchTable(state.matches);
        } else {
            alert(data.message || 'Delete failed');
        }
    } catch (e) {
        console.error('Delete match error:', e);
        alert('Failed to delete match.');
    }
}

function updateLibrarySelection() {
    const checked = document.querySelectorAll('.lib-batch-cb:checked');
    const bar = document.getElementById('lib-batch-bar');
    const count = document.getElementById('lib-batch-count');

    if (checked.length > 0) {
        bar.style.display = '';
        count.textContent = `${checked.length} selected`;
    } else {
        bar.style.display = 'none';
    }
}

function toggleSelectAllLibrary(checkbox) {
    const allCbs = document.querySelectorAll('.lib-batch-cb');
    allCbs.forEach(cb => cb.checked = checkbox.checked);
    updateLibrarySelection();
}

function deselectAllLibrary() {
    document.querySelectorAll('.lib-batch-cb').forEach(cb => cb.checked = false);
    document.getElementById('lib-select-all').checked = false;
    updateLibrarySelection();
}

function getSelectedLibraryMatches() {
    const checked = document.querySelectorAll('.lib-batch-cb:checked');
    const ids = new Set(Array.from(checked).map(cb => parseInt(cb.dataset.matchId)));
    return state.matches.filter(m => ids.has(m.id));
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

    // Event delegation for delete buttons in match library
    const matchTbody = document.getElementById('match-tbody');
    if (matchTbody) {
        matchTbody.addEventListener('click', (e) => {
            const btn = e.target.closest('.btn-delete-match');
            if (!btn) return;
            e.stopPropagation();
            const matchId = parseInt(btn.dataset.matchId);
            const opponent = btn.dataset.opponent || '';
            deleteMatch(matchId, opponent);
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
    state.selectedSourceType = 'footballia';
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

    // Scrape match preview in background
    scrapeMatchPreview(url);
}

async function scrapeMatchPreview(url) {
    const previewEl = document.getElementById('match-preview');
    const loadingEl = document.getElementById('preview-loading');
    const contentEl = document.getElementById('preview-content');

    // Show loading state
    previewEl.style.display = '';
    loadingEl.style.display = 'flex';
    contentEl.style.display = 'none';

    try {
        const data = await fetch('/api/match/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        }).then(r => r.json());

        if (data.error) {
            loadingEl.innerHTML = `<span style="color:var(--accent-red)">&#9888; ${data.error}</span>`;
            return;
        }

        // Store scraped data for use in capture
        state.previewData = data;
        state.scrapedGoals = data.goals || [];

        // Update the title with real team names
        if (data.home_team && data.away_team) {
            const title = `${data.home_team} vs ${data.away_team}`;
            document.getElementById('config-match-title').textContent = title;
            state.selectedMatch.opponent = data.away_team;
        }
        if (data.date) {
            document.getElementById('config-match-date').textContent = data.date;
        }

        // Update goals badge for Goals Only mode
        if (state.scrapedGoals.length > 0) {
            const badge = document.getElementById('goals-badge');
            badge.textContent = `${state.scrapedGoals.length} goals`;
            badge.style.display = '';

            document.getElementById('goals-list').innerHTML = state.scrapedGoals
                .map(g => `<div class="goal-item">&#x26BD; ${g.minute}' ${g.scorer}${g.team !== 'unknown' ? ` (${g.team})` : ''}</div>`)
                .join('');
        }

        renderMatchPreview(data);
    } catch (e) {
        loadingEl.innerHTML = `<span style="color:var(--text-tertiary)">Could not load match preview</span>`;
        console.log('Preview scrape failed:', e);
    }
}

function renderMatchPreview(data) {
    const loadingEl = document.getElementById('preview-loading');
    const contentEl = document.getElementById('preview-content');

    loadingEl.style.display = 'none';
    contentEl.style.display = '';

    // Teams + result
    const teamsEl = document.getElementById('preview-teams');
    let teamsHtml = '';
    if (data.home_team && data.away_team) {
        const resultStr = data.result ? ` ${data.result.home} - ${data.result.away}` : '';
        teamsHtml = `<span class="preview-home">${data.home_team}</span>${resultStr ? `<span class="preview-score">${resultStr}</span>` : ' vs '}<span class="preview-away">${data.away_team}</span>`;
    }
    teamsEl.innerHTML = teamsHtml;

    // Meta (competition, season, venue)
    const metaEl = document.getElementById('preview-meta');
    const metaParts = [];
    if (data.competition) metaParts.push(data.competition);
    if (data.season) metaParts.push(data.season);
    if (data.stage) metaParts.push(data.stage);
    if (data.venue) metaParts.push(`&#127971; ${data.venue}`);
    metaEl.innerHTML = metaParts.join(' &middot; ');

    // Lineups
    const lineupsEl = document.getElementById('preview-lineups');
    const homeCount = (data.home_lineup || []).length;
    const awayCount = (data.away_lineup || []).length;
    if (homeCount > 0 || awayCount > 0) {
        let html = `<div class="preview-section-title">&#128203; Lineups</div>`;
        if (homeCount > 0) {
            const coach = data.home_coach ? ` &middot; Coach: ${data.home_coach.name}` : '';
            html += `<div class="preview-lineup-row"><strong>${data.home_team || 'Home'}</strong>: ${homeCount} players${coach}</div>`;
            html += `<div class="preview-players">${data.home_lineup.map(p => p.name).join(', ')}</div>`;
        }
        if (awayCount > 0) {
            const coach = data.away_coach ? ` &middot; Coach: ${data.away_coach.name}` : '';
            html += `<div class="preview-lineup-row"><strong>${data.away_team || 'Away'}</strong>: ${awayCount} players${coach}</div>`;
            html += `<div class="preview-players">${data.away_lineup.map(p => p.name).join(', ')}</div>`;
        }
        lineupsEl.innerHTML = html;
    } else {
        lineupsEl.innerHTML = '';
    }

    // Goals
    const goalsEl = document.getElementById('preview-goals');
    if (data.goals && data.goals.length > 0) {
        let html = `<div class="preview-section-title">&#x26BD; Goals</div>`;
        html += data.goals.map(g =>
            `<div class="preview-goal">${g.minute}' ${g.scorer}${g.team !== 'unknown' ? ` (${g.team})` : ''}</div>`
        ).join('');
        goalsEl.innerHTML = html;
    } else {
        goalsEl.innerHTML = '';
    }
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
    state.selectedSourceType = 'footballia';

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
    showSmartRecommendations(match);
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

        const catCount = (task.categories && task.categories.length) || task.category_count || 0;
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
        // Categories can be plain strings or objects with .value
        state.cameraTypes = (task.categories || []).map(c => typeof c === 'string' ? c : c.value);
        state.cameraDescriptions = {};
        const descMap = task.category_descriptions || {};
        state.cameraTypes.forEach(cat => {
            state.cameraDescriptions[cat] = descMap[cat] || cat;
        });

        // Set suggested targets as defaults
        state.defaultTargets = task.suggested_targets || {};

        // Render presets — API returns object, convert to array
        const presetsObj = task.presets || {};
        const presetsArr = Array.isArray(presetsObj)
            ? presetsObj
            : Object.entries(presetsObj).map(([id, p]) => ({id, ...p}));
        renderPresetBar(presetsArr);
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
    const presetsObj = taskRes.presets || {};
    const presetsArr = Array.isArray(presetsObj)
        ? presetsObj
        : Object.entries(presetsObj).map(([id, p]) => ({id, ...p}));
    renderPresetBar(presetsArr);
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

        let apiKeyHint = '';
        if (!isAvailable && provider.id === 'openai') {
            apiKeyHint = `<div class="provider-card-hint">Add <code>OPENAI_API_KEY</code> to your <code>.env</code> file</div>`;
        } else if (!isAvailable && provider.id === 'gemini') {
            apiKeyHint = `<div class="provider-card-hint">Add <code>GEMINI_API_KEY</code> to your <code>.env</code> file</div>`;
        }

        card.innerHTML = `
            <div class="provider-card-name">${provider.name}</div>
            <div class="provider-card-desc">${provider.description}</div>
            <div class="provider-card-cost">${statusStr}</div>
            ${apiKeyHint}
        `;
        container.appendChild(card);
    });
}

async function selectProvider(providerId) {
    state.selectedProvider = providerId;
    const provider = state.providers.find(p => p.id === providerId);
    if (provider) {
        state.costPerFrame = provider.cost_per_frame || 0;
    }
    renderProviderCards();
    updateSummary();

    // Validate API key for AI providers
    if (providerId !== 'manual') {
        const statusEl = document.getElementById('provider-test-status');
        statusEl.style.display = 'block';
        statusEl.className = 'provider-test-status testing';
        statusEl.innerHTML = '<span class="provider-test-spinner"></span> Validating API key...';

        try {
            const res = await fetch('/api/providers/test', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider: providerId}),
            });
            const data = await res.json();
            if (data.status === 'ok') {
                statusEl.className = 'provider-test-status success';
                statusEl.textContent = '\u2713 ' + data.message;
            } else {
                statusEl.className = 'provider-test-status error';
                statusEl.textContent = '\u2717 ' + data.message;
            }
        } catch (e) {
            statusEl.className = 'provider-test-status error';
            statusEl.textContent = '\u2717 Failed to test provider: ' + e.message;
        }

        // Auto-hide success after 4s
        setTimeout(() => {
            if (statusEl.classList.contains('success')) {
                statusEl.style.display = 'none';
            }
        }, 4000);
    } else {
        document.getElementById('provider-test-status').style.display = 'none';
    }
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
                <span class="target-name">${formatCategoryLabel(type)} <span class="ref-icon" onclick="showCategoryRef('${type}')">\u2139\uFE0F</span></span>
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

    const sourceType = state.selectedSourceType || 'footballia';

    const body = {
        match_id: match.id || null,
        footballia_url: match.footballia_url || '',
        local_filepath: match.local_filepath || '',
        generic_web_url: match.generic_web_url || '',
        targets: state.targets,
        start_time: startTime,
        match_data: match,
        source_type: sourceType,
        provider: state.selectedProvider,
        task_id: state.selectedTask || 'camera_angle',
        capture_mode: state.captureMode,
        goal_times: state.scrapedGoals,
        goal_window: parseInt(document.getElementById('goal-window')?.value || '30'),
        custom_ranges: state.captureMode === 'custom_times' ? getCustomRanges() : [],
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
        } else if (data.status === 'waiting_for_video') {
            // Generic web: show the "waiting for video" modal
            document.getElementById('waiting-video-modal').style.display = 'flex';
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
    state.apiHealthLog = [];
    state.latestFrame = null;
    document.getElementById('activity-log').innerHTML = '';
    document.getElementById('latest-thumbnail').src = '';
    document.getElementById('latest-info').textContent = 'Waiting for first frame...';
    document.getElementById('dashboard-capturing').style.display = 'block';
    document.getElementById('dashboard-completed').style.display = 'none';
    document.getElementById('pause-overlay').classList.remove('active');
    document.getElementById('api-error-overlay').classList.remove('active');

    // Reset filter stats
    document.getElementById('fs-total').textContent = '0';
    document.getElementById('fs-passed').textContent = '0';
    document.getElementById('fs-black').textContent = '0';
    document.getElementById('fs-dup').textContent = '0';
    document.getElementById('fs-scene').textContent = '0';

    // Show/hide API health section based on provider
    const apiHealthSection = document.getElementById('api-health-section');
    if (state.selectedProvider !== 'manual') {
        apiHealthSection.style.display = 'block';
        document.getElementById('api-health-dot').className = 'api-health-dot healthy';
        document.getElementById('ah-total-calls').textContent = '0';
        document.getElementById('ah-successful').textContent = '0';
        document.getElementById('ah-errors').textContent = '0';
        document.getElementById('ah-last-response').textContent = 'Waiting...';
        document.getElementById('api-health-log').innerHTML = '';
    } else {
        apiHealthSection.style.display = 'none';
    }
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
            case 'annotation_ready':
                state.annotationReadyPath = msg.path;
                addToActivityLog({
                    type: 'frame_classified',
                    classified_as: 'INFO',
                    filename: `annotation_ready/ generated: ${msg.frames} frames`,
                    video_time: 0,
                    saved: false,
                });
                break;
            case 'lineup_scraped':
                addToActivityLog({
                    type: 'frame_classified',
                    classified_as: msg.home_players > 0 ? 'INFO' : 'WARN',
                    filename: msg.message,
                    video_time: 0,
                    saved: false,
                });
                state.lineupAvailable = msg.home_players > 0;
                break;
            case 'export_progress':
                {
                    const btn = document.getElementById('btn-export-annotation');
                    if (btn) btn.innerHTML = `&#9203; ${msg.message || `Frame ${msg.current}/${msg.total}`}`;
                }
                break;
            case 'export_complete':
                // Handled by the fetch response in exportForAnnotation()
                break;
            case 'api_health':
                updateApiHealthUI(msg);
                break;
            case 'api_auto_stop':
                showApiErrorOverlay(msg);
                break;
            case 'error':
                showError(msg.message);
                break;
            default:
                // Handle batch messages
                if (msg.type && msg.type.startsWith('batch_')) {
                    handleBatchMessage(msg);
                }
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

    // Also log to API health log if this is a classified frame with API info
    if (msg.type === 'frame_classified' && state.selectedProvider !== 'manual') {
        addToApiHealthLog(msg);
    }
}

function renderActivityLog() {
    const container = document.getElementById('activity-log');
    container.innerHTML = '';

    state.activityLog.forEach(entry => {
        const div = document.createElement('div');
        const isApiError = entry.api_error || entry.parse_error;
        div.className = 'log-entry';
        if (entry.anomaly) div.className += ' log-anomaly';
        if (isApiError) div.className += ' log-api-error';

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
        } else if (isApiError) {
            const errorTag = entry.api_error ? 'API ERR' : 'PARSE ERR';
            div.innerHTML = `
                <span class="log-time">${timeStr}</span>
                <span class="log-file">${videoTimeStr}</span>
                <span class="log-type log-error-badge">${errorTag}</span>
                <span class="log-conf">${(entry.reasoning || '').substring(0, 60)}</span>
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

// ===== API HEALTH UI =====
function updateApiHealthUI(msg) {
    const dot = document.getElementById('api-health-dot');
    dot.className = 'api-health-dot ' + (msg.status || 'healthy');

    document.getElementById('ah-total-calls').textContent = msg.total_calls || 0;
    document.getElementById('ah-successful').textContent = msg.total_successful || 0;
    document.getElementById('ah-errors').textContent = msg.total_errors || 0;
    document.getElementById('ah-last-response').textContent = msg.last_response || 'Waiting...';
}

function addToApiHealthLog(msg) {
    if (!state.apiHealthLog) state.apiHealthLog = [];
    state.apiHealthLog.unshift(msg);
    if (state.apiHealthLog.length > 20) state.apiHealthLog.pop();
    renderApiHealthLog();
}

function renderApiHealthLog() {
    const container = document.getElementById('api-health-log');
    if (!container) return;
    container.innerHTML = '';

    (state.apiHealthLog || []).forEach(entry => {
        const div = document.createElement('div');
        const isError = entry.api_error || entry.parse_error;
        div.className = 'api-log-entry' + (isError ? ' api-log-error' : '');

        const timeStr = formatTime(entry.video_time || 0);
        const reasoning = entry.reasoning || '';
        const classified = entry.classified_as || '';
        const conf = entry.confidence ? `${Math.round(entry.confidence * 100)}%` : '';

        if (isError) {
            div.innerHTML = `
                <span class="api-log-time">${timeStr}</span>
                <span class="api-log-status api-log-err-badge">ERROR</span>
                <span class="api-log-detail">${reasoning.substring(0, 100)}</span>
            `;
        } else {
            div.innerHTML = `
                <span class="api-log-time">${timeStr}</span>
                <span class="api-log-status api-log-ok-badge">${classified}</span>
                <span class="api-log-conf">${conf}</span>
                <span class="api-log-detail">${reasoning.substring(0, 80)}</span>
            `;
        }
        container.appendChild(div);
    });
}

function showApiErrorOverlay(msg) {
    const overlay = document.getElementById('api-error-overlay');
    overlay.classList.add('active');

    document.getElementById('api-error-detail').textContent =
        msg.message || 'Multiple consecutive API errors. Capture has been stopped.';

    const stats = document.getElementById('api-error-stats');
    stats.innerHTML = `
        <div>API errors: <span class="mono">${msg.total_api_errors || 0}</span></div>
        <div>Parse errors: <span class="mono">${msg.total_parse_errors || 0}</span></div>
    `;
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

    if (e.key === '?') {
        toggleShortcuts();
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

async function batchAcceptHighConfidence() {
    const threshold = 0.85;
    const captureId = state.galleryCaptureId;
    if (!captureId) return;

    const unreviewed = state.galleryFrames.filter(f => !f.is_reviewed);
    const eligible = unreviewed.filter(f => f.confidence >= threshold);

    if (eligible.length === 0) {
        alert('No unreviewed frames above 85% confidence.');
        return;
    }

    if (!confirm(`Accept ${eligible.length} frames with \u226585% confidence as correctly classified?`)) {
        return;
    }

    const res = await fetch('/api/gallery/batch-accept', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            capture_id: captureId,
            threshold: threshold,
        }),
    });
    const data = await res.json();

    // Update local state
    eligible.forEach(f => f.is_reviewed = true);

    // Update Gallery counter
    updateGalleryProgress();

    alert(`Accepted ${data.accepted} frames. ${state.galleryFrames.filter(f => !f.is_reviewed).length} remaining to review.`);
}

async function batchAcceptAll() {
    const captureId = state.galleryCaptureId;
    if (!captureId) return;

    const unreviewed = state.galleryFrames.filter(f => !f.is_reviewed).length;
    if (unreviewed === 0) return;

    if (!confirm(`Accept all ${unreviewed} remaining frames as correctly classified?`)) return;

    const res = await fetch('/api/gallery/batch-accept', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            capture_id: captureId,
            threshold: 0.0,  // Accept everything
        }),
    });
    const data = await res.json();

    state.galleryFrames.forEach(f => f.is_reviewed = true);
    updateGalleryProgress();
    alert(`Accepted ${data.accepted} frames. Review complete.`);
}

function updateGalleryProgress() {
    const total = state.galleryFrames.length;
    const reviewed = state.galleryFrames.filter(f => f.is_reviewed).length;
    const el = document.getElementById('gallery-progress-text');
    if (el) el.textContent = `${reviewed} / ${total} reviewed`;
    const fill = document.getElementById('gallery-progress-fill');
    if (fill) fill.style.width = total > 0 ? `${(reviewed / total) * 100}%` : '0%';
}

function toggleShortcuts() {
    const card = document.getElementById('shortcuts-card');
    card.style.display = card.style.display === 'none' ? '' : 'none';
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

    // Export for Annotation button (match_id from state)
    if (state.selectedMatch && state.selectedMatch.id) {
        const lineupStatus = state.lineupAvailable
            ? '<span style="color:#A8E6A1;">&#10003; Lineup available</span>'
            : '<span style="color:#999;">&#10007; No lineup data</span>';
        html += `
            <button class="btn-secondary" id="btn-export-annotation" onclick="exportForAnnotation(${state.selectedMatch.id})">
                &#128230; Export for Annotation
            </button>
            <span class="lineup-status" style="font-size:0.85em; margin-left:8px;">${lineupStatus}</span>
        `;
    }

    html += `
        <button class="btn-text" onclick="showView('home')">
            Done — Back to Home
        </button>
    `;

    // Add annotation_ready info if available
    if (state.annotationReadyPath) {
        html += `
            <div class="annotation-ready-info">
                <span class="annotation-ready-icon">&#128230;</span>
                <div>
                    <strong>Annotation Tool ready</strong>
                    <p class="annotation-ready-path">${state.annotationReadyPath}</p>
                    <p class="hint">Open this folder in the Football Annotation Tool to start annotating with pre-filled metadata and rosters.</p>
                </div>
            </div>
        `;
    }

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

// ===== LOCAL FILE HANDLERS =====

function showLocalFileDialog() {
    document.getElementById('local-file-modal').style.display = 'flex';
    document.getElementById('local-filepath').focus();
}

function hideLocalFile() {
    document.getElementById('local-file-modal').style.display = 'none';
}

async function openLocalFile() {
    const filepath = document.getElementById('local-filepath').value.trim();
    if (!filepath) {
        alert('Please enter a file path');
        return;
    }

    const opponent = document.getElementById('local-opponent').value.trim() || 'Local Match';
    const date = document.getElementById('local-date').value.trim() || '';

    state.selectedMatch = {
        opponent: opponent,
        date: date,
        home_away: '',
        md: 0,
        score: '',
        footballia_url: '',
        local_filepath: filepath,
    };
    state.selectedSourceType = 'local_file';
    state.quickCaptureMode = false;

    hideLocalFile();
    showConfigView(state.selectedMatch);
}

// ===== GENERIC WEB HANDLERS =====

function showGenericWebDialog() {
    document.getElementById('generic-web-modal').style.display = 'flex';

    // Show platform warning if macOS
    if (state.platformInfo?.drm_bypass_warning) {
        document.getElementById('platform-warning').style.display = '';
        document.getElementById('platform-warning').innerHTML =
            '<p class="warning">macOS detected — DRM-protected sites (ESPN+, DAZN, etc.) ' +
            'may produce black screenshots. Non-DRM sites (YouTube, Footballia) work fine. ' +
            'If you get black frames, download the video and use Local File mode instead.</p>';
    }

    document.getElementById('generic-web-url').focus();
}

function hideGenericWeb() {
    document.getElementById('generic-web-modal').style.display = 'none';
}

async function startGenericWeb() {
    const url = document.getElementById('generic-web-url').value.trim();
    if (!url) {
        alert('Please enter a URL');
        return;
    }

    state.selectedMatch = {
        opponent: document.getElementById('generic-opponent').value.trim() || 'Web Match',
        date: '',
        home_away: '',
        md: 0,
        score: '',
        generic_web_url: url,
    };
    state.selectedSourceType = 'generic_web';

    hideGenericWeb();

    // Go to config view to set task/provider/targets, then start will open browser
    showConfigView(state.selectedMatch);
}

async function confirmVideoPlaying() {
    document.getElementById('waiting-video-modal').style.display = 'none';

    try {
        const res = await fetch('/api/capture/confirm-video', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
        });
        const data = await res.json();
        if (data.status === 'started') {
            state.activeCaptureId = data.capture_id;
            setupDashboard(state.selectedMatch);
            showView('dashboard');
        } else {
            alert(data.message || 'Failed to find video');
        }
    } catch (e) {
        alert('Error confirming video: ' + e.message);
    }
}

function cancelGenericWeb() {
    document.getElementById('waiting-video-modal').style.display = 'none';
    showView('home');
}

// ===== CUSTOM TIME RANGES =====

function addCustomRange() {
    const container = document.getElementById('custom-ranges');
    const row = document.createElement('div');
    row.className = 'custom-range-row';
    row.innerHTML = `
        <input type="text" placeholder="Start (MM:SS)" class="time-input custom-start" />
        <span>to</span>
        <input type="text" placeholder="End (MM:SS)" class="time-input custom-end" />
        <button class="btn-icon" onclick="removeCustomRange(this)">✕</button>
    `;
    container.appendChild(row);
}

function removeCustomRange(btn) {
    const row = btn.closest('.custom-range-row');
    const container = document.getElementById('custom-ranges');
    if (container.querySelectorAll('.custom-range-row').length > 1) {
        row.remove();
    } else {
        row.querySelectorAll('input').forEach(inp => inp.value = '');
    }
}

function getCustomRanges() {
    const rows = document.querySelectorAll('.custom-range-row');
    const ranges = [];
    rows.forEach(row => {
        const start = row.querySelector('.custom-start')?.value.trim();
        const end = row.querySelector('.custom-end')?.value.trim();
        if (start && end) {
            ranges.push({ start, end });
        }
    });
    return ranges;
}

// ===== CUSTOM PROMPT EDITOR =====

async function testCustomPrompt() {
    const prompt = document.getElementById('custom-prompt-text').value;
    const classField = document.getElementById('custom-class-field').value;
    if (!prompt) { alert('Enter a prompt first'); return; }

    document.getElementById('custom-test-result').style.display = '';
    document.getElementById('custom-test-result').textContent = 'Testing...';

    // Use a sample frame from the most recent capture (or a placeholder)
    const res = await fetch('/api/tasks/test-prompt', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt, classification_field: classField}),
    });
    const data = await res.json();
    document.getElementById('custom-test-result').textContent =
        `Result: ${JSON.stringify(data, null, 2)}`;
}

async function saveCustomTask() {
    const name = document.getElementById('custom-task-name').value.trim();
    const prompt = document.getElementById('custom-prompt-text').value.trim();
    const classField = document.getElementById('custom-class-field').value.trim();
    const catsStr = document.getElementById('custom-categories').value.trim();

    if (!name || !prompt || !classField || !catsStr) {
        alert('Fill all fields');
        return;
    }

    const categories = catsStr.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
    const taskId = name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');

    const task = {
        id: taskId,
        name: name,
        description: `Custom task: ${name}`,
        classification_field: classField,
        categories: categories,
        prompt: prompt,
        presets: [],
    };

    const res = await fetch('/api/tasks/custom', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(task),
    });
    const data = await res.json();
    if (data.error) {
        alert(`Error: ${data.error}`);
    } else {
        alert('Custom task saved! It will appear in the task selector.');
        // Refresh tasks
        state.tasks = await fetch('/api/tasks').then(r => r.json());
        renderTaskCards();
    }
}

// ===== COACH/PLAYER/TEAM NAVIGATOR =====

function setNavMode(mode) {
    state.navMode = mode;
    document.getElementById('nav-mode-person').classList.toggle('active', mode === 'person');
    document.getElementById('nav-mode-team').classList.toggle('active', mode === 'team');

    const input = document.getElementById('nav-url');
    input.placeholder = mode === 'person'
        ? 'https://footballia.eu/players/giovanni-trapattoni'
        : 'https://footballia.eu/teams/atletico-de-madrid';
}

async function scrapeNavigator() {
    const url = document.getElementById('nav-url').value.trim();
    if (!url) return;

    document.getElementById('nav-loading').style.display = '';
    document.getElementById('nav-results').style.display = 'none';
    document.getElementById('nav-loading-text').textContent = 'Loading page...';
    document.getElementById('nav-progress-bar').style.animation = '';
    document.getElementById('nav-progress-bar').style.width = '';

    // Listen for status updates via WebSocket
    connectWebSocket();
    const statusHandler = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'status' && msg.message) {
            document.getElementById('nav-loading-text').textContent = msg.message;
        }
    };
    if (state.ws) state.ws.addEventListener('message', statusHandler);

    const endpoint = state.navMode === 'team'
        ? '/api/navigator/scrape-team'
        : '/api/navigator/scrape';

    const res = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url}),
    });
    const data = await res.json();
    state.navigatorData = data;

    // Clean up status listener
    if (state.ws) state.ws.removeEventListener('message', statusHandler);

    // Complete the progress bar
    const bar = document.getElementById('nav-progress-bar');
    bar.style.animation = 'none';
    bar.style.transform = 'none';
    bar.style.width = '100%';

    document.getElementById('nav-loading').style.display = 'none';

    if (!data.scrape_success) {
        alert('Failed to scrape page.');
        return;
    }

    document.getElementById('nav-results').style.display = '';
    document.getElementById('nav-person-name').textContent = data.name;

    if (state.navMode === 'team') {
        const seasonCount = (data.seasons || []).length;
        document.getElementById('nav-person-info').textContent =
            `${data.total_matches} matches \u00b7 ${seasonCount} season${seasonCount !== 1 ? 's' : ''}`;
    } else {
        const clubCount = (data.clubs || []).length;
        document.getElementById('nav-person-info').textContent =
            `${data.type} \u00b7 ${data.total_matches} matches across ${clubCount} club${clubCount !== 1 ? 's' : ''}`;
    }

    if (state.navMode === 'team') {
        renderTeamTree(data);
    } else {
        renderNavigatorTree(data);
    }

    populateNavigatorFilters(data);
}

async function scrapePersonPage() {
    // Legacy — redirects to scrapeNavigator
    state.navMode = 'person';
    scrapeNavigator();
}

function populateSelect(id, options) {
    const sel = document.getElementById(id);
    const firstOption = sel.options[0].textContent;
    sel.innerHTML = `<option value="">${firstOption}</option>`;
    options.forEach(opt => {
        const o = document.createElement('option');
        o.value = opt;
        o.textContent = opt;
        sel.appendChild(o);
    });
}

function renderNavigatorTree(data) {
    const tree = document.getElementById('nav-tree');
    tree.innerHTML = '';

    for (const club of data.clubs) {
        const clubDiv = document.createElement('div');
        clubDiv.className = 'nav-club';

        const header = document.createElement('div');
        header.className = 'nav-club-header';
        header.innerHTML = `<strong>\u{1F3DF} ${club.name}</strong> <span>(${club.match_count || '?'} matches, ${club.role})</span>`;
        header.onclick = () => clubDiv.classList.toggle('collapsed');
        clubDiv.appendChild(header);

        for (const season of club.seasons) {
            const seasonDiv = document.createElement('div');
            seasonDiv.className = 'nav-season';
            seasonDiv.innerHTML = `<div class="nav-season-header">${season.season} ${season.competition}</div>`;

            for (const match of season.matches) {
                const stageText = match.stage ? ` - ${match.stage}` : '';
                const matchDiv = document.createElement('div');
                matchDiv.className = 'nav-match';
                matchDiv.innerHTML = `
                    <label>
                        <input type="checkbox" class="nav-match-cb" data-url="${match.full_url || ''}"
                               data-home="${match.home_team}" data-away="${match.away_team}"
                               data-date="${match.date}" data-season="${season.season}"
                               data-comp="${season.competition}" data-stage="${match.stage}"
                               onchange="updateNavSelection()" />
                        ${match.date ? match.date + ' \u00b7 ' : ''}${match.home_team} ${match.away_team}${stageText}
                    </label>
                `;
                seasonDiv.appendChild(matchDiv);
            }
            clubDiv.appendChild(seasonDiv);
        }
        tree.appendChild(clubDiv);
    }
}

function renderTeamTree(data) {
    const tree = document.getElementById('nav-tree');
    tree.innerHTML = '';

    for (const season of data.seasons) {
        const seasonDiv = document.createElement('div');
        seasonDiv.className = 'nav-club';  // Reuse club styling

        const header = document.createElement('div');
        header.className = 'nav-club-header';
        const matchCount = season.competitions.reduce((sum, c) => sum + c.matches.length, 0);
        header.innerHTML = `<strong>\uD83D\uDCC5 ${season.season}</strong> <span>(${matchCount} matches)</span>`;
        header.onclick = () => seasonDiv.classList.toggle('collapsed');
        seasonDiv.appendChild(header);

        for (const comp of season.competitions) {
            const compDiv = document.createElement('div');
            compDiv.className = 'nav-season';
            compDiv.innerHTML = `<div class="nav-season-header">\uD83C\uDFC6 ${comp.name} <span class="nav-comp-count">(${comp.matches.length})</span></div>`;

            for (const match of comp.matches) {
                const stageText = match.stage ? ` - ${match.stage}` : '';

                const matchDiv = document.createElement('div');
                matchDiv.className = 'nav-match';
                matchDiv.innerHTML = `
                    <label>
                        <input type="checkbox" class="nav-match-cb"
                               data-url="${match.full_url || ''}"
                               data-home="${match.home_team}" data-away="${match.away_team}"
                               data-date="${match.date}" data-season="${season.season}"
                               data-comp="${comp.name}" data-ha="${match.home_away}"
                               data-score="${match.score || ''}"
                               onchange="updateNavSelection()" />
                        ${match.date ? match.date + ' \u00b7 ' : ''}${match.home_team} ${match.away_team}${stageText}
                    </label>
                `;
                compDiv.appendChild(matchDiv);
            }
            seasonDiv.appendChild(compDiv);
        }
        tree.appendChild(seasonDiv);
    }
}

function populateNavigatorFilters(data) {
    if (state.navMode === 'team') {
        const seasons = data.seasons.map(s => s.season);
        const comps = [...new Set(data.seasons.flatMap(s => s.competitions.map(c => c.name)))];
        populateSelect('nav-filter-season', seasons);
        populateSelect('nav-filter-comp', comps);
        // Hide club filter for team mode
        document.getElementById('nav-filter-club').style.display = 'none';
    } else {
        const clubs = [...new Set(data.clubs.map(c => c.name))];
        const seasons = [...new Set(data.clubs.flatMap(c => c.seasons.map(s => s.season)))];
        const comps = [...new Set(data.clubs.flatMap(c => c.seasons.map(s => s.competition)).filter(Boolean))];
        populateSelect('nav-filter-club', clubs);
        populateSelect('nav-filter-season', seasons.sort());
        populateSelect('nav-filter-comp', comps.sort());
        document.getElementById('nav-filter-club').style.display = '';
    }
}

function filterNavigatorResults() {
    const club = document.getElementById('nav-filter-club').value;
    const season = document.getElementById('nav-filter-season').value;
    const comp = document.getElementById('nav-filter-comp').value;

    if (!state.navigatorData) return;

    // 1. Show/hide individual matches based on season + competition filters
    const allMatches = document.querySelectorAll('.nav-match');
    allMatches.forEach(m => {
        const cb = m.querySelector('.nav-match-cb');
        if (!cb) return;
        let show = true;
        if (season && cb.dataset.season !== season) show = false;
        if (comp && cb.dataset.comp !== comp) show = false;
        m.style.display = show ? '' : 'none';
    });

    // 2. Hide competition containers (.nav-season) with no visible matches
    document.querySelectorAll('.nav-season').forEach(compDiv => {
        const visibleMatches = compDiv.querySelectorAll('.nav-match:not([style*="display: none"])');
        compDiv.style.display = visibleMatches.length > 0 ? '' : 'none';
    });

    // 3. Hide season/club containers (.nav-club) with no visible children
    document.querySelectorAll('.nav-club').forEach(c => {
        const header = c.querySelector('.nav-club-header strong');
        if (!header) return;

        // For person mode: also filter by club name
        if (club && !header.textContent.includes(club)) {
            c.style.display = 'none';
            return;
        }

        // Hide if all child competition sections are hidden
        const visibleChildren = c.querySelectorAll('.nav-season:not([style*="display: none"])');
        c.style.display = visibleChildren.length > 0 ? '' : 'none';
    });
}

function updateNavSelection() {
    const checked = document.querySelectorAll('.nav-match-cb:checked');
    document.getElementById('nav-selected-count').textContent = `${checked.length} selected`;
}

function selectAllClub(clubName) {
    document.querySelectorAll('.nav-match-cb').forEach(cb => {
        const clubHeader = cb.closest('.nav-club')?.querySelector('.nav-club-header strong');
        if (clubHeader && clubHeader.textContent.includes(clubName)) {
            cb.checked = true;
        }
    });
    updateNavSelection();
}

function deselectAll() {
    document.querySelectorAll('.nav-match-cb').forEach(cb => cb.checked = false);
    updateNavSelection();
}

async function addSelectedToLibrary() {
    const checked = document.querySelectorAll('.nav-match-cb:checked');
    if (checked.length === 0) {
        alert('Select at least one match.');
        return;
    }
    const teamName = state.navigatorData?.name || '';

    const matches = Array.from(checked).map(cb => ({
        home_team: cb.dataset.home,
        away_team: cb.dataset.away,
        date: cb.dataset.date,
        full_url: cb.dataset.url,
        season: cb.dataset.season,
        competition: cb.dataset.comp,
        home_away: cb.dataset.ha || '',
        score: cb.dataset.score || '',
        team_name: teamName,
    }));

    const res = await fetch('/api/navigator/add-to-library', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({matches}),
    });
    const data = await res.json();

    // Auto-create a collection from navigator
    if (data.added > 0 && state.navigatorData?.name) {
        await fetch('/api/collections', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name: state.navigatorData.name,
                description: `${data.added} matches from ${state.navMode} page`,
                match_ids: data.match_ids || [],
            }),
        });
    }

    alert(`Added ${data.added} matches to your library.`);
}

function batchCaptureSelected() {
    const checked = document.querySelectorAll('.nav-match-cb:checked');
    if (checked.length === 0) {
        alert('Select at least one match.');
        return;
    }
    const matches = Array.from(checked).map(cb => ({
        footballia_url: cb.dataset.url,
        opponent: cb.dataset.away || cb.dataset.home,
        date: cb.dataset.date,
        season: cb.dataset.season,
        competition: cb.dataset.comp,
        home_away: '',
        match_day: 0,
    }));
    state.batchMatches = matches;
    _showBatchConfigModal(matches.length);
}

function openBatchConfig() {
    const matches = getSelectedLibraryMatches();
    if (matches.length === 0) {
        alert('Select at least one match');
        return;
    }
    state.batchMatches = matches;
    _showBatchConfigModal(matches.length);
}

function _showBatchConfigModal(count) {
    document.getElementById('batch-config-summary').textContent =
        `${count} match${count > 1 ? 'es' : ''} selected`;

    // Populate task dropdown
    const taskSel = document.getElementById('batch-task');
    taskSel.innerHTML = state.tasks.map(t =>
        `<option value="${t.id}">${t.name}</option>`
    ).join('');

    onBatchTaskChange();
    document.getElementById('batch-config-modal').style.display = 'flex';
}

function closeBatchConfig() {
    document.getElementById('batch-config-modal').style.display = 'none';
}

function onBatchTaskChange() {
    const taskId = document.getElementById('batch-task').value;
    const task = state.tasks.find(t => t.id === taskId);
    if (!task) return;

    // Populate preset dropdown
    const presetSel = document.getElementById('batch-preset');
    const presetEntries = Object.entries(task.presets || {});
    presetSel.innerHTML = presetEntries.map(([pid, p]) =>
        `<option value="${pid}">${p.name || pid}</option>`
    ).join('');
    if (presetEntries.length > 0) {
        onBatchPresetChange();
    }

    // Populate target inputs
    const targetsDiv = document.getElementById('batch-targets');
    const cats = task.categories || [];
    targetsDiv.innerHTML = cats.map(cat =>
        `<div class="target-item">
            <label>${cat.replace(/_/g, ' ')}</label>
            <input type="number" class="batch-target-input" data-cat="${cat}"
                   value="${state.defaultTargets[cat] || 10}" min="0" />
        </div>`
    ).join('');
}

function onBatchPresetChange() {
    const taskId = document.getElementById('batch-task').value;
    const presetId = document.getElementById('batch-preset').value;

    fetch(`/api/tasks/${taskId}/presets/${presetId}`)
        .then(r => r.json())
        .then(data => {
            const targets = data.targets || data;
            if (targets) {
                document.querySelectorAll('.batch-target-input').forEach(input => {
                    const cat = input.dataset.cat;
                    if (cat in targets) {
                        input.value = targets[cat];
                    }
                });
            }
        });
}

async function startBatch() {
    const matches = state.batchMatches;
    if (!matches || matches.length === 0) return;

    const taskId = document.getElementById('batch-task').value;
    const provider = document.getElementById('batch-provider').value;
    const delay = parseInt(document.getElementById('batch-delay-select').value);

    // Collect targets
    const targets = {};
    document.querySelectorAll('.batch-target-input').forEach(input => {
        targets[input.dataset.cat] = parseInt(input.value || '0');
    });

    closeBatchConfig();

    // Create the batch
    const createRes = await fetch('/api/batch/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            matches,
            targets,
            provider,
            task_id: taskId,
            capture_mode: 'full_match',
            delay_between: delay,
        }),
    });
    const createData = await createRes.json();
    state.activeBatchId = createData.batch_id;

    // Start it
    await fetch('/api/batch/start', { method: 'POST' });

    showView('batch');
}

// ===== STATISTICS =====

async function loadStats() {
    const stats = await fetch('/api/stats').then(r => r.json());

    document.getElementById('stats-matches').textContent =
        `${stats.matches.captured} / ${stats.matches.total}`;
    document.getElementById('stats-frames').textContent =
        stats.frames.total.toLocaleString();
    document.getElementById('stats-cost').textContent =
        `$${stats.cost.estimated_total.toFixed(2)}`;
    document.getElementById('stats-accuracy').textContent =
        stats.frames.reviewed > 0 ? `${stats.frames.accuracy_pct}%` : '\u2014';

    // Distribution bars
    const distEl = document.getElementById('stats-distribution');
    const maxCount = Math.max(...Object.values(stats.distribution), 1);
    distEl.innerHTML = Object.entries(stats.distribution)
        .sort((a, b) => b[1] - a[1])
        .map(([type, count]) => {
            const pct = (count / maxCount * 100).toFixed(0);
            const total_pct = stats.frames.total > 0 ? (count / stats.frames.total * 100).toFixed(1) : '0';
            return `
                <div class="dist-row">
                    <span class="dist-label">${type.replace(/_/g, ' ')}</span>
                    <div class="dist-bar-bg">
                        <div class="dist-bar-fill" style="width:${pct}%"></div>
                    </div>
                    <span class="dist-count">${count} (${total_pct}%)</span>
                </div>
            `;
        }).join('');

    // Annotation feedback
    if (stats.annotation_feedback) {
        const fb = stats.annotation_feedback;
        const feedbackDiv = document.createElement('div');
        feedbackDiv.className = 'stats-section';
        feedbackDiv.innerHTML = `
            <h3>Annotation Tool Feedback</h3>
            <p>${fb.total_frames_reviewed} frames reviewed in Annotation Tool.
               ${fb.total_corrections} corrections (${fb.correction_rate_pct}% correction rate).</p>
            ${fb.recommendation ? `<div class="recommendation warning"><p>${fb.recommendation}</p></div>` : ''}
            ${fb.top_patterns.length > 0 ? `
                <div class="correction-patterns">
                    ${fb.top_patterns.map(p =>
                        `<div class="correction-row">${p.from} → ${p.to}: ${p.count}×</div>`
                    ).join('')}
                </div>
            ` : ''}
        `;
        document.getElementById('view-stats').appendChild(feedbackDiv);
    }

    // Per-match table
    const tbody = document.getElementById('stats-match-tbody');
    tbody.innerHTML = stats.per_match.map(m => {
        const status = m.captured
            ? `<span class="status-captured">\u2705 ${m.frame_count}</span>`
            : '<span class="status-pending">\u2B1C</span>';
        return `<tr>
            <td>${m.match_day || ''}</td>
            <td>${m.opponent || ''} ${m.home_away ? `(${m.home_away})` : ''}</td>
            <td>${m.frame_count || ''}</td>
            <td>${status}</td>
        </tr>`;
    }).join('');
}

async function exportDataset(format) {
    const statusEl = document.getElementById('export-status');
    statusEl.style.display = '';
    statusEl.textContent = `Exporting as ${format}...`;

    const res = await fetch('/api/export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({format}),
    });
    const data = await res.json();

    if (data.error) {
        statusEl.textContent = `Error: ${data.error}`;
    } else {
        statusEl.textContent = `\u2713 Exported to: ${data.path}`;
    }
}

// ===== EXPORT FOR ANNOTATION =====

async function exportForAnnotation(matchId) {
    const btn = document.getElementById('btn-export-annotation') || document.getElementById(`btn-export-annotation-${matchId}`);
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '&#9203; Exporting...';
    }

    try {
        const res = await fetch('/api/export-annotation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({match_id: matchId}),
        });
        const data = await res.json();

        if (data.status === 'success') {
            if (btn) {
                btn.innerHTML = `&#9989; Exported ${data.frames_exported} frames`;
                btn.style.background = '#2D5A27';
                btn.style.color = '#A8E6A1';
            }
            // Show path info
            const actionsDiv = document.getElementById('completion-actions');
            if (actionsDiv) {
                const info = document.createElement('div');
                info.className = 'annotation-ready-info';
                info.innerHTML = `
                    <span class="annotation-ready-icon">&#128230;</span>
                    <div>
                        <strong>Annotation bundle ready</strong>
                        <p class="annotation-ready-path">${data.export_path}</p>
                        <p class="hint">${data.frames_exported} frames exported, ${data.frames_skipped} skipped. Generated: ${data.files_generated.join(', ')}</p>
                        <p class="hint">Squad data: ${data.lineup_available ? '&#10003; Auto-populated from lineup' : '&#10007; Not available (squad.json is a placeholder)'}</p>
                    </div>
                `;
                actionsDiv.appendChild(info);
            }
        } else {
            if (btn) {
                btn.innerHTML = `&#10060; ${data.message || 'Export failed'}`;
                btn.disabled = false;
            }
        }
    } catch (err) {
        if (btn) {
            btn.innerHTML = `&#10060; Export failed`;
            btn.disabled = false;
        }
    }
}

// ===== BATCH DASHBOARD =====

async function loadBatchState() {
    const res = await fetch('/api/batch/state').then(r => r.json());
    if (res.status === 'none') return;

    renderBatchQueue(res.items || []);
    updateBatchProgress(res);
}

function renderBatchQueue(items) {
    const queue = document.getElementById('batch-queue');
    queue.innerHTML = items.map((item, i) => {
        let icon = '\u23F3';
        let cls = 'pending';
        let detail = 'pending';
        if (item.status === 'capturing') { icon = '\uD83D\uDD04'; cls = 'active'; detail = `${item.frames_captured} frames...`; }
        if (item.status === 'completed') { icon = '\u2705'; cls = 'completed'; detail = `${item.frames_captured} frames`; }
        if (item.status === 'failed') { icon = '\u274C'; cls = 'failed'; detail = item.error_message || 'failed'; }
        if (item.status === 'skipped') { icon = '\u23ED'; cls = 'skipped'; detail = 'skipped'; }
        return `<div class="batch-item ${cls}">${icon} ${item.match_label} \u2014 ${detail}</div>`;
    }).join('');
}

function updateBatchProgress(batchState) {
    if (!batchState || !batchState.items) return;
    const total = batchState.total || batchState.items.length;
    const completed = batchState.items.filter(i => i.status === 'completed' || i.status === 'failed' || i.status === 'skipped').length;
    const pct = total > 0 ? (completed / total * 100).toFixed(0) : 0;

    document.getElementById('batch-progress-text').textContent = `${completed} / ${total} matches`;
    document.getElementById('batch-progress-fill').style.width = `${pct}%`;
}

function toggleBatchPause() {
    state.batchPaused = !state.batchPaused;
    const btn = document.getElementById('batch-pause-btn');
    if (state.batchPaused) {
        fetch('/api/batch/pause', {method: 'POST'});
        btn.textContent = '\u25B6 Resume';
    } else {
        fetch('/api/batch/resume', {method: 'POST'});
        btn.textContent = '\u23F8 Pause';
    }
}

async function cancelBatch() {
    if (!confirm('Cancel the batch capture? Completed matches will be kept.')) return;
    await fetch('/api/batch/cancel', {method: 'POST'});
    loadBatchState();
}

// Handle batch WebSocket messages
function handleBatchMessage(msg) {
    switch (msg.type) {
        case 'batch_started':
            state.batchTotal = msg.total;
            state.batchCompleted = 0;
            document.getElementById('batch-progress-text').textContent =
                `0 / ${msg.total} matches`;
            document.getElementById('batch-progress-fill').style.width = '0%';
            break;

        case 'batch_item_started':
            state.batchCurrentIndex = msg.index;
            refreshBatchQueue();
            addToBatchLog(`\uD83D\uDD04 Starting: ${msg.match_label} (${msg.index + 1}/${msg.total})`);
            break;

        case 'batch_item_completed':
            state.batchCompleted = (state.batchCompleted || 0) + 1;
            const pct = Math.round((state.batchCompleted / state.batchTotal) * 100);
            document.getElementById('batch-progress-text').textContent =
                `${state.batchCompleted} / ${state.batchTotal} matches`;
            document.getElementById('batch-progress-fill').style.width = pct + '%';
            refreshBatchQueue();
            const icon = msg.status === 'completed' ? '\u2705' : '\u274C';
            addToBatchLog(
                `${icon} ${msg.match_label} \u2014 ${msg.frames_captured} frames (${msg.status})`
            );
            break;

        case 'batch_delay':
            document.getElementById('batch-delay').style.display = '';
            let countdown = msg.seconds;
            document.getElementById('batch-delay-seconds').textContent = countdown;
            state._batchDelayInterval = setInterval(() => {
                countdown--;
                document.getElementById('batch-delay-seconds').textContent = countdown;
                if (countdown <= 0) {
                    clearInterval(state._batchDelayInterval);
                    document.getElementById('batch-delay').style.display = 'none';
                }
            }, 1000);
            break;

        case 'batch_completed':
            document.getElementById('batch-progress-text').textContent =
                `${msg.completed} / ${msg.total} matches completed`;
            document.getElementById('batch-progress-fill').style.width = '100%';
            addToBatchLog(
                `\n\uD83D\uDCCA Batch complete: ${msg.completed} succeeded, ${msg.failed} failed. ` +
                `${msg.total_frames} total frames. $${msg.total_cost.toFixed(3)} API cost.`
            );
            document.getElementById('batch-pause-btn').disabled = true;
            break;
    }
}

async function refreshBatchQueue() {
    const res = await fetch('/api/batch/state');
    const data = await res.json();
    if (data.items) {
        renderBatchQueue(data.items);
    }
}

function addToBatchLog(message) {
    const log = document.getElementById('batch-activity-log');
    if (!log) return;
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.textContent = message;
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

// ===== SMART RECOMMENDATIONS =====

function showSmartRecommendations(matchData) {
    /**
     * Show era-based prompt recommendations in the Config view.
     * Inserted after the task selector section.
     */
    const container = document.getElementById('smart-recommendations');
    if (!container) return;

    const year = extractMatchYear(matchData);
    if (!year) {
        container.style.display = 'none';
        return;
    }

    let html = '';

    if (year < 1990) {
        html = `
            <div class="recommendation warning">
                <strong>⚠️ Pre-1990 broadcast detected</strong>
                <p>Older broadcasts typically use only 2-3 camera angles (wide and medium).
                Consider using "Scene Type Classification" instead of "Camera Angle" for
                better results. AERIAL and BEHIND_GOAL were extremely rare before the 1990s —
                lower those targets to 1-2.</p>
            </div>
        `;
    } else if (year < 2005) {
        html = `
            <div class="recommendation info">
                <strong>💡 Standard-definition era (${year})</strong>
                <p>Broadcasts from this era have moderate camera diversity. BEHIND_GOAL
                and AERIAL angles appear occasionally. The default "Camera Angle" task is
                suitable, but consider slightly lower targets for rare angles.</p>
            </div>
        `;
    } else if (year >= 2015) {
        html = `
            <div class="recommendation success">
                <strong>✓ Modern HD broadcast (${year})</strong>
                <p>Rich camera diversity expected. Full "Camera Angle Classification" is ideal.
                Consider also adding "Formation Detection" as a secondary analysis if you
                need tactical data.</p>
            </div>
        `;
    }
    // Years 2005-2014: no recommendation (standard broadcast, no special advice)

    if (html) {
        container.innerHTML = html;
        container.style.display = '';
    } else {
        container.style.display = 'none';
    }
}

function extractMatchYear(matchData) {
    /**
     * Try to extract a year from match data (date, season, or folder name).
     */
    const date = matchData?.date || '';
    const season = matchData?.season || '';

    // Try date field: "2024-08-19" or "September 30, 1981"
    let m = date.match(/(\d{4})/);
    if (m) return parseInt(m[1]);

    // Try season field: "1981-1982" or "2024-2025"
    m = season.match(/(\d{4})/);
    if (m) return parseInt(m[1]);

    return null;
}

// ===== CATEGORY REFERENCE =====
function showCategoryRef(category) {
    /**
     * Show a reference popup for a camera angle category.
     * Uses task template data for description + auto-populated sample image if available.
     */
    const task = state.tasks.find(t => t.id === state.selectedTaskId) || state.tasks[0];
    const refs = task?.category_references || {};
    const ref = refs[category];

    if (!ref) return;

    document.getElementById('ref-popup-title').textContent = category.replace(/_/g, ' ');
    document.getElementById('ref-popup-desc').textContent = ref.description;
    document.getElementById('ref-popup-cues').textContent = `Visual cues: ${ref.visual_cues}`;
    document.getElementById('ref-popup-freq').textContent = ref.frequency;

    // Check if we have a sample image from past captures
    const imageDiv = document.getElementById('ref-popup-image');
    const samplePath = state.sampleImages?.[category];
    if (samplePath) {
        imageDiv.innerHTML = `<img src="/recordings/${samplePath}" alt="${category}" />`;
    } else {
        imageDiv.innerHTML = `<div class="ref-placeholder">${getCategoryEmoji(category)}</div>`;
    }

    document.getElementById('category-ref-popup').style.display = 'flex';
}

function closeCategoryRef() {
    document.getElementById('category-ref-popup').style.display = 'none';
}

function getCategoryEmoji(cat) {
    const map = {
        'WIDE_CENTER': '\uD83D\uDCFA', 'WIDE_LEFT': '\u2B05\uFE0F\uD83D\uDCFA', 'WIDE_RIGHT': '\uD83D\uDCFA\u27A1\uFE0F',
        'MEDIUM': '\uD83C\uDFA5', 'CLOSEUP': '\uD83D\uDD0D', 'BEHIND_GOAL': '\uD83E\uDD45',
        'AERIAL': '\uD83E\uDD85', 'OTHER': '\uD83D\uDCF7',
    };
    return map[cat] || '\uD83D\uDCF7';
}

async function loadCategorySamples() {
    try {
        state.sampleImages = await fetch('/api/category-samples').then(r => r.json());
    } catch {
        state.sampleImages = {};
    }
}
// Call this after first page load
loadCategorySamples();

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
