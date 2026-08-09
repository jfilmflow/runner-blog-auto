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


def pro_limit():
    try:
        return int(os.getenv("PRO_LIMIT", "35"))
    except Exception:
        return 35


def limit_for_plan(plan):
    return pro_limit() if plan == "pro" else free_limit()


def enabled():
    """로그인·사용량 기능 켜짐 여부.
       URL + anon 키만 있으면 ON. (사용량은 로그인한 사용자 토큰으로 DB 함수 호출 →
       service_role 비밀키 불필요. 사용자는 자기 카운트를 못 내림 = 안전.)"""
    return bool(SUPABASE_URL and ANON)


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


def get_usage(token, period):
    """이번 달(period=YYYY-MM) 로그인 사용자의 생성 편수.
       DB 함수 my_usage() 를 '사용자 토큰'으로 호출 → auth.uid() 로 본인 것만 셈."""
    if not enabled() or not token:
        return 0
    st, data = _req(f"{SUPABASE_URL}/rest/v1/rpc/my_usage", method="POST",
                    headers={"apikey": ANON, "Authorization": f"Bearer {token}"},
                    data={"p_period": period})
    if st in (200, 201):
        try:
            return int(data)
        except Exception:
            return 0
    return 0


def get_plan(token):
    """로그인 사용자의 현재 플랜('free'/'pro'). DB 함수 my_plan()을 사용자 토큰으로 호출."""
    if not enabled() or not token:
        return "free"
    st, data = _req(f"{SUPABASE_URL}/rest/v1/rpc/my_plan", method="POST",
                    headers={"apikey": ANON, "Authorization": f"Bearer {token}"},
                    data={})
    if st in (200, 201) and isinstance(data, str) and data:
        return data
    return "free"


def set_plan(uid, plan, status=None, renews_at=None):
    """결제 웹훅에서 호출 — 서비스 키로 subscriptions 테이블에 유저 플랜 기록(upsert).
       서비스 키가 없으면 False (플랜 자동반영 불가)."""
    if not (SUPABASE_URL and SERVICE and uid):
        return False
    url = f"{SUPABASE_URL}/rest/v1/subscriptions?on_conflict=user_id"
    headers = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    row = {"user_id": uid, "plan": plan}
    if status is not None:
        row["status"] = status
    if renews_at is not None:
        row["renews_at"] = renews_at
    st, _ = _req(url, method="POST", headers=headers, data=[row])
    return st in (200, 201, 204)


def increment_usage(token, period):
    """생성 성공 시 편수 +1 (원자적, DB 함수 bump_usage). 새 값 반환 or None.
       사용자 토큰으로 호출하지만 함수가 auth.uid() 기준이라 남의 것/자기 것 조작 불가(증가만)."""
    if not enabled() or not token:
        return None
    st, data = _req(f"{SUPABASE_URL}/rest/v1/rpc/bump_usage", method="POST",
                    headers={"apikey": ANON, "Authorization": f"Bearer {token}"},
                    data={"p_period": period})
    if st in (200, 201):
        try:
            return int(data)
        except Exception:
            return None
    return None
