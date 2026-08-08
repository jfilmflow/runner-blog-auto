# -*- coding: utf-8 -*-
"""
러너 블로그 이미지 자동 생성기.
engine이 만든 image spec(JSON)을 받아 PNG 카드로 렌더링한다.
type: thumbnail / stat_compare / before_after / bar / summary
"""
import os, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findSystemFonts
from matplotlib.patches import FancyBboxPatch, Rectangle

# ---- 브랜드 색 (원하면 여기만 바꾸면 전체 톤이 바뀜) ----
SURFACE = "#fcfcfb"; INK = "#141414"; SUB = "#52514e"; MUTED = "#8a8880"
RED = "#d03b3b"; RED_LT = "#e06a6a"; BLUE = "#2a78d6"; GRID = "#e6e5df"
ACCENT = "#12b5a6"; ACCENT_LT = "#5fd6c9"  # 러닝 성장 강조색(민트)
THUMB_BG = "#111214"; THUMB_ACCENT = "#6fa8ff"; THUMB_HL = "#ff5a5a"
BRAND_FOOTER = "러너들의 공기의 흔적을 남깁니다"
_FOOTER_BY_LANG = {
    "en": "Runner's Blog Studio",
    "ko": "러너들의 공기의 흔적을 남깁니다",
    "ja": "ランナーの息づかいを記録に残す",
    "zh": "留下每一次奔跑的呼吸印记",
    "es": "Estudio de Blog para Corredores",
}


def set_lang(lang):
    """카드 하단 브랜드 문구 + 사진 안내 문구를 UI 언어에 맞춤. (모르는 코드는 한국어 유지)"""
    global BRAND_FOOTER, PH_GUIDE
    key = (lang or "").lower()
    BRAND_FOOTER = _FOOTER_BY_LANG.get(key, _FOOTER_BY_LANG["ko"])
    PH_GUIDE = _PH_GUIDE_BY_LANG.get(key, _PH_GUIDE_BY_LANG["ko"])


W, H = 1200, 900


def _find_korean_font():
    """번들 폰트 우선 (서버 배포 대비).
       1순위: Noto Sans CJK (한·일·중·라틴 전부 커버 → 다국어 카드 글자 OK)
       2순위: NanumGothic (한국어 전용)
       3순위: 시스템 폰트 탐색
    """
    _fd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts")

    # 1) Noto Sans CJK — 다국어(en/ko/ja/zh/es) 카드 글자를 한 폰트로 처리
    _noto_reg = os.path.join(_fd, "NotoSansCJK-Regular.ttc")
    _noto_bold = os.path.join(_fd, "NotoSansCJK-Bold.ttc")
    if os.path.exists(_noto_reg):
        _b = _noto_bold if os.path.exists(_noto_bold) else _noto_reg
        return {
            "black": FontProperties(fname=_b),
            "bold":  FontProperties(fname=_b),
            "med":   FontProperties(fname=_noto_reg),
            "reg":   FontProperties(fname=_noto_reg),
        }

    # 2) NanumGothic — 한국어 전용 폴백
    _reg = os.path.join(_fd, "NanumGothic.ttf")
    if os.path.exists(_reg):
        _bold = os.path.join(_fd, "NanumGothicBold.ttf")
        _black = os.path.join(_fd, "NanumGothicExtraBold.ttf")
        return {
            "black": FontProperties(fname=_black if os.path.exists(_black) else _reg),
            "bold":  FontProperties(fname=_bold if os.path.exists(_bold) else _reg),
            "med":   FontProperties(fname=_reg),
            "reg":   FontProperties(fname=_reg),
        }
    prefer = ["NotoSansCJK", "NotoSansKR", "NanumGothic", "NanumBarunGothic",
              "AppleSDGothic", "Malgun", "Gothic"]
    fonts = findSystemFonts()
    picks = {}
    for f in fonts:
        base = os.path.basename(f)
        for p in prefer:
            if p.lower() in base.lower():
                picks.setdefault(p, {})
                low = base.lower()
                if "black" in low: picks[p]["black"] = f
                elif "bold" in low: picks[p]["bold"] = f
                elif "medium" in low: picks[p]["medium"] = f
                elif "regular" in low or "reg" in low: picks[p]["regular"] = f
                else: picks[p].setdefault("regular", f)
    for p in prefer:
        if p in picks and picks[p]:
            d = picks[p]
            reg = d.get("regular") or d.get("medium") or next(iter(d.values()))
            return {
                "black": FontProperties(fname=d.get("black") or d.get("bold") or reg),
                "bold":  FontProperties(fname=d.get("bold") or d.get("black") or reg),
                "med":   FontProperties(fname=d.get("medium") or reg),
                "reg":   FontProperties(fname=reg),
            }
    # 못 찾으면 기본 폰트 (한글 깨질 수 있음 → README 안내)
    return {k: FontProperties() for k in ["black", "bold", "med", "reg"]}


F = _find_korean_font()


def _newfig(bg=SURFACE):
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H)
    ax.axis("off"); ax.invert_yaxis()
    return fig, ax


def _brandbar(ax):
    ax.add_patch(Rectangle((0, H - 70), W, 70, color=INK, zorder=3))
    ax.text(60, H - 35, BRAND_FOOTER, fontproperties=F["bold"], fontsize=19,
            color="#fcfcfb", va="center", zorder=4)


def _tag(ax, text, color=BLUE):
    ax.text(60, 70, text, fontproperties=F["black"], fontsize=20, color=color, va="center")


def _fit(ax, x, y, text, fp, size, max_w, color, va="center", ha="left", min_size=16, **kw):
    """글자가 max_w(px)를 넘으면 폰트를 줄여가며 상자 안에 맞춘다. (겹침·넘침 방지)"""
    text = "" if text is None else str(text)
    fig = ax.figure
    try:
        r = fig.canvas.get_renderer()
    except Exception:
        fig.canvas.draw(); r = fig.canvas.get_renderer()
    s = size
    while s > min_size:
        t = ax.text(x, y, text, fontproperties=fp, fontsize=s, color=color, va=va, ha=ha, **kw)
        try:
            w = t.get_window_extent(renderer=r).width
        except Exception:
            return t
        if w <= max_w:
            return t
        t.remove(); s -= 3
    return ax.text(x, y, text, fontproperties=fp, fontsize=s, color=color, va=va, ha=ha, **kw)


# 사진 자리 안내 문구(언어별) — Unsplash 키가 없을 때 빈칸 대신 안내 카드로
_PH_GUIDE_BY_LANG = {
    "en": "Drop your own running photo here",
    "ko": "이 자리에 러닝 사진을 넣어보세요",
    "ja": "ここにランニング写真を入れてください",
    "zh": "在此处放入你的跑步照片",
    "es": "Coloca aquí tu foto de running",
}
PH_GUIDE = _PH_GUIDE_BY_LANG["ko"]


def render_thumbnail(s, path):
    fig, ax = _newfig(THUMB_BG)
    ax.add_patch(Rectangle((0, 0), W, H, color=THUMB_BG))
    _fit(ax, 60, 110, s.get("tag", "러너 블로그"), F["black"], 26, W - 120, THUMB_ACCENT, va="center", ha="left")
    _fit(ax, 60, 255, s.get("line1", ""), F["black"], 86, W - 120, "#fff", va="center", ha="left", min_size=44)
    _fit(ax, 60, 390, s.get("line2", ""), F["black"], 86, W - 120, "#fff", va="center", ha="left", min_size=44)
    if s.get("highlight"):
        _fit(ax, 60, 530, s["highlight"], F["black"], 96, W - 120, THUMB_HL, va="center", ha="left", min_size=48)
        y3 = 650
    else:
        y3 = 530
    _fit(ax, 60, y3, s.get("line3", ""), F["black"], 58, W - 120, "#fff", va="center", ha="left", min_size=34)
    ax.add_patch(Rectangle((60, y3 + 62), 300, 8, color=THUMB_ACCENT))
    _fit(ax, 60, y3 + 140, s.get("sub", ""), F["med"], 26, W - 120, "#c7c7c7", va="center", ha="left")
    fig.savefig(path, facecolor=THUMB_BG); plt.close(fig)


def render_stat_compare(s, path):
    fig, ax = _newfig()
    _tag(ax, s.get("title", ""))
    _fit(ax, 60, 150, s.get("headline", ""), F["black"], 44, W - 120, INK, va="center", ha="left")
    bx, by, bw, bh = 60, 240, W - 120, 90
    # 단위가 같은 두 값(예: 개 vs 개)일 때만 비율 막대를 그린다. 거리 vs 시간처럼 단위가 다르면 생략.
    same_unit = bool(s.get("left_unit")) and s.get("left_unit") == s.get("right_unit")
    if same_unit:
        ax.add_patch(FancyBboxPatch((bx, by), bw, bh, boxstyle="round,pad=0,rounding_size=14", color=GRID, ec="none"))

    def block(x, label, num, unit, color):
        ax.add_patch(FancyBboxPatch((x, 430), 510, 300, boxstyle="round,pad=0,rounding_size=24",
                                    fc="#ffffff", ec=GRID, lw=2))
        _fit(ax, x + 40, 500, label, F["bold"], 26, 430, SUB, va="center", ha="left")
        _fit(ax, x + 40, 600, str(num), F["black"], 92, 430, color, va="center", ha="left", min_size=30)
        _fit(ax, x + 40, 690, unit, F["med"], 25, 430, MUTED, va="center", ha="left")
    block(60, s.get("left_label", ""), s.get("left_num", ""), s.get("left_unit", ""), ACCENT)
    block(630, s.get("right_label", ""), s.get("right_num", ""), s.get("right_unit", ""), BLUE)
    # 비율 바 (단위가 같을 때만)
    if same_unit:
        try:
            ln = float(str(s.get("left_num")).replace(",", "")); rn = float(str(s.get("right_num")).replace(",", ""))
            ratio = ln / (ln + rn) if (ln + rn) else 0.5
            ax.add_patch(FancyBboxPatch((bx, by), bw * ratio, bh, boxstyle="round,pad=0,rounding_size=14", color=ACCENT, ec="none"))
            ax.text(bx + 30, by + bh / 2, f"{ratio*100:.1f}%", fontproperties=F["black"], fontsize=28, color="#fff", va="center")
        except Exception:
            pass
    if s.get("foot"):
        ax.text(60, 790, s["foot"], fontproperties=F["reg"], fontsize=22, color=MUTED, va="center")
    _brandbar(ax); fig.savefig(path, facecolor=SURFACE); plt.close(fig)


def render_before_after(s, path):
    fig, ax = _newfig()
    _tag(ax, s.get("title", ""))
    _fit(ax, 60, 150, s.get("headline", ""), F["black"], 44, W - 120, INK, va="center", ha="left")
    # 좌: 2년 전(흰 상자) — 라벨은 위, 값은 아래. 값이 길면 상자 폭(약 360px)에 맞춰 자동 축소
    ax.add_patch(FancyBboxPatch((60, 270), 440, 220, boxstyle="round,pad=0,rounding_size=24", fc="#fff", ec=GRID, lw=2))
    _fit(ax, 280, 328, s.get("from_label", ""), F["bold"], 26, 360, SUB, ha="center", va="center")
    _fit(ax, 280, 420, s.get("from_val", ""), F["black"], 60, 360, SUB, ha="center", va="center", min_size=22)
    # 화살표
    ax.annotate("", xy=(688, 380), xytext=(516, 380),
                arrowprops=dict(arrowstyle="-|>", color=ACCENT, lw=6, mutation_scale=36))
    # 우: 지금(민트 상자)
    ax.add_patch(FancyBboxPatch((700, 270), 440, 220, boxstyle="round,pad=0,rounding_size=24", fc=ACCENT, ec="none"))
    _fit(ax, 920, 328, s.get("to_label", ""), F["bold"], 26, 360, "#dffaf6", ha="center", va="center")
    _fit(ax, 920, 420, s.get("to_val", ""), F["black"], 60, 360, "#fff", ha="center", va="center", min_size=22)
    if s.get("delta"):
        _fit(ax, 60, 600, s["delta"], F["black"], 70, W - 120, ACCENT, va="center", ha="left")
    if s.get("foot"):
        _fit(ax, 60, 690, s["foot"], F["med"], 24, W - 120, SUB, va="center", ha="left")
    _brandbar(ax); fig.savefig(path, facecolor=SURFACE); plt.close(fig)


def render_bar(s, path):
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100); fig.patch.set_facecolor(SURFACE)
    fig.text(0.05, 0.92, s.get("title", ""), fontproperties=F["black"], fontsize=20, color=BLUE)
    fig.text(0.05, 0.845, s.get("headline", ""), fontproperties=F["black"], fontsize=29, color=INK)
    items = s.get("items", [])
    labels = [str(i[0]) for i in items]; vals = [float(i[1]) for i in items]
    unit = s.get("unit", "")
    neg = any(v < 0 for v in vals)
    ax = fig.add_axes([0.28, 0.12, 0.64, 0.62]) if neg else fig.add_axes([0.08, 0.20, 0.88, 0.56])
    if neg:  # 가로 막대 (하락률 등)
        ypos = range(len(items))
        ax.barh(ypos, vals, color=[RED if v == min(vals) or v == max(vals, key=abs) else RED_LT for v in vals],
                height=0.62, zorder=3)
        ax.set_yticks(ypos); ax.set_yticklabels(labels, fontproperties=F["bold"], fontsize=26, color=INK)
        ax.invert_yaxis()
        span = max(abs(min(vals)), 1)
        for y, v in zip(ypos, vals):
            ax.text(-span * 0.02, y, f"{v:g}{unit}", fontproperties=F["black"], fontsize=24,
                    color="#fff", va="center", ha="right", zorder=4)
        ax.set_xlim(min(vals) * 1.12, 0); ax.axvline(0, color=MUTED, lw=1.5); ax.set_xticks([])
    else:  # 세로 막대
        x = range(len(items))
        ax.bar(x, vals, color=[ACCENT_LT if i == 0 else ACCENT for i in x], width=0.55, zorder=3)
        ax.set_xticks(x); ax.set_xticklabels(labels, fontproperties=F["bold"], fontsize=23, color=INK)
        for xi, v in zip(x, vals):
            ax.text(xi, v + max(vals) * 0.02, f"{v:g}{unit}", fontproperties=F["black"], fontsize=28,
                    color=INK, ha="center")
        ax.set_ylim(0, max(vals) * 1.18); ax.set_yticks([])
    for sp in ["top", "right", "left", "bottom"]:
        ax.spines[sp].set_visible(False)
    ax.tick_params(length=0)
    if s.get("foot"):
        fig.text(0.05, 0.05, s["foot"], fontproperties=F["med"], fontsize=22, color=MUTED)
    fig.savefig(path, facecolor=SURFACE); plt.close(fig)


def render_summary(s, path):
    fig, ax = _newfig()
    _tag(ax, s.get("title", "오늘의 3줄 요약"))
    _fit(ax, 60, 150, s.get("headline", "이것만 기억하세요"), F["black"], 46, W - 120, INK, va="center", ha="left")
    y = 270
    for row in s.get("lines", [])[:3]:
        n, a, b = (list(row) + ["", "", ""])[:3]
        ax.add_patch(FancyBboxPatch((60, y), W - 120, 175, boxstyle="round,pad=0,rounding_size=20", fc="#fff", ec=GRID, lw=2))
        ax.add_patch(plt.Circle((130, y + 87), 42, color=BLUE))
        ax.text(130, y + 87, str(n), fontproperties=F["black"], fontsize=40, color="#fff", ha="center", va="center")
        _fit(ax, 210, y + 58, a, F["bold"], 27, W - 300, INK, va="center", ha="left")
        _fit(ax, 210, y + 118, b, F["med"], 24, W - 300, RED, va="center", ha="left")
        y += 195
    _brandbar(ax); fig.savefig(path, facecolor=SURFACE); plt.close(fig)


def render_photo_placeholder(s, path):
    """Unsplash 키가 없을 때, 빈칸 대신 '여기에 러닝 사진을 넣어보세요' 안내 카드."""
    fig, ax = _newfig(SURFACE)
    # 민트 점선 라운드 프레임
    ax.add_patch(FancyBboxPatch((70, 70), W - 140, H - 210,
                                boxstyle="round,pad=0,rounding_size=28",
                                fc="#f2fbf9", ec=ACCENT, lw=3, linestyle=(0, (6, 6))))
    cx, cy = W / 2, (H - 140) / 2 + 30
    # 카메라 아이콘(간단 도형)
    ax.add_patch(FancyBboxPatch((cx - 95, cy - 128), 190, 130,
                                boxstyle="round,pad=0,rounding_size=22", fc=ACCENT, ec="none"))
    ax.add_patch(FancyBboxPatch((cx - 34, cy - 150), 68, 30,
                                boxstyle="round,pad=0,rounding_size=10", fc=ACCENT, ec="none"))
    ax.add_patch(plt.Circle((cx, cy - 63), 40, fc="#f2fbf9", ec="none"))
    ax.add_patch(plt.Circle((cx, cy - 63), 24, fc=ACCENT, ec="none"))
    _fit(ax, cx, cy + 40, PH_GUIDE, F["bold"], 40, W - 220, INK, va="center", ha="center", min_size=24)
    cap = s.get("caption") or ""
    if cap:
        _fit(ax, cx, cy + 110, cap, F["med"], 26, W - 240, MUTED, va="center", ha="center", min_size=18)
    _brandbar(ax); fig.savefig(path, facecolor=SURFACE); plt.close(fig)


def _wrap2(text, maxchars):
    """긴 문구를 최대 2줄로 (가운데 근처 공백에서 분리). 공백 없으면 한 줄."""
    text = (text or "").strip()
    if len(text) <= maxchars:
        return [text]
    mid = len(text) // 2
    left = text.rfind(" ", 0, mid); right = text.find(" ", mid)
    if left == -1 and right == -1:
        return [text]
    pos = left if (left != -1 and (right == -1 or (mid - left) <= (right - mid))) else right
    if pos <= 0:
        return [text]
    return [text[:pos].strip(), text[pos:].strip()]


def render_keynote(s, path):
    """빈 사진 자리 대체용 감성 카드 — 큰 문구 한 줄(캡션)로 6장을 꽉 채운다."""
    fig, ax = _newfig(THUMB_BG)
    ax.add_patch(Rectangle((0, 0), W, H, color=THUMB_BG))
    tag = (s.get("tag") or s.get("title") or "RUNNING")
    _fit(ax, 60, 120, tag, F["black"], 26, W - 120, THUMB_ACCENT, va="center", ha="left")
    cap = (s.get("caption") or s.get("headline") or s.get("sub") or "오늘도, 한 걸음").strip()
    lines = _wrap2(cap, 14)
    y = 360 if len(lines) > 1 else 430
    for ln in lines:
        _fit(ax, 60, y, ln, F["black"], 74, W - 120, "#ffffff", va="center", ha="left", min_size=40)
        y += 118
    ax.add_patch(Rectangle((60, y - 34), 300, 8, color=THUMB_ACCENT))
    _brandbar(ax); fig.savefig(path, facecolor=THUMB_BG); plt.close(fig)


_RENDERERS = {
    "thumbnail": render_thumbnail, "stat_compare": render_stat_compare,
    "before_after": render_before_after, "bar": render_bar, "summary": render_summary,
}


def render_all(images, out_dir):
    """image spec 리스트 → PNG 파일 리스트 반환"""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for img in images:
        t = img.get("type", "summary")
        fn = f"{int(img.get('id', len(paths)+1)):02d}_{t}.png"
        p = os.path.join(out_dir, fn)
        renderer = _RENDERERS.get(t)
        if not renderer:
            print(f"  [경고] 알 수 없는 이미지 type: {t} → 건너뜀"); continue
        try:
            renderer(img, p); paths.append(p); print(f"  이미지 생성: {fn}")
        except Exception as e:
            print(f"  [경고] {fn} 생성 실패: {e}")
    return paths


if __name__ == "__main__":
    # 단독 테스트: sample_images.json 있으면 렌더
    import json, sys
    src = sys.argv[1] if len(sys.argv) > 1 else "output/result.json"
    data = json.load(open(src, encoding="utf-8"))
    render_all(data["images"], "output/images")
