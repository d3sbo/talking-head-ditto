FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pin PyTorch 2.3 (last version before weights_only=True default broke TTS)
RUN pip install --no-cache-dir \
    torch==2.3.1 torchvision==0.18.1 torchaudio==2.3.1 \
    --index-url https://download.pytorch.org/whl/cu118

# Pin transformers before BeamSearchScorer was removed (4.40+)
RUN pip install --no-cache-dir \
    transformers==4.33.0 \
    TTS==0.22.0 \
    flask>=2.3.0 \
    flask-cors>=4.0.0 \
    numpy==1.26.4 \
    soundfile \
    pydub

COPY server/tts_server.py /app/tts_server.py

RUN mkdir -p /voice /tmp/tts_out

EXPOSE 5051

CMD ["python", "tts_server.py"]
