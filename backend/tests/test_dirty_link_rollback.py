"""半截脏挂接夹具：

1. 写口在挂接行插入后故障 -> 整笔事务回滚，不得「红灯已入闸却无挂接行」；
2. 历史脏状态（已入闸 + 挂接中行 / 无成功行）-> 收回口整笔拉回入闸前；
3. 好挂接 + 多出来的半截行 -> 只收脏行，不误伤有效入闸。
"""

from __future__ import annotations

import pytest

from app.db import db
from app.services.gate import admit_release
from conftest import (
    active_links,
    all_links,
    create_app_direct,
    create_release_direct,
    gate_audits,
    http_admit,
    http_pipeline,
    http_release,
    raw_release,
)


def test_injected_half_failure_rolls_back_entire_gate(client):
    """服务层故障注入：挂接行刚写完就炸，发版单/挂接/成功审计全部回到入闸前。"""
    aid = create_app_direct("半截故障应用")
    rid = create_release_direct(aid, digest="sha256:half", pipeline="红")

    with pytest.raises(RuntimeError):
        admit_release(rid, fault="after_link")

    raw = raw_release(rid)
    assert raw["status"] == "待入闸"
    assert raw["admitted_at"] is None
    assert raw["current_env_id"] is None
    assert raw["gate_verdict"] is None
    assert raw["gate_alarm"] == 0
    assert raw["gate_pipeline_status"] == "未回执"  # 快照也收回

    # 事务内插入的挂接行随事务消失：不是留下半截行，而是根本没有残留
    assert all_links(rid) == []
    assert active_links(rid) == []
    assert gate_audits(rid, "入闸") == []

    # 但独立事务留下了可核的整笔收回审计
    reclaim_audit = gate_audits(rid, "入闸异常整笔收回")
    assert len(reclaim_audit) == 1
    assert "回到入闸前" in reclaim_audit[0]["note"]

    # 总览不计在途
    ov = client.get("/api/总览").json()
    assert all(x["编号"] != rid for x in ov["在途"])

    # 整笔收回后可以干净地重新入闸
    ok = http_admit(client, rid)
    # rid 由直连库创建，HTTP 同库；红流水线仍在 -> 红入闸成功
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["入闸结论"] == "红入闸"
    assert len(active_links(rid)) == 1


def test_http_failure_returns_500_and_leaves_no_half_state(client, monkeypatch):
    """HTTP 层：挂接写入后异常 -> 500，随后 GET 证明没有任何半截成功结局。"""
    rid = http_release(client, app_id=2, digest="sha256:http-half")
    http_pipeline(client, rid, "红")

    import app.services.gate as gate_mod

    # 钉住审计函数：挂接 INSERT 已在事务内执行，但审计/提交尚未发生即抛错，
    # 由 db() 上下文整笔回滚，验证不会留下半截成功结局。
    def boom(*a, **k):
        raise RuntimeError("注入：审计前故障")

    monkeypatch.setattr(gate_mod, "persist_audit", boom)
    resp = http_admit(client, rid)
    assert resp.status_code == 500

    body = client.get(f"/api/发版单/{rid}").json()
    assert body["状态"] == "待入闸"
    assert body["入闸结论"] is None
    assert body["挂接行"] is None
    assert body["可入闸"] is True  # 回到入闸前，仍可重放判定并再次尝试

    links = client.get(f"/api/发版单/{rid}/挂接行", params={"全部": "true"}).json()
    assert links == []


def test_legacy_dirty_admitted_with_linking_row_can_be_reset(client):
    """模拟旧 defect 现场：红灯已打成已入闸，只有『挂接中』脏行，无成功挂接。"""
    rid = http_release(client, app_id=2, digest="sha256:dirty-legacy")
    http_pipeline(client, rid, "红")

    # 直接拼出半截脏状态（绕过新写口，模拟历史脏数据）
    with db() as conn:
        conn.execute(
            """
            UPDATE releases
               SET status='已入闸', admitted_at='2026-01-01 00:00:00',
                   current_env_id=(SELECT id FROM environments WHERE code='测'),
                   gate_verdict='红入闸', gate_pipeline_status='红', gate_alarm=1
             WHERE id=?
            """,
            (rid,),
        )
        conn.execute(
            """
            INSERT INTO artifact_links(
              release_id, artifact_digest, pipeline_status, verdict,
              link_state, alarm, note, created_at
            ) VALUES (?, 'sha256:dirty-legacy', '红', '红入闸',
                      '挂接中', 1, '半截', '2026-01-01 00:00:01')
            """,
            (rid,),
        )

    before = client.get("/api/总览").json()
    assert any(x["编号"] == rid for x in before["在途"])
    assert before["入闸一致性"]["挂接中脏行"] >= 1

    resp = client.post(f"/api/发版单/{rid}/收回挂接", json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["整笔重置"] is True
    assert data["收回挂接行"]

    body = data["发版单"]
    assert body["状态"] == "待入闸"
    assert body["入闸结论"] is None
    assert body["红入闸告警"] is False
    assert body["入闸时间"] is None
    assert body["当前环境"] is None
    assert body["挂接行"] is None

    # 脏行被标已收回（可核），不是物理消失
    rows = all_links(rid)
    assert len(rows) == 1 and rows[0]["link_state"] == "已收回"
    assert rows[0]["reclaimed_at"]

    # 总览在途撤销、一致性恢复
    ov = client.get("/api/总览").json()
    assert all(x["编号"] != rid for x in ov["在途"])
    assert ov["入闸一致性"]["一致"] is True

    # 审计可查收回事件
    au = client.get("/api/审计", params={"关联编号": rid, "关键字": "整笔收回"}).json()
    assert au, "必须留下整笔收回审计"

    # 收回后可重新干净入闸
    again = http_admit(client, rid)
    assert again.status_code == 200
    assert again.json()["入闸结论"] == "红入闸"
    assert len(active_links(rid)) == 1
    assert client.get("/api/总览").json()["入闸一致性"]["一致"] is True


def test_extra_half_link_is_reclaimed_without_resetting_good_gate(client, app_id):
    """绿入闸正常，却多出一条『挂接中』半截行：只收脏行，绿入闸保持。"""
    rid = http_release(client, app_id=app_id, digest="sha256:good-plus-dirty")
    http_pipeline(client, rid, "绿")
    admitted = http_admit(client, rid).json()
    good_link_id = admitted["挂接行"]["编号"]

    with db() as conn:
        conn.execute(
            """
            INSERT INTO artifact_links(
              release_id, artifact_digest, pipeline_status, verdict,
              link_state, alarm, note, created_at
            ) VALUES (?, 'sha256:half-extra', '绿', '绿入闸',
                      '挂接中', 0, '多出来的半截行', '2026-01-02 00:00:00')
            """,
            (rid,),
        )

    resp = client.post(f"/api/发版单/{rid}/收回挂接", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["整笔重置"] is False
    assert len(data["收回挂接行"]) == 1

    body = client.get(f"/api/发版单/{rid}").json()
    assert body["状态"] == "已入闸"
    assert body["入闸结论"] == "绿入闸"
    assert body["挂接行"]["编号"] == good_link_id
    assert body["挂接行"]["状态"] == "挂接成功"
    assert client.get("/api/总览").json()["入闸一致性"]["一致"] is True


def test_reclaim_specific_link_id(client, app_id):
    rid = http_release(client, app_id=app_id, digest="sha256:specific")
    http_pipeline(client, rid, "绿")
    http_admit(client, rid)
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO artifact_links(
              release_id, artifact_digest, pipeline_status, verdict,
              link_state, alarm, note, created_at
            ) VALUES (?, 'sha256:spec', '绿', '绿入闸', '挂接中', 0, 'x', '2026-01-03 00:00:00')
            """,
            (rid,),
        )
        dirty_id = cur.lastrowid

    resp = client.post(f"/api/发版单/{rid}/收回挂接", json={"挂接编号": dirty_id})
    assert resp.status_code == 200
    assert resp.json()["收回挂接行"] == [dirty_id]

    missing = client.post(f"/api/发版单/{rid}/收回挂接", json={"挂接编号": dirty_id})
    assert missing.status_code == 404
