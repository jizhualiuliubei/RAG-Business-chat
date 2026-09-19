"""
PDF OCR 兜底模块
================
作用：对"无文本层"的 PDF（如 Microsoft Print To PDF 把文字转成矢量曲线的
扫描件/特殊导出件）做 OCR 还原——pypdf/pdfminer 提取为空时调用。

管道：PDF → 纯 Python 渲染器（pdf_renderer，把矢量路径绘制成位图）
      → 图像预处理 → tesseract 中文 OCR。

依赖：
- tesseract 命令行（系统已装 D:\\development\\tesseract-oecr-5.0，需 chi_sim 中文包）
- numpy / Pillow（环境已有）
"""
import os
import subprocess
import tempfile
from pathlib import Path

from app.core.pdf_renderer import render_pdf_to_image

# tesseract 可执行文件：优先系统 PATH，其次常见安装路径
_TESSERACT_CANDIDATES = [
    "tesseract",
    r"D:\development\tesseract-oecr-5.0\tesseract.exe",
]
TESSERACT = None
for _c in _TESSERACT_CANDIDATES:
    try:
        r = subprocess.run([_c, "--version"], capture_output=True, timeout=15)
        if r.returncode == 0:
            TESSERACT = _c
            break
    except Exception:
        continue

# OCR 语言：中文简体（chi_sim）+ 英文；若缺 chi_sim 则只用 eng
_OCR_LANG = "chi_sim+eng"


def _preprocess(img):
    """图像预处理：转灰度 → 二值化 → 反色（黑字白底 → 白字黑底可选）。

    tesseract 对高对比度二值图识别更好。返回 PIL 灰度图。
    """
    import numpy as np
    from PIL import Image

    g = img.convert("L")
    arr = np.array(g)
    # 二值化：<200 视为笔画（黑），否则白
    bw = np.where(arr < 200, 0, 255).astype("uint8")
    return Image.fromarray(bw)


def ocr_pdf(pdf_path: str, scale: float = 3.0) -> str:
    """对 PDF 做 OCR 还原文本。返回提取的文本（可能为空字符串）。

    scale：渲染放大倍数（越大字形越清晰、OCR 越准，但渲染越慢）。
    """
    if TESSERACT is None:
        return ""
    try:
        # 1. 纯 Python 渲染 PDF 为位图
        img = render_pdf_to_image(pdf_path, "", scale=scale)
        img = _preprocess(img)

        # 2. tesseract OCR
        with tempfile.TemporaryDirectory() as tmp:
            png_path = os.path.join(tmp, "page.png")
            img.save(png_path)
            out_base = os.path.join(tmp, "out")
            # --psm 6：按块文本（适合整页）；--oem 3：默认引擎
            subprocess.run(
                [TESSERACT, png_path, out_base, "-l", _OCR_LANG, "--psm", "6", "--oem", "3"],
                capture_output=True,
                timeout=300,
            )
            txt_path = out_base + ".txt"
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
        return ""
    except Exception:
        return ""


def pdf_ocr_available() -> bool:
    """是否具备 OCR 能力（tesseract + chi_sim 语言包可用）。"""
    return TESSERACT is not None
