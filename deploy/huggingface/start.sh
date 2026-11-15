#!/bin/sh
# Starts Ollama, wakes Gemma up, the auto-update watcher, then the hospital.
# Hugging Face sets SPACE_HOST to the Space's address.

ollama serve > /tmp/ollama.log 2>&1 &
until ollama list > /dev/null 2>&1; do sleep 1; done

# The first answer loads Gemma into memory; doing it now keeps the first patient's questions quick.
ollama run "$MEDDY_OLLAMA_MODEL" "Say ready." > /dev/null 2>&1 &

# Rebuilds the Space when main on GitHub moves on (see watch.py). Its messages appear in the Space's logs.
python /home/user/space/watch.py &

# Lasting storage: the Space's bucket is mounted at /data. The database is copied there every few seconds and
# taken back at start, so a restart or a new version loses nothing (see hospital/db.py). Without it, every
# start of the Space begins with an empty hospital.
if [ -d /data ] && touch /data/.meddy-can-write 2>/dev/null; then
    export MEDDY_KEEP_DIR=/data
else
    echo "No writable /data: the database will not survive a restart."
fi

# The QR code on the entrance step points phones to this address.
if [ -n "$SPACE_HOST" ]; then
    export MEDDY_PUBLIC_URL="https://$SPACE_HOST/"
else
    export MEDDY_PUBLIC_URL="http://localhost:$MEDDY_PORT/"
fi

# The hospital runs in a loop, so that a new version of the code can be put in place and started in a few
# seconds (watch.py) instead of rebuilding the whole Space, which takes the site down for about five minutes.
# While /tmp/meddy-updating exists, watch.py is copying files: the server waits for it to finish.
trap 'kill "$(cat /tmp/meddy-server.pid 2>/dev/null)" 2>/dev/null; exit 0' TERM INT
rm -f /tmp/meddy-updating
while true; do
    while [ -e /tmp/meddy-updating ]; do sleep 0.2; done
    python hospital/server.py &
    echo $! > /tmp/meddy-server.pid
    wait $!
    echo "The hospital server stopped (code $?); starting it again."
    sleep 1
done
