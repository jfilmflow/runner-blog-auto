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


# ── 감정 재해석(희노애락) : 진짜 사람이 쓴 것 같은 감성·문장부호 주입 ──────────
_EMO_PUNCT = {
    "ko": ('한국어: 구어체 종결(…요 / …거든요 / …더라고요)로 진심을 담고, 감정이 북받치는 '
           '딱 한 곳에만 "…"(여운·아쉬움·먹먹함)이나 "!"(벅참·뿌듯함·응원)를 써요. '
           '"와", "후—", "헐", "아…" 같은 감탄은 자연스러울 때만. 물결(~)·느낌표 남발은 금지.'),
    "en": ('English: use an em dash (—) for a caught breath, "…" for a trailing or wistful thought, '
           'and "!" only at a genuine peak of joy/pride/drive. Contractions (I\'m, didn\'t) keep it warm. '
           'Never stack punctuation.'),
    "ja": ('日本語: 余韻・切なさは「…」、高揚・応援は「！」で（多用しない）。「〜」でやわらかさを、'
           '「うわ」「ふぅ」「あぁ…」などの感嘆は自然な瞬間だけ。記号の連打はしない。'),
    "zh": ('中文: 用省略号"……"表达留白、怅然或不舍，用"！"表达振奋或加油（少用）。'
           '语气词"啊 / 呢 / 吧"自然融入，切忌堆叠标点。'),
    "es": ('Español: usa correctamente "¡…!" y "¿…?" con signos de apertura; puntos suspensivos "…" '
           'para la nostalgia o la duda, y "!" solo en un pico real. Nada de signos repetidos.'),
}

_EMO_CORE = (
    "[Emotional re-interpretation — write like a REAL person, not a report]\n"
    "Read the emotional arc hidden in the runner's own words and answers — the 희노애락 (joy, "
    "frustration, sadness/letdown, delight) beneath the plain facts — and re-tell the experience so the "
    "reader FEELS it, not just reads it:\n"
    "- Show emotion through concrete sensory detail and sentence rhythm, NOT piled-up adjectives.\n"
    "- Match punctuation to the feeling: a trailing \"…\" for hesitation, letdown or lingering warmth; "
    "\"!\" for a real spark of joy, pride or fighting spirit; a rhetorical \"?\" for self-questioning "
    "(\"wait, I actually did it?\"). Use each mark ONLY where the emotion truly lands — one is enough; "
    "never \"!!!\" or \"………\".\n"
    "- Vary the beat: short, punchy lines at emotional peaks; longer, flowing lines for scenery and reflection.\n"
    "- Stay authentic and grounded — a genuine human diary voice, never melodramatic or fake-cheerful.\n"
    "- If a personal voice guide is given above, THAT takes priority for emoji/punctuation habits.\n"
)


def _emotion_directive(lang):
    line = _EMO_PUNCT.get((lang or "ko").lower())
    return _EMO_CORE + ("- " + line + "\n" if line else "") + "\n"


# ── 글 톤 프리셋: 성향별로 '확실히 다른 감성'이 나오도록 강하게 규정 ──────
_TONE = {
    "emotive": (
        "OVERALL VOICE — Emotional essay. Write this like a heartfelt personal essay. "
        "Foreground feeling and inner reflection; let the scenery become a metaphor for the runner's state of mind. "
        "Use flowing, longer sentences with a literary rhythm, and lean into a trailing \"…\" for lingering thoughts. "
        "Introspective, vivid and moving — but grounded in real detail, never melodramatic."
    ),
    "factual": (
        "OVERALL VOICE — Crisp & informative. Write plainly and directly, like a sharp training log or a coach's note. "
        "Short, declarative sentences. Prioritize facts, pacing strategy, method and usable tips over feeling — keep emotional coloring minimal and understated. "
        "Almost no exclamation marks; no cutesy interjections. Clear, scannable subheads. Confident and no-nonsense. "
        "This dry, factual register OVERRIDES the default emotional styling."
    ),
    "bright": (
        "OVERALL VOICE — Bright & energetic. Write upbeat, motivating and full of momentum, like a cheerful friend hyping you up. "
        "Punchy short lines, forward energy, genuine enthusiasm. Use \"!\" at real peaks (still never stacked). "
        "Frame even the hard moments positively (\"that hill? crushed it\"). Encouraging and fun — but still grounded in what really happened."
    ),
    "soft": (
        "OVERALL VOICE — Warm & gentle. Write soft-spoken, tender and comforting, like a quiet diary shared with a close friend. "
        "Gentle pacing, kind and intimate phrasing, cozy sensory warmth. Keep emotion understated — a calm, caring register rather than loud. "
        "Soothing and personal, never saccharine."
    ),
}


def _tone_directive(tone):
    t = _TONE.get((tone or "").strip().lower())
    if not t:
        return ""   # auto/기본 → 기본 감정 코어를 그대로 사용
    return ("[Tone preset — this is the dominant voice of the ENTIRE post. Commit to it fully so the result feels "
            "unmistakably different from the other tone presets. If a personal writing-voice guide is given above, "
            "keep that person's vocabulary and quirks, but apply THIS tone's mood.]\n" + t + "\n\n")


# ── 품질 상향: 상위 노출되는 '진짜 사람 블로그' 기준 ──────────────────
_QUALITY = (
    "[Quality bar — this must read like a top-ranking HUMAN running blog, not AI filler]\n"
    "- Open with a specific, grabby hook — a moment, a number, or a feeling. NEVER a generic 'Today I went for a run' intro.\n"
    "- Give every section a real job: scene-setting → the run itself → the numbers/analysis → a turning point → a concrete takeaway → what's next.\n"
    "- Use only concrete, sensory, verifiable detail from the story, answers, photos and facts. Cut vague filler, cliché, and repeated phrasing.\n"
    "- Weave the main keyword naturally into the title, the first paragraph, at least one subhead, and the closing — for SEO, but NEVER keyword-stuff.\n"
    "- Give the reader something usable: a tip, a comparison, a small lesson — so the post earns its length and deserves to rank (E-E-A-T).\n"
    "- Vary sentence length; keep paragraphs short and skimmable. Subheads should be specific and searchable, not decorative.\n"
    "- Close with a warm, human line that invites a comment or the next run.\n"
)


def _quality_directive():
    return _QUALITY + "\n"


# ── 구조: 기승전결 4단 서사 + 6하원칙(언제·어디서·누가·무엇을·어떻게·왜) ──
_STRUCTURE = (
    "[Structure — a solid post needs a clear 기승전결 (4-act) narrative arc AND the 6 basics grounded]\n"
    "Shape the body as a four-act arc. Use natural, specific subheads that fit the story — do NOT literally label them 기/승/전/결:\n"
    "1) 기 · SETUP: ground the reader — WHEN it was (time of day, season), WHERE they ran (place/route), WHO they ran with, and WHY they went out today.\n"
    "2) 승 · DEVELOPMENT: the run unfolds — the scene, the effort building, how the body and mind felt, the numbers along the way.\n"
    "3) 전 · TURN: the crux — the hardest moment, a breakthrough, a surprise, or the point it all shifted. This is the emotional core; give it the most room and the sharpest detail.\n"
    "4) 결 · RESOLUTION: how it ended and what it meant — one concrete takeaway the reader can use, and what's next.\n"
    "Across the whole post, make sure the 6 basics are answered so the story is complete and credible: "
    "WHEN, WHERE, WHO, WHAT (distance/goal/session), HOW (pace, effort, method), WHY (the motivation behind today's run).\n"
    "Weave all of this in as flowing story — NEVER as a checklist, a Q&A, or labeled sections. "
    "Use ONLY facts present in the runner's text, answers, or photos; if a basic isn't given, lean on feeling and scene rather than inventing it.\n"
)


def _structure_directive():
    return _STRUCTURE + "\n"


# ── 그라운딩 체크리스트: 글을 '탄탄'하게 만드는 구체 앵커들 (있을 때만) ──
_GROUNDING = (
    "[Grounding checklist — a solid post lands these concrete anchors, but ONLY when the material actually provides them]\n"
    "Work the following into the story where available — as natural narrative, NEVER as a bulleted spec sheet or Q&A:\n"
    "- WHEN it was, WHERE it happened, WHAT they did, HOW they ran, WHY they went out today.\n"
    "- HOW FAR and HOW LONG they ran (distance & time) — use the EXACT numbers from the photo facts or the runner's text, never rounded guesses.\n"
    "- What they WORE (apparel), the SHOES they ran in, and any SIDE ITEMS (headband, vest, cap, bag, watch) — from the gear given above.\n"
    "- The WEATHER and conditions (from the text, the answers, or a photo's scene).\n"
    "- What they ATE or drank as FUEL (gels, snacks, drinks) and roughly when.\n"
    "- CALORIES burned — ONLY if it is visibly shown in a running-app record. If shown, state it and add ONE short, natural line connecting it to a health/diet upside (e.g. 'that's a small win for the diet, too'). If it is NOT shown, skip calories entirely — never estimate, round, or invent a number.\n"
    "NEVER fabricate any of these. If a detail isn't in the runner's material, leave it out and lean on what IS there. "
    "The goal is a grounded, complete, believable post — not a form with every blank forced full.\n"
)


def _grounding_directive():
    return _GROUNDING + "\n"


# ── 러닝 장비·간식: 본문에 자연스럽게 녹이기 (광고 아님) ──────────────
def _gear_directive(gear, nutrition):
    gear = [g.strip() for g in (gear or []) if g and g.strip()]
    nutrition = [n.strip() for n in (nutrition or []) if n and n.strip()]
    if not gear and not nutrition:
        return ""
    lines = ["[Runner's REAL gear & fuel today — weave into the story naturally. Do NOT invent items or brands not listed here.]"]
    if gear:
        lines.append("Worn / gear: " + ", ".join(gear))
    if nutrition:
        lines.append("Fuel / snacks: " + ", ".join(nutrition))
    lines.append(
        "Mention each where it naturally belongs — what they wore, how the shoes felt underfoot, when they took the gel and whether it helped. "
        "Include ONE short, genuine gear-and-fuel touch in the body, like a runner's field note — specific about fit, comfort or timing, "
        "NEVER an ad and never a bullet list of products. Reference ONLY the items listed above."
    )
    return "\n".join(lines) + "\n\n"


def _build_user_text(article_text, extra_context, n, facts, style_profile=None, lang="ko", gear=None, nutrition=None, tone=None):
    parts = []
    if style_profile and style_profile.strip():
        parts.append(
            "[This runner's personal writing voice — MATCH IT CLOSELY]\n"
            "Write the blog so it reads like THIS person wrote it. Mirror the tone, sentence rhythm, "
            "vocabulary, signature phrases, and emoji/punctuation habits described below. "
            "Make it feel natural and authentically theirs — never exaggerated or a caricature.\n"
            + style_profile.strip() + "\n\n"
        )
    parts.append(_quality_directive())
    parts.append(_structure_directive())
    parts.append(_grounding_directive())
    parts.append(_emotion_directive(lang))
    parts.append(_tone_directive(tone))
    parts.append("[Runner's running story]\n" + (article_text or "(no text — build the running story from the attached photos)"))
    if extra_context:
        parts.append("\n[Runner's quick answers — use these as REAL material, weave them in naturally]\n" + extra_context.strip())
    gd = _gear_directive(gear, nutrition)
    if gd:
        parts.append("\n" + gd)
    parts.append(_photo_note(n, facts))
    parts.append("\n\nNow produce the running-blog JSON as specified. Output JSON only.")
    return "".join(parts)


# ── 스마트 후속질문: 재료를 늘리는 장치 ──────────────────────────
_Q_LANG = {
    "en": "English", "ko": "Korean (한국어)", "ja": "Japanese (日本語)",
    "zh": "Simplified Chinese (简体中文)", "es": "Spanish (Español)",
}

_Q_SYS = (
    "You are a warm running-blog interviewer. Given a runner's short note (and/or the number of photos "
    "they attached), you ask a FEW short follow-up questions whose answers will make their blog richer, "
    "more personal, and more credible — the kind of concrete, sensory, emotional detail a reader connects with. "
    "You return ONLY a JSON object, no prose, no code fences."
)

# 재료 상황별 질문 지침 (적응형): 사진(기록)이 있으면 숫자는 자동 → 부드러운 질문만/적게,
# 이미지도 글도 얇으면 → 사실 질문까지 촘촘하게.
_Q_MODE_RULE = {
    "soft": (
        "The runner ALSO attached a running photo/screenshot, so the hard numbers "
        "(distance, time, pace, heart rate, splits, elevation, date, map) are ALREADY captured from the image. "
        "Therefore you MUST NOT ask about any number or stat. Ask ONLY about the human layer that a screenshot "
        "can never hold: how their body/mind felt, the scene or weather mood, a turning point, why they ran today, "
        "who they were with, or the moment right after finishing."
    ),
    "mixed": (
        "Vary the angles — one sensory/scene, one emotional/motivation, one concrete fact or comparison. "
        "Do NOT ask for numbers already stated in the note."
    ),
    "dense": (
        "There is little material AND no record photo, so build the story fairly densely. "
        "Include exactly ONE short factual question about whatever concrete detail is missing "
        "(distance, or the weather, or pace — pick the most useful one), and make the REST about the human layer "
        "(feeling, scene, turning point, why, after-run). Keep every question short and easy."
    ),
}


def _q_task(want, mode, name):
    return (
        f"Read the runner's note below. Produce EXACTLY {want} follow-up question(s).\n"
        "Rules:\n"
        "1. Each question must be SHORT (answerable in a few words) and SPECIFIC to THIS runner's note — "
        "not generic boilerplate. Fill the real gaps in their story.\n"
        f"2. {_Q_MODE_RULE.get(mode, _Q_MODE_RULE['mixed'])}\n"
        "3. Warm, casual tone — like a friend curious about their run.\n"
        "4. Also give a tiny 'hint' for each: a 2-4 word example answer to lower the friction.\n"
        f"5. Write the questions AND hints in {name}.\n"
        "Return JSON exactly like: {\"questions\":[{\"q\":\"...\",\"hint\":\"...\"}, ...]}"
    )


def plan_questions(text_len, n_photos):
    """재료 양에 따라 (질문 개수, 모드)를 정한다 — 이탈률↓, 품질↑의 핵심 로직.
       사진 있음 → 숫자 자동, 질문 적고 부드럽게 / 이미지·글 둘 다 얇음 → 촘촘하게."""
    if n_photos and n_photos > 0:
        # 기록 이미지에서 숫자를 읽으므로 감정·이야기만. 글이 거의 없으면 스토리 더 끌어내려 3개.
        return (3 if text_len < 40 else 2), "soft"
    if text_len >= 120:
        return 3, "mixed"
    return 4, "dense"    # 이미지도 없고 글도 얇음 → Plan B: 사실 질문 포함 촘촘히


def smart_questions(article_text, lang="ko", n_photos=0, provider="claude", model=None):
    """러너의 메모(+사진 유무·글 길이)를 읽고, 재료 상황에 맞춰 후속질문을 적응형으로 생성.
       실패하면 언어별 기본 질문으로 폴백 (개수·종류도 상황에 맞게)."""
    name = _Q_LANG.get((lang or "ko").lower(), "the same language as the note")
    note = (article_text or "").strip()
    want, mode = plan_questions(len(note), n_photos)
    ctx = ""
    if n_photos and len(note) < 40:
        ctx = (f"\n\n(The runner attached {n_photos} photo(s) but wrote little text — "
               "draw out the story and feeling behind those moments.)")
    user = ("[Runner's note]\n" + (note or "(almost no text — only photos)") + ctx +
            "\n\n" + _q_task(want, mode, name))
    if provider == "claude":
        try:
            import anthropic
            client = anthropic.Anthropic()
            model = model or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
            msg = client.messages.create(
                model=model, max_tokens=800, system=_Q_SYS,
                messages=[{"role": "user", "content": user}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            data = _extract_json(raw)
            qs = [q for q in (data.get("questions") or []) if q.get("q")][:want]
            if qs:
                return qs
        except Exception as e:
            print(f"  [스마트질문] 실패(기본질문으로 폴백): {e}")
    return _fallback_questions(lang, want, mode)


# 폴백 질문 풀 — 순서 [느낌, 풍경/날씨, 끝나고/왜, 비교(사실성)]. 마지막(비교)만 숫자성 → soft에선 제외.
_FALLBACK_Q = {
    "ko": [{"q": "오늘 달리면서 몸이나 마음이 어땠어요?", "hint": "예) 다리가 무거웠다"},
           {"q": "달린 곳 풍경이나 날씨는 어땠나요?", "hint": "예) 노을 진 한강"},
           {"q": "다 뛰고 난 뒤엔 기분이 어땠어요?", "hint": "예) 뿌듯하고 개운"},
           {"q": "지난번과 비교해 달라진 점이 있나요?", "hint": "예) 3km 더 뛰었다"}],
    "en": [{"q": "How did your body or mind feel during the run?", "hint": "e.g. legs felt heavy"},
           {"q": "What was the scenery or weather like?", "hint": "e.g. river at sunset"},
           {"q": "How did you feel right after finishing?", "hint": "e.g. proud, refreshed"},
           {"q": "Anything different from last time?", "hint": "e.g. ran 3km more"}],
    "ja": [{"q": "走っている間、体や心はどんな感じでしたか？", "hint": "例）脚が重かった"},
           {"q": "走った場所の景色や天気は？", "hint": "例）夕焼けの河川敷"},
           {"q": "走り終えた後の気分は？", "hint": "例）達成感でスッキリ"},
           {"q": "前回と比べて変わった点は？", "hint": "例）3km多く走れた"}],
    "zh": [{"q": "跑步时你的身体或心情如何？", "hint": "例）腿很沉"},
           {"q": "跑步的地方风景或天气怎么样？", "hint": "例）夕阳下的河边"},
           {"q": "跑完之后的感觉如何？", "hint": "例）成就感满满"},
           {"q": "和上次相比有什么变化？", "hint": "例）多跑了3公里"}],
    "es": [{"q": "¿Cómo se sintió tu cuerpo o tu mente al correr?", "hint": "ej.) piernas pesadas"},
           {"q": "¿Cómo era el paisaje o el clima?", "hint": "ej.) el río al atardecer"},
           {"q": "¿Cómo te sentiste justo al terminar?", "hint": "ej.) orgulloso, ligero"},
           {"q": "¿Algo distinto respecto a la última vez?", "hint": "ej.) 3 km más"}],
}


def _fallback_questions(lang, want=3, mode="mixed"):
    pool = _FALLBACK_Q.get((lang or "ko").lower(), _FALLBACK_Q["en"])
    if mode == "soft":
        pool = pool[:3]        # 숫자성 비교 질문 제외 (사진에서 이미 읽음)
    return pool[:max(1, want)]


def build_style_profile(samples, lang="ko", provider="claude", model=None):
    """유저가 직접 쓴 글 샘플들(스토리·답변·편집본)에서 '이 사람 문체 가이드'를 뽑아냄.
       다음 생성 때 프롬프트에 넣어 그 사람처럼 쓰게 하는 재료. 짧고 처방적인 가이드 문자열 반환."""
    texts = [s.strip() for s in (samples or []) if s and s.strip()]
    if not texts:
        return ""
    joined = "\n\n---\n\n".join(texts)[:6000]
    lang_name = _Q_LANG.get(lang, "the runner's language")
    sys = (
        "You analyze a single person's OWN writing (short running notes / answers they typed) and produce a compact "
        "STYLE GUIDE another writer can follow to imitate this person's voice. Capture: tone & formality, sentence "
        "length and rhythm, favorite words / signature phrases, emoji & punctuation habits, how they show emotion, "
        "and any quirks. Be concrete and prescriptive (do this / avoid that). 5-8 short lines, no preamble. "
        f"Write the guide in {lang_name}. Output ONLY the guide."
    )
    if provider != "claude":
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        model = model or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
        msg = client.messages.create(
            model=model, max_tokens=500, system=sys,
            messages=[{"role": "user", "content": "This person's own writing samples:\n\n" + joined}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"  [문체] 프로필 추출 실패: {e}")
        return ""


def generate(article_text, provider="claude", model=None, photo_paths=None, lang="ko", extra_context=None, style_profile=None, gear=None, nutrition=None, tone=None):
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

        user_text = _build_user_text(article_text, extra_context, n, facts, style_profile, lang, gear, nutrition, tone)
        content = [{"type": "text", "text": user_text}]
        # 팩트 추출을 못했으면(폴백) 원본 사진을 그대로 붙여 글쓰기 단계가 직접 보게 함
        if n and not facts:
            for p in photo_paths:
                media, b64 = _encode_image(p)
                content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})

        # JSON이 깨져 파싱 실패하면 1회 더 재시도(두 번째엔 '{' 프리필로 JSON을 강제).
        # 드물게 모델이 서두 문장을 붙이거나 JSON을 미완성하는 경우가 있어 사용자에게 실패가 안 보이게 함.
        last_err = None
        for attempt in range(2):
            msgs = [{"role": "user", "content": content}]
            if attempt > 0:
                msgs.append({"role": "assistant", "content": "{"})   # 응답이 '{' 부터 시작하도록 강제
            try:
                msg = client.messages.create(
                    model=model, max_tokens=8000, system=system, messages=msgs,
                )
                raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                if attempt > 0:
                    raw = "{" + raw     # 프리필한 여는 중괄호를 앞에 다시 붙여 완성
                return _extract_json(raw)
            except Exception as e:
                last_err = e
                print(f"  [생성] JSON 파싱/생성 실패 (attempt {attempt + 1}/2): {e}")
        raise last_err

    elif provider == "openai":
        from openai import OpenAI
        client = OpenAI()
        model = model or "gpt-4o"
        user_text = _build_user_text(article_text, extra_context, n, None, style_profile, lang, gear, nutrition, tone)
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
