"""Put the latest main online. Run by .github/workflows/deploy-huggingface.yml after every push to main.

1. The Space gets this folder's Dockerfile, start.sh and README, so a change to them reaches the Space too.
2. The Space is rebuilt from scratch ("factory rebuild"). Building downloads main from GitHub, so the new
   code is what goes online, not a copy kept in the Space.
"""

import os
import sys
from pathlib import Path

from huggingface_hub import HfApi

SPACE = "darkshdws/meddy"
FOLDER = Path(__file__).parent
SPACE_FILES = ["Dockerfile", "start.sh", "README.md"]

token = os.environ.get("HF_TOKEN")
if not token:
    # A fork, or the secret is not set yet: say so instead of failing every push.
    print("HF_TOKEN is not set, so the Space was not updated. Add it under Settings → Secrets → Actions.")
    sys.exit(0)

api = HfApi(token=token)
# Unchanged files make no commit, so this is quick when only the code on GitHub changed.
api.upload_folder(folder_path=FOLDER, repo_id=SPACE, repo_type="space", allow_patterns=SPACE_FILES,
                  commit_message=f"Deploy {os.environ.get('GITHUB_SHA', 'main')[:7]} from GitHub")
api.restart_space(SPACE, factory_reboot=True)
print(f"Rebuilding https://huggingface.co/spaces/{SPACE} with the latest main. It is online again in a few minutes.")
