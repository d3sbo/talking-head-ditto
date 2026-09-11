#!/usr/bin/env python3
"""
Talking Head Server — Ditto TensorRT edition
Listens on port 5050
Accepts daily summary text, generates TTS + Ditto talking head video
with natural head movement, saves to output folder, sends HA notification.

Supports two TTS engines:
  - XTTS v2 (port 5051) — custom per-sentence generation
  - Voicebox (port 17493) — multi-engine voice studio with auto-chunking
"""

import os
import sys
import time
import subprocess
import shutil
import logging
import requests
import urllib3
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config from environment ──────────────────────────────────────────────────
HA_URL          = os.environ.get("HA_URL",            "http://homeassistant.local:8123")
HA_TOKEN        = os.environ.get("HA_TOKEN",          "")
HA_NOTIFY_SVC   = os.environ.get("HA_NOTIFY_SERVICE", "notify.mobile_app_your_phone")
AVATAR_PATH     = os.environ.get("AVATAR_PATH",       "/app/avatar.jpg")
OUTPUT_DIR      = os.environ.get("OUTPUT_DIR",        "/output")
TTS_VOICE       = os.environ.get("TTS_VOICE",         "en-GB-RyanNeural")
DITTO_DIR       = os.environ.get("DITTO_DIR",         "/app/ditto")
SERVER_PORT     = int(os.environ.get("PORT",          "5050"))

# TTS server URLs
XTTS_URL        = os.environ.get("XTTS_URL",          "http://localhost:5051")
VOICEBOX_URL    = os.environ.get("VOICEBOX_URL",      "http://localhost:17493")
VOICEBOX_PROFILE = os.environ.get("VOICEBOX_PROFILE", "Keira")

# Ditto TensorRT model paths
DITTO_DATA_ROOT = os.path.join(DITTO_DIR, "checkpoints", "ditto_trt_Ampere_Plus")
DITTO_CFG_PKL   = os.path.join(DITTO_DIR, "checkpoints", "ditto_cfg", "v0.4_hubert_cfg_trt.pkl")

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


# ── TTS via XTTS v2 server ──────────────────────────────────────────────────
def generate_tts_xtts(text: str, output_path: str):
    """Call XTTS v2 server to generate audio from text."""
    log.info(f"[XTTS v2] Generating TTS for {len(text)} chars...")
    start = time.time()
    r = requests.post(
        f"{XTTS_URL}/tts/stream",
        json={"text": text},
        timeout=600,
        stream=True
    )
    r.raise_for_status()
    total = 0
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total += len(chunk)
    elapsed = int(time.time() - start)
    log.info(f"[XTTS v2] Audio saved: {output_path} ({total} bytes in {elapsed}s)")


# ── TTS via Voicebox ─────────────────────────────────────────────────────────
def get_voicebox_profile_id(profile_name: str) -> str:
    """Look up Voicebox profile ID by name."""
    try:
        r = requests.get(f"{VOICEBOX_URL}/profiles", timeout=10)
        r.raise_for_status()
        profiles = r.json()
        for p in profiles:
            if p.get("name", "").lower() == profile_name.lower():
                return p["id"]
        raise ValueError(f"Voicebox profile '{profile_name}' not found. "
                        f"Available: {[p.get('name') for p in profiles]}")
    except requests.RequestException as e:
        raise RuntimeError(f"Cannot reach Voicebox at {VOICEBOX_URL}: {e}")


def generate_tts_voicebox(text: str, output_path: str, profile_override: str = None,
                          engine: str = None, max_chunk_chars: int = None):
    """Call Voicebox /generate/stream to get WAV audio directly."""
    profile_name = profile_override or VOICEBOX_PROFILE
    log.info(f"[Voicebox] Generating TTS for {len(text)} chars with profile '{profile_name}'...")
    start = time.time()

    # Get profile ID
    profile_id = get_voicebox_profile_id(profile_name)
    log.info(f"[Voicebox] Using profile: {profile_name} (id: {profile_id})")

    _vb_engine = engine or os.environ.get("VOICEBOX_ENGINE", "qwen")
    log.info(f"[Voicebox] Engine: {_vb_engine}")

    # Stream audio directly — no polling needed
    r = requests.post(
        f"{VOICEBOX_URL}/generate/stream",
        json={
            "profile_id": profile_id,
            "text": text,
            "language": "en",
            # Engine: "qwen" (Base clone, default) or "tada" (Hume TADA 1B,
            # stronger prosody, clones from the same reference sample).
            "engine": _vb_engine,
            # Model sizes are engine-specific: qwen uses 1.7B/0.6B, tada uses
            # 1B/3B. Sending the wrong one matches no config and fails.
            "model_size": os.environ.get("VOICEBOX_MODEL_SIZE")
                          or ("1B" if _vb_engine == "tada" else "1.7B"),
            # Chunking resets prosody at every boundary. 2000 puts a typical
            # daily summary through in one pass — one continuous read.
            "max_chunk_chars": int(max_chunk_chars
                                   or os.environ.get("VOICEBOX_MAX_CHUNK", 800)),
        },
        timeout=600,
        stream=True
    )
    r.raise_for_status()

    total = 0
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total += len(chunk)

    elapsed = int(time.time() - start)
    log.info(f"[Voicebox] Audio saved: {output_path} ({total} bytes in {elapsed}s)")


# ── TTS router ───────────────────────────────────────────────────────────────
def generate_tts(text: str, output_path: str, tts_engine: str = "XTTS v2", voicebox_profile: str = None,
                 voicebox_engine: str = None, max_chunk_chars: int = None):
    """Route TTS generation to the selected engine."""
    if tts_engine == "Voicebox":
        generate_tts_voicebox(text, output_path, voicebox_profile,
                              voicebox_engine, max_chunk_chars)
    else:
        generate_tts_xtts(text, output_path)


# ── Ditto ────────────────────────────────────────────────────────────────────
def run_ditto(audio_path: str, avatar_path: str, output_dir: str) -> str:
    """Run Ditto TensorRT inference and return the output filename."""
    tmp_out = "/tmp/ditto_out/result.mp4"
    os.makedirs("/tmp/ditto_out", exist_ok=True)

    if os.path.exists(tmp_out):
        os.remove(tmp_out)

    cmd = [
        sys.executable, "inference.py",
        "--data_root", DITTO_DATA_ROOT,
        "--cfg_pkl",   DITTO_CFG_PKL,
        "--audio_path", audio_path,
        "--source_path", avatar_path,
        "--output_path", tmp_out,
    ]

    log.info(f"Ditto starting — this takes 3-8 minutes for long summaries...")
    log.info(f"Running: {' '.join(cmd)}")

    import threading
    process = subprocess.Popen(
        cmd,
        cwd=DITTO_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    def stream_output(proc):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log.info(f"[ditto] {line}")

    t = threading.Thread(target=stream_output, args=(process,), daemon=True)
    t.start()

    start_time = time.time()
    timeout = 600
    while process.poll() is None:
        elapsed = int(time.time() - start_time)
        if elapsed % 30 == 0 and elapsed > 0:
            log.info(f"[ditto] Still running... {elapsed}s elapsed")
        if elapsed > timeout:
            process.kill()
            raise RuntimeError(f"Ditto timed out after {timeout}s")
        time.sleep(1)

    t.join(timeout=5)
    returncode = process.returncode
    elapsed = int(time.time() - start_time)

    if returncode != 0:
        if not os.path.exists(tmp_out):
            raise RuntimeError(f"Ditto failed (exit {returncode}) after {elapsed}s")
        log.warning(f"Ditto exited non-zero ({returncode}) but video was generated — continuing")
    else:
        log.info(f"Ditto completed successfully in {elapsed}s")

    if not os.path.exists(tmp_out):
        raise RuntimeError("Ditto produced no output video")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_filename = f"daily_summary_{timestamp}.mp4"
    dest_path = os.path.join(output_dir, dest_filename)
    shutil.copy2(tmp_out, dest_path)
    log.info(f"Video saved: {dest_path}")
    return dest_filename


# ── HA Notification ──────────────────────────────────────────────────────────
def send_ha_notification(filename: str):
    date_str = datetime.now().strftime("%d %b %Y %H:%M")
    media_url = f"/media/local/AI-Assistant/{filename}"
    video_url = f"{HA_URL}{media_url}"

    push_payload = {
        "title": f"📹 Daily Summary — {date_str}",
        "message": "Your talking head video is ready!",
        "data": {
            "clickAction": "homeassistant://navigate/media-browser",
            "push": {"sound": "default"}
        }
    }
    svc_parts = HA_NOTIFY_SVC.split(".", 1)
    endpoint = f"{HA_URL}/api/services/{svc_parts[0]}/{svc_parts[1]}"
    r = requests.post(
        endpoint,
        json=push_payload,
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
        verify=False,
        timeout=15
    )
    r.raise_for_status()

    persist_payload = {
        "title": f"📹 Daily Summary — {date_str}",
        "message": f"New video ready: **{filename}**\n\nGo to Media → Local Media → AI-Assistant to watch.",
        "notification_id": "daily_summary_latest"
    }
    requests.post(
        f"{HA_URL}/api/services/persistent_notification/create",
        json=persist_payload,
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
        verify=False,
        timeout=15
    )

    requests.post(
        f"{HA_URL}/api/services/input_text/set_value",
        json={"entity_id": "input_text.latest_daily_summary", "value": filename},
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
        verify=False,
        timeout=15
    )

    requests.post(
        f"{HA_URL}/api/events/talking_head_complete",
        json={"filename": filename, "video_url": video_url},
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
        verify=False,
        timeout=15
    )

    log.info(f"Notification sent via {HA_NOTIFY_SVC}: {video_url}")
    return video_url


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "talking-head-server",
        "engine": "ditto-trt",
        "tts_engines": ["XTTS v2", "Voicebox"],
        "voicebox_url": VOICEBOX_URL,
        "xtts_url": XTTS_URL
    })


@app.route("/video/<filename>", methods=["GET"])
def serve_video(filename):
    from flask import send_from_directory
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    summary_text = data.get("summary_text", "").strip()
    tts_engine = data.get("tts_engine", "XTTS v2")
    voicebox_profile = data.get("voicebox_profile", None)
    voice = data.get("voice", TTS_VOICE)

    if not summary_text:
        return jsonify({"error": "summary_text is required"}), 400

    log.info(f"Generating talking head video ({len(summary_text)} chars, TTS: {tts_engine})")

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        # 1. Generate TTS audio
        audio_path = "/tmp/tts_audio.wav"
        generate_tts(summary_text, audio_path, tts_engine, voicebox_profile,
                     data.get("voicebox_engine"), data.get("max_chunk_chars"))

        # 1a. Pacing. The Qwen Base model ignores `instruct` and inherits its
        #     speed from the reference clip, which resists being slowed down.
        #     A pitch-preserving time-stretch of the finished audio is the
        #     reliable lever — and Ditto lip-syncs to whatever it is handed,
        #     so the mouth follows the slower audio automatically.
        #     Per-request `tempo` wins; otherwise TTS_TEMPO; otherwise 1.0.
        tempo_var = "VOICEBOX_TEMPO" if tts_engine == "Voicebox" else "XTTS_TEMPO"
        try:
            tempo = float(data.get("tempo") or os.environ.get(tempo_var, 1.0))
        except (TypeError, ValueError):
            tempo = 1.0
        if abs(tempo - 1.0) > 0.001:
            tempo = max(0.5, min(2.0, tempo))   # single-pass atempo range
            stretched = "/tmp/tts_audio_tempo.wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", audio_path,
                     "-filter:a", f"atempo={tempo}", stretched],
                    check=True, timeout=120,
                )
                shutil.move(stretched, audio_path)
                log.info(f"[TTS] Applied atempo={tempo} from {tempo_var} (slower audio for Ditto)")
            except Exception as e:
                log.warning(f"[TTS] atempo={tempo} failed, using original audio: {e}")

        # 1b. Release the TTS model's VRAM before Ditto claims the card.
        #     Qwen 1.7B holds ~5.5GB; Ditto TRT then loads its own engines on
        #     the same 12GB GPU. Best-effort — a failure here must not abort
        #     the run, since the audio is already generated.
        if tts_engine == "Voicebox":
            try:
                r = requests.post(f"{VOICEBOX_URL}/models/unload", timeout=30)
                log.info(f"[Voicebox] TTS model unloaded before Ditto (HTTP {r.status_code})")
            except Exception as e:
                log.warning(f"[Voicebox] Unload failed, continuing anyway: {e}")
            time.sleep(2)   # let the CUDA allocator actually return the memory

        # 2. Run Ditto TensorRT inference
        filename = run_ditto(audio_path, AVATAR_PATH, OUTPUT_DIR)

        # 3. Send HA notification
        video_url = send_ha_notification(filename)

        return jsonify({
            "status": "success",
            "filename": filename,
            "video_url": video_url,
            "tts_engine": tts_engine
        })

    except Exception as e:
        log.exception("Generation failed")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    log.info(f"Talking Head Server (Ditto TRT) starting on port {SERVER_PORT}")
    log.info(f"Avatar:          {AVATAR_PATH}")
    log.info(f"Output:          {OUTPUT_DIR}")
    log.info(f"TTS Engines:     XTTS v2 ({XTTS_URL}), Voicebox ({VOICEBOX_URL})")
    log.info(f"Voicebox Profile: {VOICEBOX_PROFILE}")
    log.info(f"HA URL:          {HA_URL}")
    log.info(f"HA Notify:       {HA_NOTIFY_SVC}")
    log.info(f"HA Token:        {'set' if HA_TOKEN else 'NOT SET — notifications will fail'}")
    log.info(f"Ditto:           {DITTO_DATA_ROOT}")
    app.run(host="0.0.0.0", port=SERVER_PORT, threaded=False)
