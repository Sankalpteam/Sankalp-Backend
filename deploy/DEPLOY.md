# Deploying the Indoone backend

The backend runs on an AWS Lightsail instance as a systemd service, from a virtualenv in
the checkout. This note exists because until now the service definition and the environment
lived only on that machine: if the instance were lost, the setup would have been lost with
it. `deploy/indoone-backend.service` is now the copy in the repository.

## What lives where

| | |
|---|---|
| Project checkout | `/home/ubuntu/Indoone-Backend` |
| Virtualenv | `/home/ubuntu/Indoone-Backend/.venv` |
| Environment file | `/etc/indoone/indoone.env` |
| Service unit | `/etc/systemd/system/indoone-backend.service` |
| Entry point | the `run_server` module in `scripts/ops` |
| Health check | `http://localhost:8000/health` |

**The environment file is not in this repository, and must not be.** It holds the API keys,
the database paths and the Fernet key. `.env.example` lists every variable the code reads,
with placeholder values; that file *is* in the repository and is the reference for what
`/etc/indoone/indoone.env` needs.

## First-time setup on a fresh instance

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git

git clone https://github.com/indooneteam/Indoone-Backend.git /home/ubuntu/Indoone-Backend
cd /home/ubuntu/Indoone-Backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

sudo mkdir -p /etc/indoone
sudo cp .env.example /etc/indoone/indoone.env
sudo chmod 600 /etc/indoone/indoone.env
sudo nano /etc/indoone/indoone.env      # fill in the real values

sudo cp deploy/indoone-backend.service /etc/systemd/system/indoone-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now indoone-backend
```

## Deploying a new version

```bash
bash /home/ubuntu/Indoone-Backend/scripts/ops/deploy.sh
```

That is the whole flow, and it is a script rather than a one-liner because the one-liner
had two holes that each cost us an outage:

1. It never copied the unit, so a change to `deploy/indoone-backend.service` stayed in
   the repository and the box kept running the old one. When the entry point moved to
   `scripts/ops/run_server.py` the unit still named `scripts.run_server`, and the service
   restart-looped with `No module named scripts.run_server`.
2. It waited a fixed three seconds and then asked `systemctl is-active`. A unit in a
   restart loop answers `activating`, not `active`, so the check failed, the `&&` chain
   stopped, and the health check never ran — which looked like a quiet success.

The script does the five things in order: pull, install requirements, copy the unit and
reload systemd only if it changed, restart, then wait up to 45 seconds for `active` and
curl `/health`. On any failure it prints the last 40 journal lines and exits non-zero, so
a bad deploy is loud instead of silent.

Each step can be pointed elsewhere with `INDOONE_DEPLOY_*` variables if the layout ever
changes; the defaults are the table above.

## Reading the environment without printing the secrets

To see which variables are set, without their values:

```bash
sudo sed 's/=.*/=<set>/' /etc/indoone/indoone.env | sort
```

To see one variable's value when it is not a secret:

```bash
sudo grep '^INDOONE_MODEL_BACKEND=' /etc/indoone/indoone.env
```

Do not `cat` the file, and do not paste its output anywhere. It contains the keys.

## If the service will not start

```bash
systemctl status indoone-backend --no-pager
sudo journalctl -u indoone-backend -n 50 --no-pager
```

The first thing to check is that the service is reading the environment file at all: a unit
without `EnvironmentFile=` starts and then fails on the first request that needs a key.


## IMPORTANT: The one existing 2 GiB AWS server

There is **one** shared 2 GiB AWS host, currently running the live main
backend and its data services. Git branches are **not extra machines**. The
LangGraph replacement must not run simultaneously as a second deployment on
this host. Never run an additional LiteLLM proxy daemon until available RAM,
CPU and database headroom are established and the release is approved.

### Safe, read-only RAM check on the existing host

The checks below do **not** switch branches, deploy, restart or print secrets:

```bash
cd /home/ubuntu/Indoone-Backend
.venv/bin/python -m scripts.ops.memory_report --service indoone-backend
free -m
systemctl status indoone-backend --no-pager
```

The report includes host total/available RAM, process RSS, swap and (if
available) cgroup usage. It does **not** measure independent PostgreSQL or
all extra services: account for them using `ps` and `systemctl` too.
**Do not disclose environment variables or keys in a diagnostic report.**

For an *approved future single-host cutover*, add
`INDOONE_SINGLE_HOST_RAM_MB=2048` in the existing server's
private environment file. This enables a **single Uvicorn worker** guard, a
default concurrency of 32, and a maximum allowed concurrency of 64. These are
provisional safety settings, not a tested capacity guarantee. Measure p95
latency, process RSS, DB load and any OOM events before enabling production.

GitHub CI reports the isolated graph process footprint from
`python -m scripts.ops.profile_langgraph_memory`.
That **is not** production server RAM and is never a substitute for a live
single-host measurement. **No automatic branch merge, deploy or service restart.**
