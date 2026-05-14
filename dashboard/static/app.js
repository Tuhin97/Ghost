// ── State ─────────────────────────────────────────────────────────────────
const TOKEN = localStorage.getItem('ghost_token') || promptForToken();
let ws = null;
let statusData = {};

function promptForToken() {
    const token = prompt('Enter your Ghost API token:');
    if (token) localStorage.setItem('ghost_token', token);
    return token;
}

// ── Clock ──────────────────────────────────────────────────────────────────
function updateClock() {
    const now = new Date();
    document.getElementById('current-time').textContent = now.toLocaleTimeString('en-AU', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        timeZone: 'Australia/Adelaide'
    });
}
setInterval(updateClock, 1000);
updateClock();

// ── WebSocket chat ─────────────────────────────────────────────────────────
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);

    ws.onopen = () => {
        setStatus(true);
    };

    ws.onclose = () => {
        setStatus(false);
        // Reconnect after 3 seconds
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => {
        setStatus(false);
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'response') {
            appendMessage('Ghost', data.text, 'ghost');
        }
    };
}

function setStatus(online) {
    const dot  = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    if (online) {
        dot.classList.remove('offline');
        text.textContent = 'Online';
    } else {
        dot.classList.add('offline');
        text.textContent = 'Reconnecting...';
    }
}

// ── Chat ───────────────────────────────────────────────────────────────────
function sendMessage() {
    const input = document.getElementById('chat-input');
    const text  = input.value.trim();
    if (!text) return;

    appendMessage('You', text, 'user');
    input.value = '';

    // Send via WebSocket
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ command: text, token: TOKEN }));
    } else {
        // Fallback to HTTP if WebSocket not connected
        fetch('/command', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${TOKEN}`,
            },
            body: JSON.stringify({ command: text }),
        })
        .then(r => r.json())
        .then(data => appendMessage('Ghost', data.response, 'ghost'))
        .catch(err => appendMessage('Ghost', `Error: ${err}`, 'ghost'));
    }
}

function appendMessage(sender, text, type) {
    const messages = document.getElementById('chat-messages');

    const div = document.createElement('div');
    div.className = `message ${type}-message`;

    const senderSpan = document.createElement('span');
    senderSpan.className = 'message-sender';
    senderSpan.textContent = sender;

    const textSpan = document.createElement('span');
    textSpan.className = 'message-text';
    textSpan.textContent = text;

    div.appendChild(senderSpan);
    div.appendChild(textSpan);
    messages.appendChild(div);

    // Scroll to bottom
    messages.scrollTop = messages.scrollHeight;
}

// ── Status panels ──────────────────────────────────────────────────────────
async function fetchStatus() {
    try {
        const response = await fetch('/api/status', {
            headers: { 'Authorization': `Bearer ${TOKEN}` }
        });
        const data = await response.json();
        statusData = data;
        updateCards(data);
    } catch (err) {
        console.error('Status fetch failed:', err);
    }
}

function updateCards(data) {
    // Devices
    if (data.devices) {
        const d = data.devices;
        let preview = `ThinkPad: CPU ${d.thinkpad?.cpu ?? '—'}% | RAM ${d.thinkpad?.ram ?? '—'}%`;
        if (d.mac) preview += `\nMac: CPU ${d.mac.cpu ?? '—'}% | RAM ${d.mac.ram ?? '—'}%`;
        document.getElementById('devices-preview').textContent = preview;
        document.getElementById('devices-badge').textContent = 'Online';
        document.getElementById('devices-badge').className = 'card-badge ok';
    }

    // GitHub
    if (data.github) {
        const g = data.github;
        const alertCount = (g.open_prs || 0) + (g.open_issues || 0);
        document.getElementById('github-preview').textContent =
            `${g.open_prs ?? 0} open PRs · ${g.open_issues ?? 0} open issues`;
        const badge = document.getElementById('github-badge');
        badge.textContent = alertCount > 0 ? alertCount : '✓';
        badge.className = alertCount > 0 ? 'card-badge alert' : 'card-badge ok';
    }

    // Email
    if (data.email) {
        document.getElementById('email-preview').textContent =
            `${data.email.unread ?? 0} unread emails`;
        const badge = document.getElementById('email-badge');
        badge.textContent = data.email.unread ?? 0;
        badge.className = data.email.unread > 0 ? 'card-badge alert' : 'card-badge ok';
    }

    // Calendar
    if (data.calendar) {
        const events = data.calendar.events || [];
        document.getElementById('calendar-preview').textContent =
            events.length > 0 ? events[0].summary : 'No events today';
        document.getElementById('calendar-badge').textContent = events.length;
        document.getElementById('calendar-badge').className = 'card-badge';
    }

    // API usage
    if (data.api_usage) {
        document.getElementById('api-preview').textContent =
            `Today: $${data.api_usage.cost_today?.toFixed(4) ?? '0.0000'}`;
        document.getElementById('api-badge').textContent =
            `$${data.api_usage.cost_today?.toFixed(3) ?? '0.000'}`;
        document.getElementById('api-badge').className = 'card-badge';
    }
}

// ── Slide-out panel ────────────────────────────────────────────────────────
const PANEL_TITLES = {
    devices:  '💻 Devices',
    github:   '🐙 GitHub',
    email:    '✉️ Email',
    calendar: '📅 Calendar',
    api:      '📊 API Usage',
    jobs:     '💼 Jobs',
};

function openPanel(type) {
    document.getElementById('panel-title').textContent = PANEL_TITLES[type] || type;
    document.getElementById('panel-content').textContent = 'Loading...';
    document.getElementById('slide-panel').classList.add('active');
    document.getElementById('panel-overlay').classList.add('active');

    loadPanelContent(type);
}

function closePanel() {
    document.getElementById('slide-panel').classList.remove('active');
    document.getElementById('panel-overlay').classList.remove('active');
}

async function loadPanelContent(type) {
    const content = document.getElementById('panel-content');

    try {
        const response = await fetch(`/api/panel/${type}`, {
            headers: { 'Authorization': `Bearer ${TOKEN}` }
        });
        const data = await response.json();
        content.textContent = data.content || 'No data available.';
    } catch (err) {
        content.textContent = `Error loading ${type}: ${err}`;
    }
}

// ── Init ───────────────────────────────────────────────────────────────────
connectWebSocket();
fetchStatus();
// Refresh status every 30 seconds
setInterval(fetchStatus, 30000);