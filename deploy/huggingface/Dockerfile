# Meddy on a Hugging Face Space: the hospital server, both trained models, Ollama with Gemma and the
# SQLite database, all in one container. The code itself is downloaded from GitHub's main branch, so a
# "Factory rebuild" in the Space settings is all it takes to put the latest main online.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates zstd \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://ollama.com/install.sh | sh

# The same scikit-learn and numpy the models in models/ were saved with (models/model_info.json), so they
# are loaded as they are instead of being trained again at every start.
RUN pip install --no-cache-dir fastapi uvicorn pandas joblib numpy==2.5.4 scikit-learn==1.9.1

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
ADD --chown=user https://github.com/potlatiy/emergency-triage-ai/archive/refs/heads/main.tar.gz /tmp/meddy.tar.gz
RUN tar -xzf /tmp/meddy.tar.gz --strip-components=1 && rm /tmp/meddy.tar.gz

COPY --chown=user start.sh /home/user/start.sh
EXPOSE 7860
CMD ["sh", "/home/user/start.sh"]
