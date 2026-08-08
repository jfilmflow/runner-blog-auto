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
W, H = 1200, 900


def _find_korean_font():
    """번들 폰트(fonts/NanumGothic*) 우선 → 없으면 시스템 한글 폰트 탐색. (서버 배포 대비)"""
    _fd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts")
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


def render_thumbnail(s, path):
    fig, ax = _newfig(THUMB_BG)
    ax.add_patch(Rectangle((0, 0), W, H, color=THUMB_BG))
    ax.text(60, 110, s.get("tag", "러너 블로그"), fontproperties=F["black"],
            fontsize=26, color=THUMB_ACCENT, va="center")
    ax.text(60, 255, s.get("line1", ""), fontproperties=F["black"], fontsize=86, color="#fff", va="center")
    ax.text(60, 390, s.get("line2", ""), fontproperties=F["black"], fontsize=86, color="#fff", va="center")
    if s.get("highlight"):
        ax.text(60, 530, s["highlight"], fontproperties=F["black"], fontsize=96, color=THUMB_HL, va="center")
        y3 = 650
    else:
        y3 = 530
    ax.text(60, y3, s.get("line3", ""), fontproperties=F["black"], fontsize=58, color="#fff", va="center")
    ax.add_patch(Rectangle((60, y3 + 62), 300, 8, color=THUMB_ACCENT))
    ax.text(60, y3 + 140, s.get("sub", ""), fontproperties=F["med"], fontsize=26, color="#c7c7c7", va="center")
    fig.savefig(path, facecolor=THUMB_BG); plt.close(fig)


def render_stat_compare(s, path):
    fig, ax = _newfig()
    _tag(ax, s.get("title", ""))
    ax.text(60, 150, s.get("headline", ""), fontproperties=F["black"], fontsize=44, color=INK, va="center")
    bx, by, bw, bh = 60, 240, W - 120, 90
    # 단위가 같은 두 값(예: 개 vs 개)일 때만 비율 막대를 그린다. 거리 vs 시간처럼 단위가 다르면 생략.
    same_unit = bool(s.get("left_unit")) and s.get("left_unit") == s.get("right_unit")
    if same_unit:
        ax.add_patch(FancyBboxPatch((bx, by), bw, bh, boxstyle="round,pad=0,rounding_size=14", color=GRID, ec="none"))

    def block(x, label, num, unit, color):
        ax.add_patch(FancyBboxPatch((x, 430), 510, 300, boxstyle="round,pad=0,rounding_size=24",
                                    fc="#ffffff", ec=GRID, lw=2))
        ax.text(x + 40, 500, label, fontproperties=F["bold"], fontsize=26, color=SUB, va="center")
        ax.text(x + 40, 600, str(num), fontproperties=F["black"], fontsize=92, color=color, va="center")
        ax.text(x + 40, 690, unit, fontproperties=F["med"], fontsize=25, color=MUTED, va="center")
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
    ax.text(60, 150, s.get("headline", ""), fontproperties=F["black"], fontsize=44, color=INK, va="center")
    ax.add_patch(FancyBboxPatch((60, 270), 440, 220, boxstyle="round,pad=0,rounding_size=24", fc="#fff", ec=GRID, lw=2))
    ax.text(280, 330, s.get("from_label", ""), fontproperties=F["bold"], fontsize=26, color=SUB, ha="center", va="center")
    ax.text(280, 420, str(s.get("from_val", "")), fontproperties=F["black"], fontsize=66, color=SUB, ha="center", va="center")
    ax.annotate("", xy=(690, 380), xytext=(520, 380),
                arrowprops=dict(arrowstyle="-|>", color=ACCENT, lw=6, mutation_scale=40))
    ax.add_patch(FancyBboxPatch((700, 270), 440, 220, boxstyle="round,pad=0,rounding_size=24", fc=ACCENT, ec="none"))
    ax.text(920, 330, s.get("to_label", ""), fontproperties=F["bold"], fontsize=26, color="#dffaf6", ha="center", va="center")
    ax.text(920, 420, str(s.get("to_val", "")), fontproperties=F["black"], fontsize=66, color="#fff", ha="center", va="center")
    if s.get("delta"):
        ax.text(60, 600, s["delta"], fontproperties=F["black"], fontsize=74, color=ACCENT, va="center")
    if s.get("foot"):
        ax.text(60, 690, s["foot"], fontproperties=F["med"], fontsize=24, color=SUB, va="center")
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
    ax.text(60, 150, s.get("headline", "이것만 기억하세요"), fontproperties=F["black"], fontsize=46, color=INK, va="center")
    y = 270
    for row in s.get("lines", [])[:3]:
        n, a, b = (list(row) + ["", "", ""])[:3]
        ax.add_patch(FancyBboxPatch((60, y), W - 120, 175, boxstyle="round,pad=0,rounding_size=20", fc="#fff", ec=GRID, lw=2))
        ax.add_patch(plt.Circle((130, y + 87), 42, color=BLUE))
        ax.text(130, y + 87, str(n), fontproperties=F["black"], fontsize=40, color="#fff", ha="center", va="center")
        ax.text(210, y + 58, a, fontproperties=F["bold"], fontsize=27, color=INK, va="center")
        ax.text(210, y + 118, b, fontproperties=F["med"], fontsize=24, color=RED, va="center")
        y += 195
    _brandbar(ax); fig.savefig(path, facecolor=SURFACE); plt.close(fig)


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
