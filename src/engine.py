# -*- coding: utf-8 -*-
"""
러너 블로그 엔진 (v3 · 다국어·2패스).
러닝 이야기 + 스마트 답변 + 사진 → LLM → 블로그 JSON(dict)

핵심 업그레이드:
- 언어별 전용 프롬프트 로딩 (engine_{lang}.txt, 없으면 ko→v2 폴백)
- 사진 정밀 판독 2패스: (1) 비전 추출 → 구조화 팩트 (2) 팩트+이야기+답변 → 글쓰기 (환각 방지·디테일↑)
- 스마트 후속답변(extra_context)을 재료로 주입
provider: claude(기본) / openai / mock
"""
import os, json, re

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_DIR = os.path.join(HERE, "..", "prompts")

MAX_VISION_PHOTOS = 8
TWO_PASS = os.getenv("ENGINE_TWO_PASS", "1") != "0"   # 사진 있을 때 2패스(비전 추출) 사용


def _load_prompt(lang):
    """언어별 프롬프트 로딩. engine_{lang}.txt → engine_ko.txt → engine_v2.txt 순 폴백."""
    for name in (f"engine_{(lang or 'ko').lower()}.txt", "engine_ko.txt", "engine_v2.txt"):
        path = os.path.join(PROMPT_DIR, name)
        if os.path.exists(path):
            return open(path, encoding="utf-8").read()
    raise FileNotFoundError("프롬프트 파일을 찾을 수 없어요 (prompts/engine_*.txt)")


def _extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text); text = re.sub(r"\n?```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def _encode_image(path, max_side=1568):
    import base64, mimetypes
    media = mimetypes.guess_type(path)[0] or "image/jpeg"
    try:
        from PIL import Image
        import io
        im = Image.open(path)
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB"); media = "image/jpeg"; fmt = "JPEG"
        else:
            fmt = "PNG" if media == "image/png" else "JPEG"
            if fmt == "JPEG":
                media = "image/jpeg"
        w, h = im.size
        if max(w, h) > max_side:
            r = max_side / max(w, h); im = im.resize((int(w * r), int(h * r)))
        buf = io.BytesIO(); im.save(buf, format=fmt); data = buf.getvalue()
    except Exception:
        data = open(path, "rb").read()
    return media, base64.b64encode(data).decode("utf-8")


# ── 1패스: 사진 정밀 판독 (구조화 팩트) ────────────────────────────
_VISION_SYS = (
    "You are a precise vision extractor for running photos. You NEVER guess or invent numbers. "
    "You return ONLY a JSON object, no prose, no code fences."
)
_VISION_TASK = (
    "Look at the attached running photo(s), indexed 0..N-1 in order. For EACH photo, read ONLY what is actually visible.\n"
    "- If it is a running-app screenshot (Strava/Nike/Garmin/etc.): distance, distance_unit, total_time, avg_pace, "
    "pace_unit, splits (list of [label, value]), elevation_gain, avg_hr, max_hr, cadence, calories, date_time, weather, location_or_map.\n"
    "- If it is a scenery/lifestyle/selfie photo: kind, scene, time_of_day, weather, mood, notable_objects.\n"
    "Use null for anything not clearly visible. Do NOT infer numbers that are not shown.\n"
    "Also give a short natural caption for each photo (in the same language as the user, if unknown use Korean).\n"
    'Return JSON exactly like: {"photos":[{"index":0,"kind":"app_screenshot|scenery|selfie|other", ...fields..., "caption":"..."}], '
    '"facts_summary":"one-line plain summary of the concrete, verified facts across all photos"}'
)


def _extract_photo_facts(client, model, photo_paths):
    """Claude 비전으로 사진에서 구조화 팩트 추출. 실패해도 파이프라인은 계속."""
    content = [{"type": "text", "text": _VISION_TASK}]
    for p in photo_paths:
        media, b64 = _encode_image(p)
        content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
    try:
        msg = client.messages.create(
            model=model, max_tokens=1500, system=_VISION_SYS,
            messages=[{"role": "user", "content": content}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _extract_json(raw)
    except Exception as e:
        print(f"  [비전추출] 실패(무시하고 진행): {e}")
        return None


def _photo_note(n, facts=None):
    if not n:
        return ""
    note = (f"\n\n[Attached photos: {n}] (index 0..{n-1}, in order)\n"
            "- Place these photos into the body where they fit; their image slots use type \"user_photo\" with the matching index.\n"
            "- Reflect what is in the photos naturally. NEVER invent a number that is not in the photos or the user's text.")
    if facts:
        note += ("\n[Facts read from the photos — treat as GROUND TRUTH. Use these exact numbers, "
                 "do not contradict them, and base each user_photo caption on the matching photo here]:\n"
                 + json.dumps(facts, ensure_ascii=False))
    return note


def _build_user_text(article_text, extra_context, n, facts):
    parts = []
    parts.append("[Runner's running story]\n" + (article_text or "(no text — build the running story from the attached photos)"))
    if extra_context:
        parts.append("\n[Runner's quick answers — use these as REAL material, weave them in naturally]\n" + extra_context.strip())
    parts.append(_photo_note(n, facts))
    parts.append("\n\nNow produce the running-blog JSON as specified. Output JSON only.")
    return "".join(parts)


def generate(article_text, provider="claude", model=None, photo_paths=None, lang="ko", extra_context=None):
    system = _load_prompt(lang)
    photo_paths = (photo_paths or [])[:MAX_VISION_PHOTOS]
    n = len(photo_paths)

    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic()
        model = model or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"

        facts = None
        if n and TWO_PASS:
            facts = _extract_photo_facts(client, model, photo_paths)

        user_text = _build_user_text(article_text, extra_context, n, facts)
        content = [{"type": "text", "text": user_text}]
        # 팩트 추출을 못했으면(폴백) 원본 사진을 그대로 붙여 글쓰기 단계가 직접 보게 함
        if n and not facts:
            for p in photo_paths:
                media, b64 = _encode_image(p)
                content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})

        msg = client.messages.create(
            model=model, max_tokens=8000, system=system,
            messages=[{"role": "user", "content": content}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    elif provider == "openai":
        from openai import OpenAI
        client = OpenAI()
        model = model or "gpt-4o"
        user_text = _build_user_text(article_text, extra_context, n, None)
        content = [{"type": "text", "text": user_text}]
        for p in photo_paths:
            media, b64 = _encode_image(p)
            content.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{b64}"}})
        resp = client.chat.completions.create(
            model=model, max_tokens=8000,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        )
        raw = resp.choices[0].message.content

    elif provider == "mock":
        raw = json.dumps(_MOCK, ensure_ascii=False)
    else:
        raise ValueError(f"알 수 없는 provider: {provider}")

    return _extract_json(raw)


# 구조 테스트용 더미 (mock provider)
_MOCK = {
    "title": "한강 10km 러닝 후기, 러닝 초보가 페이스 지키는 법",
    "title_options": ["새벽 러닝 3주차, 몸이 바뀌기 시작한 순간들", "러닝 초보의 첫 10km, 힘들었지만 남은 건 뿌듯함"],
    "main_keyword": "한강 러닝", "sub_keywords": ["러닝 초보", "10km 페이스", "새벽 러닝"],
    "body": ("오늘 새벽, 커튼도 안 걷었는데 밖이 벌써 파랬어요.\n\n[사진1]\n\n"
             "목표는 10km. 사실 지난주까지만 해도 7km에서 숨이 턱까지 찼거든요.\n\n[사진2]\n\n"
             "초반 3km가 제일 힘들었어요.\n다리가 무겁고 '오늘도 실패인가' 싶었죠.\n\n[사진3]\n\n"
             "근데 반환점을 돌고 나니 신기하게 몸이 풀리더라고요.\n\n[사진4]\n\n"
             "3주 전엔 5km도 겨우였는데, 오늘 10km를 완주했어요.\n\n[사진5]\n\n"
             "여러분은 어느 구간이 제일 힘드세요?\n\n[사진6]"),
    "hashtags": ["#한강러닝", "#10km완주", "#러닝초보", "#러닝일기", "#새벽러닝", "#러닝스타그램", "#달리기", "#러닝기록"],
    "images": [
        {"id": 1, "type": "thumbnail", "tag": "오늘의 러닝", "line1": "한강 10km,", "line2": "드디어 완주",
         "highlight": "58분", "line3": "러닝 3주차 기록", "sub": "새벽에 만난 한강의 공기"},
        {"id": 2, "type": "stat_compare", "title": "오늘의 기록", "headline": "10km, 이렇게 달렸어요",
         "left_label": "거리", "left_num": "10", "left_unit": "km", "right_label": "시간", "right_num": "58",
         "right_unit": "분", "foot": "평균 페이스 약 5분 48초/km"},
        {"id": 3, "type": "summary", "title": "오늘의 3줄", "headline": "달리며 배운 것",
         "lines": [["1", "초반 3km는 원래 힘들다", "그 고비만 넘기면 몸이 풀린다"],
                   ["2", "지난주보다 3km 더 뛰었다", "기록은 조금씩 쌓인다"],
                   ["3", "무리하지 않고 완주", "내일도 가볍게 또 나가자"]]},
        {"id": 4, "type": "bar", "title": "구간별 페이스", "headline": "뒤로 갈수록 빨라졌어요", "unit": "분/km",
         "items": [["0-3km", 6.2], ["3-6km", 5.9], ["6-10km", 5.6]], "foot": "초반이 늘 제일 무겁죠"},
        {"id": 5, "type": "before_after", "title": "3주의 변화", "headline": "몸이 바뀌고 있어요",
         "from_label": "3주 전", "from_val": "5km", "to_label": "오늘", "to_val": "10km", "delta": "▲ 2배", "foot": "천천히, 그러나 확실히"},
        {"id": 6, "type": "thumbnail", "tag": "다음 러닝", "line1": "내일도", "line2": "가볍게 또",
         "highlight": "습관", "line3": "멈추지 않기", "sub": "오늘의 공기를 기억하며"},
    ],
    "seo_report": {"main_keyword_count": 6, "in_title": True, "in_first_para": True, "in_subheads": True, "in_last_para": True},
}
