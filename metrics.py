"""
Prometheus metrics, served at http://<host>:METRICS_PORT/metrics (default 8000).

Kept in a separate module because Streamlit re-runs app.py on every interaction
while an imported module runs once per process; this avoids "Duplicated
timeseries" errors. Restart Streamlit after editing this file.
"""

import os
import threading

import psutil
from prometheus_client import Counter, Gauge, Histogram, Info, Summary, start_http_server

METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))
METRICS_ADDR = os.getenv("METRICS_ADDR", "0.0.0.0")

# Bucket layouts; CPU inference takes roughly 0.5-5 s per image
LATENCY_BUCKETS = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0)
FAST_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
BYTES_BUCKETS = (10e3, 50e3, 100e3, 250e3, 500e3, 1e6, 2.5e6, 5e6, 10e6, 20e6)
MEGAPIXEL_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 4, 8, 12, 24, 50)
TOKEN_BUCKETS = (2, 4, 6, 8, 10, 12, 15, 20, 25, 30)
ZIP_BUCKETS = (1e5, 1e6, 5e6, 10e6, 25e6, 50e6, 100e6, 200e6)
BATCH_BUCKETS = (1, 2, 5, 10, 20, 50, 100)

# Counters: monotonic, exposed with the _total suffix
REQUESTS = Counter(
    "caption_requests",
    "User requests (one Single-mode click or one Bulk batch), with client metadata",
    ["mode", "status", "session_id", "client_ip", "browser"],
)
IMAGES_PROCESSED = Counter(
    "caption_images_processed",
    "Images handled, by mode and outcome (ok | error | skipped)",
    ["mode", "status"],
)
CLIENT_IMAGES = Counter(
    "caption_client_images",
    "Images captioned per client session",
    ["session_id", "client_ip", "mode"],
)
ERRORS = Counter(
    "caption_errors",
    "Errors by mode, pipeline stage (zip | decode | inference) and exception type",
    ["mode", "stage", "error_type"],
)
IMAGES_SKIPPED = Counter(
    "caption_images_skipped",
    "Files not captioned in bulk mode, by reason (unsupported_file | too_large | over_limit)",
    ["reason"],
)
ZIP_UPLOADS = Counter(
    "caption_zip_uploads",
    "ZIP archives uploaded, by validity (valid | empty | invalid)",
    ["status"],
)
UPLOADED_BYTES = Counter(
    "caption_uploaded_bytes",
    "Total bytes uploaded by users",
    ["mode"],
)
TOKENS_GENERATED = Counter(
    "caption_tokens_generated",
    "Total caption tokens generated",
    ["mode"],
)
SESSIONS_STARTED = Counter(
    "caption_sessions_started",
    "New browser sessions opened",
    ["client_ip", "browser"],
)

# Gauges: current value, can go up and down
ACTIVE_REQUESTS = Gauge(
    "caption_active_requests",
    "Requests currently being processed",
    ["mode"],
)
INFERENCES_IN_PROGRESS = Gauge(
    "caption_inferences_in_progress",
    "Model forward passes running right now",
)
BULK_QUEUE_REMAINING = Gauge(
    "caption_bulk_queue_remaining",
    "Images still waiting to be captioned across all running bulk jobs",
)
LAST_BATCH_SIZE = Gauge("caption_last_batch_size", "Number of images in the most recent bulk batch")
LAST_BATCH_DURATION = Gauge("caption_last_batch_duration_seconds", "Wall time of the most recent bulk batch")
LAST_INFERENCE_TS = Gauge("caption_last_inference_timestamp_seconds", "Unix time of the last successful caption")

MODEL_LOADED = Gauge("caption_model_loaded", "1 if the captioning model is loaded in memory, else 0")
MODEL_LOAD_SECONDS = Gauge("caption_model_load_seconds", "Time taken to load the model")
MODEL_PARAMETERS = Gauge("caption_model_parameters", "Number of parameters in the loaded model")
TORCH_THREADS = Gauge("caption_torch_threads", "Intra-op CPU threads used by torch")

# Process gauges via psutil, evaluated at scrape time
_proc = psutil.Process()
_proc.cpu_percent(None)  # prime the counter; first call always returns 0.0
_NCPU = psutil.cpu_count() or 1

PROCESS_RSS = Gauge("caption_process_resident_memory_bytes", "Resident memory of the app process (model included)")
PROCESS_RSS.set_function(lambda: _proc.memory_info().rss)

PROCESS_CPU = Gauge(
    "caption_process_cpu_ratio",
    "App-process CPU usage as a fraction of all cores (0-1), averaged since the previous scrape",
)
PROCESS_CPU.set_function(lambda: _proc.cpu_percent(None) / (100.0 * _NCPU))

PROCESS_THREADS = Gauge("caption_process_threads", "OS threads in the app process")
PROCESS_THREADS.set_function(lambda: _proc.num_threads())

# Histograms: _bucket/_sum/_count, quantiles via histogram_quantile
INFERENCE_SECONDS = Histogram(
    "caption_inference_duration_seconds",
    "model.generate() time per image",
    ["mode"],
    buckets=LATENCY_BUCKETS,
)
PREPROCESS_SECONDS = Histogram(
    "caption_preprocess_duration_seconds",
    "Processor (resize + normalise) time per image",
    ["mode"],
    buckets=FAST_BUCKETS,
)
DECODE_SECONDS = Histogram(
    "caption_decode_duration_seconds",
    "Image decode (bytes -> RGB) time per image",
    ["mode"],
    buckets=FAST_BUCKETS,
)
IMAGE_BYTES = Histogram(
    "caption_image_size_bytes",
    "Size of each uploaded image file",
    ["mode"],
    buckets=BYTES_BUCKETS,
)
IMAGE_MEGAPIXELS = Histogram(
    "caption_image_megapixels",
    "Resolution of each image in megapixels",
    ["mode"],
    buckets=MEGAPIXEL_BUCKETS,
)
CAPTION_TOKENS = Histogram(
    "caption_length_tokens",
    "Tokens in each generated caption",
    ["mode"],
    buckets=TOKEN_BUCKETS,
)
ZIP_BYTES = Histogram("caption_zip_size_bytes", "Size of each uploaded ZIP archive", buckets=ZIP_BUCKETS)
BATCH_SIZE = Histogram("caption_batch_size_images", "Images per bulk batch", buckets=BATCH_BUCKETS)

# Summaries: exposed as _count and _sum (no _total); mean = rate(_sum) / rate(_count)
REQUEST_SECONDS = Summary(
    "caption_request_duration_seconds",
    "End-to-end request time (Single: one image; Bulk: whole batch)",
    ["mode"],
)
BULK_IMAGE_SECONDS = Summary(
    "caption_bulk_image_duration_seconds",
    "Per-image time in bulk mode (read + decode + preprocess + inference)",
)
CAPTION_CHARS = Summary(
    "caption_length_chars",
    "Characters in each generated caption",
    ["mode"],
)

# Info: static metadata as labels on caption_app_info = 1
APP_INFO = Info("caption_app", "Static build/runtime information")


# A labelled series only appears after its first .labels() call. Creating known
# combinations at 0 up front lets rate()/increase() see the 0 -> 1 step.
MODES = ("single", "bulk")


def init_label_sets():
    for mode in MODES:
        for status in ("ok", "error", "skipped"):
            IMAGES_PROCESSED.labels(mode, status)
        UPLOADED_BYTES.labels(mode)
        TOKENS_GENERATED.labels(mode)
        ACTIVE_REQUESTS.labels(mode)
        REQUEST_SECONDS.labels(mode)
        CAPTION_CHARS.labels(mode)
        for hist in (INFERENCE_SECONDS, PREPROCESS_SECONDS, DECODE_SECONDS,
                     IMAGE_BYTES, IMAGE_MEGAPIXELS, CAPTION_TOKENS):
            hist.labels(mode)
    for reason in ("unsupported_file", "too_large", "over_limit"):
        IMAGES_SKIPPED.labels(reason)
    for status in ("valid", "empty", "invalid"):
        ZIP_UPLOADS.labels(status)


def init_session_labels(session_id: str, client_ip: str, browser: str):
    """Create a new session's per-client series at 0 before its first request."""
    for mode, statuses in (("single", ("ok", "error")), ("bulk", ("ok", "partial", "error"))):
        for status in statuses:
            REQUESTS.labels(mode, status, session_id, client_ip, browser)
        CLIENT_IMAGES.labels(session_id, client_ip, mode)


init_label_sets()


_server_lock = threading.Lock()
_server_started = False


def start_metrics_server() -> bool:
    """Start the /metrics HTTP server once. Returns False if the port is busy."""
    global _server_started
    with _server_lock:
        if _server_started:
            return True
        try:
            start_http_server(METRICS_PORT, addr=METRICS_ADDR)
        except OSError as e:
            print(f"[metrics] could not bind {METRICS_ADDR}:{METRICS_PORT}: {e}")
            return False
        _server_started = True
        print(f"[metrics] serving on http://{METRICS_ADDR}:{METRICS_PORT}/metrics")
        return True
