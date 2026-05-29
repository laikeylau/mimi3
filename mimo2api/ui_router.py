import os
import json
import re
import time
import asyncio
import httpx
from typing import Any
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .auth import (
    create_webui_session_token,
    get_webui_cookie_name,
    get_webui_session_ttl,
    get_webui_username,
    is_ai_auth_enabled,
    is_web_auth_enabled,
    is_webui_authenticated,
    verify_webui_login,
    webui_cookie_secure,
)
from .gateway_state import state

router = APIRouter()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_DIR = os.path.join(ROOT_DIR, "users")


@router.get("/")
async def root_page():
    return RedirectResponse(url="/webui", status_code=307)

@router.get("/webui")
async def webui_page():
    ui_path = os.path.join(os.path.dirname(__file__), "webui.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return Response("webui.html not found", status_code=404)

def _safe_node_label(ws) -> str:
    try:
        if ws and ws.client:
            return f"{ws.client.host}:{ws.client.port}"
    except Exception:
        pass
    return "Unknown"


def _metadata_for_ws(ws) -> dict[str, Any]:
    return state.client_metadata.get(id(ws), {})


def _snapshot_gateway_nodes() -> dict[str, Any]:
    now = time.time()
    nodes = []
    active_count = len(state.active_clients)
    available_count = 0

    for index, client in enumerate(list(state.active_clients)):
        cooldown_until = state.client_cooldowns.get(id(client), 0)
        is_available = cooldown_until <= now
        if is_available:
            available_count += 1
        nodes.append({
            "slot": index,
            "label": _safe_node_label(client),
            "available": is_available,
            "cooldown_until": int(cooldown_until) if cooldown_until else 0,
            "user_id": _metadata_for_ws(client).get("user_id"),
            "account_name": _metadata_for_ws(client).get("account_name"),
            "ph": _metadata_for_ws(client).get("ph"),
        })

    return {
        "active_clients": active_count,
        "available_clients": available_count,
        "cooldown_clients": active_count - available_count,
        "nodes": nodes,
    }


@router.get("/api/system/status")
async def api_status():
    return JSONResponse(_snapshot_gateway_nodes())


@router.get("/api/auth/session")
async def api_auth_session(request: Request):
    auth_enabled = is_web_auth_enabled()
    authenticated = is_webui_authenticated(request)
    return JSONResponse({
        "enabled": auth_enabled,
        "authenticated": authenticated,
        "username": get_webui_username(),
        "ai_auth_enabled": is_ai_auth_enabled(),
    })


@router.post("/api/auth/login")
async def api_auth_login(request: Request):
    if not is_web_auth_enabled():
        return JSONResponse({"ok": True, "enabled": False, "username": get_webui_username()})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求体不是合法 JSON"}, status_code=400)

    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not verify_webui_login(username, password):
        return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)

    response = JSONResponse({"ok": True, "enabled": True, "username": get_webui_username()})
    response.set_cookie(
        key=get_webui_cookie_name(),
        value=create_webui_session_token(get_webui_username()),
        max_age=get_webui_session_ttl(),
        httponly=True,
        samesite="lax",
        secure=webui_cookie_secure(),
        path="/",
    )
    return response


@router.post("/api/auth/logout")
async def api_auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=get_webui_cookie_name(), path="/")
    return response

async def fetch_user_status(data: dict, gateway_snapshot: dict[str, Any] | None = None) -> dict:
    uid = data.get("userId")
    cookies = {
        "serviceToken": data.get("serviceToken", ""),
        "userId": uid,
        "xiaomichatbot_ph": data.get("xiaomichatbot_ph", "")
    }
    url = "https://aistudio.xiaomimimo.com/open-apis/user/mimo-claw/status"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://aistudio.xiaomimimo.com",
        "Referer": "https://aistudio.xiaomimimo.com/",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, cookies=cookies, headers=headers, timeout=5)
            if r.status_code == 401:
                return {
                    **data,
                    "claw_status": "EXPIRED(401)",
                    "remain_sec": 0,
                    "create_probe_status": "SKIPPED",
                    "create_probe_http": 401,
                    "local_online": False,
                    "local_match_mode": "none",
                }
            r_data = r.json()
            st = r_data.get("data", {}).get("status", "UNKNOWN")
            expire_ms = r_data.get("data", {}).get("expireTime")
            remain_sec = max(0, int(int(expire_ms) / 1000 - time.time())) if expire_ms else 0

            create_http = None
            create_probe_status = "UNKNOWN"
            try:
                create_url = f"https://aistudio.xiaomimimo.com/open-apis/user/mimo-claw/create?xiaomichatbot_ph={data.get('xiaomichatbot_ph', '')}"
                r2 = await c.post(create_url, cookies=cookies, headers=headers, timeout=8)
                create_http = r2.status_code
                if r2.status_code == 401:
                    create_probe_status = "AUTH_FAILED"
                elif r2.status_code == 429:
                    create_probe_status = "RATE_LIMITED"
                else:
                    try:
                        d2 = r2.json()
                        create_probe_status = d2.get("data", {}).get("status") or d2.get("msg") or d2.get("message") or f"HTTP_{r2.status_code}"
                    except Exception:
                        create_probe_status = f"HTTP_{r2.status_code}"
            except Exception:
                create_probe_status = "ERROR"

            local_online = False
            local_match_mode = "none"
            owner_node = None
            for node in (gateway_snapshot or {}).get("nodes", []):
                if str(node.get("user_id") or "") == str(uid or ""):
                    owner_node = node
                    break
            if owner_node and owner_node.get("available") and create_probe_status != "AUTH_FAILED" and st == "AVAILABLE":
                local_online = True
                local_match_mode = "exact_user_node"
            elif gateway_snapshot and gateway_snapshot.get("active_clients", 0) > 0 and create_probe_status != "AUTH_FAILED" and st == "AVAILABLE":
                local_online = True
                local_match_mode = "gateway_has_online_node"

            return {
                **data,
                "claw_status": st,
                "remain_sec": remain_sec,
                "create_probe_status": create_probe_status,
                "create_probe_http": create_http,
                "local_online": local_online,
                "local_match_mode": local_match_mode,
                "owner_node": owner_node,
            }
    except Exception:
        return {
            **data,
            "claw_status": "ERROR",
            "remain_sec": 0,
            "create_probe_status": "ERROR",
            "create_probe_http": None,
            "local_online": False,
            "local_match_mode": "none",
        }

@router.get("/api/users/list")
async def api_users_list():
    raw_users = []
    if os.path.exists(USERS_DIR):
        for fn in os.listdir(USERS_DIR):
            if fn.startswith("user_") and fn.endswith(".json"):
                try:
                    with open(os.path.join(USERS_DIR, fn), "r", encoding="utf-8") as f:
                        raw_users.append(json.load(f))
                except:
                    pass

    gateway_snapshot = _snapshot_gateway_nodes()

    # 并发查询所有用户的实例状态
    tasks = [fetch_user_status(rd, gateway_snapshot) for rd in raw_users]
    results = await asyncio.gather(*tasks) if raw_users else []

    users = []
    for data in results:
        users.append({
            "userId": data.get("userId"),
            "name": data.get("name"),
            "serviceToken": data.get("serviceToken"),
            "claw_status": data.get("claw_status", "UNKNOWN"),
            "remain_sec": data.get("remain_sec", 0),
            "create_probe_status": data.get("create_probe_status", "UNKNOWN"),
            "create_probe_http": data.get("create_probe_http"),
            "local_online": data.get("local_online", False),
            "local_match_mode": data.get("local_match_mode", "none"),
            "owner_node": data.get("owner_node")
        })
    return JSONResponse({"users": users, "gateway": gateway_snapshot})

@router.post("/api/users/add")
async def api_users_add(request: Request):
    try:
        body = await request.json()
        raw_text = body.get("raw_text", "")
        # 解析正则提取
        parsed = {}
        for match in re.finditer(r'([a-zA-Z0-9_]+)="?([^;"]+)"?', raw_text):
            parsed[match.group(1)] = match.group(2)
            
        uid = parsed.get("userId")
        st = parsed.get("serviceToken")
        ph = parsed.get("xiaomichatbot_ph")
        
        if not uid or not st or not ph:
            return JSONResponse({"detail": "缺少必要字段 userId, serviceToken 或 xiaomichatbot_ph"}, status_code=400)
            
        os.makedirs(USERS_DIR, exist_ok=True)
        target_file = os.path.join(USERS_DIR, f"user_{uid}.json")
        
        user_data = {
            "userId": uid,
            "serviceToken": st,
            "xiaomichatbot_ph": ph,
            "name": f"Imported_{uid}"
        }
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
            
        return JSONResponse({"status": "ok", "userId": uid})
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

@router.delete("/api/users/delete/{uid}")
async def api_users_delete(uid: str):
    target_file = os.path.join(USERS_DIR, f"user_{uid}.json")
    if os.path.exists(target_file):
        os.remove(target_file)
        return JSONResponse({"status": "ok"})
    return JSONResponse({"detail": "User not found"}, status_code=404)
