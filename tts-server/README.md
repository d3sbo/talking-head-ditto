# XTTS v2 server

Small Flask server that clones a voice from a single reference WAV with Coqui XTTS v2 and returns speech for a block of text. The talking-head server calls it when the TTS engine is set to "XTTS v2".

- Splits text into sentences, generates each one, trims the tail artifact XTTS leaves at the end of a sentence, and joins them with a short silence.
- Put your own reference clip at `voice/sample.wav` (mounted to `/voice/sample.wav`). Voice samples are not included in this repo.
- `SILENCE_MS` and `TRIM_TAIL_MS` in `docker-compose.yml` tune the gaps and trimming.

```bash
docker compose up -d --build   # listens on port 5051
```

The Dockerfile pins PyTorch 2.3 and transformers 4.33 because newer versions break Coqui TTS 0.22.
