# PromQL reference · DA3408 A4 dashboard

**AI attribution (Code of Conduct).** The PromQL in `grafana/dashboard.json` and `monitoring/alert_rules.yml` was generated with Claude (Anthropic). Prompt used: "give the remaining required files for node exporter, prometheus.yaml and grafana dashboard". The logic of every query is written out below so it can be worked by hand.

## Building blocks (derive these, don't memorise them)

**Counter → `rate(c[w])`.** A counter only increases (until the process restarts). Over a window of length *w* with first sample $c_1$ at $t_1$ and last $c_n$ at $t_n$, `rate` ≈ $(c_n - c_1)/(t_n - t_1)$, after adding back any drop (a reset) and extrapolating to the window edges. Units: events per second. `increase(c[w])` is the same thing multiplied by *w*: events in the window.

**Gauge.** A value that goes up and down (active requests, RSS). It is graphed as-is; never `rate()` a gauge.

**Histogram → `histogram_quantile(q, …)`.** The app exports cumulative buckets `_bucket{le="b"}` = number of observations ≤ b, plus `_sum` and `_count`. Take `rate` of each bucket and `sum by (le)` so all label sets merge. With total rate *N* (the `+Inf` bucket), find the first bucket whose cumulative rate ≥ q·N. If that bucket spans (b_lo, b_hi] and holds cumulative rates (n_lo, n_hi), then $\text{quantile} = b_{lo} + (b_{hi}-b_{lo})\cdot\frac{qN - n_{lo}}{n_{hi}-n_{lo}}$ (linear interpolation, so the answer is only as precise as the bucket boundaries).

**Summary → mean.** A Summary exports `_count` (number of observations) and `_sum` (their total). Mean over a window = `rate(_sum[w]) / rate(_count[w])`: (seconds observed per second) ÷ (observations per second) = seconds per observation. Summaries can't be aggregated into quantiles across instances; histograms can, which is why latency percentiles come from the histogram.

**`$__rate_interval`** is Grafana's window: max(4 × scrape interval, query step + scrape interval). With a 5 s scrape interval set on the data source it is at least 20 s, so every window holds at least 4 samples. **`$__range`** is the whole dashboard time range.

## 1 · Service health (now)

### App status · /metrics scrape

- `app`

  ```promql
  up{job="caption_app"}
  ```

**Logic:** `up` is a synthetic series Prometheus writes for every target: 1 if the scrape succeeded, 0 if it failed.

### Node exporter · host metrics

- `node`

  ```promql
  up{job="node"}
  ```

**Logic:** Same `up` series as above, for the node job.

### Model · BLIP in memory

- `model`

  ```promql
  caption_model_loaded
  ```

**Logic:** Gauge set to 1 at the end of `load_model()`; initialised to 0 at process start.

### App uptime · since process start

- `uptime`

  ```promql
  time() - process_start_time_seconds{job="caption_app"}
  ```

**Logic:** `process_start_time_seconds` (default Linux process collector) is a Unix timestamp; `time() - start` = age in seconds.

### Active requests · being processed now

- `active`

  ```promql
  sum(caption_active_requests)
  ```

**Logic:** Sum over modes of the `caption_active_requests` gauge, which is +1 on request start and -1 in a `finally` block.

### Images captioned · in selected time range

- `captioned`

  ```promql
  round(sum(increase(caption_images_processed_total{status="ok"}[$__range])))
  ```

**Logic:** `increase(counter[$__range])` = counter value at the end of the range minus at the start (reset-aware, extrapolated to the range edges), summed over label sets and rounded.

### Image error ratio · in selected time range

- `error ratio`

  ```promql
  (sum(increase(caption_images_processed_total{status="error"}[$__range])) or vector(0)) / clamp_min(sum(increase(caption_images_processed_total[$__range])), 1)
  ```

**Logic:** errors / max(all, 1) using `increase` over the range; `or vector(0)` gives 0 instead of 'No data' when nothing failed.

### Firing alerts · Prometheus rules

- `firing`

  ```promql
  count(ALERTS{alertstate="firing"}) or vector(0)
  ```

**Logic:** `ALERTS` is a synthetic series Prometheus creates for every pending/firing alert; count the firing ones.

## 2 · Throughput & latency (application)

### Throughput · images captioned per second, by mode

- `{{mode}}`

  ```promql
  sum by (mode) (rate(caption_images_processed_total{status="ok"}[$__rate_interval]))
  ```
- `failed / skipped`

  ```promql
  sum(rate(caption_images_processed_total{status!="ok"}[$__rate_interval]))
  ```

**Logic:** `rate(counter[w])` = per-second slope of a monotonic counter over window w (reset-aware). Summed `by (mode)` over the other labels.

### Inference latency · p50 / p95 / p99 per image

- `p50`

  ```promql
  histogram_quantile(0.50, sum by (le) (rate(caption_inference_duration_seconds_bucket[$__rate_interval])))
  ```
- `p95`

  ```promql
  histogram_quantile(0.95, sum by (le) (rate(caption_inference_duration_seconds_bucket[$__rate_interval])))
  ```
- `p99`

  ```promql
  histogram_quantile(0.99, sum by (le) (rate(caption_inference_duration_seconds_bucket[$__rate_interval])))
  ```

**Logic:** `histogram_quantile(q, sum by (le) (rate(_bucket[w])))`: take the per-second growth of each cumulative bucket, find the bucket where the cumulative count crosses q·total, and linearly interpolate inside it.

### Request duration · mean end-to-end time (Summary)

- `{{mode}}`

  ```promql
  sum by (mode) (rate(caption_request_duration_seconds_sum[$__rate_interval])) / sum by (mode) (rate(caption_request_duration_seconds_count[$__rate_interval]))
  ```

**Logic:** From the Summary's `_sum` and `_count`: mean = rate(_sum) / rate(_count) = (seconds spent per second) / (requests per second) = seconds per request.

### Pipeline breakdown · mean seconds per stage, per image

- `decode`

  ```promql
  sum(rate(caption_decode_duration_seconds_sum[$__rate_interval])) / sum(rate(caption_decode_duration_seconds_count[$__rate_interval]))
  ```
- `preprocess`

  ```promql
  sum(rate(caption_preprocess_duration_seconds_sum[$__rate_interval])) / sum(rate(caption_preprocess_duration_seconds_count[$__rate_interval]))
  ```
- `inference`

  ```promql
  sum(rate(caption_inference_duration_seconds_sum[$__rate_interval])) / sum(rate(caption_inference_duration_seconds_count[$__rate_interval]))
  ```

**Logic:** Mean per stage = rate(<histogram>_sum[w]) / rate(<histogram>_count[w]) for each stage histogram.

### Latency distribution · inference-time heatmap

- `{{le}}`

  ```promql
  sum by (le) (increase(caption_inference_duration_seconds_bucket[$__rate_interval]))
  ```

**Logic:** `increase(_bucket[w])` per bucket, de-cumulated by Grafana (format = heatmap) into per-bucket counts.

### Errors · failures per minute, by pipeline stage

- `{{stage}}`

  ```promql
  sum by (stage) (rate(caption_errors_total[$__rate_interval])) * 60
  ```

**Logic:** rate(caption_errors_total[w]) is errors per second; ×60 converts to errors per minute; summed by stage.

### Requests by outcome · requests per second

- `{{status}}`

  ```promql
  sum by (status) (rate(caption_requests_total[$__rate_interval]))
  ```

**Logic:** Per-second rate of the `caption_requests_total` counter, summed by its `status` label.

## 3 · App ↔ system correlation (node_exporter)

### Host CPU busy · now

- `CPU busy`

  ```promql
  100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))
  ```

**Logic:** 100 × (1 − avg over cores of rate(node_cpu_seconds_total{mode="idle"}[w])): each core's idle seconds per second is its idle fraction.

### Host RAM used · now

- `RAM used`

  ```promql
  100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)
  ```

**Logic:** 100 × (1 − MemAvailable / MemTotal).

### Root disk used · now

- `disk used`

  ```promql
  max(100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))
  ```

**Logic:** 100 × (1 − avail / size) for the filesystem mounted at /.

### Load vs work · run-queue length and inferences in flight

- `load (1m)`

  ```promql
  node_load1
  ```
- `CPU cores`

  ```promql
  count(node_cpu_seconds_total{mode="idle"})
  ```
- `inferences in flight`

  ```promql
  caption_inferences_in_progress
  ```

**Logic:** `node_load1` is the kernel's exponentially-damped 1-min run-queue length; core count = number of idle-mode CPU series; in-flight inferences is a gauge tracked with `track_inprogress()`.

### CPU vs bulk throughput · does load follow the work?

- `host CPU %`

  ```promql
  100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))
  ```
- `app process CPU %`

  ```promql
  100 * caption_process_cpu_ratio
  ```
- `bulk img/s`

  ```promql
  sum(rate(caption_images_processed_total{mode="bulk",status="ok"}[$__rate_interval]))
  ```

**Logic:** Host CPU as in the gauge; app CPU = psutil `cpu_percent` since the previous scrape ÷ cores; bulk rate = rate(caption_images_processed_total{mode="bulk",status="ok"}[w]).

### Memory vs queue · host RAM, app RSS and images waiting

- `host RAM used`

  ```promql
  node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes
  ```
- `app RSS`

  ```promql
  caption_process_resident_memory_bytes
  ```
- `images queued`

  ```promql
  caption_bulk_queue_remaining
  ```

**Logic:** Host used = MemTotal − MemAvailable; app RSS from psutil; queue is a gauge +N at batch start, −1 per image.

### Disk & upload I/O · bytes per second

- `disk read`

  ```promql
  sum(rate(node_disk_read_bytes_total[$__rate_interval]))
  ```
- `disk write`

  ```promql
  sum(rate(node_disk_written_bytes_total[$__rate_interval]))
  ```
- `uploaded to app`

  ```promql
  sum(rate(caption_uploaded_bytes_total[$__rate_interval]))
  ```

**Logic:** Per-second rate of node_exporter's cumulative disk byte counters and of `caption_uploaded_bytes_total`.

### App process · OS threads vs torch intra-op threads

- `process threads`

  ```promql
  caption_process_threads
  ```
- `torch threads`

  ```promql
  caption_torch_threads
  ```

**Logic:** Two gauges: `caption_process_threads` (psutil, read at scrape time) and `caption_torch_threads` (set at model load).

## 4 · Workload characteristics

### Work in flight · queued images and active requests

- `bulk images queued`

  ```promql
  caption_bulk_queue_remaining
  ```
- `active {{mode}} requests`

  ```promql
  sum by (mode) (caption_active_requests)
  ```

**Logic:** Plain gauge values; the step interpolation reflects that they change in whole units.

### Input images · file size and resolution (p50 / p95)

- `size p50`

  ```promql
  histogram_quantile(0.50, sum by (le) (rate(caption_image_size_bytes_bucket[2m])))
  ```
- `size p95`

  ```promql
  histogram_quantile(0.95, sum by (le) (rate(caption_image_size_bytes_bucket[2m])))
  ```
- `megapixels p50`

  ```promql
  histogram_quantile(0.50, sum by (le) (rate(caption_image_megapixels_bucket[2m])))
  ```

**Logic:** histogram_quantile over 2-minute bucket rates (uploads are sporadic, so a longer window avoids gaps).

### Caption length · mean tokens and characters

- `tokens`

  ```promql
  sum(rate(caption_length_tokens_sum[2m])) / sum(rate(caption_length_tokens_count[2m]))
  ```
- `characters`

  ```promql
  sum(rate(caption_length_chars_sum[2m])) / sum(rate(caption_length_chars_count[2m]))
  ```

**Logic:** Mean = rate(_sum[2m]) / rate(_count[2m]) for `caption_length_tokens` (histogram) and `caption_length_chars` (summary).

### Bulk batches · size and duration of the latest batch

- `batch size`

  ```promql
  caption_last_batch_size
  ```
- `batch duration`

  ```promql
  caption_last_batch_duration_seconds
  ```

**Logic:** Two gauges set when each bulk batch starts (size) and ends (duration).

### Upload hygiene · ZIP uploads and skipped files (time range)

- `ZIP {{status}}`

  ```promql
  round(sum by (status) (increase(caption_zip_uploads_total[$__range])))
  ```
- `skipped: {{reason}}`

  ```promql
  round(sum by (reason) (increase(caption_images_skipped_total[$__range])))
  ```

**Logic:** round(sum by (label) (increase(counter[$__range]))) for `caption_zip_uploads_total` and `caption_images_skipped_total`.

## 5 · Clients (custom labels: session, IP, browser)

### Per-client activity · requests per session (time range)

- `A`

  ```promql
  round(sum by (session_id, client_ip, browser, mode, status) (increase(caption_requests_total[$__range]))) > 0
  ```

**Logic:** round(increase(caption_requests_total[$__range])) grouped by the custom labels; table format, zero rows hidden.

### Traffic by browser · requests per second

- `{{browser}}`

  ```promql
  sum by (browser) (rate(caption_requests_total[$__rate_interval]))
  ```

**Logic:** Per-second rate of `caption_requests_total`, summed by the `browser` label.

### Distinct sessions · since app start

- `sessions`

  ```promql
  count(count by (session_id) (caption_requests_total))
  ```

**Logic:** Each session gets its own `session_id` label value; count the distinct values present on `caption_requests_total`.

## 6 · Runtime & monitoring health

### Build info · model and library versions

- `A`

  ```promql
  caption_app_info
  ```

**Logic:** `Info` metric: a constant 1 whose labels carry the metadata; shown as a table with the value column hidden.

### Model parameters · BLIP base

- `params`

  ```promql
  caption_model_parameters
  ```

**Logic:** Gauge set from sum(p.numel()) at model load.

### Model load time · cold start

- `load time`

  ```promql
  caption_model_load_seconds
  ```

**Logic:** Gauge set from a perf_counter() timer around model loading.

### Total uploaded · since app start

- `uploaded`

  ```promql
  sum(caption_uploaded_bytes_total)
  ```

**Logic:** Sum of the `caption_uploaded_bytes_total` counter.

### Scrape health · time to scrape each target

- `{{job}}`

  ```promql
  scrape_duration_seconds
  ```
- `app samples per scrape`

  ```promql
  scrape_samples_scraped{job="caption_app"}
  ```

**Logic:** `scrape_duration_seconds` and `scrape_samples_scraped` are synthetic series Prometheus records per target.

### Tokens generated · caption tokens per second, by mode

- `{{mode}}`

  ```promql
  sum by (mode) (rate(caption_tokens_generated_total[$__rate_interval]))
  ```

**Logic:** Per-second rate of the `caption_tokens_generated_total` counter, by mode.

## Annotation

- `Bulk batch running`: `sum(caption_active_requests{mode="bulk"}) > 0` is non-empty only while a ZIP batch is in progress; Grafana shades those intervals on every graph.
