#!/usr/bin/env python3
"""
TTS Server — XTTS v2 voice cloning edition
Listens on port 5051
Accepts text, generates speech using cloned voice sample
Returns WAV audio file

Splits text into sentences, generates each individually,
trims trailing audio artifacts, and joins with silence gaps.
"""

import os
import re
import logging
import tempfile
import numpy as np
import soundfile as sf
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

# ── Config ────────────────────────────────────────────────────────────────────
VOICE_SAMPLE   = os.environ.get("VOICE_SAMPLE", "/voice/sample.wav")
SERVER_PORT    = int(os.environ.get("PORT", "5051"))
LANGUAGE       = os.environ.get("LANGUAGE", "en")
SILENCE_MS     = int(os.environ.get("SILENCE_MS", "400"))   # silence between sentences
TRIM_TAIL_MS   = int(os.environ.get("TRIM_TAIL_MS", "200")) # trim from end of each sentence
SAMPLE_RATE    = 24000  # XTTS v2 output sample rate

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ── Load XTTS v2 model ────────────────────────────────────────────────────────
log.info("Loading XTTS v2 model — this takes 30-60 seconds...")
from TTS.api import TTS
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)
log.info("XTTS v2 model loaded ✅")


def split_sentences(text):
    """Split text into sentences by newlines, then by common punctuation."""
    chunks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Split on sentence-ending punctuation followed by space
        parts = re.split(r'(?<=[.!?])\s+', line)
        for part in parts:
            part = part.strip()
            if part:
                chunks.append(part)
    return chunks


def generate_silence(duration_ms):
    """Generate silence as a numpy array."""
    num_samples = int(SAMPLE_RATE * duration_ms / 1000)
    return np.zeros(num_samples, dtype=np.float32)


def trim_audio_tail(audio, trim_ms, sample_rate):
    """
    Trim trailing audio to remove XTTS end-of-sentence artifacts.
    Uses energy-based detection: finds where speech actually ends,
    then trims a buffer after that point.
    """
    if len(audio) == 0:
        return audio

    trim_samples = int(sample_rate * trim_ms / 1000)

    # Don't trim more than half the audio
    if trim_samples >= len(audio) // 2:
        return audio

    # Scan from the end using small windows to find where energy drops
    window_size = int(sample_rate * 0.02)  # 20ms windows
    threshold = 0.01  # energy threshold

    # Find the last point where energy is above threshold
    last_speech = len(audio)
    for i in range(len(audio) - window_size, max(len(audio) // 2, 0), -window_size):
        window = audio[i:i + window_size]
        rms = np.sqrt(np.mean(window ** 2))
        if rms > threshold:
            last_speech = i + window_size
            break

    # Trim: use the earlier of (energy-based end) or (fixed trim from end)
    fixed_trim_point = len(audio) - trim_samples
    trim_point = min(last_speech, fixed_trim_point)

    # Apply a short fade-out (10ms) to avoid clicks
    fade_samples = int(sample_rate * 0.01)
    trimmed = audio[:trim_point].copy()
    if len(trimmed) > fade_samples:
        fade = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        trimmed[-fade_samples:] *= fade

    return trimmed


def generate_audio_per_sentence(text, language):
    """Generate TTS audio sentence by sentence with silence gaps."""
    sentences = split_sentences(text)
    log.info(f"Text splitted to sentences.\n{sentences}")

    if not sentences:
        raise ValueError("No sentences to generate")

    audio_chunks = []
    silence = generate_silence(SILENCE_MS)

    for i, sentence in enumerate(sentences):
        if len(sentence.strip()) == 0:
            continue
        log.info(f"  Generating sentence {i+1}/{len(sentences)}: {sentence[:60]}...")
        try:
            wav = tts.tts(
                text=sentence,
                speaker_wav=VOICE_SAMPLE,
                language=language
            )
            audio = np.array(wav, dtype=np.float32)

            # Trim trailing artifacts
            audio = trim_audio_tail(audio, TRIM_TAIL_MS, SAMPLE_RATE)

            audio_chunks.append(audio)
            # Add silence between sentences (not after the last one)
            if i < len(sentences) - 1:
                audio_chunks.append(silence)
        except Exception as e:
            log.warning(f"  Skipping sentence {i+1} due to error: {e}")
            continue

    if not audio_chunks:
        raise ValueError("No audio generated from any sentence")

    # Concatenate all audio chunks
    full_audio = np.concatenate(audio_chunks)
    return full_audio


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    voice_ready = os.path.exists(VOICE_SAMPLE)
    return jsonify({
        "status": "ok",
        "service": "tts-server",
        "engine": "xtts-v2",
        "voice_sample": VOICE_SAMPLE,
        "voice_ready": voice_ready
    })


@app.route("/tts", methods=["POST"])
def generate_tts_endpoint():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    language = data.get("language", LANGUAGE)
    output_path = data.get("output_path", "/tmp/tts_out/output.wav")

    if not text:
        return jsonify({"error": "text is required"}), 400

    if not os.path.exists(VOICE_SAMPLE):
        return jsonify({
            "error": f"Voice sample not found at {VOICE_SAMPLE}. "
                     f"Mount a WAV file to /voice/sample.wav"
        }), 500

    log.info(f"Generating TTS ({len(text)} chars) with XTTS v2 (per-sentence)...")

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        full_audio = generate_audio_per_sentence(text, language)
        sf.write(output_path, full_audio, SAMPLE_RATE)

        log.info(f"TTS audio saved: {output_path}")
        return jsonify({
            "status": "success",
            "output_path": output_path,
            "chars": len(text)
        })

    except Exception as e:
        log.exception("TTS generation failed")
        return jsonify({"error": str(e)}), 500


@app.route("/tts/stream", methods=["POST"])
def generate_tts_stream():
    """Generate TTS and return the WAV file directly"""
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    language = data.get("language", LANGUAGE)

    if not text:
        return jsonify({"error": "text is required"}), 400

    if not os.path.exists(VOICE_SAMPLE):
        return jsonify({"error": f"Voice sample not found at {VOICE_SAMPLE}"}), 500

    try:
        full_audio = generate_audio_per_sentence(text, language)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            output_path = f.name
        sf.write(output_path, full_audio, SAMPLE_RATE)

        return send_file(output_path, mimetype="audio/wav", as_attachment=True,
                        download_name="output.wav")

    except Exception as e:
        log.exception("TTS stream failed")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    log.info(f"TTS Server (XTTS v2) starting on port {SERVER_PORT}")
    log.info(f"Voice sample: {VOICE_SAMPLE}")
    log.info(f"Language: {LANGUAGE}")
    log.info(f"Silence between sentences: {SILENCE_MS}ms")
    log.info(f"Tail trim per sentence: {TRIM_TAIL_MS}ms")
    voice_ready = os.path.exists(VOICE_SAMPLE)
    log.info(f"Voice sample ready: {voice_ready}")
    app.run(host="0.0.0.0", port=SERVER_PORT, threaded=False)
