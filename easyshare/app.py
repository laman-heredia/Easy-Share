import hmac
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, send_file,
    session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


def create_app(test_config=None):
    app = Flask(__name__)
    data_dir = Path(os.environ.get("EASYSHARE_DATA_DIR", "/var/lib/easyshare"))
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("EASYSHARE_SECRET_KEY"),
        PASSWORD=os.environ.get("EASYSHARE_PASSWORD"),
        DATA_DIR=data_dir,
        DATABASE=data_dir / "easyshare.db",
        UPLOAD_DIR=data_dir / "uploads",
        MAX_CONTENT_LENGTH=int(os.environ.get("EASYSHARE_MAX_UPLOAD_MB", "1024")) * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("EASYSHARE_COOKIE_SECURE", "1") == "1",
        PERMANENT_SESSION_LIFETIME=3600 * 12,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("EASYSHARE_SECRET_KEY must be set")
    if not app.config["PASSWORD"]:
        raise RuntimeError("EASYSHARE_PASSWORD must be set")

    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True, mode=0o750)
    Path(app.config["DATA_DIR"]).mkdir(parents=True, exist_ok=True, mode=0o750)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
        return g.db

    with app.app_context():
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                stored_name TEXT NOT NULL UNIQUE,
                original_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL
            )
        """)
        connection.commit()
        connection.close()

    @app.teardown_appcontext
    def close_db(_error=None):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    def valid_csrf():
        token = request.form.get("csrf_token", "")
        return hmac.compare_digest(token, session.get("csrf_token", ""))

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.endpoint not in {"static"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if not valid_csrf():
                abort(400, "Invalid CSRF token")
            if hmac.compare_digest(request.form.get("password", ""), app.config["PASSWORD"]):
                session.clear()
                session["authenticated"] = True
                session.permanent = True
                flash("登录成功。", "success")
                return redirect(url_for("index"))
            flash("密码错误。", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        if not valid_csrf():
            abort(400, "Invalid CSRF token")
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        files = db().execute(
            "SELECT id, original_name, size, uploaded_at FROM files ORDER BY uploaded_at DESC"
        ).fetchall()
        return render_template("index.html", files=files)

    @app.post("/upload")
    @login_required
    def upload():
        if not valid_csrf():
            abort(400, "Invalid CSRF token")
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("请选择文件。", "error")
            return redirect(url_for("index"))
        original_name = secure_filename(uploaded.filename)
        if not original_name:
            original_name = "unnamed-file"
        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}.bin"
        destination = Path(app.config["UPLOAD_DIR"]) / stored_name
        try:
            uploaded.save(destination)
            size = destination.stat().st_size
            db().execute(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
                (file_id, stored_name, original_name, size, datetime.now(timezone.utc).isoformat()),
            )
            db().commit()
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        flash(f"已上传 {original_name}。", "success")
        return redirect(url_for("index"))

    @app.get("/download/<file_id>")
    @login_required
    def download(file_id):
        record = db().execute(
            "SELECT stored_name, original_name FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if record is None:
            abort(404)
        path = Path(app.config["UPLOAD_DIR"]) / record["stored_name"]
        if not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True, download_name=record["original_name"])

    @app.post("/delete/<file_id>")
    @login_required
    def delete(file_id):
        if not valid_csrf():
            abort(400, "Invalid CSRF token")
        record = db().execute("SELECT stored_name FROM files WHERE id = ?", (file_id,)).fetchone()
        if record is None:
            abort(404)
        (Path(app.config["UPLOAD_DIR"]) / record["stored_name"]).unlink(missing_ok=True)
        db().execute("DELETE FROM files WHERE id = ?", (file_id,))
        db().commit()
        flash("文件已删除。", "success")
        return redirect(url_for("index"))

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("error.html", message="文件超过服务器允许的大小。"), 413

    return app
