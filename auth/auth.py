import os
import tempfile
from pathlib import Path

from flask import Flask, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
DATA_DIR = Path("/data")

# sish auth payloads are tiny; anything bigger is not sish.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


def read_key(user_file):
    try:
        return user_file.read_text().strip()
    except FileNotFoundError:
        return None


@app.route("/", methods=["POST"])
def auth():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return "Invalid JSON", 400

    username = data.get("user")
    key = data.get("auth_key")
    remote = data.get("remote_addr")
    remote = remote if isinstance(remote, str) else "?"

    if not isinstance(username, str) or not isinstance(key, str) or not username or not key:
        return "Missing data", 403

    # Reject any name the sanitizer would have to touch: distinct raw
    # usernames must never collapse onto one key file ("vm a" vs "vm_a"
    # both sanitize to vm_a). VM usernames are machine-generated hostnames
    # and always pass unchanged; !r keeps the rejected raw value from
    # injecting into the log line.
    if secure_filename(username) != username:
        print(f"auth: {username!r} denied (unsafe username) from {remote}")
        return "Forbidden", 403

    user_file = DATA_DIR / username

    stored_key = read_key(user_file)
    if stored_key is not None:
        if stored_key == key:
            print(f"auth: {username} allowed from {remote}")
            return "OK", 200
        print(f"auth: {username} denied (key mismatch) from {remote}")
        return "Forbidden", 403

    # First-use registration, atomically: write the key to a temp file and
    # hard-link it into place, so the key file appears fully written or not
    # at all. A create-then-write would expose an empty file to concurrent
    # workers — or leave one behind on a crash, permanently locking the
    # username out. Usernames can't start with "." (secure_filename strips
    # leading dots), so the temp prefix can never collide with a user file.
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.link(tmp_path, user_file)
    except FileExistsError:
        # Lost the race. If the winner stored the same key (same VM
        # connecting twice at boot), it's still an allow.
        if read_key(user_file) == key:
            print(f"auth: {username} allowed from {remote}")
            return "OK", 200
        print(f"auth: {username} denied (race) from {remote}")
        return "Forbidden", 403
    finally:
        os.unlink(tmp_path)

    print(f"auth: {username} registered from {remote}")
    return "OK", 200


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    app.run(host="0.0.0.0", port=5000)
