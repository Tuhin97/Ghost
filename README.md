# Ghost

A self-hosted personal AI assistant running on a ThinkPad (Debian Trixie). Ghost controls your devices, monitors your digital life, and responds via voice, dashboard, or any device on your Tailscale network.

---

## What Ghost Does

- **Email** — read, send, and attach files (including from Mac via SSH)
- **Calendar** — read and create Google Calendar events
- **GitHub** — monitor repos, PRs, issues, and CI status
- **Mac control** — find files, open apps, get stats via SSH
- **Web search** — DuckDuckGo search summarised by Claude
- **Job search** — Adzuna API, CV scoring, resume tailoring, cover letters
- **Coding agent** — autonomous project builder with git integration
- **Dashboard** — live web UI with chat, status cards, and slide-out panels
- **Voice** — speak commands, hear responses (Piper TTS)
- **Morning briefing** — daily summary pushed via WebSocket

---

## Architecture

```
scripts/voice_client.py     — voice interface (mic → STT → Ghost → TTS)
orchestrator.py             — FastAPI server, JWT auth, Claude intent parsing, agent dispatch
persona.py                  — Ghost's personality and action catalogue (system prompt)
memory.py                   — ChromaDB vector memory (recall past interactions)
auth.py                     — JWT token generation and verification
config.py                   — all config loaded from .env

agents/
  email_agent.py            — Gmail read/send via Google API
  calendar_agent.py         — Google Calendar read/create
  browser_agent.py          — DuckDuckGo web search + Claude summary
  mac_agent.py              — SSH control of MacBook (find, open, stats, SFTP)
  job_agent.py              — Adzuna job search, CV scoring, resume tailoring
  coding_agent.py           — file tools and git operations
  coding_loop.py            — agentic EXPLORE → PLAN → EXECUTE → VERIFY loop
  github_agent.py           — GitHub REST API monitoring
  file_agent.py             — local file search

monitor/
  poller.py                 — background health poller (ThinkPad + Mac via SSH)
  rules.py                  — alert thresholds (CPU, RAM, disk, offline)
  mqtt_subscriber.py        — MQTT alert listener
  briefing.py               — morning briefing scheduler

dashboard/
  templates/index.html      — dashboard UI
  static/style.css          — dark theme CSS
  static/app.js             — WebSocket chat, card updates, slide-out panels

transport/
  websocket_manager.py      — shared WebSocket connection manager

data/
  ghost_memory/             — ChromaDB vector store
  api_usage.db              — SQLite API usage and cost tracking
```

---

## Setup

### 1. Clone and create venv

```bash
git clone https://github.com/your-username/Ghost.git
cd Ghost
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
JWT_SECRET=your-random-secret

# Devices
THINKPAD_TAILSCALE_IP=100.x.x.x
MAC_HOST=x.x.x.x
MAC_USER=your-mac-username
LINUX_USER=your-linux-username
SSH_KEY_PATH=~/.ssh/id_ed25519

# Device tokens (generate with auth.py)
DEVICE_TOKEN_THINKPAD=...
DEVICE_TOKEN_MACBOOK=...
DEVICE_TOKEN_SAMSUNG=...
DEVICE_TOKEN_IPHONE=...

# Google (Gmail + Calendar)
GMAIL_CREDENTIALS_PATH=./credentials.json

# GitHub
GITHUB_TOKEN=ghp_...
GITHUB_USERNAME=your-github-username
GITHUB_REPOS=repo1,repo2

# Job search
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...
CV_PATH=~/Documents/Your_CV.docx
```

### 3. Generate device tokens

```bash
venv/bin/python3 auth.py generate thinkpad
venv/bin/python3 auth.py generate macbook
venv/bin/python3 auth.py generate samsung
venv/bin/python3 auth.py generate iphone
```

Add each token to `.env`.

### 4. Google OAuth

Download `credentials.json` from Google Cloud Console (Gmail + Calendar APIs enabled). On first run, authenticate:

```bash
venv/bin/python3 -c "from agents.email_agent import read_emails; read_emails(1)"
```

Approve Gmail and Calendar access in the browser. A `token.pickle` file is saved — both agents reuse it.

### 5. SSH key for Mac

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519
ssh-copy-id your-mac-username@your-mac-ip
ssh-keyscan your-mac-ip >> ~/.ssh/known_hosts
```

---

## Running

### Production (systemd)

Ghost starts automatically on boot via systemd. Create `/etc/systemd/system/ghost.service`:

```ini
[Unit]
Description=Ghost AI Orchestrator
After=network.target tailscaled.service

[Service]
Type=simple
User=your-linux-username
WorkingDirectory=/home/your-linux-username/Ghost
ExecStart=/home/your-linux-username/Ghost/venv/bin/uvicorn orchestrator:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ghost
sudo systemctl start ghost
journalctl -u ghost -f          # live logs
```

### Development

```bash
venv/bin/uvicorn orchestrator:app --host 0.0.0.0 --port 8000 --reload
```

---

## Accessing Ghost

### Dashboard

```
http://<THINKPAD_TAILSCALE_IP>:8000/dashboard
```

Enter your device token when prompted (stored in browser localStorage).

### Voice (ThinkPad)

```bash
venv/bin/python3 scripts/voice_client.py
```

### API

```bash
curl -X POST http://localhost:8000/command \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"command": "check my emails"}'
```

---

## Network Security

### Tailscale

All devices are connected via Tailscale VPN. Ghost is only accessible on the Tailscale network — not exposed to the public internet.

Install Tailscale on each device and log in with the same account:
- Linux: `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
- Mac/iPhone/Android: download from tailscale.com or the app store

### UFW Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow in on tailscale0 to any port 8000
sudo ufw enable
```

Port 8000 is only reachable via Tailscale. SSH remains open for local access.

---

## Roadmap

| Weekend | Feature |
|---------|---------|
| 1–5     | Core: auth, memory, email, calendar, voice |
| 6       | Agents: browser, Mac, jobs, coding, GitHub |
| 7–8     | Dashboard, Tailscale, UFW, systemd |
| 9       | Desktop app (pywebview + pystray), PWA for mobile |
| 10      | Automated job pipeline: n8n self-hosted → Ghost API integration |
| 11      | PWA for mobile (Samsung + iPhone) |
| 12      | Ghost-native workflow engine (n8n-style: visual pipelines, scheduled agents) |
| 13–16   | Local LLM (Qwen 2.5 3B + LoRA fine-tuning), self-healing code |
| 17–18   | Neo4j knowledge graph, smart home, distributed inference |