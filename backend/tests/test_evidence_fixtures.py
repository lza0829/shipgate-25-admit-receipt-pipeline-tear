"""四路证据撕裂夹具，必须分测：

1. 过线戳红/致命红戳；
2. 入闸事件缺失；
3. 流水线入闸水位假加；
4. 入闸写口/过线戳刷新口半截与交错。

这些测试只验证诚实缺口和同命运回滚，不使用 surface 粉刷。
"""

from __future__ import annotations

import threading
from threading import Barrier

import pytest

from app.db import db
from app.services.gate import admit_release, record_pipeline
from conftest import gate_audits, http_admit, http_pipeline, http_release


def _green_admitted(client, app_id, digest="sha256:evidence"):
    rid = http_release(client, app_id=app_id, digest=digest)
    http_pipeline(client, rid, "绿")
    body = http_admit(client, rid).json()
    return rid, body


def _gap_types(payload) -> set[str]:
    gaps = payload.get("缺口") or payload.get("gaps") or []
    return {g.get("缺口类型") or g.get("type") for g in gaps}


# ---------------------------------------------------------------------------
# 第一路：过线戳红
# ---------------------------------------------------------------------------

def test_red_stamp_fixture_pins_gap_and_replay_unmasks_fake_success(client, app_id):
    rid, body = _green_admitted(client, app_id, "sha256:red-stamp")
    stamp_id = body["过线戳"]["编号"]

    # 人工把同命运过线戳拧成致命红戳，模拟「单号已绿、旁读却红」事故。
    with db() as conn:
        conn.execute(
            """
            UPDATE cross_line_stamps
               SET color='红', state='红戳', established=0, passed=0,
                   green=0, red=1, fatal=1
             WHERE id=?
            """,
            (stamp_id,),
        )

    detail = client.get(f"/api/发版单/{rid}").json()
    types = _gap_types(detail)
    assert any("过线戳" in x for x in types)
    assert detail["有缺口"] is True
    assert detail["同命运"] is False
    assert detail["可重放成功单号"] is None
    assert detail["假成功单号"] == detail["发版单号"]
    # 缺口未清，本单水位灯不得亮。
    assert detail["在途入闸格"]["水位亮灯"] is False

    replay = client.get(f"/api/事件重放/{rid}").json()
    assert replay["可重放成功"] is False
    assert replay["假成功"] is True
    assert replay["被揭穿假入闸成功单号"] == detail["发版单号"]
    assert any("过线戳" in (g.get("缺口类型") or "") for g in replay["缺口"])

    ov = client.get("/api/总览").json()
    assert ov["缺口总数"] >= 1
    assert any(c["编号"] == rid and c["缺口数"] >= 1 and c["水位亮灯"] is False
               for c in ov["在途入闸格"])

    rc = client.get("/api/对账").json()
    assert rc["对账差额"]["差额总数"] >= 1
    assert rc["对账差额"]["对齐"] is False

    gaps = client.get("/api/缺口", params={"发版单编号": rid}).json()
    assert gaps["缺口总数"] >= 1
    assert any(p["gap_type"] for p in gaps["钉住"])


def test_ordinary_red_gate_is_legal_and_not_an_evidence_gap(client):
    """旧臂 B：普通红流水线可形成红入闸；红色戳+红事件+水位仍同命运。"""
    rid = http_release(client, app_id=2, digest="sha256:legal-red-evidence")
    http_pipeline(client, rid, "红")
    resp = http_admit(client, rid)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["入闸结论"] == "红入闸"
    assert body["缺口"] == []
    assert body["同命运"] is True
    assert body["过线戳旁读"]["过线戳红"] is True
    assert body["过线戳旁读"]["异常红戳"] is False

    replay = client.get(f"/api/事件重放/{rid}").json()
    assert replay["可重放成功"] is True
    assert replay["缺口"] == []
    assert client.get("/api/总览").json()["入闸一致性"]["一致"] is True


def test_new_admit_with_fatal_stamp_is_rejected_and_rolled_back(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:fatal-before-admit")
    http_pipeline(client, rid, "绿")
    with db() as conn:
        sid = conn.execute(
            "SELECT id FROM cross_line_stamps WHERE release_id=? ORDER BY id DESC LIMIT 1",
            (rid,),
        ).fetchone()["id"]
        conn.execute(
            "UPDATE cross_line_stamps SET state='半截', fatal=1, half_state='半截' WHERE id=?",
            (sid,),
        )

    resp = http_admit(client, rid)
    assert resp.status_code == 400
    assert "过线戳" in resp.json()["detail"]
    raw = client.get(f"/api/发版单/{rid}").json()
    assert raw["状态"] == "待入闸"
    assert raw["入闸事件"] is None
    assert raw["水位令牌"] is None


# ---------------------------------------------------------------------------
# 第二路：入闸事件缺
# ---------------------------------------------------------------------------

def test_missing_admit_event_is_replayed_as_gap(client, app_id):
    rid, body = _green_admitted(client, app_id, "sha256:event-missing")

    # 外键开着时水位令牌会阻止删事件；事故夹具模拟人工绕过外键抹事件册。
    with db() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM admit_events WHERE release_id=?", (rid,))

    replay = client.get(f"/api/事件重放/{rid}").json()
    types = _gap_types(replay)
    assert "入闸事件缺失" in types
    assert replay["可重放成功"] is False
    assert replay["被揭穿假入闸成功单号"]

    detail = client.get(f"/api/发版单/{rid}").json()
    assert "入闸事件缺失" in _gap_types(detail)
    assert detail["入闸事件"] is None
    # 水位令牌还挂着旧 event_id，必须被识别为假加/挂空，而不是亮真水位。
    assert any(t in types for t in ("水位假加或挂错事件", "流水线入闸水位令牌缺失"))
    assert detail["在途入闸格"]["水位亮灯"] is False

    events = client.get("/api/入闸事件", params={"release_id": rid}).json()
    assert events == []


# ---------------------------------------------------------------------------
# 第三路：水位假加
# ---------------------------------------------------------------------------

def test_stored_water_fake_increment_is_exposed_as_difference(client, app_id):
    rid, _ = _green_admitted(client, app_id, "sha256:water-fake")

    before = client.get("/api/流水线入闸水位").json()
    assert before["账面水位"] == before["真实水位"] >= 1
    with db() as conn:
        conn.execute(
            "UPDATE admit_water_level SET level=level+1 WHERE scope=?",
            ("流水线入闸总水位",),
        )

    water = client.get("/api/流水线入闸水位").json()
    assert water["差额"] == 1
    assert water["可信"] is False
    assert water["亮灯"] == "缺口红"

    diff = client.get("/api/对账差额").json()
    assert diff["水位差额"] == 1
    assert diff["水位数值差额"] == 1
    assert diff["差额总数"] == 1
    assert diff["对齐"] is False
    assert any(g.get("缺口类型") == "流水线入闸水位差额" for g in diff["gaps"])

    ov = client.get("/api/总览").json()
    assert ov["流水线入闸水位"]["账面水位"] == ov["流水线入闸水位"]["真实水位"] + 1
    assert ov["对账差额"]["水位差额"] == 1


def test_orphan_water_token_is_detected(client, app_id):
    rid, _ = _green_admitted(client, app_id, "sha256:orphan-water")
    with db() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("UPDATE admit_water_entries SET event_id=999999 WHERE release_id=?", (rid,))
        conn.execute("UPDATE admit_water_level SET level=level+0 WHERE scope='流水线入闸总水位'")

    gaps = client.get("/api/缺口", params={"release_id": rid}).json()["gaps"]
    types = {g["缺口类型"] for g in gaps}
    assert "水位假加或挂错事件" in types
    assert client.get("/api/流水线入闸水位").json()["可信"] is False


# ---------------------------------------------------------------------------
# 第四路：入闸与过线戳刷新半截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fault", ["after_stamp", "after_event", "after_water", "after_link"])
def test_admit_half_faults_rollback_all_evidence(client, app_id, fault):
    rid = http_release(client, app_id=app_id, digest=f"sha256:half-{fault}")
    http_pipeline(client, rid, "绿")

    with pytest.raises(RuntimeError):
        admit_release(rid, fault=fault)

    detail = client.get(f"/api/发版单/{rid}").json()
    assert detail["状态"] == "待入闸"
    assert detail["入闸结论"] is None
    assert detail["入闸事件"] is None
    assert detail["水位令牌"] is None
    # 入闸事务里的戳必须随事务回滚；此前流水线回执自己的戳仍可保留。
    with db() as conn:
        admit_stamps = conn.execute(
            "SELECT COUNT(*) c FROM cross_line_stamps WHERE release_id=? AND source='入闸写口'",
            (rid,),
        ).fetchone()["c"]
    assert admit_stamps == 0
    assert gate_audits(rid, "入闸") == []

    with db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM admit_events WHERE release_id=?", (rid,)).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM admit_water_entries WHERE release_id=?", (rid,)).fetchone()["c"] == 0


def test_pipeline_refresh_half_failure_rolls_back(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:refresh-rollback")
    http_pipeline(client, rid, "绿")
    with db() as conn:
        before_stamps = conn.execute(
            "SELECT COUNT(*) c FROM cross_line_stamps WHERE release_id=?", (rid,)
        ).fetchone()["c"]

    with pytest.raises(RuntimeError):
        record_pipeline(rid, "红", fault="after_stamp")

    detail = client.get(f"/api/发版单/{rid}").json()
    assert detail["流水线"] == "绿"
    with db() as conn:
        after_stamps = conn.execute(
            "SELECT COUNT(*) c FROM cross_line_stamps WHERE release_id=?", (rid,)
        ).fetchone()["c"]
    assert after_stamps == before_stamps


def test_committed_half_refresh_stamp_can_be_separately_reproduced(client, app_id):
    rid, _ = _green_admitted(client, app_id, "sha256:half-committed")
    resp = client.post(
        f"/api/发版单/{rid}/过线戳刷新",
        json={"流水线": "绿", "半截": True, "戳状态": "刷新半截"},
    )
    assert resp.status_code == 200, resp.text

    side = client.get(f"/api/发版单/{rid}/过线戳旁读").json()["过线戳旁读"]
    assert side["刷新半截"] is True
    gaps = _gap_types(client.get(f"/api/发版单/{rid}").json())
    assert "过线戳刷新半截或红戳" in gaps
    replay = client.get(f"/api/事件重放/{rid}").json()
    assert replay["可重放成功"] is False


def test_half_refresh_and_admit_interleave_never_fakes_water(app_id):
    """栅栏并发：刷新口注入半截回滚，入闸口同时抢 IMMEDIATE 锁。"""
    from conftest import create_release_direct

    rid = create_release_direct(app_id, pipeline="绿", digest="sha256:interleave-half")
    barrier = Barrier(2)
    errors: list[Exception] = []

    def broken_refresh():
        try:
            barrier.wait()
            record_pipeline(rid, "红", fault="after_stamp")
        except RuntimeError as exc:
            errors.append(exc)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def admit():
        try:
            barrier.wait()
            admit_release(rid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=broken_refresh)
    t2 = threading.Thread(target=admit)
    t1.start(); t2.start()
    t1.join(10); t2.join(10)
    assert not t1.is_alive() and not t2.is_alive()
    # 半截刷新的 RuntimeError 是预期夹具异常；不得有锁/库异常。
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)

    with db() as conn:
        events = conn.execute(
            "SELECT COUNT(*) c FROM admit_events WHERE release_id=? AND state='有效'", (rid,)
        ).fetchone()["c"]
        tokens = conn.execute(
            "SELECT COUNT(*) c FROM admit_water_entries WHERE release_id=?", (rid,)
        ).fetchone()["c"]
        stored = conn.execute("SELECT level FROM admit_water_level WHERE scope='流水线入闸总水位'").fetchone()["level"]
        true_level = conn.execute(
            """
            SELECT COUNT(*) c FROM admit_water_entries w
            JOIN admit_events e ON e.id=w.event_id AND e.state='有效'
            """
        ).fetchone()["c"]
    assert events == tokens == 1
    assert stored == true_level
    assert client_get_consistent(rid)


def client_get_consistent(rid: int) -> bool:
    from app.services.gate import gate_consistency

    with db() as conn:
        return gate_consistency(conn)["一致"]
