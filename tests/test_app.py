import io
from pathlib import Path

import pytest

from easyshare.app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "PASSWORD": "correct-horse",
        "DATA_DIR": tmp_path,
        "DATABASE": tmp_path / "test.db",
        "UPLOAD_DIR": tmp_path / "uploads",
        "SESSION_COOKIE_SECURE": False,
        "MAX_CONTENT_LENGTH": 1024,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    client.get("/login")
    with client.session_transaction() as session:
        return session["csrf_token"]


def login(client):
    return client.post("/login", data={"password": "correct-horse", "csrf_token": csrf(client)})


def test_authentication_required(client):
    assert client.get("/").status_code == 302
    response = login(client)
    assert response.status_code == 302
    assert client.get("/").status_code == 200


def test_wrong_password_is_rejected(client):
    response = client.post("/login", data={"password": "wrong", "csrf_token": csrf(client)})
    assert "密码错误" in response.get_data(as_text=True)


def test_upload_download_and_delete(client, app):
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post("/upload", data={
        "csrf_token": token,
        "file": (io.BytesIO(b"safe content"), "../report.txt"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert "report.txt" in response.get_data(as_text=True)
    files = list(Path(app.config["UPLOAD_DIR"]).iterdir())
    assert len(files) == 1 and files[0].name.endswith(".bin")

    with app.app_context():
        import sqlite3
        connection = sqlite3.connect(app.config["DATABASE"])
        file_id = connection.execute("SELECT id FROM files").fetchone()[0]
        connection.close()
    download = client.get(f"/download/{file_id}")
    assert download.data == b"safe content"
    assert "attachment" in download.headers["Content-Disposition"]

    deleted = client.post(f"/delete/{file_id}", data={"csrf_token": token})
    assert deleted.status_code == 302
    assert not files[0].exists()


def test_csrf_is_required(client):
    login(client)
    response = client.post("/upload", data={"file": (io.BytesIO(b"x"), "x.txt")})
    assert response.status_code == 400


def test_security_headers(client):
    response = client.get("/login")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
