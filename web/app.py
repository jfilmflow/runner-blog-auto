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

OUT = os.path.join(ROOT, "output")
IMG_DIR = os.path.join(OUT, "images")
app = Flask(__name__, static_folder=None)


def _label(spec):
    return spec.get("headline") or spec.get("caption") or spec.get("type", "이미지")


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/images/<path:fname>")
def images(fname):
    return send_from_directory(IMG_DIR, fname)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    text = (request.form.get("text") or "").strip()
    provider = os.getenv("ENGINE_PROVIDER", "claude")

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
    try:
        result = engine_mod.generate(text, provider=provider, photo_paths=photos)
    except Exception as e:
        return jsonify({"error": f"글 생성 실패: {e}"}), 500

    kws = [result.get("main_keyword", "")] + result.get("sub_keywords", [])
    result["keyword_report"] = keyword_mod.analyze([k for k in kws if k])

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

    return jsonify({
        "title": result.get("title"),
        "title_options": [result.get("title")] + result.get("title_options", []),
        "main_keyword": result.get("main_keyword"),
        "keyword_report": result.get("keyword_report", []),
        "hashtags": result.get("hashtags", []),
        "body": result.get("body"),
        "images": imgs_out,
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


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  러너 블로그 생성기  →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
