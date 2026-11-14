#!/bin/sh
# Starts Ollama, wakes Gemma up, the auto-update watcher, then the hospital.
# Hugging Face sets SPACE_HOST to the Space's address.

ollama serve > /tmp/ollama.log 2>&1 &
until ollama list > /dev/null 2>&1; do sleep 1; done

# The first answer loads Gemma into memory; doing it now keeps the first patient's questions quick.
ollama run "$MEDDY_OLLAMA_MODEL" "Say ready." > /dev/null 2>&1 &

# Rebuilds the Space when main on GitHub moves on (see watch.py). Its messages appear in the Space's logs.
python /home/user/space/watch.py &

# The QR code on the entrance step points phones to this address.
if [ -n "$SPACE_HOST" ]; then
    export MEDDY_PUBLIC_URL="https://$SPACE_HOST/"
else
    export MEDDY_PUBLIC_URL="http://localhost:$MEDDY_PORT/"
fi

exec python hospital/server.py
