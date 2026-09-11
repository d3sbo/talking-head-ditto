#!/usr/bin/env python3
"""
Talking Head Server — Ditto TensorRT edition
Listens on port 5050
Accepts daily summary text, generates TTS + Ditto talking head video
with natural head movement, saves to output folder, sends HA notification.
"""

import os
import sys
import asyncio
import subprocess
import shutil
import glob
import logging
import requests
import urllib3
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
# edge_tts removed — using XTTS v2 server

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

# Ditto TensorRT model paths (Ampere+ pre-built engines)
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


# ── TTS via XTTS v2 server ────────────────────────────────────────────────────
XTTS_URL = os.environ.get("XTTS_URL", "http://localhost:5051")

def generate_tts(text: str, output_path: str, voice: str = None):
    """Call XTTS v2 server to generate audio from text."""
    log.info(f"Calling XTTS v2 server for {len(text)} chars...")
    r = requests.post(
        f"{XTTS_URL}/tts",
        json={"text": text, "output_path": output_path},
        timeout=120
    )
    r.raise_for_status()
    log.info(f"TTS audio saved: {output_path}")


# ── Ditto ────────────────────────────────────────────────────────────────────
def run_ditto(audio_path: str, avatar_path: str, output_dir: str) -> str:
    """Run Ditto TensorRT inference and return the output filename."""
    tmp_out = "/tmp/ditto_out/result.mp4"
    os.makedirs("/tmp/ditto_out", exist_ok=True)

    # Remove previous output
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

    log.info(f"Running Ditto: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=DITTO_DIR,
        capture_output=True,
        text=True,
        timeout=300
    )

    if result.returncode != 0:
        # Check if video was generated despite non-zero exit
        if not os.path.exists(tmp_out):
            log.error(f"Ditto stderr:\n{result.stderr}")
            raise RuntimeError(f"Ditto failed (exit {result.returncode}): {result.stderr[-500:]}")
        log.warning("Ditto exited non-zero but video was generated — continuing")

    if not os.path.exists(tmp_out):
        raise RuntimeError("Ditto produced no output video")

    # Copy to output dir with timestamped name
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

    # 1. Mobile push notification — opens media browser in HA app
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

    # 2. Persistent notification inside HA
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

    # 3. Update HA input_text helper with latest filename for Lovelace card
    requests.post(
        f"{HA_URL}/api/services/input_text/set_value",
        json={"entity_id": "input_text.latest_daily_summary", "value": filename},
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
        verify=False,
        timeout=15
    )

    log.info(f"Notification sent via {HA_NOTIFY_SVC}: {video_url}")
    return video_url


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "talking-head-server", "engine": "ditto-trt"})


@app.route("/video/<filename>", methods=["GET"])
def serve_video(filename):
    from flask import send_from_directory
    return send_from_directory(OUTPUT_DIR, filename)



@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    summary_text = data.get("summary_text", "").strip()
    voice = data.get("voice", TTS_VOICE)

    if not summary_text:
        return jsonify({"error": "summary_text is required"}), 400

    log.info(f"Generating talking head video ({len(summary_text)} chars)")

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        # 1. Generate TTS audio
        audio_path = "/tmp/tts_audio.wav"
        generate_tts(summary_text, audio_path)

        # 2. Run Ditto TensorRT inference
        filename = run_ditto(audio_path, AVATAR_PATH, OUTPUT_DIR)

        # 3. Send HA notification
        video_url = send_ha_notification(filename)

        return jsonify({
            "status": "success",
            "filename": filename,
            "video_url": video_url
        })

    except Exception as e:
        log.exception("Generation failed")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    log.info(f"Talking Head Server (Ditto TRT) starting on port {SERVER_PORT}")
    log.info(f"Avatar:    {AVATAR_PATH}")
    log.info(f"Output:    {OUTPUT_DIR}")
    log.info(f"TTS Voice: {TTS_VOICE}")
    log.info(f"HA URL:    {HA_URL}")
    log.info(f"HA Notify: {HA_NOTIFY_SVC}")
    log.info(f"HA Token:  {'set' if HA_TOKEN else 'NOT SET — notifications will fail'}")
    log.info(f"Ditto:     {DITTO_DATA_ROOT}")
    app.run(host="0.0.0.0", port=SERVER_PORT, threaded=False)