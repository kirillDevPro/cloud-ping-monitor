<p align="center">
  <img src="assets/logo.png" alt="cloud-ping-monitor" width="180" height="180">
</p>

<h1 align="center">cloud-ping-monitor</h1>

<p align="center">
  <b>24/7 cloud-server uptime monitoring from Telegram — Vultr · Hetzner · AWS</b>
</p>

<p align="center">
  <a href="https://github.com/kirillDevPro/cloud-ping-monitor/actions/workflows/ci.yml"><img src="https://github.com/kirillDevPro/cloud-ping-monitor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12"></a>
  <a href="https://docs.aiogram.dev/"><img src="https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white" alt="aiogram 3.x"></a>
  <img src="https://img.shields.io/badge/i18n-EN%20%7C%20RU%20%7C%20UK-success" alt="i18n: EN | RU | UK">
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"></a>
  <a href="https://github.com/kirillDevPro/cloud-ping-monitor/stargazers"><img src="https://img.shields.io/github/stars/kirillDevPro/cloud-ping-monitor?style=social" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#supported-providers">Providers</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#running">Running</a> ·
  <a href="#development">Development</a>
</p>

A Telegram bot for monitoring cloud-server availability across **Vultr**, **Hetzner**,
and **AWS** via ICMP ping. It watches your instances around the clock, alerts you the
moment one goes down (or comes back), lets you start / stop / reboot servers from chat,
and keeps an eye on provider balances and costs.

Built on [aiogram 3.x](https://docs.aiogram.dev/), with one isolated worker process per
server, supervised background tasks, and heartbeat-based stall detection for unattended
24/7 operation.

> The bot's user-facing text is available in **English, Russian, and Ukrainian** — default
> English, with each user picking their language in Settings (or `/language`). The code,
> docstrings, and this documentation are in English.

---

## Features

- **Multi-provider monitoring** — Vultr, Hetzner Cloud, and AWS (EC2 + Lightsail) side by side.
- **Multi-account support** — several API keys per provider, auto-discovered from environment
  variables (no manual provider list to maintain).
- **Per-server ping workers** — each enabled server is monitored by its own isolated process;
  one crash never takes down the others.
- **Instant up/down alerts** — delivery-confirmed notifications with per-direction cooldowns and
  debouncing of transient provider failures (alerts only on sustained outages).
- **Power management from chat** — start, stop, reboot, and graceful (ACPI) shutdown where the
  provider supports it.
- **Multilingual UI (EN / RU / UK)** — per-user language selection persisted across restarts;
  even background alerts are rendered in each recipient's own language.
- **Balance & cost tracking** — prepaid balance for Vultr, monthly costs via AWS Cost Explorer,
  with low-balance threshold alerts.
- **Statistics** — hourly availability stats and ping-error history persisted in SQLite.
- **Self-healing** — a supervisor restarts crashed background tasks and reconciles missing
  workers; subsystem health (queue fill, live worker count, manager liveness) is monitored.

---

## Supported providers

| Provider | Instances           | Balance / cost                | Graceful shutdown |
|----------|---------------------|-------------------------------|-------------------|
| Vultr    | Cloud Compute       | Prepaid balance               | No                |
| Hetzner  | Cloud               | Postpaid (no balance API)     | Yes               |
| AWS      | EC2 + Lightsail     | Postpaid (AWS Cost Explorer)  | EC2 only          |

---

## Architecture

```
main.py (main process)
|
+-- ApplicationContainer (DI: settings -> repos -> providers -> PingManager -> bot)
|
+-- PingManager
|   +-- one worker process per enabled server
|   +-- ping_results_queue   (IPC Queue: results -> main process)
|   +-- shared_state         (DictProxy: current status sync)
|
+-- up to 5 supervised background tasks
|   +-- ping_results_processor  reads the queue, writes SQLite, sends notifications
|   +-- balance_checker         polls balances, alerts below threshold (only if a provider exposes balance)
|   +-- servers_sync_task       syncs the server list with provider APIs
|   +-- workers_health_task     monitors + reconciles worker processes
|   +-- log_cleanup_task        removes rotated logs
|
+-- supervisor + heartbeat registry
|   crash -> CRITICAL log + alert + recreate; stale heartbeat -> alert
|
+-- Telegram bot (aiogram polling)
```

Servers are identified by a **composite key** `f"{provider_alias}:{server_id}"`
(e.g. `hetzner_prod:12345`, `aws_main:us-east-1:i-0123456789abcdef`), which keeps
instances from different accounts and regions cleanly separated.

### Project layout

```
src/
+-- config/          settings (Pydantic), config.yaml, provider auto-discovery
+-- providers/       BaseProvider + Vultr / Hetzner / AWS, factory, manager, mixins
+-- monitoring/      PingManager and the per-server ping worker
+-- background_tasks/ ping processor, balance checker, sync, health, supervisor, heartbeat
+-- bot/             routers, formatters, keyboards, middlewares, notifications, utils
+-- storage/         JSON + SQLite repositories (servers, balance, statistics)
+-- models/          Server, Provider, PingResult, billing models
+-- utils/           logging, log cleanup
+-- container.py     application container / wiring
+-- exceptions.py    typed exception hierarchy
main.py              entry point
```

---

## Requirements

- **Python 3.12+**
- **Raw-socket / ICMP privileges.** ICMP ping requires elevated privileges:
  - Linux: run as root, or grant the capability once with
    `sudo setcap cap_net_raw+ep $(readlink -f $(which python3))`.
  - Windows: run the terminal / service as Administrator.
- API credentials for at least one provider (see below).

---

## Installation

```bash
git clone https://github.com/kirillDevPro/cloud-ping-monitor.git
cd cloud-ping-monitor

python -m venv venv
# Linux/macOS:
source venv/bin/activate
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Windows (Git Bash):
source venv/Scripts/activate

pip install -r requirements.txt
```

---

## Configuration

Configuration comes from two sources, with the following priority:

**environment variables (`.env`) > `src/config/config.yaml` > built-in defaults**

### 1. Secrets — `.env`

Copy the template and fill it in:

```bash
cp .env.example .env
```

```dotenv
TELEGRAM_BOT_TOKEN=123456:your-bot-token   # from @BotFather
ADMIN_IDS=123456789                         # Telegram user IDs, comma-separated

# Providers are auto-discovered from variable names:
HETZNER_PROD_API_KEY=...        # -> alias "hetzner_prod"
VULTR_MAIN_API_KEY=...           # -> alias "vultr_main"
AWS_MAIN_ACCESS_KEY_ID=...       # -> alias "aws_main" (both AWS keys required)
AWS_MAIN_SECRET_ACCESS_KEY=...
```

#### Provider auto-discovery

The bot detects providers from the **shape** of your environment variables — there is no
provider list to maintain by hand:

| Pattern                                                       | Resulting alias      |
|--------------------------------------------------------------|----------------------|
| `HETZNER_{SUFFIX}_API_KEY`                                    | `hetzner_{suffix}`   |
| `VULTR_{SUFFIX}_API_KEY`                                      | `vultr_{suffix}`     |
| `AWS_{SUFFIX}_ACCESS_KEY_ID` + `AWS_{SUFFIX}_SECRET_ACCESS_KEY` | `aws_{suffix}`     |

`{SUFFIX}` matches `[A-Z0-9_]+` (the full pattern is e.g. `^HETZNER_([A-Z0-9_]+)_API_KEY$`).

The display name is generated automatically: `Hetzner (prod)`, `Vultr`, `AWS (prod)`, etc.
The suffix `main` is hidden, so `VULTR_MAIN_API_KEY` simply shows as `Vultr`. Add a second
account by adding another suffix, e.g. `HETZNER_STAGING_API_KEY`.

### 2. Non-secrets — `src/config/config.yaml`

Ping intervals, balance threshold, sync interval, and log level:

```yaml
monitoring:
  ping_interval: 60     # seconds between pings (10-3600)
  ping_timeout: 5       # ping timeout in seconds (1-30)
  ping_attempts: 3      # attempts before marking offline (1-10)
balance:
  threshold: 2000.0     # low-balance alert threshold (USD)
  check_interval: 10800 # balance poll interval (seconds)
sync:
  servers_interval: 600 # server-list sync interval (seconds)
logging:
  level: INFO           # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

---

## Running

```bash
python main.py

# with debug logging:
LOG_LEVEL=DEBUG python main.py
```

In Telegram, open the bot and use `/start`. The main menu exposes **Monitoring**,
**Management**, and **Balance**. Only the user IDs listed in `ADMIN_IDS` are allowed in.

Runtime data lives in `data/` (server cache, balance history, SQLite statistics, callback
cache) and logs in `logs/` — both are gitignored and safe to delete to reset state.

---

## Adding a new provider

1. Create a subclass of `BaseProvider, RetryMixin, HttpClientMixin` in `src/providers/`.
2. Implement `get_servers()`, `start_server()`, `stop_server()`, `reboot_server()`,
   `shutdown_server()`, and optionally `get_balance()`.
3. Add the type to the `ProviderType` enum (`src/models/provider.py`) and register it in
   `ProviderFactory` (`src/providers/factory.py`).
4. Add the env-var pattern to `src/config/provider_discovery.py`.

---

## Development

Install the dev tools (not pinned in `requirements.txt`):

```bash
pip install ruff mypy
```

Then run the same checks CI runs:

```bash
ruff check src main.py
mypy src --ignore-missing-imports --no-strict-optional
```

Both checks run automatically in CI on every push and pull request.

---

## Contributing

Issues and pull requests are welcome. Please run `ruff` and `mypy` before opening a PR and
keep the existing conventions: docstrings and comments in English, user-facing strings in
Russian.

---

## License

Released under the [MIT License](LICENSE).
