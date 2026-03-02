// ===== STATE =====
const state = {
    currentView: 'select',
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
};

// ===== VIEW SWITCHING =====
function showView(viewName) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(`view-${viewName}`).classList.add('active');
    state.currentView = viewName;

    if (viewName === 'dashboard') {
        connectWebSocket();
    }
}

// ===== INITIALIZATION =====
async function init() {
    try {
        const [matchesRes, configRes] = await Promise.all([
            fetch('/api/matches').then(r => r.json()),
            fetch('/api/config').then(r => r.json()),
        ]);

        state.matches = matchesRes;
        state.cameraTypes = configRes.camera_types || [];
        state.cameraDescriptions = configRes.camera_descriptions || {};
        state.defaultTargets = configRes.defaults?.targets || {};

        renderMatchTable(state.matches);

        // Check if a capture is in progress
        const statusRes = await fetch('/api/capture/status').then(r => r.json());
        if (statusRes.status === 'capturing' || statusRes.status === 'paused') {
            showView('dashboard');
        }
    } catch (e) {
        console.error('Init error:', e);
    }
}

// ===== VIEW 1: MATCH TABLE =====
function renderMatchTable(matches) {
    const tbody = document.getElementById('match-tbody');
    tbody.innerHTML = '';

    matches.forEach(m => {
        const tr = document.createElement('tr');
        const hasUrl = m.footballia_url && m.footballia_url.length > 0;

        if (hasUrl) {
            tr.className = 'clickable';
            tr.onclick = () => selectMatch(m);
        } else {
            tr.className = 'disabled';
            tr.title = 'Add URL in Excel to enable';
        }

        const resultClass = m.result ? `result-${m.result.charAt(0).toUpperCase()}` : '';
        const urlIcon = hasUrl
            ? '<span class="url-yes">&#10003;</span>'
            : '<span class="url-no">&#10007;</span>';

        tr.innerHTML = `
            <td class="col-md">${m.md}</td>
            <td>${m.date}</td>
            <td>${m.home_away}</td>
            <td>${m.opponent}</td>
            <td class="col-score">${m.score}</td>
            <td class="${resultClass}">${m.result}</td>
            <td style="text-align:center">${urlIcon}</td>
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
                m.opponent.toLowerCase().includes(q) ||
                m.date.includes(q) ||
                String(m.md).includes(q)
            );
            renderMatchTable(filtered);
        });
    }

    // Radio buttons for start position
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

// ===== VIEW 2: CONFIGURATION =====
function selectMatch(match) {
    state.selectedMatch = match;

    const ha = match.home_away === 'H' ? '(H)' : '(A)';
    document.getElementById('config-match-title').textContent =
        `MD${match.md} \u00b7 ${match.opponent} ${ha} \u00b7 ${match.score}`;
    document.getElementById('config-match-date').textContent = match.date;

    renderTargetInputs();
    updateSummary();
    showView('config');
}

function renderTargetInputs() {
    const container = document.getElementById('target-inputs');
    container.innerHTML = '';

    state.cameraTypes.forEach(type => {
        const defaultVal = state.defaultTargets[type] || 0;
        state.targets[type] = defaultVal;

        const desc = state.cameraDescriptions[type] || '';

        const row = document.createElement('div');
        row.className = 'target-row';
        row.innerHTML = `
            <div class="target-info">
                <span class="target-label">${formatCameraLabel(type)}</span>
                <span class="target-desc">${desc}</span>
            </div>
            <input type="number" class="target-input" data-type="${type}"
                   value="${defaultVal}" min="0" max="500" />
        `;
        container.appendChild(row);
    });

    // Bind input events
    container.querySelectorAll('.target-input').forEach(input => {
        input.addEventListener('input', () => {
            state.targets[input.dataset.type] = parseInt(input.value) || 0;
            updateSummary();
        });
    });
}

function formatCameraLabel(type) {
    return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
               .replace('Closeup', 'Close-up')
               .replace('Behind Goal', 'Behind Goal');
}

function updateSummary() {
    const total = Object.values(state.targets).reduce((a, b) => a + b, 0);
    const cost = total * state.costPerFrame;

    document.getElementById('total-count').textContent = total;
    document.getElementById('est-cost').textContent = `$${cost.toFixed(3)}`;
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
        match_id: `MD${String(match.md).padStart(2, '0')}`,
        footballia_url: match.footballia_url,
        targets: state.targets,
        start_time: startTime,
        match_data: match,
    };

    try {
        const res = await fetch('/api/capture/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await res.json();

        if (data.status === 'started') {
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

// ===== VIEW 3: DASHBOARD =====
function setupDashboard(match) {
    const ha = match.home_away === 'H' ? '(H)' : '(A)';
    const title = `MD${match.md} \u00b7 ${match.opponent} ${ha} \u00b7 ${match.score}`;

    document.getElementById('dash-match-title').textContent = title;
    document.getElementById('completed-match-title').textContent =
        `${title} \u00b7 ${match.date}`;

    // Setup progress table
    const tbody = document.getElementById('progress-tbody');
    tbody.innerHTML = '';

    state.cameraTypes.forEach(type => {
        const target = state.targets[type] || 0;
        if (target <= 0) return;

        const tr = document.createElement('tr');
        tr.id = `prog-row-${type}`;
        tr.innerHTML = `
            <td>${formatCameraLabel(type)}</td>
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
            case 'status':
                handleStatusChange(msg);
                break;
            case 'completed':
                showCompletionSummary(msg.summary);
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
        // Fallback to REST
        fetch(`/api/capture/${action}`, {method: 'POST'});
    }
}

// ===== UI UPDATE FUNCTIONS =====
function updateProgressUI(msg) {
    document.getElementById('video-time').textContent = formatTime(msg.video_time);
    document.getElementById('video-duration').textContent = formatTime(msg.video_duration);
    document.getElementById('video-part').textContent =
        `Part ${msg.video_part} of ${msg.total_parts}`;

    // Update each camera type row
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

    // Update totals
    document.getElementById('total-progress').textContent =
        `${msg.total_captured} / ${msg.total_target}`;
    document.getElementById('api-cost').textContent =
        `$${(msg.api_cost || 0).toFixed(4)}`;

    // Overall bar
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

        const now = new Date();
        const timeStr = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
        const videoTimeStr = formatTime(entry.video_time || 0);

        if (entry.skipped) {
            div.innerHTML = `
                <span class="log-time">${timeStr}</span>
                <span class="log-file">${videoTimeStr}</span>
                <span class="log-type">${entry.camera_type || ''}</span>
                <span class="log-skip">\u2192skip</span>
            `;
        } else {
            const conf = entry.confidence ? `${Math.round(entry.confidence * 100)}%` : '';
            div.innerHTML = `
                <span class="log-time">${timeStr}</span>
                <span class="log-file">${entry.filename || videoTimeStr}</span>
                <span class="log-type">${entry.camera_type || ''}</span>
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

        document.getElementById('latest-info').textContent =
            `${formatTime(msg.video_time)} \u00b7 ${msg.camera_type} \u00b7 ${Math.round(msg.confidence * 100)}% confidence`;
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

    // Fill completed table
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
                <td>${formatCameraLabel(type)}</td>
                <td class="col-num">${data.target}</td>
                <td class="col-num">${data.captured}</td>
                <td class="${statusClass}">${statusText}</td>
            `;
            tbody.appendChild(tr);
        }
    }

    // Totals row
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
    document.getElementById('completed-output-dir').textContent =
        summary.output_dir || '';
}

function showError(message) {
    // Show in activity log
    addToActivityLog({
        camera_type: 'ERROR',
        video_time: 0,
        filename: message,
        skipped: false,
        confidence: 0,
    });
    console.error('Pipeline error:', message);
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
