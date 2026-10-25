# DA3408 A4 · Application Monitoring with Prometheus & Grafana

A CPU-only image-captioning app (Streamlit + `Salesforce/blip-image-captioning-base`) instrumented with
`prometheus_client`. Prometheus scrapes it together with `node_exporter`, and a Grafana dashboard correlates
application behaviour with host CPU, RAM and disk usage.

```
Browser ──► Streamlit app :8501 ──► /metrics :8000 ─┐
                                                     ├──► Prometheus :9090 ──► Grafana :3000
            node_exporter :9100 (CPU/RAM/disk) ─────┘        (+ alert rules)
```

## Repository layout

| Path | What it is |
|---|---|
| `app.py` | Streamlit app: Single image and Bulk (ZIP) captioning |
| `metrics.py` | All Prometheus metrics (Counter, Gauge, Histogram, Summary, Info) + `/metrics` server on :8000 |
| `monitoring/prometheus.yml` | Scrape config: app (:8000), node_exporter (:9100), Prometheus itself (:9090), 5 s interval |
| `monitoring/alert_rules.yml` | 10 alert rules (app down, p95 latency, error ratio, CPU/RAM/disk, stuck queue…) |
| `monitoring/setup.sh` | Downloads node_exporter + Prometheus for your CPU architecture into `monitoring/bin` |
| `monitoring/start.sh`, `stop.sh` | Start / stop node_exporter and Prometheus in the background |
| `grafana/dashboard.json` | Grafana dashboard export (37 panels, 6 rows) |
| `grafana/build_dashboard.py` | Script that generates `dashboard.json` and `PROMQL.md` |
| `PROMQL.md` | Every query on the dashboard with its logic worked out, plus AI attribution |

## Ports

| Service | URL |
|---|---|
| Streamlit app | http://localhost:8501 |
| App metrics | http://localhost:8000/metrics |
| node_exporter | http://localhost:9100/metrics |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (first login admin / admin) |

---

## Setup (Linux)

Tested on Ubuntu (x86_64 and arm64, including a VM).

### 1. App

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch
pip install -r requirements.txt

streamlit run app.py                      # terminal 1, keep it running
```

Open http://localhost:8501 once so the model loads (first run downloads ~1 GB).

### 2. node_exporter + Prometheus

```bash
bash monitoring/setup.sh                  # one-time download (auto-detects amd64 / arm64)
bash monitoring/start.sh                  # starts both in the background
```

Check http://localhost:9090: `caption_app`, `node` and `prometheus` should all be **UP**.
Stop them later with `bash monitoring/stop.sh`.

### 3. Grafana (Ubuntu / Debian)

```bash
sudo apt-get install -y apt-transport-https software-properties-common wget
sudo mkdir -p /etc/apt/keyrings
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt-get update && sudo apt-get install -y grafana
sudo systemctl daemon-reload && sudo systemctl enable --now grafana-server
# no systemd?  use:  sudo service grafana-server start
```

### 4. Connect Grafana and import the dashboard

1. Open http://localhost:3000 and log in (admin / admin, then set a new password).
2. **Connections → Data sources → Add data source → Prometheus**
   - Prometheus server URL: `http://localhost:9090`
   - **Scrape interval: `5s`** (under Interval behaviour). This makes `$__rate_interval` = 20 s, so graphs react quickly.
   - **Save & test** → "Successfully queried the Prometheus API".
3. **Dashboards → New → Import → Upload dashboard JSON file** → pick `grafana/dashboard.json` → **Import**.
4. The dashboard opens with a 5 s refresh and the last 15 minutes shown.

**Running the app in a VM but browsing from another machine?** Start Streamlit with
`streamlit run app.py --server.address 0.0.0.0 --server.headless true`, then use `http://<vm-ip>:8501`,
`:9090` and `:3000` from the other machine (`hostname -I` in the VM gives the IP).

---

## Quick test and recording the video

1. Make a test ZIP: `zip -j test.zip ~/Pictures/*.jpg` (30-50 images works well; add one renamed `.txt` to see the
   error path).
2. In Grafana: refresh **5s**, time range **Last 15 minutes**.
3. Start recording (OBS, or Ctrl+Alt+Shift+R on Ubuntu).
4. ~10 s idle → caption one image in Single mode → upload the ZIP in Bulk mode and click **Caption all** →
   scroll through the rows while it runs. Purple shaded bands on the graphs mark the bulk batch, and the
   "CPU vs bulk throughput" and "Memory vs queue" panels show the correlation.
5. Re-export after any dashboard edits: **Share → Export → Save to file** → replace `grafana/dashboard.json`.

## Dashboard rows

1. **Service health**: app / node_exporter UP-DOWN, model loaded, uptime, active requests, images captioned,
   error ratio, firing alerts
2. **Throughput & latency**: images/s by mode, inference p50/p95/p99, Summary mean request time, per-stage
   breakdown, latency heatmap, errors by stage, requests by outcome
3. **App ↔ system correlation**: CPU / RAM / disk gauges, load vs inferences in flight, CPU vs bulk throughput,
   memory vs queue, disk & upload I/O, threads
4. **Workload**: queue and active requests, image size and resolution, caption length, batch size and duration,
   ZIP uploads and skipped files
5. **Clients**: per-session table (session ID, IP, browser), traffic by browser, distinct sessions
6. **Runtime & monitoring**: build info, model parameters and load time, total uploaded, scrape health, tokens/s

The 7 plotting commandments are applied on every panel: colour-blind-safe Okabe-Ito palette, line styles and
point markers that survive greyscale printing, named y-axes, units and linear/log₁₀ scale stated, legends for
multi-series plots, "Title · subtitle" titles, and a story in each panel's description (ⓘ).

## AI attribution

`monitoring/prometheus.yml`, `monitoring/alert_rules.yml`, `grafana/dashboard.json` and `PROMQL.md` were generated
with Claude (Anthropic). Prompt: *"give the required yml and prom ql files according to the given logic"* and *"give the required json file for the grafana dashboard"*. The logic of every query is written out in `PROMQL.md`. The remaining files were given as the context to these prompts.
