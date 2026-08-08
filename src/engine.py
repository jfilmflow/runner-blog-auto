# -*- coding: utf-8 -*-
"""
러너 블로그 엔진.
뉴스 기사 텍스트 + engine_v2 프롬프트 → LLM → 블로그 JSON(dict)
provider: claude(기본) / openai / mock(키 없이 구조 테스트)
"""
import os, json, re

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = os.path.join(HERE, "..", "prompts", "engine_v2.txt")


def _load_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


def _extract_json(text):
    """모델 응답에서 JSON 블록만 안전하게 추출."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text); text = re.sub(r"\n?```$", "", text)
    # 첫 { 부터 마지막 } 까지
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


MAX_VISION_PHOTOS = 8  # 비용·토큰 보호 (앞에서부터 최대 N장만 AI가 읽음)


def _encode_image(path, max_side=1568):
    """이미지를 (media_type, base64) 로 인코딩. 크면 축소해 토큰 절약. PIL 없으면 원본 사용."""
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


def _photo_note(n):
    if not n:
        return ""
    return (f"\n\n[첨부 사진 {n}장]\n러너가 러닝 사진/앱 캡처 {n}장을 올렸어요 (순서대로 index 0~{n-1}).\n"
            "- 사진 속 정보(거리·페이스·시간·장소·풍경·표정 등)를 읽어 글과 기록 카드에 자연스럽게 반영하세요.\n"
            "- 이 사진들을 되도록 본문에 배치하세요. 해당 이미지 슬롯은 type을 \"user_photo\"로, index를 그 사진 번호로 지정합니다.\n"
            "- 앱 캡처에서 읽은 수치는 기록 카드(stat_compare/bar 등)로도 만들 수 있어요. 사진에서 확인되지 않는 수치는 지어내지 마세요.")


def generate(article_text, provider="claude", model=None, photo_paths=None):
    system = _load_prompt()
    photo_paths = (photo_paths or [])[:MAX_VISION_PHOTOS]
    n = len(photo_paths)
    user_text = (f"[러너의 러닝 이야기]\n{article_text or '(글은 없음 — 첨부 사진을 보고 러닝 이야기를 구성하세요)'}"
                 f"{_photo_note(n)}\n\n위 내용으로 러너 블로그 JSON을 만들어라.")

    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
        model = model or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
        content = [{"type": "text", "text": user_text}]
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
        client = OpenAI()  # OPENAI_API_KEY 환경변수 사용
        model = model or "gpt-4o"
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
        # 키 없이 파이프라인 구조만 테스트할 때 쓰는 더미 (실제 글 아님)
        raw = json.dumps(_MOCK, ensure_ascii=False)
    else:
        raise ValueError(f"알 수 없는 provider: {provider}")

    data = _extract_json(raw)
    return data


# 구조 테스트용 더미 (mock provider · 러닝 예시)
_MOCK = {
    "title": "한강 10km 러닝 후기, 러닝 초보가 페이스 지키는 법",
    "title_options": ["새벽 러닝 3주차, 몸이 바뀌기 시작한 순간들", "러닝 초보의 첫 10km, 힘들었지만 남은 건 뿌듯함"],
    "main_keyword": "한강 러닝", "sub_keywords": ["러닝 초보", "10km 페이스", "새벽 러닝"],
    "body": ("오늘 새벽 5시 반, 알람 끄고 그냥 잘까 딱 3초 고민했어요.\n\n"
             "근데 결국 일어나서 한강으로 나갔죠.\n\n[사진1]\n\n"
             "목표는 10km. 사실 지난주까지만 해도 7km에서 숨이 턱까지 찼거든요.\n\n[사진2]\n\n"
             "초반 3km가 제일 힘들었어요.\n다리가 무겁고 '오늘도 실패인가' 싶었죠.\n\n[사진3]\n\n"
             "근데 반환점을 돌고 나니 신기하게 몸이 풀리더라고요. 페이스가 살아났어요.\n\n[사진4]\n\n"
             "3주 전엔 5km도 겨우였는데, 오늘 10km를 완주했어요.\n\n[사진5]\n\n"
             "러닝 끝나고 마신 아이스 아메리카노 한 잔. 그 맛에 또 나올 것 같아요.\n\n[사진6]"),
    "hashtags": ["#한강러닝", "#10km완주", "#러닝초보", "#러닝일기", "#새벽러닝", "#러닝스타그램", "#달리기", "#러닝기록"],
    "images": [
        {"id": 1, "type": "thumbnail", "tag": "오늘의 러닝", "line1": "한강 10km,", "line2": "드디어 완주",
         "highlight": "58분", "line3": "러닝 3주차 기록", "sub": "새벽에 만난 한강의 공기"},
        {"id": 2, "type": "stat_compare", "title": "오늘의 기록", "headline": "10km, 이렇게 달렸어요",
         "left_label": "거리", "left_num": "10", "left_unit": "km", "right_label": "시간", "right_num": "58",
         "right_unit": "분", "foot": "평균 페이스 약 5분 48초/km"},
        {"id": 3, "type": "photo", "query": "running by the han river at sunrise", "caption": "새벽 한강 러닝 코스"},
        {"id": 4, "type": "bar", "title": "구간별 페이스", "headline": "뒤로 갈수록 빨라졌어요", "unit": "분/km",
         "items": [["0-3km", 6.2], ["3-6km", 5.9], ["6-10km", 5.6]], "foot": "초반이 늘 제일 무겁죠"},
        {"id": 5, "type": "before_after", "title": "3주의 변화", "headline": "몸이 바뀌고 있어요",
         "from_label": "3주 전", "from_val": "5km", "to_label": "오늘", "to_val": "10km", "delta": "▲ 2배", "foot": "천천히, 그러나 확실히"},
        {"id": 6, "type": "summary", "title": "오늘의 3줄", "headline": "달리며 배운 것",
         "lines": [["1", "초반 3km는 원래 힘들다", "그 고비만 넘기면 몸이 풀린다"],
                   ["2", "지난주보다 3km 더 뛰었다", "기록은 조금씩 쌓인다"],
                   ["3", "무리하지 않고 완주", "내일도 가볍게 또 나가자"]]},
    ],
    "seo_report": {"main_keyword_count": 6, "in_title": True, "in_first_para": True, "in_subheads": True, "in_last_para": True},
}
