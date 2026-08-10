# -*- coding: utf-8 -*-
"""
러너 블로그 생성기 - 웹 서버 (글로벌 버전)

- '/'                    : UI(index.html)
- '/api/generate'        : 러닝 이야기+사진 → 블로그 글·이미지·SEO 생성 (JSON)
- '/images/<f>'          : 생성된 이미지 서빙
- '/api/export/images'   : 생성된 이미지들을 zip으로 다운로드
(네이버 자동발행은 제거 — 완성본을 복사/다운로드해서 원하는 블로그 어디든 붙여넣는 방식)
"""
import os, sys, io, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from flask import Flask, request, jsonify, send_from_directory, send_file
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

import engine as engine_mod
import keyword_seo as keyword_mod
import main as pipeline           # prepare_images 재사용
import images as images_mod       # 카드 하단 브랜드 문구 언어 전환용
import authz                      # 로그인·사용량(P3)
from datetime import datetime, timezone

OUT = os.path.join(ROOT, "output")
IMG_DIR = os.path.join(OUT, "images")
app = Flask(__name__, static_folder=None)


def _label(spec):
    return spec.get("headline") or spec.get("caption") or spec.get("type", "이미지")


# 출력 언어 지시문 (UI에서 고른 언어로 블로그 글까지 생성)
_LANG_NAME = {
    "en": "English", "ko": "Korean (한국어)", "ja": "Japanese (日本語)",
    "zh": "Simplified Chinese (简体中文)", "es": "Spanish (Español)",
}


def _lang_directive(lang):
    name = _LANG_NAME.get((lang or "").lower())
    if not name:
        return ""   # 모르는 코드면 프롬프트 기본(한국어) 유지
    return (
        f"\n\n[OUTPUT LANGUAGE — 최우선]\n"
        f"Write EVERYTHING (title, title_options, body, hashtags, image card text/captions, "
        f"main_keyword, sub_keywords) in {name}. "
        f"Localize naturally for a native {name} reader — do NOT translate word-for-word.\n"
        f"EXCEPTION: keep the image markers EXACTLY as [사진1], [사진2], ... (do not translate or renumber them). "
        f"Also keep any Unsplash photo `query` field in English.\n"
        f"The user's source text below may be in another language; still output in {name}."
    )


def _free_tier_directive():
    """무료 플랜: 짧은 글 + 이미지 2장 (Pro 업셀용 품질 게이팅). 언어 중립 지시."""
    return ("\n\n[FREE TIER — REQUIRED]\n"
            "Keep the body SHORT: about 1,100-1,300 characters (or ~200-260 words for English/Spanish).\n"
            "Make EXACTLY 2 images: [사진1] cover thumbnail + [사진2] one card. "
            "Do NOT put [사진3] or higher in the body or in the images list. "
            "(Keep the [사진N] markers literally as-is.)")


def _apply_free_limits(result):
    """무료 플랜 결과 후처리 — 이미지 2장 초과 제거 + body의 [사진3+] 마커 제거."""
    import re
    result["images"] = [im for im in result.get("images", []) if int(im.get("id", 0) or 0) <= 2]
    body = result.get("body", "") or ""
    body = re.sub(r"\[사진(?:[3-9]|\d{2,})\]", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    result["body"] = body
    return result


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/terms")
def terms():
    return send_from_directory(HERE, "terms.html")


@app.route("/privacy")
def privacy():
    return send_from_directory(HERE, "privacy.html")


@app.route("/refund")
def refund():
    return send_from_directory(HERE, "refund.html")


@app.route("/api/config")
def api_config():
    """프론트가 로그인 붙일 때 필요한 공개 설정(URL·anon키·무료한도)."""
    return jsonify(authz.public_config())


def _bearer():
    h = request.headers.get("Authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _period():
    return datetime.now(timezone.utc).strftime("%Y-%m")


@app.route("/api/usage")
def api_usage():
    """로그인 직후 남은/사용 횟수를 바로 보여주기 위한 조회 (생성 안 해도 표시)."""
    if not authz.enabled():
        return jsonify({})
    token = _bearer()
    user = authz.verify_token(token)
    if not user:
        return jsonify({}), 401
    plan = authz.get_plan(token)
    lim = authz.limit_for_plan(plan)
    used = authz.get_usage(token, _period())
    return jsonify({"used": used, "limit": lim, "remaining": max(0, lim - used),
                    "plan": plan, "email": user.get("email")})


@app.route("/images/<path:fname>")
def images(fname):
    return send_from_directory(IMG_DIR, fname)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    text = (request.form.get("text") or "").strip()
    lang = (request.form.get("lang") or "").strip()
    answers = (request.form.get("answers") or "").strip()   # 스마트 후속답변(프론트에서 조립한 재료)
    provider = os.getenv("ENGINE_PROVIDER", "claude")

    # ── 로그인·사용량(P3) ── 로그인 기능이 켜져 있으면 검증 + 무료 한도 확인
    user = None
    token = _bearer()
    plan = "free"
    if authz.enabled():
        user = authz.verify_token(token)
        if not user:
            return jsonify({"error": "로그인이 필요해요.", "auth_required": True}), 401
        plan = authz.get_plan(token)
        limit = authz.limit_for_plan(plan)
        used = authz.get_usage(token, _period())
        if used >= limit:
            return jsonify({
                "error": f"이번 달 {limit}편을 모두 사용했어요.",
                "limit_reached": True, "used": used, "limit": limit, "plan": plan,
            }), 402

    # 업로드된 러닝 사진 저장 (AI가 읽고 본문에 삽입)
    up_dir = os.path.join(OUT, "uploads")
    if os.path.isdir(up_dir):
        import shutil; shutil.rmtree(up_dir, ignore_errors=True)
    os.makedirs(up_dir, exist_ok=True)
    photos = []
    for i, f in enumerate(request.files.getlist("shots")):
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1] or ".jpg"
            dest = os.path.join(up_dir, f"{i:02d}{ext}")
            f.save(dest); photos.append(dest)

    if not text and not photos:
        return jsonify({"error": "러닝 이야기(글) 또는 러닝 사진 중 하나는 넣어주세요."}), 400

    story = text                            # 언어별 네이티브 프롬프트가 출력 언어를 처리
    if lang and lang.lower() not in ("ko", "en", "ja", "zh", "es"):
        story += _lang_directive(lang)      # 지원 목록 밖 언어일 때만 보조 지시
    if plan != "pro":                       # 무료 플랜: 짧은 글 + 이미지 2장
        story += _free_tier_directive()
    try:
        result = engine_mod.generate(story, provider=provider, photo_paths=photos,
                                     lang=lang, extra_context=answers or None)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"글 생성 실패 [{type(e).__name__}]: {e}"}), 500
    if plan != "pro":
        result = _apply_free_limits(result)

    kws = [result.get("main_keyword", "")] + result.get("sub_keywords", [])
    result["keyword_report"] = keyword_mod.analyze([k for k in kws if k])

    images_mod.set_lang(lang)     # 카드 하단 브랜드 문구를 선택 언어로
    image_paths = pipeline.prepare_images(result, user_photos=photos)

    specs = sorted(result.get("images", []), key=lambda x: int(x.get("id", 0)))
    used = {os.path.basename(p): p for p in image_paths}
    imgs_out = []
    for s in specs:
        sid = int(s.get("id", 0))
        cand = [b for b in used if b.startswith(f"{sid:02d}_")]
        fname = cand[0] if cand else None
        imgs_out.append({"id": sid, "type": s.get("type"), "label": _label(s),
                         "url": f"/images/{fname}" if fname else None, "file": fname})

    # 생성 성공 → 이번 달 편수 +1, 남은 편수 응답에 포함
    usage = None
    if user:
        new_count = authz.increment_usage(token, _period())
        lim = authz.limit_for_plan(plan)
        cnt = new_count if new_count is not None else authz.get_usage(token, _period())
        usage = {"used": cnt, "limit": lim, "remaining": max(0, lim - cnt),
                 "plan": plan, "email": user.get("email")}

    return jsonify({
        "title": result.get("title"),
        "title_options": [result.get("title")] + result.get("title_options", []),
        "main_keyword": result.get("main_keyword"),
        "keyword_report": result.get("keyword_report", []),
        "hashtags": result.get("hashtags", []),
        "body": result.get("body"),
        "images": imgs_out,
        "usage": usage,
    })


@app.route("/api/export/images", methods=["POST"])
def export_images():
    """생성된 이미지 파일명 리스트를 받아 zip으로 묶어 다운로드."""
    data = request.get_json(force=True, silent=True) or {}
    files = data.get("files", [])
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for i, fn in enumerate(files, start=1):
            fn = os.path.basename(fn or "")
            p = os.path.join(IMG_DIR, fn)
            if fn and os.path.exists(p):
                z.write(p, arcname=f"{i:02d}_{fn.split('_',1)[-1]}")
    mem.seek(0)
    return send_file(mem, mimetype="application/zip", as_attachment=True,
                     download_name="running-blog-images.zip")


@app.route("/api/lemon-webhook", methods=["POST"])
def lemon_webhook():
    """Lemon Squeezy 결제 웹훅 — 결제/구독 상태에 따라 유저 플랜(pro/free)을 자동 반영.
       서명(X-Signature)을 LEMON_WEBHOOK_SECRET으로 검증한다."""
    import hmac, hashlib
    secret = os.getenv("LEMON_WEBHOOK_SECRET", "")
    raw = request.get_data()
    if secret:
        sig = request.headers.get("X-Signature", "")
        digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, sig):
            return jsonify({"error": "bad signature"}), 401

    data = request.get_json(force=True, silent=True) or {}
    meta = data.get("meta", {}) or {}
    event = meta.get("event_name", "")
    custom = meta.get("custom_data", {}) or {}
    uid = custom.get("user_id")
    attrs = (data.get("data", {}) or {}).get("attributes", {}) or {}
    status = attrs.get("status", "")          # active, on_trial, past_due, cancelled, expired, unpaid, paused
    renews_at = attrs.get("renews_at")

    # 접근 유지 상태 = pro, 그 외(만료/미납/일시정지) = free
    active_like = {"active", "on_trial", "past_due", "cancelled"}
    if event.startswith("subscription"):
        plan = "pro" if status in active_like else "free"
        if uid:
            ok = authz.set_plan(uid, plan, status=status, renews_at=renews_at)
            print(f"[webhook] {event} status={status} user={uid} -> plan={plan} ok={ok}")
    return jsonify({"received": True}), 200


# ── USDT(크립토) 결제: NOWPayments ─────────────────────────────
import json as _json, calendar
CRYPTO_PLANS = {"1m": (12, 1), "6m": (60, 6), "12m": (96, 12)}   # 요금제키 → (USD 가격, 개월수)


def _add_months(dt, months):
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d)


def _app_base():
    return (os.getenv("APP_BASE_URL") or request.host_url or "https://runner-blog-auto.onrender.com").rstrip("/")


@app.route("/api/crypto/create", methods=["POST"])
def crypto_create():
    """로그인 유저용 NOWPayments USDT 인보이스 생성 → 결제 페이지 URL 반환."""
    key = os.getenv("NOWPAYMENTS_API_KEY", "")
    if not key:
        return jsonify({"error": "크립토 결제가 아직 설정되지 않았어요."}), 503
    token = _bearer()
    user = authz.verify_token(token) if authz.enabled() else None
    if authz.enabled() and not user:
        return jsonify({"error": "로그인이 필요해요.", "auth_required": True}), 401
    body = request.get_json(force=True, silent=True) or {}
    plan_key = body.get("plan") or request.form.get("plan")
    if plan_key not in CRYPTO_PLANS:
        return jsonify({"error": "요금제를 확인해주세요."}), 400
    price, months = CRYPTO_PLANS[plan_key]
    uid = (user or {}).get("id", "anon")
    base = _app_base()
    payload = {
        "price_amount": price, "price_currency": "usd",
        "order_id": f"{uid}|{months}",
        "order_description": f"Runner Blog Pro · {months} month(s)",
        "ipn_callback_url": base + "/api/nowpayments-webhook",
        "success_url": base + "/?paid=1", "cancel_url": base + "/?canceled=1",
        "is_fixed_rate": True,
    }
    import urllib.request, urllib.error
    req = urllib.request.Request(
        "https://api.nowpayments.io/v1/invoice",
        data=_json.dumps(payload).encode("utf-8"), method="POST",
        headers={"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            inv = _json.loads(r.read().decode("utf-8"))
        return jsonify({"invoice_url": inv.get("invoice_url"), "id": inv.get("id")})
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"NOWPayments 오류({e.code})", "detail": e.read().decode("utf-8")[:300]}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/nowpayments-webhook", methods=["POST"])
def nowpayments_webhook():
    """NOWPayments IPN — 결제 완료(finished) 시 유저를 Pro로(개월수만큼 만료일 부여)."""
    import hmac, hashlib
    secret = os.getenv("NOWPAYMENTS_IPN_SECRET", "")
    data = request.get_json(force=True, silent=True) or {}
    if secret:
        sig = request.headers.get("x-nowpayments-sig", "")
        sorted_body = _json.dumps(data, sort_keys=True, separators=(",", ":"))
        digest = hmac.new(secret.encode("utf-8"), sorted_body.encode("utf-8"), hashlib.sha512).hexdigest()
        if not hmac.compare_digest(digest, sig):
            return jsonify({"error": "bad signature"}), 401
    status = (data.get("payment_status") or "").lower()
    order_id = data.get("order_id") or ""
    if status == "finished" and "|" in order_id:
        uid, _, months_s = order_id.partition("|")
        try:
            months = int(months_s)
        except Exception:
            months = 1
        renews = _add_months(datetime.now(timezone.utc), months).isoformat()
        ok = authz.set_plan(uid, "pro", status="crypto", renews_at=renews)
        print(f"[nowpayments] finished user={uid} +{months}mo -> pro ok={ok}")
    return jsonify({"received": True}), 200


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  러너 블로그 생성기  →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
