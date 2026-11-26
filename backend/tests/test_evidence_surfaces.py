"""扩展回归：所有事故定位面都必须能通过 API 摸到。"""

from __future__ import annotations

from conftest import http_admit, http_pipeline, http_release


def _admit(client, app_id, digest="sha256:surface"):
    rid = http_release(client, app_id=app_id, digest=digest)
    http_pipeline(client, rid, "绿")
    assert http_admit(client, rid).status_code == 200
    return rid


def test_clean_admit_exposes_every_requested_read_surface(client, app_id):
    rid = _admit(client, app_id, "sha256:all-surfaces")

    detail = client.get(f"/api/发版单/{rid}").json()
    assert detail["过线戳旁读"]["过线戳状态"] == "已成立"
    assert detail["入闸事件"]["事件类型"] == "入闸成立"
    assert detail["水位令牌"]["release_id"] == rid
    assert detail["缺口"] == []
    assert detail["同命运"] is True
    assert detail["发版详情旁读"]["digest旁读"]["齐全"] is True
    assert detail["digest旁读"]["digest"] == "sha256:all-surfaces"
    assert detail["环境旁读"]["代号"] == "测"
    assert detail["挂接行"]["状态"] == "挂接成功"

    # 过线戳旁读：主路径与英文别名都能打开。
    side = client.get(f"/api/发版单/{rid}/过线戳旁读").json()
    assert side["过线戳旁读"]["编号"] == detail["过线戳旁读"]["编号"]
    assert client.get(f"/api/stamp-side-read/{rid}").status_code == 200
    assert len(client.get("/api/过线戳", params={"release_id": rid}).json()) >= 2

    # 事件册与别名。
    events = client.get("/api/入闸事件", params={"release_id": rid}).json()
    assert len(events) == 1 and events[0]["state"] == "有效"
    book = client.get("/api/入闸事件册", params={"发版单编号": rid}).json()
    assert book["总数"] == 1 and book["入闸事件册"][0]["发版单编号"] == rid
    assert client.get(f"/api/发版单/{rid}/events").status_code == 200
    assert client.get("/api/events", params={"release_id": rid}).status_code == 200

    assert client.get("/api/events", params={"releaseId": rid}).status_code == 200
    assert client.get("/api/admit-event-book", params={"releaseId": rid}).status_code == 200
    assert client.get("/api/replay", params={"releaseId": rid}).json()["可重放成功"] is True
    assert client.get("/api/reconcile/replay", params={"releaseId": rid}).status_code == 200
    assert client.get("/api/difference").status_code == 200
    assert client.get("/api/admit-water-level").status_code == 200

    # 水位同屏账。
    water = client.get("/api/流水线入闸水位").json()
    assert water["真实水位"] >= 1 and water["差额"] == 0 and water["可信"] is True
    assert client.get("/api/water-level").json()["scope"] == water["scope"]

    # 单单重放/证据/digest/环境旁读。
    replay = client.get(f"/api/发版单/{rid}/事件重放").json()
    assert replay["可重放成功"] is True
    assert replay["成功单号"] == detail["发版单号"]
    assert client.get(f"/api/replay/{rid}").json()["可重放成功"] is True
    assert client.get(f"/api/evidence/{rid}").json()["可重放成功"] is True
    digest_side = client.get(f"/api/发版单/{rid}/digest旁读").json()
    assert digest_side["齐全"] is True and digest_side["digest"] == "sha256:all-surfaces"
    env_side = client.get(f"/api/发版单/{rid}/环境旁读").json()
    assert env_side["代号"] == "测"

    # 总览：在途入闸格与水位。
    ov = client.get("/api/总览").json()
    cell = next(x for x in ov["在途入闸格"] if x["编号"] == rid)
    assert cell["水位亮灯"] is True and cell["缺口数"] == 0
    assert ov["流水线入闸水位"]["真实水位"] >= 1
    row = next(x for x in ov["在途"] if x["编号"] == rid)
    assert row["过线戳旁读"] and row["在途入闸格"]["testid"] == "transit-admit-cell"

    # 对账差额块与挂接行。
    rc = client.get("/api/对账").json()
    assert rc["对账差额"]["对齐"] is True
    assert rc["对账差额"]["差额总数"] == 0
    assert any(x["发版单编号"] == rid for x in rc["挂接行"])
    diff = client.get("/api/对账差额").json()
    assert diff["对齐"] is True and diff["difference"]["差额总数"] == 0

    # 审计过滤：中文/英文/别名参数均应摸到同一条成功入闸。
    audit_cn = client.get("/api/审计", params={"关联编号": rid, "动作": "入闸"}).json()
    assert len(audit_cn) == 1 and audit_cn[0]["action"] == "入闸"
    audit_en = client.get("/api/审计", params={"ref_id": rid, "action": "入闸"}).json()
    assert len(audit_en) == 1 and audit_en[0]["release_id"] == rid
    audit_q = client.get("/api/审计", params={"release_id": rid, "action": "入闸", "q": "过线戳"}).json()
    assert len(audit_q) == 1 and "过线戳" in audit_q[0]["note"]


def test_pipeline_refresh_alias_updates_stamp_side(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:refresh-alias")
    resp = client.post(f"/api/过线戳刷新/{rid}", json={"status": "green"})
    assert resp.status_code == 200, resp.text
    side = client.get(f"/api/发版单/{rid}/过线戳旁读").json()["过线戳旁读"]
    assert side["过线戳颜色"] == "绿"
    assert client.post(f"/api/stamps/{rid}/refresh", json={"color": "red"}).status_code == 200
    side2 = client.get(f"/api/过线戳旁读", params={"release_id": rid}).json()["过线戳旁读"]
    assert side2["过线戳颜色"] == "红"


def test_english_write_aliases_and_collection_refresh(client, app_id):
    created = client.post("/api/releases", json={
        "appId": app_id,
        "version": "9.9.9-alias",
        "artifactDigest": "sha256:english-create",
    })
    assert created.status_code == 200, created.text
    rid = created.json()["编号"]
    assert client.post(f"/api/releases/{rid}/pipeline", json={"pipelineStatus": "green"}).status_code == 200
    assert client.post(f"/api/releases/{rid}/admit", json={}).status_code == 200
    replay = client.get(f"/api/replay/{rid}").json()
    assert replay["可重放成功"] is True

    assert client.post("/api/过线戳刷新", json={"release_id": rid, "color": "green"}).status_code == 200
    assert client.post(f"/api/pipeline/{rid}", json={"status": "green"}).status_code == 200


def test_http_fault_aliases_keep_half_transactions_invisible(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:http-fault-alias")
    http_pipeline(client, rid, "绿")

    failed = client.post(f"/api/releases/{rid}/admit", json={"injectFault": "after_stamp"})
    assert failed.status_code == 500
    detail = client.get(f"/api/releases/{rid}").json()
    assert detail["状态"] == "待入闸"
    assert detail["入闸事件"] is None
    assert detail["水位令牌"] is None

    broken_refresh = client.post(
        "/api/过线戳刷新",
        json={"releaseId": rid, "pipelineStatus": "green", "faultType": "after_stamp"},
    )
    assert broken_refresh.status_code == 500
    side = client.get(f"/api/stamp-side-read/{rid}").json()["stamp_side_read"]
    assert side["过线戳状态"] == "已成立"


def test_correction_endpoint_repairs_fake_water_and_missing_event(client, app_id):
    rid = _admit(client, app_id, "sha256:correct")

    # 水位账面假加：纠偏应对齐。
    from app.db import db
    with db() as conn:
        conn.execute("UPDATE admit_water_level SET level=level+3 WHERE scope='流水线入闸总水位'")
    out = client.post("/api/对账/纠偏", json={}).json()
    assert out["缺口总数"] == 0
    assert out["水位"]["差额"] == 0
    assert client.get("/api/对账差额").json()["对齐"] is True

    # 事件册被抹：先尝试诚实补账；补账后应恢复可重放。
    with db() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM admit_events WHERE release_id=?", (rid,))
        conn.execute("DELETE FROM admit_water_entries WHERE release_id=?", (rid,))
    out2 = client.post(f"/api/发版单/{rid}/纠偏", json={}).json()
    assert out2["缺口总数"] == 0, out2
    replay = client.get(f"/api/replay/{rid}").json()
    assert replay["可重放成功"] is True
    assert replay["入闸事件"] is not None


def test_correction_force_resets_fatal_stamp(client, app_id):
    rid = _admit(client, app_id, "sha256:correct-fatal")
    stamp_id = client.get(f"/api/发版单/{rid}/过线戳旁读").json()["过线戳旁读"]["编号"]
    from app.db import db
    with db() as conn:
        conn.execute("UPDATE cross_line_stamps SET fatal=1,state='红戳',color='红' WHERE id=?", (stamp_id,))

    out = client.post(f"/api/发版单/{rid}/纠偏", json={}).json()
    assert rid in out["整笔退回"]
    detail = client.get(f"/api/发版单/{rid}").json()
    assert detail["状态"] == "待入闸"
    assert detail["缺口"] == []
    assert client.get("/api/对账差额").json()["对齐"] is True


def test_english_evidence_fields_and_release_scoped_pin_aliases(client, app_id):
    rid = _admit(client, app_id, "sha256:english-fields")

    stamp = client.get(f"/api/stamp-side-read/{rid}").json()["stamp_side_read"]
    assert stamp["release_id"] == rid
    assert stamp["established"] is True
    assert stamp["fatal"] is False
    assert stamp["red"] is False
    assert stamp["half"] is False
    assert stamp["stampState"] == "已成立"

    book = client.get("/api/admit-event-book", params={"releaseId": rid}).json()
    event = book["events"][0]
    assert event["release_id"] == rid
    assert event["valid"] is True
    assert event["active"] is True
    assert event["eventType"] == "入闸成立"

    # 制造全局水位差额钉：单单 pin 列表必须严格按 release_id 收窄，
    # 全局 NULL pin 只能出现在 globalPins，不能污染该单的 pins。
    from app.db import db
    with db() as conn:
        conn.execute("UPDATE admit_water_level SET level=level+1 WHERE scope='流水线入闸总水位'")

    scoped = client.get("/api/gap-pins", params={"releaseId": rid}).json()
    assert all(p["release_id"] == rid for p in scoped["pins"])
    assert scoped["globalPins"], "全局水位差额 pin 应单独返回"
    assert all(p["release_id"] is None for p in scoped["globalPins"])
    assert any(p["gap_type"] == "流水线入闸水位差额" for p in scoped["globalPins"])
    gap = client.get("/api/reconcile/gaps").json()["gaps"][0]
    assert gap["gap_type"]
    assert gap["gapType"] == gap["gap_type"]
    assert gap["severity"]

    water = client.get("/api/admit-water-level").json()
    assert water["true_level"] == water["真实水位"]
    assert water["stored_level"] == water["账面水位"]
    assert water["trusted"] is False

    replay = client.get(f"/api/replay/{rid}").json()
    assert replay["id"] == rid and replay["release_id"] == rid
    assert replay["releaseId"] == rid
    assert replay["fake_success"] is True
    assert replay["fakeSuccess"] is True
    assert replay["gap_count"] >= 1

    reconcile = client.get("/api/reconcile").json()
    link = next(x for x in reconcile["挂接行"] if x["release_id"] == rid)
    assert link["id"] == link["挂接编号"]
    assert link["link_state"] == "挂接成功"
    assert "verdict" in link

    for path in [
        f"/api/release/{rid}",
        f"/api/releases/{rid}/events",
        f"/api/releases/{rid}/admit-event",
        f"/api/releases/{rid}/gaps",
        f"/api/releases/{rid}/water-level",
        f"/api/releases/{rid}/cross-line-stamp",
        f"/api/releases/{rid}/stamp",
        f"/api/stamps/{rid}",
        f"/api/cross-line-stamp?release_id={rid}",
        "/api/audit-events?ref_id=" + str(rid),
    ]:
        assert client.get(path).status_code == 200, path


def test_orphan_admit_evidence_on_inactive_release_pins_named_gap(client, app_id):
    rid = _admit(client, app_id, "sha256:orphan-active-evidence")
    from app.db import db
    with db() as conn:
        conn.execute(
            "UPDATE releases SET status='待入闸', gate_verdict=NULL WHERE id=?",
            (rid,),
        )

    body = client.get(f"/api/releases/{rid}").json()
    assert "入闸证据无在途单态" in {g["缺口类型"] for g in body["缺口"]}
    assert body["有缺口"] is True
