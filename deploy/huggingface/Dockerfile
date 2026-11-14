# Meddy on a Hugging Face Space: the hospital server, both trained models, Ollama with Gemma and the
# SQLite database, all in one container. The code itself is downloaded from GitHub's main branch while
# building, and watch.py rebuilds the Space by itself when main moves on.
FROM python:3.13-slim

# git: watch.py asks GitHub for the newest commit on main with git ls-remote.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates zstd git \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://ollama.com/install.sh | sh

# The same scikit-learn and numpy the models in models/ were saved with (models/model_info.json), so they
# are loaded as they are instead of being trained again at every start. huggingface_hub is for watch.py.
RUN pip install --no-cache-dir fastapi uvicorn pandas joblib numpy==2.5.4 scikit-learn==1.9.1 huggingface_hub

# Hugging Face runs the container as user 1000.
RUN useradd -m -u 1000 user
USER user
# The Space has two plain processors and no graphics card: the 1B Gemma answers in seconds there,
# the 4B one would often miss the time limit and the patient would get the standard questions.
ENV HOME=/home/user \
    OLLAMA_MODELS=/home/user/ollama \
    MEDDY_OLLAMA_MODEL=gemma3:1b \
    MEDDY_OLLAMA_TIMEOUT=45 \
    MEDDY_PORT=7860
WORKDIR /home/user/app

# Gemma is downloaded once, while the image is built, so the Space starts quickly.
RUN ollama serve > /tmp/ollama.log 2>&1 & \
    until ollama list > /dev/null 2>&1; do sleep 1; done; \
    ollama pull "$MEDDY_OLLAMA_MODEL"

# Docker checks this download at every build, so a new commit on main is always picked up.
# GitHub writes the commit into the download; watch.py compares it with the newest commit on main.
ADD --chown=user https://github.com/potlatiy/emergency-triage-ai/archive/refs/heads/main.tar.gz /tmp/meddy.tar.gz
RUN python -c "import tarfile; print(tarfile.open('/tmp/meddy.tar.gz').pax_headers.get('comment', ''))" \
        > /home/user/built_commit \
    && tar -xzf /tmp/meddy.tar.gz --strip-components=1 && rm /tmp/meddy.tar.gz

# The Space's own files, kept as they were built so watch.py can tell when main has newer ones.
COPY --chown=user . /home/user/space/
EXPOSE 7860
CMD ["sh", "/home/user/space/start.sh"]
