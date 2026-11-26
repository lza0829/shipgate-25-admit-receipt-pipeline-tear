"""入闸后再打红夹具（另立时序，不混进「入闸当下」判定）。

关键：入闸结论是入闸当下的冻结事实，事后翻红不得改写它；
但当下红灯要单独打「入闸后翻红」标、写单独审计、冻结后续晋升。
"""

from __future__ import annotations

from conftest import http_admit, http_pipeline, http_release


def test_post_admit_flip_red_keeps_gate_verdict_but_freezes_promotion(client, app_id):
    rid = http_release(client, app_id=app_id, version="4.2.0", digest="sha256:post-red")
    http_pipeline(client, rid, "绿")
    admitted = http_admit(client, rid).json()
    assert admitted["入闸结论"] == "绿入闸"

    # 入闸后再打红
    flipped = http_pipeline(client, rid, "红")
    assert flipped["入闸结论"] == "绿入闸"          # 不回写
    assert flipped["入闸时流水线"] == "绿"          # 闸时快照不变
    assert flipped["流水线"] == "红"
    assert flipped["入闸后翻红"] is True
    assert flipped["告警标"] == "入闸后翻红"
    assert flipped["红入闸告警"] is False           # 它不是「红入闸」

    # 挂接行仍是入闸当下的绿结论（历史事实），但整单出现翻红标
    assert flipped["挂接行"]["结论"] == "绿入闸"

    # 读侧：当下红 -> 晋升冻结，原因明确区分于红入闸
    assert flipped["晋升可用"] is False
    assert "入闸后流水线翻红" in flipped["晋升不可用原因"]

    p = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "测"})
    assert p.status_code == 400
    assert "翻红" in p.json()["detail"]

    # 审计里有独立的翻红记录，且说明入闸结论不改写
    rows = client.get("/api/审计", params={"关联编号": rid, "关键字": "翻红"}).json()
    assert rows, "必须留下入闸后翻红审计"
    assert "不被改写" in rows[0]["备注"] or "不变" in rows[0]["备注"]

    # 总览在途仍计在途，但带翻红（区别于红入闸告警数）
    ov = client.get("/api/总览").json()
    row = next(x for x in ov["在途"] if x["编号"] == rid)
    assert row["入闸结论"] == "绿入闸"
    assert row["入闸后翻红"] is True
    assert ov["红入闸告警数"] == 0  # 没有红入闸单


def test_post_admit_red_then_green_restores_promotion_without_rewriting_gate(client, app_id):
    rid = http_release(client, app_id=app_id, version="4.2.1", digest="sha256:post-red-green")
    http_pipeline(client, rid, "绿")
    http_admit(client, rid)

    http_pipeline(client, rid, "红")
    assert client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "测"}).status_code == 400

    back = http_pipeline(client, rid, "绿")
    assert back["入闸后翻红"] is False
    assert back["晋升可用"] is True
    # 入闸已在测，恢复后可逐级晋升到预
    assert client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"}).status_code == 200


def test_post_admit_red_on_red_gate_remains_red_gate(client):
    """红入闸后红绿怎么翻，红入闸事实都不变，晋升始终不可用。"""
    rid = http_release(client, app_id=2, version="7.0.0", digest="sha256:red-fixed")
    http_pipeline(client, rid, "红")
    http_admit(client, rid)

    for color in ("绿", "红", "绿"):
        body = http_pipeline(client, rid, color)
        assert body["入闸结论"] == "红入闸"
        assert body["红入闸告警"] is True
        assert body["晋升可用"] is False
        assert client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "测"}).status_code == 400
