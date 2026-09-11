FROM nvcr.io/nvidia/tensorrt:23.10-py3

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y \
    git \
    git-lfs \
    wget \
    curl \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgles2-mesa \
    libegl1-mesa \
    && git lfs install \
    && rm -rf /var/lib/apt/lists/*

# ── Replace system ffmpeg with static ffmpeg 7.x ─────────────────────────────
RUN curl -sL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    | tar xJ --wildcards --strip-components=1 -C /usr/local/bin '*/ffmpeg' '*/ffprobe'

WORKDIR /app

# Clone Ditto
RUN git clone https://github.com/antgroup/ditto-talkinghead.git /app/ditto

WORKDIR /app/ditto

# Patch Python 3.8 type union syntax and numpy 1.x atan2 compatibility
RUN sed -i '1s/^/from __future__ import annotations\n/' inference.py && \
    find /app/ditto -name "*.py" -exec sed -i 's/np\.atan2/np.arctan2/g' {} \;

# Install PyTorch (CUDA 11.8)
RUN pip install --no-cache-dir \
    torch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0 \
    --index-url https://download.pytorch.org/whl/cu118

# Install Ditto dependencies
RUN pip install --no-cache-dir \
    tensorrt==8.6.1 \
    librosa \
    tqdm \
    filetype \
    imageio \
    opencv-python-headless \
    scikit-image \
    cython \
    "cuda-python==12.1.0" \
    imageio-ffmpeg \
    colored \
    polygraphy \
    "numpy==1.26.4" \
    onnxruntime \
    mediapipe \
    insightface \
    transformers \
    einops \
    timm

# Install server dependencies
RUN pip install --no-cache-dir \
    flask>=2.3.0 \
    flask-cors>=4.0.0 \
    edge-tts>=6.1.9 \
    requests>=2.31.0 \
    urllib3>=2.0.0 \
    pyyaml \
    huggingface_hub

# ── Download Ditto checkpoints ────────────────────────────────────────────────
# Uses pre-built TensorRT engines for Ampere+ GPUs (RTX 3080 = Ampere ✅)
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download(repo_id='digital-avatar/ditto-talkinghead', local_dir='checkpoints')"

# ── Copy server files ─────────────────────────────────────────────────────────
COPY server/talking_head_server.py /app/talking_head_server.py
COPY server/avatar.jpg /app/avatar.jpg

RUN mkdir -p /output /tmp/ditto_out

WORKDIR /app

EXPOSE 5050

CMD ["python", "talking_head_server.py"]
