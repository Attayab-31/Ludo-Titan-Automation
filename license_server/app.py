"""
License server for Ludo Titan Bot.
- GET /validate?key=XXX&machine_id=YYY  -> 200 = valid, 403 = invalid, 503 = paused by Kaptaan
- POST /add_key  body: {"secret": "...", "key": "..."}  -> add new key
- POST /add_machine body: {"secret": "...", "machine_id": "..."} -> add machine id
- POST /delete_machine body: {"secret": "...", "machine_id": "..."} -> delete machine id
- POST /admin/pause, POST /admin/resume  (body: {"secret": "..."})  -> master pause/resume everyone
- GET /admin  -> HTML page with Pause / Resume buttons (for Kaptaan)
"""
import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify, Response, session, redirect, url_for
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
def _require_env(name, allow_empty=False):
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"Missing required env var: {name}")
    if not allow_empty and not str(value).strip():
        raise RuntimeError(f"Empty required env var: {name}")
    return value


MONGO_URI = _require_env("MONGO_URI")
MONGO_DB_NAME = _require_env("MONGO_DB_NAME")
ADMIN_SECRET = _require_env("ADMIN_SECRET")
MASTER_LICENSE_KEY = _require_env("MASTER_LICENSE_KEY", allow_empty=True).strip()

PAUSED_MESSAGE = _require_env("PAUSED_MESSAGE")
DISCLAIMER_VERSION = _require_env("DISCLAIMER_VERSION")
DISCLAIMER_TEXT = _require_env("DISCLAIMER_TEXT", allow_empty=True)
PAYMENT_INSTRUCTIONS = _require_env("PAYMENT_INSTRUCTIONS")
PAYMENT_URL = _require_env("PAYMENT_URL", allow_empty=True)
HEADER_TEXT = _require_env("HEADER_TEXT", allow_empty=True)

# Session secret for admin UI
app.secret_key = _require_env("ADMIN_SESSION_SECRET")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
# Keep logs clean and avoid leaking sensitive config.
logging.getLogger("werkzeug").setLevel(logging.ERROR)
app.logger.setLevel(logging.INFO)


client = MongoClient(MONGO_URI)
db = client[MONGO_DB_NAME]
licenses_col = db["licenses"]
settings_col = db["settings"]
acceptances_col = db["acceptances"]


def init_db():
    settings_col.update_one(
        {"_id": "paused"},
        {"$setOnInsert": {"value": "0"}},
        upsert=True,
    )
    licenses_col.create_index([("machine_id", ASCENDING)], unique=True, sparse=True)
    licenses_col.create_index([("key", ASCENDING)], unique=True, sparse=True)
    acceptances_col.create_index(
        [("machine_id", ASCENDING), ("version", ASCENDING)],
        unique=True,
    )


def is_paused():
    row = settings_col.find_one({"_id": "paused"})
    return row and str(row.get("value")) == "1"


def set_paused(paused):
    settings_col.update_one(
        {"_id": "paused"},
        {"$set": {"value": "1" if paused else "0"}},
        upsert=True,
    )


def check_admin():
    data = request.get_json(silent=True) or {}
    secret = data.get("secret") or request.form.get("secret") or request.args.get("secret")
    return secret == ADMIN_SECRET


def is_admin_session():
    return session.get("admin_logged_in") is True


@app.route("/validate", methods=["GET"])
def validate():
    """Check machine ID: 200 = valid, 403 = invalid, 503 = paused by Kaptaan."""
    if is_paused():
        return jsonify({"paused": True, "message": PAUSED_MESSAGE}), 503

    machine_id = (request.args.get("machine_id") or "").strip()
    if not machine_id:
        return jsonify({"error": "Missing machine_id"}), 403

    row = licenses_col.find_one({"machine_id": machine_id})
    if not row:
        return jsonify({"error": "Unauthorized machine_id"}), 403

    return jsonify({"authorized": True, "message": "Machine ID is authorized."}), 200


@app.route("/add_machine", methods=["POST"])
def add_machine():
    """Add a new machine ID to the allowed list. Requires ADMIN_SECRET."""
    data = request.get_json(silent=True) or {}
    secret = data.get("secret") or request.form.get("secret")
    machine_id = (data.get("machine_id") or request.form.get("machine_id") or "").strip()

    if secret != ADMIN_SECRET:
        return jsonify({"error": "Invalid secret"}), 401
    if not machine_id:
        return jsonify({"error": "Missing machine_id"}), 400

    try:
        licenses_col.insert_one(
            {"machine_id": machine_id, "created_at": datetime.utcnow()}
        )
        return jsonify({"ok": True, "machine_id": machine_id}), 201
    except DuplicateKeyError:
        return jsonify({"error": "Machine ID already exists"}), 409


@app.route("/add_key", methods=["POST"])
def add_key():
    """Add a new license key. Requires ADMIN_SECRET."""
    data = request.get_json(silent=True) or {}
    secret = data.get("secret") or request.form.get("secret")
    key = (data.get("key") or request.form.get("key") or "").strip()

    if secret != ADMIN_SECRET:
        return jsonify({"error": "Invalid secret"}), 401
    if not key:
        return jsonify({"error": "Missing key"}), 400

    try:
        licenses_col.insert_one({"key": key, "created_at": datetime.utcnow()})
        return jsonify({"ok": True, "key": key}), 201
    except DuplicateKeyError:
        return jsonify({"error": "Key already exists"}), 409


@app.route("/disclaimer/status", methods=["GET"])
def disclaimer_status():
    """Check disclaimer acceptance status for a machine ID."""
    machine_id = (request.args.get("machine_id") or "").strip()
    if not machine_id:
        return jsonify({"error": "Missing machine_id"}), 400

    row = acceptances_col.find_one(
        {"machine_id": machine_id, "version": DISCLAIMER_VERSION},
        {"accepted_at": 1},
    )
    return jsonify(
        {
            "accepted": bool(row),
            "version": DISCLAIMER_VERSION,
            "text": DISCLAIMER_TEXT,
        }
    )


@app.route("/disclaimer/accept", methods=["POST"])
def disclaimer_accept():
    """Record disclaimer acceptance for a machine ID."""
    data = request.get_json(silent=True) or {}
    machine_id = (data.get("machine_id") or request.form.get("machine_id") or "").strip()
    version = (data.get("version") or request.form.get("version") or DISCLAIMER_VERSION).strip()

    if not machine_id:
        return jsonify({"error": "Missing machine_id"}), 400

    acceptances_col.update_one(
        {"machine_id": machine_id, "version": version},
        {"$set": {"accepted_at": datetime.utcnow()}},
        upsert=True,
    )

    return jsonify({"ok": True, "machine_id": machine_id, "version": version}), 200


@app.route("/payment_info", methods=["GET"])
def payment_info():
    """Return payment instructions configured via env."""
    return jsonify(
        {
            "instructions": PAYMENT_INSTRUCTIONS,
            "url": PAYMENT_URL,
        }
    )


@app.route("/ui_header", methods=["GET"])
def ui_header():
    """Return banner/header text configured via env."""
    raw = str(HEADER_TEXT or "")
    lines = [line for line in raw.replace("\\r", "").split("\\n") if line.strip()]
    return jsonify(
        {
            "text": HEADER_TEXT,
            "lines": lines,
        }
    )


@app.route("/delete_machine", methods=["POST"])
def delete_machine():
    """Delete a machine ID from the allowed list. Requires ADMIN_SECRET."""
    data = request.get_json(silent=True) or {}
    secret = data.get("secret") or request.form.get("secret")
    machine_id = (data.get("machine_id") or request.form.get("machine_id") or "").strip()

    if secret != ADMIN_SECRET:
        return jsonify({"error": "Invalid secret"}), 401
    if not machine_id:
        return jsonify({"error": "Missing machine_id"}), 400

    result = licenses_col.delete_one(
        {"$or": [{"machine_id": machine_id}, {"key": machine_id}]}
    )
    if result.deleted_count == 0:
        return jsonify({"error": "Machine ID not found"}), 404
    return jsonify({"ok": True, "deleted": machine_id}), 200


@app.route("/admin/pause", methods=["POST"])
def admin_pause():
    """Master only: pause usage for everyone. Users will see 'Kaptaan has stopped users from using'."""
    if not check_admin():
        return jsonify({"error": "Invalid secret"}), 401
    set_paused(True)
    return jsonify({"ok": True, "paused": True})


@app.route("/admin/resume", methods=["POST"])
def admin_resume():
    """Master only: resume usage for everyone."""
    if not check_admin():
        return jsonify({"error": "Invalid secret"}), 401
    set_paused(False)
    return jsonify({"ok": True, "paused": False})


@app.route("/admin/status", methods=["GET"])
def admin_status():
    """Public: check if paused (for admin page)."""
    return jsonify({"paused": is_paused()})


@app.route("/admin", methods=["GET", "POST"])
def admin_page():
    """Admin page with options to pause, resume, and manage Machine IDs."""
    if request.method == "POST":
        action = request.form.get("action")

        if action == "login":
            secret = request.form.get("secret", "").strip()
            if secret != ADMIN_SECRET:
                return _admin_login_html(error="Invalid admin secret!"), 401
            session["admin_logged_in"] = True
            return redirect(url_for("admin_page"))

        if action == "logout":
            session.pop("admin_logged_in", None)
            return redirect(url_for("admin_page"))

        if not is_admin_session():
            return _admin_login_html(error="Please log in first."), 401

        if action == "pause":
            set_paused(True)
            return redirect(url_for("admin_page"))
        if action == "resume":
            set_paused(False)
            return redirect(url_for("admin_page"))
        if action == "add_machine":
            machine_id = (request.form.get("machine_id") or "").strip()
            if not machine_id:
                return _admin_page_html(error="Machine ID is required!"), 400
            try:
                licenses_col.insert_one(
                    {"machine_id": machine_id, "created_at": datetime.utcnow()}
                )
                return redirect(url_for("admin_page"))
            except DuplicateKeyError:
                return _admin_page_html(error=f"Machine ID {machine_id} already exists."), 409
        if action == "delete_machine":
            machine_id = (request.form.get("machine_id") or "").strip()
            if not machine_id:
                return _admin_page_html(error="Machine ID is required!"), 400
            result = licenses_col.delete_one(
                {"$or": [{"machine_id": machine_id}, {"key": machine_id}]}
            )
            if result.deleted_count == 0:
                return _admin_page_html(error=f"Machine ID {machine_id} not found."), 404
            return redirect(url_for("admin_page"))

    if not is_admin_session():
        return _admin_login_html()

    return _admin_page_html()


def _admin_html(message="", error=""):
    status = "PAUSED" if is_paused() else "Running"
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Kaptaan – Pause / Resume</title>
<style>
  body {{ font-family: sans-serif; max-width: 400px; margin: 40px auto; padding: 20px; }}
  h1 {{ font-size: 1.3em; }}
  .status {{ padding: 8px; margin: 10px 0; border-radius: 6px; font-weight: bold; }}
  .paused {{ background: #fcc; color: #800; }}
  .running {{ background: #cfc; color: #080; }}
  input[type="password"] {{ width: 100%; padding: 8px; margin: 8px 0; box-sizing: border-box; }}
  button {{ padding: 10px 20px; margin: 4px; cursor: pointer; border-radius: 6px; border: 1px solid #888; }}
  button.pause {{ background: #faa; }}
  button.resume {{ background: #afa; }}
  .msg {{ margin: 10px 0; padding: 8px; border-radius: 6px; }}
  .ok {{ background: #dfd; }} .err {{ background: #fdd; }}
</style>
</head>
<body>
  <h1>Ludo Bot – Master control</h1>
  <p>Status: <span class="status {'paused' if is_paused() else 'running'}">{status}</span></p>
  <form method="post">
    <label>Admin password:</label>
    <input type="password" name="secret" placeholder="Your ADMIN_SECRET" required>
    <br>
    <button type="submit" name="action" value="pause" class="pause">Pause everyone</button>
    <button type="submit" name="action" value="resume" class="resume">Resume</button>
  </form>
  {f'<p class="msg ok">{message}</p>' if message else ''}
  {f'<p class="msg err">{error}</p>' if error else ''}
</body>
</html>"""


def _admin_login_html(error=""):
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Kaptaan - Admin Login</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 40px auto; padding: 24px; }}
  h1 {{ font-size: 1.4em; margin-bottom: 10px; }}
  .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 16px; }}
  input[type="password"] {{ width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; }}
  button {{ padding: 10px 16px; border-radius: 8px; border: 1px solid #888; cursor: pointer; }}
  .err {{ margin-top: 10px; padding: 10px; border-radius: 8px; background: #fdd; }}
</style>
</head>
<body>
  <h1>Ludo Bot Admin</h1>
  <div class="card">
    <form method="post">
      <input type="password" name="secret" placeholder="Admin Password" required>
      <button type="submit" name="action" value="login">Login</button>
    </form>
    {f'<div class="err">{error}</div>' if error else ''}
  </div>
</body>
</html>"""


def _admin_page_html(message="", error=""):
    status = "PAUSED" if is_paused() else "Running"
    rows = list(
        licenses_col.find({"machine_id": {"$ne": None}})
        .sort("created_at", -1)
    )

    machine_ids = [r.get("machine_id") for r in rows if r.get("machine_id")]
    accepted_map = {}
    if machine_ids:
        for doc in acceptances_col.find(
            {"version": DISCLAIMER_VERSION, "machine_id": {"$in": machine_ids}},
            {"machine_id": 1, "accepted_at": 1},
        ):
            accepted_map[doc.get("machine_id")] = doc.get("accepted_at")

    def _fmt_dt(value):
        if not value:
            return "-"
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return str(value)

    rows_html = ""
    for row in rows:
        mid = row.get("machine_id")
        created = _fmt_dt(row.get("created_at"))
        accepted_at = _fmt_dt(accepted_map.get(mid))
        rows_html += f"""
        <tr>
          <td class="mid">{mid}</td>
          <td>{created}</td>
          <td>{accepted_at or '-'}</td>
          <td>
            <div class="actions">
              <button type="button" class="copy" data-mid="{mid}">Copy</button>
              <form method="post" style="margin:0">
                <input type="hidden" name="machine_id" value="{mid}">
                <button type="submit" name="action" value="delete_machine" class="danger">Delete</button>
              </form>
            </div>
          </td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Kaptaan - Admin Panel</title>
<style>
  :root {{
    --bg: #f7f7fb;
    --card: #ffffff;
    --text: #111;
    --muted: #666;
    --accent: #0a7;
    --danger: #c33;
  }}
  body {{ font-family: system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }}
  .wrap {{ max-width: 900px; margin: 28px auto; padding: 16px; }}
  h1 {{ font-size: 1.6em; margin: 0 0 8px; }}
  .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .card {{ background: var(--card); border-radius: 12px; padding: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
  .status {{ padding: 6px 10px; border-radius: 999px; font-weight: 600; display: inline-block; }}
  .paused {{ background: #fee; color: #900; }}
  .running {{ background: #e9fff3; color: #075; }}
  button {{ padding: 8px 14px; border-radius: 8px; border: 1px solid #777; cursor: pointer; background: #fff; }}
  button.primary {{ background: #111; color: #fff; border-color: #111; }}
  button.danger {{ background: #ffecec; color: var(--danger); border-color: #f5b2b2; }}
  input[type="text"] {{ width: 100%; padding: 8px; margin: 8px 0; box-sizing: border-box; }}
  .topbar {{ display: flex; justify-content: space-between; align-items: center; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #eee; }}
  th {{ font-size: 0.9em; color: var(--muted); }}
  .actions {{ display: inline-flex; gap: 8px; }}
  .copy {{ border: 1px solid #99a; background: #f4f6ff; }}
  .toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
  #filter {{ width: 260px; padding: 8px; }}
  .msg {{ margin: 10px 0; padding: 10px; border-radius: 8px; }}
  .ok {{ background: #dfd; }} .err {{ background: #fdd; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>Ludo Bot Admin</h1>
        <div>Status: <span class="status {'paused' if is_paused() else 'running'}">{status}</span></div>
      </div>
      <form method="post">
        <button type="submit" name="action" value="logout">Logout</button>
      </form>
    </div>

    {f'<div class="msg ok">{message}</div>' if message else ''}
    {f'<div class="msg err">{error}</div>' if error else ''}

    <div class="row" style="margin-top:16px;">
      <div class="card">
        <h3>Server Control</h3>
        <form method="post" style="display:flex; gap:8px; margin-top:8px;">
          <button class="danger" type="submit" name="action" value="pause">Pause Everyone</button>
          <button class="primary" type="submit" name="action" value="resume">Resume</button>
        </form>
      </div>
      <div class="card">
        <h3>Add Machine ID</h3>
        <form method="post">
          <input type="text" name="machine_id" placeholder="Enter Machine ID" required>
          <button type="submit" name="action" value="add_machine">Add</button>
        </form>
      </div>
    </div>

    <div class="card" style="margin-top:16px;">
      <div class="toolbar">
        <h3>Authorized Machines ({len(rows)})</h3>
        <input type="text" id="filter" placeholder="Search machine ID">
      </div>
      <table>
        <thead>
          <tr><th>Machine ID</th><th>Created</th><th>Accepted</th><th>Action</th></tr>
        </thead>
        <tbody id="machineRows">
          {rows_html or '<tr><td colspan="3">No machines added yet.</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>
  <script>
    const filter = document.getElementById('filter');
    const rows = Array.from(document.querySelectorAll('#machineRows tr'));
    filter?.addEventListener('input', () => {{
      const q = filter.value.trim().toLowerCase();
      rows.forEach(r => {{
        const mid = r.querySelector('.mid')?.textContent?.toLowerCase() || '';
        r.style.display = !q || mid.includes(q) ? '' : 'none';
      }});
    }});
    document.querySelectorAll('button.copy').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const text = btn.getAttribute('data-mid') || '';
        try {{
          await navigator.clipboard.writeText(text);
          btn.textContent = 'Copied';
          setTimeout(() => btn.textContent = 'Copy', 1200);
        }} catch (e) {{
          const tmp = document.createElement('textarea');
          tmp.value = text;
          document.body.appendChild(tmp);
          tmp.select();
          document.execCommand('copy');
          document.body.removeChild(tmp);
          btn.textContent = 'Copied';
          setTimeout(() => btn.textContent = 'Copy', 1200);
        }}
      }});
    }});
  </script>
</body>
</html>"""


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "Ludo Bot License Server",
        "endpoints": [
            "GET /validate",
            "POST /add_key",
            "POST /add_machine",
            "POST /delete_machine",
            "GET /disclaimer/status",
            "POST /disclaimer/accept",
            "GET /payment_info",
            "GET/POST /admin (pause/resume/add/delete)",
        ],
    })


init_db()

if __name__ == "__main__":
    port = int(_require_env("PORT"))
    app.run(host="0.0.0.0", port=port)
