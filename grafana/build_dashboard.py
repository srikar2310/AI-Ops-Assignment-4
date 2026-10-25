"""
Generate grafana/dashboard.json and PROMQL.md from one panel specification.

    python grafana/build_dashboard.py

Every panel is built through helpers that enforce the 7 plotting commandments:
  1. colour-blind-safe palette  -> Okabe-Ito colours, fixed per series
  2. markers survive print/PDF  -> each series in a panel also differs by line
                                   style (solid / dash / dot) and shows points
  3. named axes                 -> axisLabel on every y-axis (x is always time)
  4. scale + units explicit     -> unit set on every field; "linear"/"log10"
                                   written into the axis label
  5. legend for >1 variable     -> table legend (mean / max / last) on graphs
  6. title (+ subtitle)         -> "Title · subtitle"
  7. story                      -> panel description (hover the (i) icon)

AI attribution: generated with Claude (Anthropic). Prompt used:
  "give the remaining required files for node exporter, prometheus.yaml and
   grafana dashboard"
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "grafana" / "dashboard.json"
OUT_MD = ROOT / "PROMQL.md"

PROMPT = ('"give the remaining required files for node exporter, prometheus.yaml '
          'and grafana dashboard"')

DS = {"type": "prometheus", "uid": "${datasource}"}
RI = "$__rate_interval"

# Okabe-Ito palette (safe for protanopia, deuteranopia, tritanopia)
C = {
    "blue": "#0072B2", "orange": "#E69F00", "vermillion": "#D55E00",
    "sky": "#56B4E9", "green": "#009E73", "purple": "#CC79A7",
    "yellow": "#F0E442", "grey": "#999999", "black": "#000000",
}
SOLID = {"fill": "solid"}
DASH = {"fill": "dash", "dash": [10, 10]}
DOT = {"fill": "dot", "dash": [0, 10]}

panels: list[dict] = []
docs: list[dict] = []
_next_id = [1]
_cursor = {"x": 0, "y": 0, "h": 0}


def _newline():
    _cursor["y"] += _cursor["h"]
    _cursor["x"] = 0
    _cursor["h"] = 0


def _place(w, h):
    if _cursor["x"] + w > 24:
        _newline()
    pos = {"x": _cursor["x"], "y": _cursor["y"], "w": w, "h": h}
    _cursor["x"] += w
    _cursor["h"] = max(_cursor["h"], h)
    return pos


def _id():
    i = _next_id[0]
    _next_id[0] += 1
    return i


def row(title):
    _newline()
    panels.append({"type": "row", "title": title, "id": _id(), "collapsed": False,
                   "gridPos": {"x": 0, "y": _cursor["y"], "w": 24, "h": 1}, "panels": []})
    _cursor["y"] += 1
    docs.append({"row": title})


def tgt(expr, legend="", ref="A", instant=False, fmt="time_series", interval=""):
    t = {"refId": ref, "datasource": DS, "expr": expr, "legendFormat": legend,
         "range": not instant, "instant": instant, "format": fmt, "editorMode": "code"}
    if interval:
        t["interval"] = interval
    return t


def ser(name, color, style=SOLID, right=False, unit=None, axis_label=None, width=None, extra=()):
    """Override for one named series: fixed colour + line style (+ optional right axis)."""
    props = [
        {"id": "color", "value": {"mode": "fixed", "fixedColor": C[color]}},
        {"id": "custom.lineStyle", "value": style},
    ]
    if right:
        props.append({"id": "custom.axisPlacement", "value": "right"})
    if unit:
        props.append({"id": "unit", "value": unit})
    if axis_label:
        props.append({"id": "custom.axisLabel", "value": axis_label})
    if width:
        props.append({"id": "custom.lineWidth", "value": width})
    props.extend(extra)
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def _story(desc, y_label, right_label=None):
    axes = f"\n\nAxes: X = wall-clock time. Y (left) = {y_label}."
    if right_label:
        axes += f" Y (right) = {right_label}."
    return desc + axes


def _doc(title, targets, explain):
    docs.append({"title": title, "queries": [(t["legendFormat"] or t["refId"], t["expr"]) for t in targets],
                 "explain": explain})


def timeseries(title, desc, explain, targets, unit, y_label, *, overrides=(), log=False,
               w=12, h=8, right_label=None, interp="linear", minimum=None, maximum=None, draw="line"):
    custom = {
        "drawStyle": draw, "lineWidth": 2, "lineInterpolation": interp,
        "fillOpacity": 0 if draw == "line" else 70, "gradientMode": "none",
        "showPoints": "always" if draw == "line" else "never", "pointSize": 4,
        "spanNulls": False, "insertNulls": False,
        "axisLabel": y_label, "axisPlacement": "left", "axisBorderShow": True,
        "axisCenteredZero": False, "axisColorMode": "text", "axisGridShow": True,
        "scaleDistribution": {"type": "log", "log": 10} if log else {"type": "linear"},
        "stacking": {"mode": "none", "group": "A"}, "barAlignment": 0,
        "lineStyle": SOLID, "thresholdsStyle": {"mode": "off"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "fixed", "fixedColor": C["blue"]},
                "thresholds": {"mode": "absolute", "steps": [{"color": C["blue"], "value": None}]},
                "mappings": []}
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    p = {
        "type": "timeseries", "id": _id(), "title": title, "datasource": DS,
        "description": _story(desc, y_label, right_label),
        "gridPos": _place(w, h), "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": list(overrides)},
        "options": {
            "legend": {"showLegend": True, "displayMode": "table", "placement": "bottom",
                       "calcs": ["mean", "max", "lastNotNull"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }
    panels.append(p)
    _doc(title, targets, explain)
    return p


def stat(title, desc, explain, targets, *, unit="none", mappings=(), steps=None, w=3, h=4,
         decimals=None, color_mode="background"):
    steps = steps or [{"color": C["blue"], "value": None}]
    defaults = {"unit": unit, "mappings": list(mappings), "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": steps}}
    if decimals is not None:
        defaults["decimals"] = decimals
    p = {
        "type": "stat", "id": _id(), "title": title, "datasource": DS, "description": desc,
        "gridPos": _place(w, h), "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "none", "justifyMode": "center",
                    "textMode": "value", "orientation": "auto", "wideLayout": True,
                    "showPercentChange": False},
    }
    panels.append(p)
    _doc(title, targets, explain)
    return p


def gauge(title, desc, explain, targets, *, unit="percent", w=4, h=6, warn=70, crit=90):
    p = {
        "type": "gauge", "id": _id(), "title": title, "datasource": DS, "description": desc,
        "gridPos": _place(w, h), "targets": targets,
        "fieldConfig": {"defaults": {
            "unit": unit, "min": 0, "max": 100, "decimals": 1, "color": {"mode": "thresholds"},
            "thresholds": {"mode": "absolute", "steps": [
                {"color": C["blue"], "value": None}, {"color": C["orange"], "value": warn},
                {"color": C["vermillion"], "value": crit}]}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "showThresholdLabels": True, "showThresholdMarkers": True, "orientation": "auto",
                    "minVizHeight": 75, "minVizWidth": 75, "sizing": "auto"},
    }
    panels.append(p)
    _doc(title, targets, explain)
    return p


def bargauge(title, desc, explain, targets, *, unit="short", w=12, h=8):
    p = {
        "type": "bargauge", "id": _id(), "title": title, "datasource": DS, "description": desc,
        "gridPos": _place(w, h), "targets": targets,
        "fieldConfig": {"defaults": {
            "unit": unit, "min": 0, "decimals": 0, "color": {"mode": "fixed", "fixedColor": C["blue"]},
            "thresholds": {"mode": "absolute", "steps": [{"color": C["blue"], "value": None}]}},
            "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "orientation": "horizontal", "displayMode": "basic", "showUnfilled": True,
                    "valueMode": "text", "namePlacement": "left", "sizing": "auto",
                    "minVizHeight": 16, "minVizWidth": 8, "maxVizHeight": 300, "legend": {"showLegend": False}},
    }
    panels.append(p)
    _doc(title, targets, explain)
    return p


def table(title, desc, explain, targets, *, w=12, h=8, rename=None, hide=(), sort_by=None):
    exclude = {k: True for k in ("Time", "__name__", "job", "instance", *hide)}
    transforms = [{"id": "organize", "options": {"excludeByName": exclude, "renameByName": rename or {}}}]
    if sort_by:
        transforms.append({"id": "sortBy", "options": {"sort": [{"field": sort_by, "desc": True}]}})
    p = {
        "type": "table", "id": _id(), "title": title, "datasource": DS, "description": desc,
        "gridPos": _place(w, h), "targets": targets, "transformations": transforms,
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                                "filterable": True}}, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
    }
    panels.append(p)
    _doc(title, targets, explain)
    return p


def heatmap(title, desc, explain, targets, *, y_label, w=8, h=8):
    p = {
        "type": "heatmap", "id": _id(), "title": title, "datasource": DS, "description": desc,
        "gridPos": _place(w, h), "targets": targets,
        "fieldConfig": {"defaults": {"custom": {"scaleDistribution": {"type": "linear"},
                                                "hideFrom": {"legend": False, "tooltip": False, "viz": False}}},
                        "overrides": []},
        "options": {
            "calculate": False, "cellGap": 1, "showValue": "never",
            "color": {"mode": "scheme", "scheme": "Cividis", "steps": 64, "exponent": 0.5,
                      "fill": C["orange"], "reverse": False},
            "yAxis": {"axisPlacement": "left", "axisLabel": y_label, "unit": "s", "reverse": False},
            "rowsFrame": {"layout": "le"}, "filterValues": {"le": 1e-9},
            "legend": {"show": True}, "tooltip": {"mode": "single", "yHistogram": True, "showColorScale": True},
            "exemplars": {"color": C["purple"]},
        },
    }
    panels.append(p)
    _doc(title, targets, explain)
    return p


def text(title, content, *, w=24, h=4):
    panels.append({"type": "text", "id": _id(), "title": title, "gridPos": _place(w, h),
                   "options": {"mode": "markdown", "content": content}})


UP_DOWN = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": C["vermillion"], "index": 1},
    "1": {"text": "UP", "color": C["blue"], "index": 0}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "NO DATA", "color": C["grey"], "index": 2}}}]
UP_STEPS = [{"color": C["vermillion"], "value": None}, {"color": C["blue"], "value": 1}]


text("How to read this dashboard", (
    "**CPU image-captioning service: health → performance → system correlation → workload → clients → runtime.** "
    "Read top to bottom: if a stat in row 1 is not blue, drill into the rows below.  \n"
    "Colours use the colour-blind-safe Okabe-Ito palette, and within a graph each series also has its own line style "
    "(solid / dashed / dotted) with point markers, so plots stay readable in greyscale print or PDF. "
    "Purple shaded regions on every graph mark when a **bulk ZIP batch** was running, so app spikes can be matched "
    "against CPU / RAM / disk. Hover the ⓘ on any panel for its story. "
    "Every graph states its y-axis units and scale (linear or log₁₀); the x-axis is always wall-clock time."
), h=4)

row("1 · Service health (now)")
stat("App status · /metrics scrape",
     "UP when Prometheus' latest scrape of the Streamlit app's /metrics endpoint (port 8000) succeeded. "
     "DOWN means the app process is not running or not reachable.",
     "`up` is a synthetic series Prometheus writes for every target: 1 if the scrape succeeded, 0 if it failed.",
     [tgt('up{job="caption_app"}', "app", instant=True)], mappings=UP_DOWN, steps=UP_STEPS)
stat("Node exporter · host metrics",
     "UP when node_exporter (port 9100) is being scraped. Without it the system-resource rows below go blank.",
     "Same `up` series as above, for the node job.",
     [tgt('up{job="node"}', "node", instant=True)], mappings=UP_DOWN, steps=UP_STEPS)
stat("Model · BLIP in memory",
     "LOADED once the first session has loaded the captioning model into RAM (it is cached for all later sessions).",
     "Gauge set to 1 at the end of `load_model()`; initialised to 0 at process start.",
     [tgt("caption_model_loaded", "model", instant=True)],
     mappings=[{"type": "value", "options": {
         "0": {"text": "NOT LOADED", "color": C["orange"], "index": 1},
         "1": {"text": "LOADED", "color": C["blue"], "index": 0}}}],
     steps=[{"color": C["orange"], "value": None}, {"color": C["blue"], "value": 1}])
stat("App uptime · since process start",
     "Time since the Streamlit process started. A sudden drop to ~0 means the app restarted (counters reset).",
     "`process_start_time_seconds` (default Linux process collector) is a Unix timestamp; `time() - start` = age in seconds.",
     [tgt('time() - process_start_time_seconds{job="caption_app"}', "uptime", instant=True)], unit="s", decimals=0)
stat("Active requests · being processed now",
     "Requests currently inside the app (a Single caption click or a whole Bulk batch). Orange from 2, vermillion from 4: "
     "concurrent work on a CPU-only model queues up and inflates latency.",
     "Sum over modes of the `caption_active_requests` gauge, which is +1 on request start and -1 in a `finally` block.",
     [tgt("sum(caption_active_requests)", "active", instant=True)], decimals=0,
     steps=[{"color": C["blue"], "value": None}, {"color": C["orange"], "value": 2},
            {"color": C["vermillion"], "value": 4}])
stat("Images captioned · in selected time range",
     "How many images were captioned successfully (both modes) within the dashboard's time range.",
     "`increase(counter[$__range])` = counter value at the end of the range minus at the start "
     "(reset-aware, extrapolated to the range edges), summed over label sets and rounded.",
     [tgt('round(sum(increase(caption_images_processed_total{status="ok"}[$__range])))', "captioned", instant=True)],
     decimals=0)
stat("Image error ratio · in selected time range",
     "Share of images that failed (corrupt file, decode or inference error) out of all images handled in the range. "
     "Orange above 5%, vermillion above 10%.",
     "errors / max(all, 1) using `increase` over the range; `or vector(0)` gives 0 instead of 'No data' when nothing failed.",
     [tgt('(sum(increase(caption_images_processed_total{status="error"}[$__range])) or vector(0))'
          ' / clamp_min(sum(increase(caption_images_processed_total[$__range])), 1)', "error ratio", instant=True)],
     unit="percentunit", decimals=1,
     steps=[{"color": C["blue"], "value": None}, {"color": C["orange"], "value": 0.05},
            {"color": C["vermillion"], "value": 0.10}])
stat("Firing alerts · Prometheus rules",
     "Number of alert rules currently firing (see http://localhost:9090/alerts for which ones).",
     "`ALERTS` is a synthetic series Prometheus creates for every pending/firing alert; count the firing ones.",
     [tgt('count(ALERTS{alertstate="firing"}) or vector(0)', "firing", instant=True)], decimals=0,
     steps=[{"color": C["blue"], "value": None}, {"color": C["vermillion"], "value": 1}])

row("2 · Throughput & latency (application)")
timeseries(
    "Throughput · images captioned per second, by mode",
    "How fast the service is producing captions. Single-mode traffic shows as short blips; a bulk ZIP shows as a "
    "sustained plateau whose height is the CPU's sustainable captioning rate. The dotted vermillion line counts "
    "images that failed or were skipped.",
    "`rate(counter[w])` = per-second slope of a monotonic counter over window w (reset-aware). "
    "Summed `by (mode)` over the other labels.",
    [tgt(f'sum by (mode) (rate(caption_images_processed_total{{status="ok"}}[{RI}]))', "{{mode}}"),
     tgt(f'sum(rate(caption_images_processed_total{{status!="ok"}}[{RI}]))', "failed / skipped", "B")],
    "suffix: img/s", "Images per second (linear)",
    overrides=[ser("single", "blue"), ser("bulk", "orange", DASH), ser("failed / skipped", "vermillion", DOT)],
    minimum=0)
timeseries(
    "Inference latency · p50 / p95 / p99 per image",
    "Time spent in `model.generate()` per image. p50 is the typical image, p99 the slowest 1%. The gap between them "
    "widens when the CPU is contended (e.g. a bulk batch running while someone uses Single mode). Log scale so "
    "sub-second and multi-second values are both readable.",
    "`histogram_quantile(q, sum by (le) (rate(_bucket[w])))`: take the per-second growth of each cumulative bucket, "
    "find the bucket where the cumulative count crosses q·total, and linearly interpolate inside it.",
    [tgt(f"histogram_quantile(0.50, sum by (le) (rate(caption_inference_duration_seconds_bucket[{RI}])))", "p50"),
     tgt(f"histogram_quantile(0.95, sum by (le) (rate(caption_inference_duration_seconds_bucket[{RI}])))", "p95", "B"),
     tgt(f"histogram_quantile(0.99, sum by (le) (rate(caption_inference_duration_seconds_bucket[{RI}])))", "p99", "C")],
    "s", "Latency per image (seconds, log₁₀ scale)", log=True,
    overrides=[ser("p50", "blue"), ser("p95", "orange", DASH), ser("p99", "vermillion", DOT)])
timeseries(
    "Request duration · mean end-to-end time (Summary)",
    "Average wall time of a whole user request: one image for Single mode, the entire ZIP for Bulk mode. "
    "Bulk sits orders of magnitude higher because it covers many images, hence the log scale.",
    "From the Summary's `_sum` and `_count`: mean = rate(_sum) / rate(_count) = "
    "(seconds spent per second) / (requests per second) = seconds per request.",
    [tgt(f"sum by (mode) (rate(caption_request_duration_seconds_sum[{RI}])) / "
         f"sum by (mode) (rate(caption_request_duration_seconds_count[{RI}]))", "{{mode}}")],
    "s", "Mean request time (seconds, log₁₀ scale)", log=True, w=8,
    overrides=[ser("single", "blue"), ser("bulk", "orange", DASH)])
timeseries(
    "Pipeline breakdown · mean seconds per stage, per image",
    "Where an image's time goes: decoding bytes, BLIP preprocessing (resize + normalise) and model inference. "
    "Inference dominates by ~2 orders of magnitude, so optimisation effort belongs there. Log scale.",
    "Mean per stage = rate(<histogram>_sum[w]) / rate(<histogram>_count[w]) for each stage histogram.",
    [tgt(f"sum(rate(caption_decode_duration_seconds_sum[{RI}])) / sum(rate(caption_decode_duration_seconds_count[{RI}]))", "decode"),
     tgt(f"sum(rate(caption_preprocess_duration_seconds_sum[{RI}])) / sum(rate(caption_preprocess_duration_seconds_count[{RI}]))", "preprocess", "B"),
     tgt(f"sum(rate(caption_inference_duration_seconds_sum[{RI}])) / sum(rate(caption_inference_duration_seconds_count[{RI}]))", "inference", "C")],
    "s", "Mean time per image (seconds, log₁₀ scale)", log=True, w=8,
    overrides=[ser("decode", "sky", DOT), ser("preprocess", "green", DASH), ser("inference", "vermillion")])
heatmap(
    "Latency distribution · inference-time heatmap",
    "Each column is a time slice; each row a latency bucket; colour = how many images landed in that bucket "
    "(Cividis scale: dark = few, bright = many). Shows whether latency is a tight band or spread out, which "
    "percentiles alone hide.\n\nAxes: X = wall-clock time. Y = inference-latency bucket upper bound (seconds).",
    "`increase(_bucket[w])` per bucket, de-cumulated by Grafana (format = heatmap) into per-bucket counts.",
    [tgt(f"sum by (le) (increase(caption_inference_duration_seconds_bucket[{RI}]))", "{{le}}", fmt="heatmap")],
    y_label="Inference latency bucket (s)")
timeseries(
    "Errors · failures per minute, by pipeline stage",
    "Failures split by where they happened: `zip` (bad archive), `decode` (corrupt / non-image file) or "
    "`inference` (model error). A rise in `decode` usually means a bad upload, not a broken service.",
    "rate(caption_errors_total[w]) is errors per second; ×60 converts to errors per minute; summed by stage.",
    [tgt(f"sum by (stage) (rate(caption_errors_total[{RI}])) * 60", "{{stage}}")],
    "suffix: err/min", "Errors per minute (linear)", minimum=0,
    overrides=[ser("zip", "purple", DASH), ser("decode", "orange", DOT), ser("inference", "vermillion")])
timeseries(
    "Requests by outcome · requests per second",
    "User requests (one Single click or one Bulk batch) split by result. `partial` = a bulk batch where some "
    "images failed.",
    "Per-second rate of the `caption_requests_total` counter, summed by its `status` label.",
    [tgt(f"sum by (status) (rate(caption_requests_total[{RI}]))", "{{status}}")],
    "reqps", "Requests per second (linear)", minimum=0,
    overrides=[ser("ok", "blue"), ser("partial", "purple", DASH), ser("error", "vermillion", DOT)])

row("3 · App ↔ system correlation (node_exporter)")
gauge("Host CPU busy · now",
      "Share of CPU time not idle, averaged over all cores. Expect it near 100% / (cores) × torch threads during bulk.",
      "100 × (1 − avg over cores of rate(node_cpu_seconds_total{mode=\"idle\"}[w])): each core's idle seconds per second "
      "is its idle fraction.",
      [tgt(f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[{RI}])))', "CPU busy", instant=True)])
gauge("Host RAM used · now",
      "Memory in use by everything on the host (not counting reclaimable cache). The BLIP model alone takes ~1.5-2 GB.",
      "100 × (1 − MemAvailable / MemTotal).",
      [tgt("100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)", "RAM used", instant=True)])
gauge("Root disk used · now",
      "How full the root filesystem is. The model cache (~1 GB) and Prometheus' TSDB live here.",
      "100 × (1 − avail / size) for the filesystem mounted at /.",
      [tgt('max(100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))',
           "disk used", instant=True)], warn=80, crit=90)
timeseries(
    "Load vs work · run-queue length and inferences in flight",
    "1-minute load average against the number of CPU cores (dashed grey). When a bulk batch runs, load climbs "
    "towards the core count while exactly one inference is in flight: the model is using several cores per image.",
    "`node_load1` is the kernel's exponentially-damped 1-min run-queue length; core count = number of idle-mode "
    "CPU series; in-flight inferences is a gauge tracked with `track_inprogress()`.",
    [tgt("node_load1", "load (1m)"),
     tgt('count(node_cpu_seconds_total{mode="idle"})', "CPU cores", "B"),
     tgt("caption_inferences_in_progress", "inferences in flight", "C")],
    "short", "Runnable tasks (linear)", right_label="inferences in flight (count)", h=6, minimum=0,
    overrides=[ser("load (1m)", "blue"), ser("CPU cores", "grey", DASH),
               ser("inferences in flight", "orange", DOT, right=True, axis_label="Inferences in flight (count)")])
timeseries(
    "CPU vs bulk throughput · does load follow the work?",
    "The key correlation plot. Host CPU % (solid) and the app process' own CPU % (dashed) on the left axis, bulk "
    "captioning rate (dotted, right axis). During a ZIP batch all three rise together; the app line sitting just "
    "under the host line shows the spike is the captioning app, not something else on the machine.",
    "Host CPU as in the gauge; app CPU = psutil `cpu_percent` since the previous scrape ÷ cores; bulk rate = "
    "rate(caption_images_processed_total{mode=\"bulk\",status=\"ok\"}[w]).",
    [tgt(f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[{RI}])))', "host CPU %"),
     tgt("100 * caption_process_cpu_ratio", "app process CPU %", "B"),
     tgt(f'sum(rate(caption_images_processed_total{{mode="bulk",status="ok"}}[{RI}]))', "bulk img/s", "C")],
    "percent", "CPU busy (% of all cores, linear)", right_label="bulk images per second", h=9, minimum=0,
    overrides=[ser("host CPU %", "blue"), ser("app process CPU %", "green", DASH),
               ser("bulk img/s", "orange", DOT, right=True, unit="suffix: img/s",
                   axis_label="Bulk images per second (linear)")])
timeseries(
    "Memory vs queue · host RAM, app RSS and images waiting",
    "Host memory in use (solid) and the app's resident memory (dashed, mostly the BLIP weights) on the left axis; "
    "images still waiting in running bulk batches (dotted, right axis). RSS jumps once when the model loads and "
    "then stays flat, so memory is not what scales with batch size; CPU time is.",
    "Host used = MemTotal − MemAvailable; app RSS from psutil; queue is a gauge +N at batch start, −1 per image.",
    [tgt("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "host RAM used"),
     tgt("caption_process_resident_memory_bytes", "app RSS", "B"),
     tgt("caption_bulk_queue_remaining", "images queued", "C")],
    "bytes", "Memory (bytes, linear)", right_label="images queued (count)", h=9, minimum=0,
    overrides=[ser("host RAM used", "blue"), ser("app RSS", "green", DASH),
               ser("images queued", "orange", DOT, right=True, unit="short",
                   axis_label="Images queued (count, linear)", extra=[{"id": "custom.lineInterpolation", "value": "stepAfter"}])])
timeseries(
    "Disk & upload I/O · bytes per second",
    "Disk reads/writes (all block devices) next to the bytes users upload into the app. The first model load is a "
    "large disk-read burst; ZIP uploads appear as upload spikes.",
    "Per-second rate of node_exporter's cumulative disk byte counters and of `caption_uploaded_bytes_total`.",
    [tgt(f"sum(rate(node_disk_read_bytes_total[{RI}]))", "disk read"),
     tgt(f"sum(rate(node_disk_written_bytes_total[{RI}]))", "disk write", "B"),
     tgt(f"sum(rate(caption_uploaded_bytes_total[{RI}]))", "uploaded to app", "C")],
    "Bps", "Throughput (bytes per second, linear)", h=8, minimum=0,
    overrides=[ser("disk read", "blue"), ser("disk write", "sky", DASH), ser("uploaded to app", "orange", DOT)])
timeseries(
    "App process · OS threads vs torch intra-op threads",
    "Threads in the Streamlit process versus the threads torch may use for matrix maths. Torch is capped at "
    "(cores − 1) so the UI thread stays responsive.",
    "Two gauges: `caption_process_threads` (psutil, read at scrape time) and `caption_torch_threads` (set at model load).",
    [tgt("caption_process_threads", "process threads"), tgt("caption_torch_threads", "torch threads", "B")],
    "short", "Threads (count, linear)", h=8, minimum=0, interp="stepAfter",
    overrides=[ser("process threads", "blue"), ser("torch threads", "orange", DASH)])

row("4 · Workload characteristics")
timeseries(
    "Work in flight · queued images and active requests",
    "Bulk images not yet captioned (dotted) and requests being processed, by mode. A queue that drains at a steady "
    "slope shows healthy batch progress; a flat non-zero queue fires the BulkQueueStuck alert.",
    "Plain gauge values; the step interpolation reflects that they change in whole units.",
    [tgt("caption_bulk_queue_remaining", "bulk images queued"),
     tgt("sum by (mode) (caption_active_requests)", "active {{mode}} requests", "B")],
    "short", "Count (linear)", w=8, minimum=0, interp="stepAfter",
    overrides=[ser("bulk images queued", "orange", DOT), ser("active single requests", "blue"),
               ser("active bulk requests", "vermillion", DASH)])
timeseries(
    "Input images · file size and resolution (p50 / p95)",
    "What users are uploading. File size (left, log scale) and megapixels (right). BLIP resizes everything to "
    "384×384, so resolution mainly affects decode time, not inference time.",
    "histogram_quantile over 2-minute bucket rates (uploads are sporadic, so a longer window avoids gaps).",
    [tgt("histogram_quantile(0.50, sum by (le) (rate(caption_image_size_bytes_bucket[2m])))", "size p50"),
     tgt("histogram_quantile(0.95, sum by (le) (rate(caption_image_size_bytes_bucket[2m])))", "size p95", "B"),
     tgt("histogram_quantile(0.50, sum by (le) (rate(caption_image_megapixels_bucket[2m])))", "megapixels p50", "C")],
    "bytes", "File size (bytes, log₁₀ scale)", right_label="megapixels (linear)", w=8, log=True,
    overrides=[ser("size p50", "blue"), ser("size p95", "sky", DASH),
               ser("megapixels p50", "orange", DOT, right=True, unit="suffix: MP",
                   axis_label="Resolution (megapixels, linear)",
                   extra=[{"id": "custom.scaleDistribution", "value": {"type": "linear"}}])])
timeseries(
    "Caption length · mean tokens and characters",
    "How long the generated captions are. Tokens come from the histogram, characters from the Summary. A sudden "
    "change would hint at a model or preprocessing change.",
    "Mean = rate(_sum[2m]) / rate(_count[2m]) for `caption_length_tokens` (histogram) and `caption_length_chars` (summary).",
    [tgt("sum(rate(caption_length_tokens_sum[2m])) / sum(rate(caption_length_tokens_count[2m]))", "tokens"),
     tgt("sum(rate(caption_length_chars_sum[2m])) / sum(rate(caption_length_chars_count[2m]))", "characters", "B")],
    "short", "Tokens per caption (linear)", right_label="characters per caption", w=8, minimum=0,
    overrides=[ser("tokens", "blue"),
               ser("characters", "orange", DASH, right=True, unit="short", axis_label="Characters per caption (linear)")])
timeseries(
    "Bulk batches · size and duration of the latest batch",
    "Images in the most recent ZIP (solid, left) and how long it took (dashed, right). Their ratio is the "
    "per-image cost: roughly linear means the CPU is the only bottleneck.",
    "Two gauges set when each bulk batch starts (size) and ends (duration).",
    [tgt("caption_last_batch_size", "batch size"), tgt("caption_last_batch_duration_seconds", "batch duration", "B")],
    "short", "Images in batch (count, linear)", right_label="batch duration (seconds)", minimum=0, interp="stepAfter",
    overrides=[ser("batch size", "blue"),
               ser("batch duration", "orange", DASH, right=True, unit="s", axis_label="Batch duration (seconds, linear)")])
bargauge(
    "Upload hygiene · ZIP uploads and skipped files (time range)",
    "ZIP archives by validity and files the bulk mode refused to caption, by reason, within the selected time range. "
    "Each bar is labelled, so no legend is needed. Unit: count of files / archives.",
    "round(sum by (label) (increase(counter[$__range]))) for `caption_zip_uploads_total` and `caption_images_skipped_total`.",
    [tgt("round(sum by (status) (increase(caption_zip_uploads_total[$__range])))", "ZIP {{status}}", instant=True),
     tgt("round(sum by (reason) (increase(caption_images_skipped_total[$__range])))", "skipped: {{reason}}", "B", instant=True)])

row("5 · Clients (custom labels: session, IP, browser)")
table(
    "Per-client activity · requests per session (time range)",
    "One row per browser session: its client IP, browser, mode and outcome, with the number of requests made in the "
    "selected range. Sort or filter the columns to find a heavy user.",
    "round(increase(caption_requests_total[$__range])) grouped by the custom labels; table format, zero rows hidden.",
    [tgt("round(sum by (session_id, client_ip, browser, mode, status) (increase(caption_requests_total[$__range]))) > 0",
         "", instant=True, fmt="table")],
    rename={"session_id": "Session", "client_ip": "Client IP", "browser": "Browser", "mode": "Mode",
            "status": "Outcome", "Value": "Requests"}, sort_by="Requests", w=12, h=9)
timeseries(
    "Traffic by browser · requests per second",
    "Which browsers the requests come from (parsed from the User-Agent header).",
    "Per-second rate of `caption_requests_total`, summed by the `browser` label.",
    [tgt(f"sum by (browser) (rate(caption_requests_total[{RI}]))", "{{browser}}")],
    "reqps", "Requests per second (linear)", w=8, h=9, minimum=0,
    overrides=[ser("chrome", "blue"), ser("firefox", "orange", DASH), ser("edge", "green", DOT),
               ser("safari", "purple", DASH), ser("opera", "sky", DOT), ser("other", "grey", DASH)])
stat("Distinct sessions · since app start",
     "Browser sessions that have opened the app since it last restarted.",
     "Each session gets its own `session_id` label value; count the distinct values present on `caption_requests_total`.",
     [tgt("count(count by (session_id) (caption_requests_total))", "sessions", instant=True)],
     w=4, h=9, decimals=0, color_mode="value")

row("6 · Runtime & monitoring health")
table(
    "Build info · model and library versions",
    "Static metadata exported once at model load: model name, device, torch / transformers / Python versions, OS.",
    "`Info` metric: a constant 1 whose labels carry the metadata; shown as a table with the value column hidden.",
    [tgt("caption_app_info", "", instant=True, fmt="table")],
    rename={"model": "Model", "device": "Device", "torch_version": "torch", "transformers_version": "transformers",
            "python_version": "Python", "os": "OS"}, hide=("Value", "service", "component"), w=12, h=4)
stat("Model parameters · BLIP base",
     "Number of weights in the loaded model (~247 M for BLIP-base).",
     "Gauge set from sum(p.numel()) at model load.",
     [tgt("caption_model_parameters", "params", instant=True)], unit="short", w=4, h=4, color_mode="value")
stat("Model load time · cold start",
     "How long `from_pretrained` took. Much longer on the very first run, when the weights are downloaded.",
     "Gauge set from a perf_counter() timer around model loading.",
     [tgt("caption_model_load_seconds", "load time", instant=True)], unit="s", w=4, h=4, decimals=1, color_mode="value")
stat("Total uploaded · since app start",
     "All bytes users have uploaded (images + ZIPs) since the app started.",
     "Sum of the `caption_uploaded_bytes_total` counter.",
     [tgt("sum(caption_uploaded_bytes_total)", "uploaded", instant=True)], unit="bytes", w=4, h=4, color_mode="value")
timeseries(
    "Scrape health · time to scrape each target",
    "How long Prometheus takes to collect each target (solid lines, left) and how many samples it got from the app "
    "(dotted, right). Scrapes must finish well under the 5 s interval; samples grow as new label sets appear.",
    "`scrape_duration_seconds` and `scrape_samples_scraped` are synthetic series Prometheus records per target.",
    [tgt("scrape_duration_seconds", "{{job}}"),
     tgt('scrape_samples_scraped{job="caption_app"}', "app samples per scrape", "B")],
    "s", "Scrape duration (seconds, log₁₀ scale)", right_label="samples per scrape", log=True, h=8,
    overrides=[ser("caption_app", "blue"), ser("node", "orange", DASH), ser("prometheus", "grey", DOT),
               ser("app samples per scrape", "green", DOT, right=True, unit="short",
                   axis_label="Samples per scrape (linear)",
                   extra=[{"id": "custom.scaleDistribution", "value": {"type": "linear"}}])])
timeseries(
    "Tokens generated · caption tokens per second, by mode",
    "Output volume of the model. Together with throughput this gives tokens per image.",
    "Per-second rate of the `caption_tokens_generated_total` counter, by mode.",
    [tgt(f"sum by (mode) (rate(caption_tokens_generated_total[{RI}]))", "{{mode}}")],
    "suffix: tok/s", "Tokens per second (linear)", h=8, minimum=0,
    overrides=[ser("single", "blue"), ser("bulk", "orange", DASH)])


dashboard = {
    "title": "DA3408 A4 · CPU Image Captioning – Application & Host Monitoring",
    "uid": "da3408-a4-captioning",
    "description": ("Streamlit + BLIP captioning app instrumented with prometheus_client, correlated with node_exporter "
                    "host metrics. AI attribution: generated with Claude (Anthropic) from the prompt " + PROMPT +
                    "; every query is explained in PROMQL.md."),
    "tags": ["da3408", "prometheus", "node_exporter", "streamlit"],
    "editable": True, "graphTooltip": 1, "liveNow": False,
    "time": {"from": "now-15m", "to": "now"}, "refresh": "5s",
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "timezone": "browser", "fiscalYearStartMonth": 0, "weekStart": "",
    "schemaVersion": 39, "version": 1, "links": [],
    "templating": {"list": [{
        "name": "datasource", "label": "Prometheus", "type": "datasource", "query": "prometheus",
        "current": {}, "hide": 0, "includeAll": False, "multi": False, "options": [], "refresh": 1, "regex": ""}]},
    "annotations": {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        {"datasource": DS, "enable": True, "hide": False, "iconColor": C["purple"],
         "name": "Bulk batch running", "expr": 'sum(caption_active_requests{mode="bulk"}) > 0',
         "step": "5s", "titleFormat": "Bulk ZIP batch in progress", "textFormat": "",
         "useValueForTime": False},
    ]},
    "panels": panels,
}

OUT_JSON.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

md = [
    "# PromQL reference · DA3408 A4 dashboard",
    "",
    "**AI attribution (Code of Conduct).** The PromQL in `grafana/dashboard.json` and `monitoring/alert_rules.yml` "
    f"was generated with Claude (Anthropic). Prompt used: {PROMPT}. "
    "The logic of every query is written out below so it can be worked by hand.",
    "",
    "## Building blocks (derive these, don't memorise them)",
    "",
    "**Counter → `rate(c[w])`.** A counter only increases (until the process restarts). Over a window of length *w* "
    "with first sample $c_1$ at $t_1$ and last $c_n$ at $t_n$, `rate` ≈ $(c_n - c_1)/(t_n - t_1)$, after "
    "adding back any drop (a reset) and extrapolating to the window edges. Units: events per second. "
    "`increase(c[w])` is the same thing multiplied by *w*: events in the window.",
    "",
    "**Gauge.** A value that goes up and down (active requests, RSS). It is graphed as-is; never `rate()` a gauge.",
    "",
    "**Histogram → `histogram_quantile(q, …)`.** The app exports cumulative buckets `_bucket{le=\"b\"}` = number of "
    "observations ≤ b, plus `_sum` and `_count`. Take `rate` of each bucket and `sum by (le)` so all label sets merge. "
    "With total rate *N* (the `+Inf` bucket), find the first bucket whose cumulative rate ≥ q·N. If that bucket spans "
    "(b_lo, b_hi] and holds cumulative rates (n_lo, n_hi), then "
    "$\\text{quantile} = b_{lo} + (b_{hi}-b_{lo})\\cdot\\frac{qN - n_{lo}}{n_{hi}-n_{lo}}$ (linear interpolation, "
    "so the answer is only as precise as the bucket boundaries).",
    "",
    "**Summary → mean.** A Summary exports `_count` (number of observations) and `_sum` (their total). "
    "Mean over a window = `rate(_sum[w]) / rate(_count[w])`: (seconds observed per second) ÷ (observations per "
    "second) = seconds per observation. Summaries can't be aggregated into quantiles across instances; histograms can, "
    "which is why latency percentiles come from the histogram.",
    "",
    "**`$__rate_interval`** is Grafana's window: max(4 × scrape interval, query step + scrape interval). With a 5 s "
    "scrape interval set on the data source it is at least 20 s, so every window holds at least 4 samples. "
    "**`$__range`** is the whole dashboard time range.",
    "",
]
for d in docs:
    if "row" in d:
        md += [f"## {d['row']}", ""]
        continue
    md.append(f"### {d['title']}")
    md.append("")
    for legend, expr in d["queries"]:
        md.append(f"- `{legend}`")
        md.append("")
        md.append("  ```promql")
        md.append(f"  {expr}")
        md.append("  ```")
    md.append("")
    md.append(f"**Logic:** {d['explain']}")
    md.append("")
md += [
    "## Annotation",
    "",
    "- `Bulk batch running`: `sum(caption_active_requests{mode=\"bulk\"}) > 0` is non-empty only while a ZIP batch "
    "is in progress; Grafana shades those intervals on every graph.",
    "",
]
OUT_MD.write_text("\n".join(md), encoding="utf-8")

n_viz = sum(1 for p in panels if p["type"] not in ("row", "text"))
print(f"wrote {OUT_JSON.relative_to(ROOT)} ({n_viz} panels) and {OUT_MD.relative_to(ROOT)}")
