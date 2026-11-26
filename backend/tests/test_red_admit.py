"""红入闸夹具（架构臂：红单可告警入闸）。

红流水线允许入闸，但红入闸告警事实必须可核：发版单红裁决、告警标、
红结论挂接行、总览露出、晋升硬不可用、对账不单列绿通过、审计可查，全部对齐。
"""

from __future__ import annotations

from conftest import gate_audits, http_admit, http_pipeline, http_release


def _admit_red(client, app_id=2, digest="sha256:red-1"):
    rid = http_release(client, app_id=app_id, version="9.9.9", digest=digest)
    http_pipeline(client, rid, "红")
    return rid


def test_red_admit_marks_every_surface_with_same_verdict(client):
    rid = _admit_red(client)

    # 读侧判定：红 -> 可入闸但拟定红入闸告警
    d = client.get(f"/api/发版单/{rid}/入闸判定").json()
    assert d["可入闸"] is True
    assert d["裁决"] == "红入闸"
    assert d["告警"] is True
    assert "红单告警" in d["判定"]

    resp = http_admit(client, rid)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 发版单态 + 红入闸告警标
    assert body["状态"] == "已入闸"
    assert body["入闸结论"] == "红入闸"
    assert body["入闸时流水线"] == "红"
    assert body["入闸告警标记"] == 1
    assert body["红入闸告警"] is True
    assert body["告警标"] == "红入闸告警"

    # 入闸回执与流水线结论同一判定
    assert body["入闸回执"]["裁决"] == "红入闸"
    assert body["入闸回执"]["告警"] is True

    # 制品挂接行不得假成功/不得漂绿：状态成功但结论是红入闸且带告警
    link = body["挂接行"]
    assert link["状态"] == "挂接成功"
    assert link["结论"] == "红入闸"
    assert link["告警"] is True
    assert "不得视作绿通过" in link["备注"]
    assert body["挂接裁决一致"] is True

    # 总览必须露出红入闸，且仍计在途（臂 B：允许入闸）
    ov = client.get("/api/总览").json()
    assert ov["红入闸告警数"] >= 1
    red_cell = ov["红入闸告警数"]
    assert isinstance(red_cell, int) and red_cell >= 1
    row = next(x for x in ov["在途"] if x["编号"] == rid)
    assert row["状态"] == "已入闸"
    assert row["入闸结论"] == "红入闸"
    assert row["红入闸告警"] is True
    assert row["告警标"] == "红入闸告警"
    assert row["挂接行"]["结论"] == "红入闸"

    # 晋升入口可用性：后端硬拒，读侧也给出不可用
    assert body["晋升可用"] is False
    assert "红入闸" in body["晋升不可用原因"]
    for target in ("测", "预", "产"):
        p = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": target})
        assert p.status_code == 400
        assert "红入闸" in p.json()["detail"]

    # 红入闸不得产生任何晋升事件 / 环境版本推进
    rc = client.get("/api/对账").json()
    assert rc["发版统计"]["红入闸在途"] >= 1
    assert rc["发版统计"]["按绿通过口径在途"] == rc["发版统计"]["绿入闸在途"]
    # 种子绿单 1 + 本红单 1：绿口径不含红
    assert rc["发版统计"]["绿入闸在途"] == 1

    # 审计同刷：入闸行可核红入闸事实
    audits = gate_audits(rid, "入闸")
    assert len(audits) == 1
    assert "红入闸" in audits[0]["note"]
    assert "告警=是" in audits[0]["note"]
    assert "挂接成功" in audits[0]["note"]

    q = client.get("/api/审计", params={"关键字": "红入闸告警", "类别": "发版单"}).json()
    assert any(x["关联编号"] == rid and x["动作"] == "入闸" for x in q)


def test_red_admit_then_later_green_cannot_launder_verdict(client):
    """入闸后流水线转绿，不能把红入闸洗成绿通过。"""
    rid = _admit_red(client, digest="sha256:red-2")
    assert http_admit(client, rid).status_code == 200
    client.post(f"/api/发版单/{rid}/流水线", json={"流水线": "绿"})
    body = client.get(f"/api/发版单/{rid}").json()
    assert body["入闸结论"] == "红入闸"  # 冻结事实不改写
    assert body["红入闸告警"] is True
    assert body["流水线"] == "绿"
    assert body["晋升可用"] is False  # 仍不得按绿通过
    p = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "测"})
    assert p.status_code == 400


def test_red_and_green_fixtures_are_separate_and_counted_apart(client, make_app):
    """红/绿两套夹具同库并存时，总览与对账把两套结局分清楚。"""
    green_app = make_app("绿对照应用")
    red_app = make_app("红告警应用")
    green = http_release(client, app_id=green_app, version="1.0.0", digest="sha256:g")
    http_pipeline(client, green, "绿")
    assert http_admit(client, green).status_code == 200

    red = _admit_red(client, app_id=red_app, digest="sha256:r")
    assert http_admit(client, red).status_code == 200

    ov = client.get("/api/总览").json()
    by_id = {x["编号"]: x for x in ov["在途"]}
    assert by_id[green]["入闸结论"] == "绿入闸"
    assert by_id[red]["入闸结论"] == "红入闸"
    # 种子绿 + 夹具绿 = 2 绿；夹具红 = 1 红
    assert ov["绿入闸在途数"] == 2
    assert ov["红入闸告警数"] == 1
