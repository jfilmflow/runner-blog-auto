# -*- coding: utf-8 -*-
"""
로그인·사용량(P3) — Supabase 연동.

- verify_token(token): 프론트에서 온 access token을 Supabase Auth로 검증 → 유저(id,email)
- get_usage / increment_usage: 서비스 키로 usage_counters 테이블 조회·증가 (이번 달 편수)
- enabled(): SUPABASE_URL + SERVICE_ROLE_KEY 가 있으면 로그인·과금 기능 ON.
             (없으면 예전처럼 로그인 없이 열려 있는 상태 = 로컬/개발용)

환경변수:
  SUPABASE_URL                (예: https://xxxx.supabase.co)
  SUPABASE_ANON_KEY           (공개용 anon 키 — 토큰 검증 호출에 필요)
  SUPABASE_SERVICE_ROLE_KEY   (비밀 — 서버에서만, usage 테이블 읽고 쓰기)
  FREE_LIMIT                  (무료 월 편수, 기본 3)
"""
import os, json, urllib.request, urllib.error

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
ANON = os.getenv("SUPABASE_ANON_KEY") or ""
SERVICE = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""


def free_limit():
    try:
        return int(os.getenv("FREE_LIMIT", "3"))
    except Exception:
        return 3


def enabled():
    """로그인·사용량 기능 켜짐 여부 (URL + 서비스키 있어야 authoritative 하게 셀 수 있음)."""
    return bool(SUPABASE_URL and SERVICE)


def public_config():
    return {
        "authEnabled": enabled(),
        "supabaseUrl": SUPABASE_URL,
        "supabaseAnonKey": ANON,
        "freeLimit": free_limit(),
    }


def _req(url, method="GET", headers=None, data=None, timeout=10):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except Exception:
        return 0, None


def verify_token(token):
    """access token → 유저 dict {id,email} 또는 None."""
    if not token or not SUPABASE_URL:
        return None
    st, data = _req(f"{SUPABASE_URL}/auth/v1/user",
                    headers={"apikey": ANON or SERVICE, "Authorization": f"Bearer {token}"})
    if st == 200 and isinstance(data, dict) and data.get("id"):
        return {"id": data["id"], "email": data.get("email")}
    return None


def get_usage(uid, period):
    """이번 달(period=YYYY-MM) 해당 유저의 생성 편수."""
    if not enabled():
        return 0
    url = f"{SUPABASE_URL}/rest/v1/usage_counters?user_id=eq.{uid}&period=eq.{period}&select=count"
    st, data = _req(url, headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"})
    if st == 200 and isinstance(data, list) and data:
        try:
            return int(data[0].get("count", 0))
        except Exception:
            return 0
    return 0


def increment_usage(uid, period):
    """생성 성공 시 편수 +1 (원자적, DB 함수 increment_usage 사용). 새 값 반환 or None."""
    if not enabled():
        return None
    url = f"{SUPABASE_URL}/rest/v1/rpc/increment_usage"
    st, data = _req(url, method="POST",
                    headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"},
                    data={"p_user": uid, "p_period": period})
    if st in (200, 201):
        try:
            return int(data)
        except Exception:
            return None
    return None
