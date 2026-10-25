"""CPU-only image captioning (Streamlit) with Prometheus metrics from metrics.py on :8000."""

import io
import os
import platform
import time
import uuid
import zipfile
from pathlib import PurePosixPath

import pandas as pd
import streamlit as st
import torch
import transformers
from PIL import Image
from transformers import BlipForConditionalGeneration, BlipProcessor

import metrics as m

MODEL_NAME = "Salesforce/blip-image-captioning-base"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
MAX_IMAGES_PER_ZIP = 100              # cap batch size so the CPU isn't swamped
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_NEW_TOKENS = 30

# Leave one core free so the Streamlit UI stays responsive during inference.
torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))


@st.cache_resource(show_spinner="Loading captioning model (first run downloads ~1 GB)...")
def load_model():
    t0 = time.perf_counter()
    processor = BlipProcessor.from_pretrained(MODEL_NAME)
    model = BlipForConditionalGeneration.from_pretrained(MODEL_NAME)
    model.eval()

    m.MODEL_LOAD_SECONDS.set(time.perf_counter() - t0)
    m.MODEL_PARAMETERS.set(sum(p.numel() for p in model.parameters()))
    m.TORCH_THREADS.set(torch.get_num_threads())
    m.MODEL_LOADED.set(1)
    m.APP_INFO.info({
        "model": MODEL_NAME,
        "device": "cpu",
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
        "python_version": platform.python_version(),
        "os": platform.system(),
    })
    return processor, model


def generate_caption(image: Image.Image, processor, model, mode: str) -> str:
    with m.INFERENCES_IN_PROGRESS.track_inprogress():
        t0 = time.perf_counter()
        inputs = processor(images=image, return_tensors="pt")
        m.PREPROCESS_SECONDS.labels(mode).observe(time.perf_counter() - t0)

        t1 = time.perf_counter()
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)
        m.INFERENCE_SECONDS.labels(mode).observe(time.perf_counter() - t1)

    caption = processor.decode(out[0], skip_special_tokens=True).strip()

    n_tokens = len(processor.tokenizer(caption, add_special_tokens=False).input_ids)
    m.CAPTION_TOKENS.labels(mode).observe(n_tokens)
    m.TOKENS_GENERATED.labels(mode).inc(n_tokens)
    m.CAPTION_CHARS.labels(mode).observe(len(caption))
    m.LAST_INFERENCE_TS.set_to_current_time()
    return caption


def browser_family(user_agent: str) -> str:
    ua = user_agent or ""
    # Order matters: Edge/Opera UAs also contain "Chrome"; Chrome UAs contain "Safari".
    if "Edg" in ua:
        return "edge"
    if "OPR" in ua or "Opera" in ua:
        return "opera"
    if "Firefox" in ua:
        return "firefox"
    if "Chrome" in ua:
        return "chrome"
    if "Safari" in ua:
        return "safari"
    return "other"


def get_client_meta() -> dict:
    ip, ua = None, ""
    try:
        ip = st.context.ip_address          # needs Streamlit >= 1.45
    except Exception:
        pass
    try:
        headers = st.context.headers
        forwarded = headers.get("X-Forwarded-For")
        if forwarded:                       # behind a proxy: first hop is the client
            ip = forwarded.split(",")[0].strip()
        ua = headers.get("User-Agent", "")
    except Exception:
        pass
    return {"client_ip": ip or "localhost", "browser": browser_family(ua)}


def client() -> dict:
    return st.session_state["client"]


def record_request(mode: str, status: str):
    c = client()
    m.REQUESTS.labels(mode, status, st.session_state["session_id"], c["client_ip"], c["browser"]).inc()


def record_image(mode: str, status: str):
    m.IMAGES_PROCESSED.labels(mode, status).inc()
    if status == "ok":
        m.CLIENT_IMAGES.labels(st.session_state["session_id"], client()["client_ip"], mode).inc()


def first_time(event_key) -> bool:
    """True only the first time an event is seen in this session (Streamlit reruns a lot)."""
    seen = st.session_state.setdefault("_seen_events", set())
    if event_key in seen:
        return False
    seen.add(event_key)
    return True


def load_image(data: bytes) -> tuple[Image.Image, float]:
    """Decode bytes into an RGB image; returns (image, decode_seconds)."""
    t0 = time.perf_counter()
    img = Image.open(io.BytesIO(data))
    img.load()
    img = img.convert("RGB")
    return img, time.perf_counter() - t0


def observe_image(mode: str, n_bytes: int, image: Image.Image, decode_s: float):
    m.IMAGE_BYTES.labels(mode).observe(n_bytes)
    m.DECODE_SECONDS.labels(mode).observe(decode_s)
    m.IMAGE_MEGAPIXELS.labels(mode).observe(image.width * image.height / 1e6)


def is_image_entry(info: zipfile.ZipInfo) -> bool:
    """True for image files; skips folders and hidden/junk entries."""
    if info.is_dir():
        return False
    path = PurePosixPath(info.filename)
    if any(part.startswith((".", "__MACOSX")) for part in path.parts):
        return False
    return path.suffix.lower() in IMAGE_EXTS


def is_junk_entry(info: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(info.filename)
    return info.is_dir() or any(part.startswith((".", "__MACOSX")) for part in path.parts)


def single_mode(processor, model):
    st.subheader("Single image")
    uploaded = st.file_uploader(
        "Upload an image",
        type=sorted(e.lstrip(".") for e in IMAGE_EXTS),
        key="single_uploader",
    )
    if uploaded is None:
        st.info("Upload a JPG / PNG / WEBP / BMP / GIF to get a caption.")
        return

    data = uploaded.getvalue()
    upload_key = ("single", uploaded.name, uploaded.size)
    if first_time(("upload",) + upload_key):
        m.UPLOADED_BYTES.labels("single").inc(len(data))

    try:
        image, decode_s = load_image(data)
    except Exception as e:
        if first_time(("decode_error",) + upload_key):
            m.ERRORS.labels("single", "decode", type(e).__name__).inc()
            record_image("single", "error")
            record_request("single", "error")
        st.error(f"Could not read this image: {e}")
        return

    st.image(image, width=450)

    if not st.button("Generate caption", type="primary"):
        return

    status, caption = "error", ""
    m.ACTIVE_REQUESTS.labels("single").inc()
    t0 = time.perf_counter()
    try:
        observe_image("single", len(data), image, decode_s)
        with st.spinner("Generating caption on CPU..."):
            caption = generate_caption(image, processor, model, "single")
        status = "ok"
    except Exception as e:
        m.ERRORS.labels("single", "inference", type(e).__name__).inc()
        st.error(f"Captioning failed: {type(e).__name__}: {e}")
    finally:
        elapsed = time.perf_counter() - t0
        m.ACTIVE_REQUESTS.labels("single").dec()
        m.REQUEST_SECONDS.labels("single").observe(elapsed)
        record_image("single", status)
        record_request("single", status)

    if status == "ok":
        st.success(caption)
        st.caption(f"Inference time: {elapsed:.2f} s")


def run_bulk(zf: zipfile.ZipFile, entries, processor, model) -> tuple[pd.DataFrame, float]:
    n = len(entries)
    m.BATCH_SIZE.observe(n)
    m.LAST_BATCH_SIZE.set(n)
    m.ACTIVE_REQUESTS.labels("bulk").inc()
    m.BULK_QUEUE_REMAINING.inc(n)
    remaining = n

    results = []
    progress = st.progress(0.0, text="Starting...")
    t_start = time.perf_counter()

    try:
        for idx, info in enumerate(entries, start=1):
            row = {"file": info.filename, "caption": "", "status": "ok", "latency_s": None}
            t_img = time.perf_counter()
            stage = "decode"
            try:
                if info.file_size > MAX_IMAGE_BYTES:
                    m.IMAGES_SKIPPED.labels("too_large").inc()
                    row["status"] = "skipped: >20 MB"
                else:
                    data = zf.read(info)
                    image, decode_s = load_image(data)
                    observe_image("bulk", len(data), image, decode_s)
                    stage = "inference"
                    t0 = time.perf_counter()
                    row["caption"] = generate_caption(image, processor, model, "bulk")
                    row["latency_s"] = round(time.perf_counter() - t0, 3)
            except Exception as e:  # one bad file must not kill the batch
                m.ERRORS.labels("bulk", stage, type(e).__name__).inc()
                row["status"] = f"error: {type(e).__name__}"

            m.BULK_IMAGE_SECONDS.observe(time.perf_counter() - t_img)
            outcome = "ok" if row["status"] == "ok" else ("skipped" if row["status"].startswith("skipped") else "error")
            record_image("bulk", outcome)

            results.append(row)
            m.BULK_QUEUE_REMAINING.dec()
            remaining -= 1
            progress.progress(idx / n, text=f"{idx}/{n} · {info.filename}")
    finally:
        # If the run is interrupted (user navigates away), keep gauges honest.
        if remaining:
            m.BULK_QUEUE_REMAINING.dec(remaining)
        m.ACTIVE_REQUESTS.labels("bulk").dec()

    total_s = time.perf_counter() - t_start
    m.REQUEST_SECONDS.labels("bulk").observe(total_s)
    m.LAST_BATCH_DURATION.set(total_s)

    progress.empty()
    df = pd.DataFrame(results)
    n_ok = int((df["status"] == "ok").sum())
    record_request("bulk", "ok" if n_ok == n else ("partial" if n_ok else "error"))
    return df, total_s


def bulk_mode(processor, model):
    st.subheader("Bulk (ZIP)")
    uploaded = st.file_uploader("Upload a ZIP of images", type=["zip"], key="bulk_uploader")
    if uploaded is None:
        st.info(f"Upload a .zip containing images (max {MAX_IMAGES_PER_ZIP} processed).")
        return

    data = uploaded.getvalue()
    upload_key = ("bulk", uploaded.name, uploaded.size)
    new_upload = first_time(("upload",) + upload_key)
    if new_upload:
        m.UPLOADED_BYTES.labels("bulk").inc(len(data))
        m.ZIP_BYTES.observe(len(data))

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        if new_upload:
            m.ZIP_UPLOADS.labels("invalid").inc()
            m.ERRORS.labels("bulk", "zip", type(e).__name__).inc()
            record_request("bulk", "error")
        st.error("That file is not a valid ZIP archive.")
        return

    with zf:
        all_entries = zf.infolist()
        entries = [i for i in all_entries if is_image_entry(i)]
        n_unsupported = sum(1 for i in all_entries if not is_junk_entry(i) and not is_image_entry(i))

        if not entries:
            if new_upload:
                m.ZIP_UPLOADS.labels("empty").inc()
                record_request("bulk", "error")
            st.warning("No supported images found in the ZIP.")
            return
        if new_upload:
            m.ZIP_UPLOADS.labels("valid").inc()

        n_over = max(0, len(entries) - MAX_IMAGES_PER_ZIP)
        if n_over:
            st.warning(f"ZIP has {len(entries)} images; only the first {MAX_IMAGES_PER_ZIP} will be processed.")
            entries = entries[:MAX_IMAGES_PER_ZIP]
        if n_unsupported:
            st.caption(f"Ignoring {n_unsupported} non-image file(s).")

        st.write(f"Found **{len(entries)}** image(s).")

        if st.button("Caption all", type="primary"):
            if n_unsupported:
                m.IMAGES_SKIPPED.labels("unsupported_file").inc(n_unsupported)
            if n_over:
                m.IMAGES_SKIPPED.labels("over_limit").inc(n_over)
            df, total_s = run_bulk(zf, entries, processor, model)
            # Persist across reruns (e.g. clicking the download button reruns the script)
            st.session_state["bulk_result"] = {"key": upload_key, "df": df, "total_s": total_s}

    result = st.session_state.get("bulk_result")
    if not result or result["key"] != upload_key:
        return

    df, total_s = result["df"], result["total_s"]
    n_ok = int((df["status"] == "ok").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Images", len(df))
    c2.metric("Captioned", n_ok)
    c3.metric("Failed / skipped", len(df) - n_ok)
    c4.metric("Total time", f"{total_s:.1f} s")

    st.dataframe(df, hide_index=True)
    st.download_button(
        "Download captions (CSV)",
        df.to_csv(index=False).encode("utf-8"),
        file_name="captions.csv",
        mime="text/csv",
    )


def main():
    st.set_page_config(page_title="CPU Image Captioning", page_icon="🖼️", layout="wide")

    metrics_ok = m.start_metrics_server()

    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex[:8]
        st.session_state["client"] = get_client_meta()
        c = st.session_state["client"]
        m.init_session_labels(st.session_state["session_id"], c["client_ip"], c["browser"])
        m.SESSIONS_STARTED.labels(c["client_ip"], c["browser"]).inc()

    st.title("CPU Image Captioning")
    st.caption(f"Model: `{MODEL_NAME}` · device: CPU")

    with st.sidebar:
        mode = st.radio("Mode", ["Single", "Bulk (ZIP)"])
        st.divider()
        st.caption(f"Session: `{st.session_state['session_id']}`")
        st.caption(f"Client: `{st.session_state['client']['client_ip']}` · {st.session_state['client']['browser']}")
        if metrics_ok:
            st.caption(f"Metrics: http://localhost:{m.METRICS_PORT}/metrics")
        else:
            st.warning(f"Metrics port {m.METRICS_PORT} is busy; /metrics not served.")

    processor, model = load_model()

    if mode == "Single":
        single_mode(processor, model)
    else:
        bulk_mode(processor, model)


if __name__ == "__main__":
    main()
