"""绿入闸夹具：流水线绿 -> 绿入闸，发版单/挂接行/总览/晋升/审计同一判定。"""

from __future__ import annotations

from conftest import gate_audits, http_admit, http_pipeline, http_release


def test_green_admit_full_alignment(client, app_id):
    rid = http_release(client, app_id=app_id, version="2.0.1", digest="sha256:green-1")
    http_pipeline(client, rid, "绿")

    # 读侧重放：入闸前可入闸，裁决绿入闸；重复读取结论一致
    d1 = client.get(f"/api/发版单/{rid}/入闸判定").json()
    d2 = client.get(f"/api/发版单/{rid}/入闸判定").json()
    assert d1["可入闸"] is True
    assert d1["裁决"] == "绿入闸"
    assert d1["告警"] is False
    assert d1 == d2  # 可重放

    resp = http_admit(client, rid)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 发版单态
    assert body["状态"] == "已入闸"
    assert body["入闸结论"] == "绿入闸"
    assert body["入闸时流水线"] == "绿"
    assert body["红入闸告警"] is False
    assert body["告警标"] == ""
    assert body["入闸时间"]

    # 入闸回执
    receipt = body["入闸回执"]
    assert receipt["裁决"] == "绿入闸"
    assert receipt["告警"] is False
    assert receipt["挂接状态"] == "挂接成功"

    # 制品挂接行：绿结论，不得有告警/假成功歧义
    link = body["挂接行"]
    assert link is not None
    assert link["状态"] == "挂接成功"
    assert link["结论"] == "绿入闸"
    assert link["告警"] is False
    assert link["制品"] == "sha256:green-1"
    assert body["挂接裁决一致"] is True

    links = client.get(f"/api/发版单/{rid}/挂接行").json()
    assert len(links) == 1 and links[0]["link_state"] == "挂接成功"

    # 总览在途：计在途、无红告警
    ov = client.get("/api/总览").json()
    assert ov["分布"]["已入闸"] >= 1
    row = next(x for x in ov["在途"] if x["编号"] == rid)
    assert row["入闸结论"] == "绿入闸"
    assert row["红入闸告警"] is False
    assert row["挂接行"]["结论"] == "绿入闸"
    assert ov["红入闸告警数"] == 0
    assert ov["入闸一致性"]["一致"] is True

    # 晋升入口可用：入闸已落在测，逐级首跳为预；种子维护窗全开
    p = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert p.status_code == 200, p.text

    # 审计同刷：恰好一条成功入闸，备注带绿入闸裁决与挂接行号
    audits = gate_audits(rid, "入闸")
    assert len(audits) == 1
    assert "绿入闸" in audits[0]["note"]
    assert "挂接成功" in audits[0]["note"]

    # 审计查询口能按关键字捞到
    q = client.get("/api/审计", params={"关联编号": rid, "关键字": "绿入闸"}).json()
    assert len(q) == 1


def test_green_seed_is_promotable_and_consistent(client):
    """种子自带的绿入闸对照单必须自洽且可晋升。"""
    ov = client.get("/api/总览").json()
    assert ov["入闸一致性"]["一致"] is True
    seed = next(x for x in ov["在途"] if x["发版单号"].endswith("0001"))
    assert seed["入闸结论"] == "绿入闸"
    assert seed["晋升可用"] is True
    assert seed["挂接行"]["状态"] == "挂接成功"
