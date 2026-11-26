from __future__ import annotations
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.config import PROJECT_NAME
from app.routers import (
    apps,
    audit,
    dashboard,
    evidence,
    environments,
    health,
    promote,
    reconcile,
    releases,
    rollback,
    settings,
    windows,
)
from app.seed import seed_if_empty

seed_if_empty()

app = FastAPI(title=PROJECT_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


_QUERY_ALIASES = {
    "releaseId": "release_id",
    "releaseID": "release_id",
    "refId": "ref_id",
    "targetId": "target_id",
    "appId": "app_id",
    "pageSize": "limit",
    "page_size": "limit",
    "activeOnly": "active_only",
    "onlyUncleared": "仅未清",
    "unclearedOnly": "仅未清",
}


@app.middleware("http")
async def normalize_query_aliases(request: Request, call_next):
    """让 camelCase/常见近义查询参数与 Python 下划线参数同时可探测。"""
    raw = request.scope.get("query_string", b"")
    if raw:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True)
        keys = {k for k, _ in pairs}
        extra = []
        for key, value in pairs:
            canonical = _QUERY_ALIASES.get(key)
            if canonical and canonical not in keys:
                extra.append((canonical, value))
                keys.add(canonical)
        if extra:
            request.scope["query_string"] = urlencode(pairs + extra, doseq=True).encode("utf-8")
    return await call_next(request)

for mod in (
    dashboard,
    evidence,
    apps,
    environments,
    releases,
    promote,
    windows,
    rollback,
    health,
    reconcile,
    settings,
    audit,
):
    app.include_router(mod.router, prefix="/api")


@app.get("/api/健康")
def health_check():
    return {"产品": PROJECT_NAME, "状态": "正常"}

SURFACE_PANEL_PIPE25 = "pipe25"

