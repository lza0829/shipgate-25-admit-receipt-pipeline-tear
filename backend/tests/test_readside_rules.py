"""读侧可重放 + 闸规边界：未回执/空摘要/停用应用/并行在途/非法颜色/设置臂。"""

from __future__ import annotations

from conftest import http_admit, http_pipeline, http_release


def test_decision_is_replayable_and_blocks_without_receipt(client):
    rid = http_release(client, app_id=1, digest="sha256:x")
    d1 = client.get(f"/api/发版单/{rid}/入闸判定").json()
    assert d1["可入闸"] is False
    assert d1["裁决"] is None
    assert d1["检查"]["流水线已有红绿回执"] is False
    assert any("未收到流水线回执" in r for r in d1["原因"])
    # 重复读完全一致（可重放）
    d2 = client.get(f"/api/发版单/{rid}/入闸判定").json()
    assert d1 == d2

    resp = http_admit(client, rid)
    assert resp.status_code == 400
    assert "回执" in resp.json()["detail"]


def test_empty_digest_is_still_separate_hard_rule(client):
    """空制品摘要另立闸规：即便流水线绿也禁止入闸（本任务不改变它）。"""
    rid = http_release(client, app_id=1, digest="")
    http_pipeline(client, rid, "绿")
    d = client.get(f"/api/发版单/{rid}/入闸判定").json()
    assert d["可入闸"] is False
    assert d["检查"]["制品摘要非空"] is False
    resp = http_admit(client, rid)
    assert resp.status_code == 400
    assert "制品摘要" in resp.json()["detail"]


def test_disabled_app_blocks_gate(client):
    aid = client.post("/api/应用", json={"名称": "将停用应用"}).json()["编号"]
    rid = http_release(client, app_id=aid, digest="sha256:d")
    http_pipeline(client, rid, "绿")
    client.patch(f"/api/应用/{aid}", json={"名称": "将停用应用", "状态": "停用"})
    resp = http_admit(client, rid)
    assert resp.status_code == 400
    assert "停用" in resp.json()["detail"]


def test_parallel_active_release_blocks_second_gate(client, app_id):
    r1 = http_release(client, app_id=app_id, version="1.0.1", digest="sha256:p1")
    http_pipeline(client, r1, "绿")
    assert http_admit(client, r1).status_code == 200

    r2 = http_release(client, app_id=app_id, version="1.0.2", digest="sha256:p2")
    http_pipeline(client, r2, "绿")
    resp = http_admit(client, r2)
    assert resp.status_code == 400
    assert "在途" in resp.json()["detail"]


def test_only_waiting_status_can_admit(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:s")
    http_pipeline(client, rid, "绿")
    http_admit(client, rid)
    again = http_admit(client, rid)
    assert again.status_code == 400
    assert "待入闸" in again.json()["detail"]


def test_illegal_pipeline_color_rejected(client):
    rid = http_release(client, app_id=1, digest="sha256:c")
    resp = client.post(f"/api/发版单/{rid}/流水线", json={"流水线": "花"})
    assert resp.status_code in (400, 422)


def test_settings_declare_single_arm_and_old_switch_gone(client):
    settings = {x["键"]: x["值"] for x in client.get("/api/设置").json()}
    assert settings.get("入闸架构臂") == "红单可告警入闸"
    assert "入闸须流水线绿" not in settings  # 不再保留与所选臂矛盾的开关


def test_audit_query_filters(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:audit-q")
    http_pipeline(client, rid, "绿")
    http_admit(client, rid)

    by_ref = client.get("/api/审计", params={"关联编号": rid}).json()
    assert all(x["关联编号"] == rid for x in by_ref)
    assert any(x["动作"] == "入闸" for x in by_ref)

    by_kind = client.get("/api/审计", params={"类别": "发版单"}).json()
    assert all(x["类别"] == "发版单" for x in by_kind)

    by_kw = client.get("/api/审计", params={"关键字": "不存在的词-xyz"}).json()
    assert by_kw == []
