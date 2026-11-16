#!/bin/sh
# Starts Ollama, wakes Gemma up, the auto-update watcher, then the hospital.
# Hugging Face sets SPACE_HOST to the Space's address.

ollama serve > /tmp/ollama.log 2>&1 &
# Wait for Ollama, but not for ever: the hospital works without it (standard questions, no AI summary). This line
# used to wait until Ollama answered, so if Ollama did not start, neither did the hospital.
tries=0
until ollama list > /dev/null 2>&1 || [ "$tries" -ge 60 ]; do tries=$((tries + 1)); sleep 1; done
if [ "$tries" -ge 60 ]; then echo "Ollama did not start within a minute: the hospital starts without the language model."; fi

# The first answer loads Gemma into memory; doing it now keeps the first patient's questions quick.
ollama run "$MEDDY_OLLAMA_MODEL" "Say ready." > /dev/null 2>&1 &

# Before the hospital starts: catch up with main on GitHub. After a sleep the Space wakes on the code its image
# was built with, which can be days old; the hospital would run that old code for the first minutes of a visit.
python /home/user/space/watch.py --catch-up

# Then keeps the Space on main while it runs (see watch.py). Its messages appear in the Space's logs.
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
