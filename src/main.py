# -*- coding: utf-8 -*-
"""
러너 블로그 완전 자동화 - 메인 오케스트레이터 (레퍼런스 봇과 동일 기능 세트)

흐름:
  러닝 이야기(인스타·스레드 글·메모)
   → [engine] Claude로 블로그 JSON 생성
   → [keyword] 네이버 검색량·문서수·골든점수 분석 (키 있으면)
   → [images] 숫자카드·차트 자동 렌더 + [unsplash] 사진 자동 다운로드
   → [naver] 로그인 세션 재사용 → 제목·본문 자동 타이핑 → 이미지 자동 업로드 → 임시저장

기본은 완전 자동 + 임시저장. 발행까지 원하면 --publish-live.

사용 예:
  python src/setup_login.py                                  # (처음 1회) 네이버 로그인 세션 저장
  python src/main.py --article input/article.txt             # 완전 자동(임시저장)
  python src/main.py --article input/article.txt --no-post   # 글·이미지만 만들고 네이버는 건너뜀
  python src/main.py --article input/article.txt --publish-live   # 발행까지(권장X, 검토 후 발행이 안전)
"""
import os, sys, json, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as engine_mod
import images as images_mod
import keyword_seo as keyword_mod
import unsplash as unsplash_mod

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")
IMG_DIR = os.path.join(OUT, "images")


def prepare_images(result, user_photos=None):
    """result['images']를 id순으로 처리.
    user_photo=사용자가 올린 사진 삽입 / photo=Unsplash 다운로드 / 나머지=카드 렌더. 경로 리스트 반환."""
    import shutil
    user_photos = user_photos or []
    os.makedirs(IMG_DIR, exist_ok=True)
    specs = sorted(result.get("images", []), key=lambda x: int(x.get("id", 0)))
    card_specs = [s for s in specs if s.get("type") not in ("photo", "user_photo")]
    # 카드(차트/숫자) 렌더
    images_mod.render_all(card_specs, IMG_DIR)
    # id순으로 최종 경로 조립
    paths = []
    for s in specs:
        sid = int(s.get("id", len(paths) + 1))
        t = s.get("type")
        if t == "user_photo":
            idx = int(s.get("index", 0))
            if 0 <= idx < len(user_photos) and os.path.exists(user_photos[idx]):
                ext = os.path.splitext(user_photos[idx])[1] or ".jpg"
                dest = os.path.join(IMG_DIR, f"{sid:02d}_user{ext}")
                shutil.copy(user_photos[idx], dest); paths.append(dest)
                print(f"  [사진{sid}] 사용자 사진 삽입 (index {idx})")
            else:
                print(f"  [사진{sid}] 사용자 사진 index {idx} 없음 — 건너뜀")
        elif t == "photo":
            dest = os.path.join(IMG_DIR, f"{sid:02d}_photo.jpg")
            got = unsplash_mod.fetch(s.get("query", s.get("caption", "running")), dest) if unsplash_mod.available() else None
            if got:
                paths.append(got)
            else:
                print(f"  [사진{sid}] Unsplash 건너뜀(키 없음/실패) — 이 자리엔 이미지 없이 진행")
        else:
            p = os.path.join(IMG_DIR, f"{sid:02d}_{t}.png")
            if os.path.exists(p):
                paths.append(p)
    return paths


def build_paste_txt(result, image_paths):
    name_by_pos = {i: os.path.basename(p) for i, p in enumerate(image_paths, start=1)}
    body = result.get("body", "")

    def repl(m):
        n = int(m.group(1)); fn = name_by_pos.get(n, f"(사진{n})")
        return f"\n━━━━━━ 📷 [사진{n}] {fn} ━━━━━━\n"
    body = re.sub(r"\[사진(\d+)\]", repl, body)
    tags = " ".join(result.get("hashtags", []))
    return f"{result.get('title','')}\n\n{body}\n\n{tags}\n"


def main():
    ap = argparse.ArgumentParser(description="러너 블로그 완전 자동화")
    ap.add_argument("--article", default=None, help="러닝 이야기 텍스트 파일")
    ap.add_argument("--text", default=None, help="러닝 이야기 텍스트 직접 입력")
    ap.add_argument("--photos", default=None, help="러닝 사진 파일들(쉼표로 구분). AI가 읽고 본문에 삽입")
    ap.add_argument("--provider", default="claude", choices=["claude", "openai", "mock"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--blog-id", default=os.getenv("NAVER_BLOG_ID"))
    ap.add_argument("--no-post", action="store_true", help="네이버 자동입력을 건너뜀(글·이미지만 생성)")
    ap.add_argument("--publish-live", action="store_true", help="임시저장이 아니라 '발행'까지(권장X)")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    # 사진 목록
    photos = [p.strip() for p in (args.photos or "").split(",") if p.strip()]
    photos = [p for p in photos if os.path.exists(p)]

    # 1) 러닝 이야기 읽기
    if args.text:
        article = args.text
    elif args.article:
        article = open(args.article, encoding="utf-8").read()
    else:
        article = ""
    if not article.strip() and not photos:
        print("러닝 이야기(--article/--text) 또는 사진(--photos) 중 하나는 필요해요."); sys.exit(1)

    # 2) 엔진
    print(f"\n[1/5] 엔진({args.provider})으로 블로그 생성 중... (사진 {len(photos)}장 읽기)")
    result = engine_mod.generate(article, provider=args.provider, model=args.model, photo_paths=photos)
    body_len = len(result.get("body", "").replace("\n", ""))
    print(f"      제목: {result.get('title','')}")
    print(f"      본문 글자수(개행 제외): 약 {body_len}자")

    # 3) 키워드 SEO 분석 (레퍼런스 봇 기능)
    print(f"\n[2/5] 네이버 키워드 SEO 분석...")
    kws = [result.get("main_keyword", "")] + result.get("sub_keywords", [])
    kws = [k for k in kws if k]
    rows = keyword_mod.analyze(kws)
    keyword_mod.print_report(rows)
    result["keyword_report"] = rows

    os.makedirs(OUT, exist_ok=True)
    json.dump(result, open(os.path.join(OUT, "result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 4) 이미지 (카드 렌더 + Unsplash)
    print(f"\n[3/5] 이미지 생성 (숫자카드/차트 + Unsplash 사진)...")
    image_paths = prepare_images(result, user_photos=photos)
    txt = build_paste_txt(result, image_paths)
    open(os.path.join(OUT, "blog_paste.txt"), "w", encoding="utf-8").write(txt)
    print(f"      이미지 {len(image_paths)}장 · 붙여넣기용 텍스트: output/blog_paste.txt")

    # 5) 네이버 완전 자동 입력
    if args.no_post:
        print("\n[4/5] 네이버 자동입력 건너뜀(--no-post).")
        print("\n[5/5] 완료!"); return
    if not args.blog_id:
        print("\n[!] --blog-id 또는 .env NAVER_BLOG_ID 가 필요합니다."); sys.exit(1)

    print(f"\n[4/5] 네이버 완전 자동 입력 시작 (로그인 세션 재사용)...")
    import naver as naver_mod
    ok = naver_mod.run(result, image_paths, blog_id=args.blog_id,
                       auto_publish=args.publish_live, headless=args.headless)
    print(f"\n[5/5] 완료! {'(임시저장까지 자동)' if not args.publish_live else '(발행 시도)'}")


if __name__ == "__main__":
    main()
