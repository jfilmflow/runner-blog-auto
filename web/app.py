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
import mailer                     # 결제 확인 이메일(#58)
import threading                  # 문체 프로필 백그라운드 재생성(P5)
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


@app.route("/ranking")
def ranking_page():
    return send_from_directory(HERE, "ranking.html")


@app.route("/og.png")
def og_image():
    resp = send_from_directory(HERE, "og.png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/banner/<path:fname>")
def banner_image(fname):
    resp = send_from_directory(os.path.join(HERE, "banner"), fname)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


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


@app.route("/api/questions", methods=["POST"])
def api_questions():
    """러너 메모(+사진 개수)를 읽고 재료를 늘릴 스마트 후속질문 3개를 선택 언어로 반환.
       로그인 기능이 켜져 있으면 로그인 필요(생성과 동일 게이트). 실패해도 기본질문 폴백."""
    text = (request.form.get("text") or "").strip()
    lang = (request.form.get("lang") or "ko").strip()
    try:
        n_photos = int(request.form.get("n_photos") or 0)
    except Exception:
        n_photos = 0
    provider = os.getenv("ENGINE_PROVIDER", "claude")

    if authz.enabled():
        user = authz.verify_token(_bearer())
        if not user:
            return jsonify({"error": "로그인이 필요해요.", "auth_required": True}), 401

    try:
        qs = engine_mod.smart_questions(text, lang=lang, n_photos=n_photos, provider=provider)
    except Exception:
        import traceback; traceback.print_exc()
        qs = engine_mod._fallback_questions(lang)
    return jsonify({"questions": qs})


def _rebuild_style_async(token, lang, provider):
    """문체 프로필 재생성을 백그라운드에서 수행 → 생성 응답을 지연시키지 않음."""
    def _work():
        try:
            samples = authz.get_style_samples(token, limit=12)
            prof = engine_mod.build_style_profile(samples, lang=lang, provider=provider)
            if prof:
                authz.set_style_profile(token, prof, len(samples), lang)
        except Exception as e:
            print("[문체] 백그라운드 재생성 실패:", e)
    try:
        threading.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


@app.route("/api/style/save", methods=["POST"])
def api_style_save():
    """유저가 최종 편집/발행한 글을 문체 샘플로 저장(가장 강한 신호). 프론트에서 복사/발행 시 호출."""
    if not authz.enabled():
        return jsonify({"ok": False}), 200
    token = _bearer()
    user = authz.verify_token(token)
    if not user:
        return jsonify({"error": "로그인이 필요해요.", "auth_required": True}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    lang = (data.get("lang") or "").strip()
    if len(text) < 40:
        return jsonify({"ok": False}), 200
    n = authz.save_style_sample(token, lang, text, kind="edited")
    if n:
        _rebuild_style_async(token, lang, os.getenv("ENGINE_PROVIDER", "claude"))
    return jsonify({"ok": True, "samples": n})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    text = (request.form.get("text") or "").strip()
    lang = (request.form.get("lang") or "").strip()
    answers = (request.form.get("answers") or "").strip()   # 스마트 후속답변(프론트에서 조립한 재료)
    gear = [s.strip() for s in (request.form.get("gear") or "").split("|") if s.strip()]            # 착용·장비 (큐레이션·본문 반영)
    nutrition = [s.strip() for s in (request.form.get("nutrition") or "").split("|") if s.strip()]  # 젤·간식
    tone = (request.form.get("tone") or "").strip()   # 글 톤 프리셋 (emotive/factual/bright/soft, 빈값=자동)
    try: length = int(request.form.get("length") or 2800)      # 목표 글자수(하한선)
    except Exception: length = 2800
    height = (request.form.get("height") or "").strip()        # 키(cm) — 건강 데이터 역산용(선택)
    weight = (request.form.get("weight") or "").strip()        # 몸무게(kg) — 칼로리·건강 역산용(선택)
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
    # 문체 학습(P5): 이 유저의 문체 프로필이 있으면 프롬프트에 주입 → "그 사람처럼" 씀
    style_profile = authz.get_style_profile(token) if user else ""
    try:
        result = engine_mod.generate(story, provider=provider, photo_paths=photos,
                                     lang=lang, extra_context=answers or None,
                                     style_profile=style_profile or None,
                                     gear=gear or None, nutrition=nutrition or None,
                                     tone=tone or None, target_len=length,
                                     height=height or None, weight=weight or None)
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
        # 문체 학습: 이번 원문(스토리+답변)을 샘플로 저장 → 초반엔 매번, 이후 3회마다 프로필 재생성(백그라운드)
        try:
            _sample = (text or "")
            if answers:
                _sample += "\n" + answers
            _n = authz.save_style_sample(token, lang, _sample, kind="input")
            if _n and (_n <= 6 or _n % 3 == 0):
                _rebuild_style_async(token, lang, provider)
        except Exception as _se:
            print("[문체] 샘플 저장 스킵:", _se)

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

    # #58 결제 확인 이메일 — 최초 결제/구독 생성 시 1회 발송 (중복 방지 위해 created 이벤트만)
    if event in ("subscription_created", "order_created"):
        email = attrs.get("user_email") or custom.get("email") or (authz.get_user_email(uid) if uid else "")
        variant = custom.get("variant_name", "") or attrs.get("variant_name", "") or ""
        months = 6 if "6" in variant else (12 if "12" in variant else 1)
        if email:
            try:
                mailer.payment_confirmation(email, months, method="card")
            except Exception as _me:
                print("[mail] lemon confirm skip:", _me)
    return jsonify({"received": True}), 200


@app.route("/api/polar-webhook", methods=["POST"])
def polar_webhook():
    """Polar 결제 웹훅 (Standard Webhooks 규격).
       - 서명 검증: POLAR_WEBHOOK_SECRET (webhook-id / webhook-timestamp / webhook-signature 헤더)
       - order.paid / subscription.active → pro,  subscription.canceled/revoked → free
       - 유저 매칭: 체크아웃에서 넘긴 reference_id(=user_id)가 Order/Subscription metadata로 전달됨."""
    import hmac, hashlib, base64
    raw = request.get_data()  # bytes
    secret = os.getenv("POLAR_WEBHOOK_SECRET", "")

    # ── Standard Webhooks 서명 검증 (secret 설정 시에만) ──
    if secret:
        wh_id = request.headers.get("webhook-id", "")
        wh_ts = request.headers.get("webhook-timestamp", "")
        wh_sig = request.headers.get("webhook-signature", "")
        signed = wh_id.encode() + b"." + wh_ts.encode() + b"." + raw
        # Standard Webhooks: 시크릿은 'whsec_' + base64(패딩 없을 수 있음). 접두사 떼고 base64 디코드한 값이 raw key.
        def _b64(s):
            s = s + "=" * (-len(s) % 4)               # 패딩 보정 (Polar 시크릿은 '=' 없이 옴)
            return base64.b64decode(s)
        sk = secret[6:] if secret.startswith("whsec_") else secret
        keys = []
        for cand in (sk, secret):                     # 표준(접두사 제거) + 원문 둘 다 시도
            try: keys.append(_b64(cand))
            except Exception: pass
        keys.append(secret.encode("utf-8"))           # 최후 fallback: 원문 그대로
        ok_sig = False
        for key in keys:
            digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
            for part in wh_sig.split():            # 헤더는 "v1,<sig> v1,<sig2>" 공백 구분
                if hmac.compare_digest(digest, part.split(",", 1)[-1]):
                    ok_sig = True; break
            if ok_sig: break
        if not ok_sig:
            print("[polar] bad signature"); return jsonify({"error": "bad signature"}), 401

    data = request.get_json(force=True, silent=True) or {}
    event = data.get("type", "")               # order.paid, subscription.active, subscription.canceled ...
    obj = data.get("data", {}) or {}

    def _dig(d, *ks):
        for k in ks:
            v = (d or {}).get(k)
            if v: return v
        return None
    meta     = obj.get("metadata") or {}
    cust     = obj.get("customer") or {}
    checkout = obj.get("checkout") or {}
    sub      = obj.get("subscription") or {}
    uid = (_dig(meta, "reference_id", "user_id")
           or obj.get("reference_id")
           or _dig(checkout, "reference_id")
           or _dig(checkout.get("metadata") or {}, "reference_id", "user_id")
           or _dig(sub.get("metadata") or {}, "reference_id", "user_id")
           or _dig(cust, "external_id")
           or obj.get("customer_external_id"))
    print(f"[polar] event={data.get('type','')} uid={uid} meta={meta} custkeys={list(cust.keys()) if isinstance(cust,dict) else cust}")
    status = obj.get("status", "")
    email  = _dig(cust, "email") or obj.get("customer_email") or (authz.get_user_email(uid) if uid else "")

    active_events = {"order.paid", "subscription.active", "subscription.created", "subscription.updated"}
    cancel_events = {"subscription.canceled", "subscription.revoked"}

    if event in active_events:
        plan = "pro"
        if event.startswith("subscription") and status and status not in ("active", "trialing", "past_due"):
            plan = "free"
        if uid:
            ok = authz.set_plan(uid, plan, status=status or event)
            print(f"[polar] {event} status={status} user={uid} -> plan={plan} ok={ok}")
    elif event in cancel_events:
        if uid:
            ok = authz.set_plan(uid, "free", status=status or event)
            print(f"[polar] {event} user={uid} -> plan=free ok={ok}")

    # 결제 확인 메일 — 최초 결제(order.paid) 1회
    if event == "order.paid" and email:
        prod = obj.get("product") or {}
        name = (prod.get("name") or "") + " " + str(obj.get("amount") or "")
        months = 6 if "6" in name else (12 if "12" in name else 1)
        try:
            mailer.payment_confirmation(email, months, method="card")
        except Exception as _me:
            print("[mail] polar confirm skip:", _me)

    return jsonify({"received": True}), 200


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  러너 블로그 생성기  →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
