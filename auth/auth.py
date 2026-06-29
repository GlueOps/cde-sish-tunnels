from pathlib import Path

from flask import Flask, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
DATA_DIR = Path("/data")


@app.route("/", methods=["POST"])
def auth():
    if not request.is_json:
        return "Invalid JSON", 400

    data = request.json
    username = secure_filename(data.get("user", ""))
    key = data.get("auth_key")

    if not username or not key:
        return "Missing data", 403

    user_file = DATA_DIR / username

    if user_file.exists():
        stored_key = user_file.read_text().strip()
        if stored_key == key:
            print(f"auth: {username} allowed")
            return "OK", 200
        print(f"auth: {username} denied (key mismatch)")
        return "Forbidden", 403

    try:
        with open(user_file, "x") as f:
            f.write(key)
        print(f"auth: {username} registered")
        return "OK", 200
    except FileExistsError:
        print(f"auth: {username} denied (race)")
        return "Forbidden", 403


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    app.run(host="0.0.0.0", port=5000)