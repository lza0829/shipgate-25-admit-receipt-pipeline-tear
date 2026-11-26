"""pytest 夹具公共件：每个用例独立临时 SQLite 库 + TestClient + 造数助手。

注意：必须在 import app.* 之前指定 SHIPGATE_DB，使 config/db 绑定到临时位置；
用例级隔离通过 monkeypatch app.db.DB_PATH 到各自 tmp_path 实现。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("SHIPGATE_DB", str(Path(tempfile.gettempdir()) / "shipgate-pytest-import.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402
from app.services.common import next_serial, now_str  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "shipgate_case.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)

    # 本环境 fsync 很慢；夹具库关闭同步刷盘/用内存回滚日志，把时间留给断言。
    # 只在测试进程内包装 connect，生产默认 PRAGMA 不受影响。
    import app.db as dbmod

    orig_connect = dbmod.connect

    def fast_connect():
        c = orig_connect()
        c.execute("PRAGMA synchronous=OFF")
        c.execute("PRAGMA journal_mode=MEMORY")
        return c

    monkeypatch.setattr(dbmod, "connect", fast_connect)

    init_db()
    seed_if_empty()
    yield db_path


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def make_app(_fresh_db):
    """每个用例可申请全新应用，避开种子应用 1 自带的在途入闸单。"""
    import uuid

    created: list[int] = []

    def _make(name: str | None = None, status: str = "启用") -> int:
        aid = create_app_direct(name or f"用例应用-{uuid.uuid4().hex[:8]}", status)
        created.append(aid)
        return aid

    return _make


@pytest.fixture
def app_id(make_app):
    return make_app()


# ---------------------------------------------------------------------------
# 直接走库的造数助手（并发夹具用）
# ---------------------------------------------------------------------------

def create_app_direct(name: str = "夹具应用", status: str = "启用") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO apps(name, owner, status) VALUES (?, '夹具负责人', ?)",
            (name, status),
        )
        return cur.lastrowid


def create_release_direct(
    app_id: int = 1,
    version: str = "3.1.4",
    digest: str = "sha256:fixture-digest",
    pipeline: str = "未回执",
) -> int:
    with db() as conn:
        serial = next_serial(conn)
        cur = conn.execute(
            """
            INSERT INTO releases(
              serial, app_id, target_version, artifact_digest, status, pipeline_status,
              canary_ratio, note, created_at
            ) VALUES (?, ?, ?, ?, '待入闸', ?, 10, '夹具单', ?)
            """,
            (serial, app_id, version, digest, pipeline, now_str()),
        )
        return cur.lastrowid


def set_pipeline_direct(rid: int, color: str) -> None:
    with db() as conn:
        conn.execute("UPDATE releases SET pipeline_status=? WHERE id=?", (color, rid))


def raw_release(rid: int):
    with db() as conn:
        return dict(conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone())


def active_links(rid: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM artifact_links WHERE release_id=? AND link_state!='已收回' ORDER BY id",
            (rid,),
        ).fetchall()
        return [dict(x) for x in rows]


def all_links(rid: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM artifact_links WHERE release_id=? ORDER BY id", (rid,)
        ).fetchall()
        return [dict(x) for x in rows]


def gate_audits(rid: int, action: str = "入闸") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE ref_id=? AND action=? ORDER BY id",
            (rid, action),
        ).fetchall()
        return [dict(x) for x in rows]


# ---------------------------------------------------------------------------
# HTTP 造数助手
# ---------------------------------------------------------------------------

def http_release(client, app_id: int = 1, version: str = "3.1.4", digest: str = "sha256:fx"):
    resp = client.post(
        "/api/发版单",
        json={"应用编号": app_id, "目标版本": version, "制品摘要": digest},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["编号"]


def http_pipeline(client, rid: int, color: str):
    resp = client.post(f"/api/发版单/{rid}/流水线", json={"流水线": color})
    assert resp.status_code == 200, resp.text
    return resp.json()


def http_admit(client, rid: int):
    return client.post(f"/api/发版单/{rid}/入闸")
