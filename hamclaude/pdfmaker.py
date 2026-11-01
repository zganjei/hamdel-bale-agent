"""Persian PDF maker for the agent (`hamctl pdf`). Needs reportlab, arabic-reshaper and python-bidi; they are installed on first use
into DATA_DIR/pylibs (no root needed). Text is shaped and reordered per wrapped line, so right-to-left Persian with numbers reads correctly."""
import os, subprocess, sys
from pathlib import Path

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
PKGS = ["reportlab", "arabic-reshaper", "python-bidi"]

def ensure_libs(data_dir):
    libs = Path(data_dir) / "pylibs"
    if str(libs) not in sys.path: sys.path.insert(0, str(libs))
    try: import reportlab, arabic_reshaper, bidi  # noqa
    except ImportError:
        libs.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(libs), *PKGS], check=True, timeout=300, stdin=subprocess.DEVNULL)
        import importlib; importlib.invalidate_caches()

def build(path, title, body, data_dir):
    ensure_libs(data_dir)
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(TTFont("V", str(FONTS / "Vazirmatn-Regular.ttf"))); pdfmetrics.registerFont(TTFont("VB", str(FONTS / "Vazirmatn-Bold.ttf")))
    shaper = arabic_reshaper.ArabicReshaper({"support_ligatures": True})
    vis = lambda s: get_display(shaper.reshape(s), base_dir="R")
    W, H = A4; L, R, T, B = 50, W - 50, H - 60, 55
    c = canvas.Canvas(str(path), pagesize=A4); c.setTitle(title[:120]); y = T

    def wrap(text, font, size, width):
        lines, cur = [], ""
        for w in text.split():
            trial = (cur + " " + w).strip()
            if cur and pdfmetrics.stringWidth(vis(trial), font, size) > width: lines.append(cur); cur = w
            else: cur = trial
        return lines + ([cur] if cur else [])

    def put(text, font, size, lead, indent=0, gap=0):
        nonlocal y
        for ln in wrap(text, font, size, R - L - indent) or [""]:
            if y < B: c.showPage(); y = T
            c.setFont(font, size); c.drawRightString(R - indent, y, vis(ln)); y -= lead
        y -= gap
    put(title, "VB", 20, 30, gap=10)
    for raw in body.splitlines():
        s = raw.strip()
        if not s: y -= 8
        elif s.startswith("#"): put(s.lstrip("# ").strip(), "VB", 14, 24, gap=4)
        elif s[:2] in ("- ", "• ", "* "): put("• " + s[2:], "V", 12, 21, indent=12)
        else: put(s, "V", 12, 21)
    c.save(); return Path(path)
