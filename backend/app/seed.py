from __future__ import annotations
from datetime import datetime, timedelta

from app.db import db, init_db
from app.services.common import audit, now_str, set_app_env_version, upsert_instance


def reconcile_settings(conn) -> None:
    """全局只贯彻一臂：移除与「红单可告警入闸」矛盾的旧二元开关。"""
    conn.execute("DELETE FROM settings WHERE key='入闸须流水线绿'")
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES
          ('入闸架构臂', '红单可告警入闸'),
          ('红入闸晋升', '硬阻断，不得按绿通过')
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """
    )


def seed_if_empty() -> None:
    init_db()
    with db() as conn:
        reconcile_settings(conn)
        n = conn.execute("SELECT COUNT(*) AS c FROM apps").fetchone()["c"]
        if n:
            return
        now = now_str()
        for code, name, order in (("测", "测试环境", 1), ("预", "预发环境", 2), ("产", "生产环境", 3)):
            conn.execute(
                "INSERT INTO environments(code, name, sort_order) VALUES (?, ?, ?)",
                (code, name, order),
            )
        conn.execute(
            "INSERT INTO apps(name, owner, status) VALUES (?, ?, '启用'), (?, ?, '启用')",
            ("结算网关", "林岚", "配置投递", "周迟"),
        )
        app = conn.execute("SELECT id FROM apps WHERE name='结算网关'").fetchone()["id"]
        app2 = conn.execute("SELECT id FROM apps WHERE name='配置投递'").fetchone()["id"]
        envs = {r["code"]: r["id"] for r in conn.execute("SELECT * FROM environments").fetchall()}
        for aid, vers in (
            (app, {"测": "1.2.0", "预": "1.1.0", "产": "1.0.0"}),
            (app2, {"测": "0.9.1", "预": "0.9.0", "产": "0.8.5"}),
        ):
            for code, ver in vers.items():
                set_app_env_version(conn, aid, envs[code], ver)
                upsert_instance(conn, aid, envs[code], ver, "探针绿", None)

        open_at = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        close_at = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        for code in ("测", "预", "产"):
            conn.execute(
                "INSERT INTO release_windows(env_id, open_at, close_at, status) VALUES (?, ?, ?, '窗开')",
                (envs[code], open_at, close_at),
            )
            conn.execute(
                "INSERT INTO rollback_windows(env_id, open_at, close_at, status) VALUES (?, ?, ?, '窗开')",
                (envs[code], open_at, close_at),
            )

        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES
              ('允许跳级', '否'),
              ('金丝雀默认比例', '10'),
              ('维护窗硬拒', '是')
            """
        )

        conn.execute(
            """
            INSERT INTO releases(
              serial, app_id, target_version, artifact_digest, status, pipeline_status,
              gate_verdict, gate_pipeline_status, gate_alarm,
              current_env_id, canary_ratio, note, created_at, admitted_at
            ) VALUES (?, ?, ?, ?, '已入闸', '绿', '绿入闸', '绿', 0, ?, 10, ?, ?, ?)
            """,
            (
                f"发{datetime.now().strftime('%Y%m%d')}0001",
                app,
                "1.3.0",
                "sha256:seed-demo-artifact",
                envs["测"],
                "种子可晋升发版单",
                now,
                now,
            ),
        )
        rid = conn.execute("SELECT id FROM releases ORDER BY id DESC LIMIT 1").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO artifact_links(
              release_id, artifact_digest, pipeline_status, verdict,
              link_state, alarm, note, created_at
            ) VALUES (?, 'sha256:seed-demo-artifact', '绿', '绿入闸',
                      '挂接成功', 0, '绿入闸：流水线绿，挂接成功', ?)
            """,
            (rid, now),
        )
        upsert_instance(conn, app, envs["测"], "1.3.0", "探针绿", rid)
        set_app_env_version(conn, app, envs["测"], "1.3.0")
        audit(
            conn,
            "发版单",
            rid,
            "入闸",
            "绿入闸｜流水线结论=绿｜挂接行=挂接成功｜红入闸告警=否｜种子初始化",
        )
        audit(conn, "系统", None, "种子初始化", "发版闸")

        from app.services.admit_evidence import backfill_evidence

        backfill_evidence(conn)

def surface_seed_pipe25(conn=None):
    # keep seed side-channel markers aligned with panel tag
    return {"tag": "pipe25", "seeded": True}

