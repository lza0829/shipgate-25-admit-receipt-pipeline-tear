"""并发竞态夹具：红绿翻转 × 入闸、重复双击入闸。

要求可复现：不依赖偶发时序发现 defect，而是用栅栏制造真实竞争并对每一轮
断言全局不变量；另含确定性的写锁互斥证明与真实 uvicorn HTTP 并发。
"""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from threading import Barrier

import httpx
import pytest
import uvicorn

from app.db import connect, db
from app.main import app
from app.services.gate import GateRejected, admit_release, gate_consistency, promotion_block_reason, record_pipeline
from conftest import (
    active_links,
    create_app_direct,
    create_release_direct,
    gate_audits,
    raw_release,
)


def _assert_gate_verdict_coherent_everywhere(rid: int):
    raw = raw_release(rid)
    verdict = raw["gate_verdict"]
    assert raw["status"] == "已入闸"
    assert verdict in ("绿入闸", "红入闸")
    # 闸时快照必须与裁决同命运
    assert raw["gate_pipeline_status"] == ("绿" if verdict == "绿入闸" else "红")
    assert raw["gate_alarm"] == (1 if verdict == "红入闸" else 0)

    # 挂接行唯一有效，且结论/告警与发版单完全一致
    links = active_links(rid)
    assert len(links) == 1
    link = links[0]
    assert link["link_state"] == "挂接成功"
    assert link["verdict"] == verdict
    assert link["alarm"] == raw["gate_alarm"]
    assert link["pipeline_status"] == raw["gate_pipeline_status"]

    # 审计恰好一条成功入闸，且备注以同一裁决开头
    audits = gate_audits(rid, "入闸")
    assert len(audits) == 1
    assert audits[0]["note"].startswith(verdict)
    assert "挂接成功" in audits[0]["note"]

    # 晋升口径与裁决/当下颜色一致
    block = promotion_block_reason(raw)
    if verdict == "红入闸":
        assert block and "红入闸" in block
    elif raw["pipeline_status"] == "红":
        assert block and "翻红" in block
    else:
        assert block is None

    # 全库一致性视图不得检出脏挂接/无行单/结论打架
    with db() as conn:
        cons = gate_consistency(conn)
    assert cons["挂接中脏行"] == 0
    assert cons["无挂接行的在途单"] == []
    assert cons["裁决不一致挂接行"] == []


def test_repeated_flip_vs_admit_races_never_split_outcomes():
    """每轮一个独立应用/单，栅栏同时释放「翻红」与「入闸」，重复 24 轮。"""
    rounds = 24
    for i in range(rounds):
        aid = create_app_direct(f"竞态应用{i}")
        rid = create_release_direct(aid, version=f"1.0.{i}", pipeline="绿")
        barrier = Barrier(2)
        errors: list[Exception] = []

        def flip():
            try:
                barrier.wait()
                record_pipeline(rid, "红")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def admit():
            try:
                barrier.wait()
                admit_release(rid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=flip)
        t2 = threading.Thread(target=admit)
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive(), "竞态线程超时"
        assert errors == [], f"竞态中出现非预期异常: {errors!r}"

        _assert_gate_verdict_coherent_everywhere(rid)

        # 两种合法结局必居其一，且都自洽：
        raw = raw_release(rid)
        if raw["gate_verdict"] == "红入闸":
            assert raw["gate_pipeline_status"] == "红"  # 翻红先得锁
        else:
            assert raw["gate_verdict"] == "绿入闸"
            assert raw["gate_pipeline_status"] == "绿"  # 入闸先得锁，红是入闸后翻的
            assert raw["pipeline_status"] == "红"


def test_concurrent_double_click_admit_has_single_outcome():
    """同一单被同时点 5 次入闸：恰好 1 笔成功，其余驳回，挂接/审计只有一套。"""
    aid = create_app_direct("双击应用")
    rid = create_release_direct(aid, pipeline="绿")
    n = 5
    barrier = Barrier(n)
    outcomes: list[tuple[str, int | None]] = []
    unexpected: list[Exception] = []

    def worker():
        barrier.wait()
        try:
            admit_release(rid)
            outcomes.append(("ok", 200))
        except GateRejected as gr:
            outcomes.append(("rejected", gr.status_code))
        except Exception as exc:  # noqa: BLE001
            unexpected.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert unexpected == [], f"出现锁/库异常而非业务驳回: {unexpected!r}"
    oks = [o for o in outcomes if o[0] == "ok"]
    assert len(oks) == 1, outcomes
    assert len(outcomes) == n
    assert all(o[0] == "rejected" for o in outcomes if o[0] != "ok")

    assert raw_release(rid)["status"] == "已入闸"
    assert len(active_links(rid)) == 1
    assert len(gate_audits(rid, "入闸")) == 1


def test_immediate_write_lock_serializes_pipeline_flip():
    """确定性证明：入闸的 BEGIN IMMEDIATE 持有写锁时，流水线翻色写必须排队。"""
    holder = connect()
    try:
        holder.execute("BEGIN IMMEDIATE")  # 模拟入闸已进入裁决临界区并持锁

        waiter = connect()
        waiter.execute("PRAGMA busy_timeout=300")
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            waiter.execute("BEGIN IMMEDIATE")
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, "写锁互斥未按预期生效"
        waiter.close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    after = connect()
    after.execute("BEGIN IMMEDIATE")  # 锁释放后立即可进
    after.execute("ROLLBACK")
    after.close()


# ---------------------------------------------------------------------------
# 真实 HTTP 并发：uvicorn 线程内跑同一 app（同一临时库）
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(base + "/api/健康", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:  # pragma: no cover
        raise RuntimeError("live server 启动超时")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def test_http_redgreen_flip_and_admit_concurrency(live_server):
    base = live_server
    plan = []
    for i in range(10):
        aid = httpx.post(base + "/api/应用", json={"名称": f"HTTP竞态应用{i}"}).json()["编号"]
        rid = httpx.post(
            base + "/api/发版单",
            json={"应用编号": aid, "目标版本": f"2.0.{i}", "制品摘要": f"sha256:race-{i}"},
        ).json()["编号"]
        httpx.post(f"{base}/api/发版单/{rid}/流水线", json={"流水线": "绿"})
        plan.append(rid)

    barrier = Barrier(len(plan) * 2)

    def pair(rid: int):
        def flip():
            barrier.wait()
            r = httpx.post(f"{base}/api/发版单/{rid}/流水线", json={"流水线": "红"}, timeout=10)
            assert r.status_code == 200

        def admit():
            barrier.wait()
            r = httpx.post(f"{base}/api/发版单/{rid}/入闸", timeout=10)
            assert r.status_code == 200, r.text

        tf = threading.Thread(target=flip)
        ta = threading.Thread(target=admit)
        tf.start(); ta.start(); tf.join(15); ta.join(15)

    pair_threads = []
    for rid in plan:
        t = threading.Thread(target=pair, args=(rid,))
        pair_threads.append(t)
    # pair 内部又起两个线程；直接全部启动即可，barrier 计数为 20
    for t in pair_threads:
        t.start()
    for t in pair_threads:
        t.join(30)

    for rid in plan:
        got = httpx.get(f"{base}/api/发版单/{rid}").json()
        assert got["状态"] == "已入闸"
        assert got["挂接行"] and got["挂接裁决一致"] is True
        assert got["入闸结论"] in ("绿入闸", "红入闸")
        ov = httpx.get(base + "/api/总览").json()
        assert ov["入闸一致性"]["一致"] is True
