# -*- coding: utf-8 -*-
"""纯 Python PDF 路径渲染器：把"文字转曲线"的 PDF 内容流解析并绘制成位图。

适用于 Microsoft Print To PDF 等把文字转成贝塞尔曲线的 PDF——
这类 PDF 无文本层，pypdf/pdfminer 提取为空，Chrome 渲染可能异常，
但路径指令本身可解析绘制，配合 tesseract OCR 可还原文字。

关键点：
1. 汉字是"复合路径"（外轮廓 + 内孔洞），必须按**偶奇规则**填充，
   不能用简单 polygon 逐个填充（否则孔洞被填死，字形变实心块，OCR 失效）。
2. 贝塞尔曲线需分段采样（本实现用 12 段直线近似）。
3. 填充在字形 bbox 的像素网格上按"被覆盖次数的奇偶"判定，正确处理内外。

支持操作符：cm, q/Q, m/l/c/v/y/h, f/f*, rg
"""
import re
import zlib
import numpy as np
from PIL import Image


def parse_pdf_streams(pdf_bytes: bytes) -> list:
    """提取 PDF 所有内容流并解压，返回指令文本列表。

    兼容 FlateDecode 压缩流与未压缩流（某些构造/损坏 PDF 无压缩）。
    """
    streams = re.findall(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.DOTALL)
    out = []
    for s in streams:
        raw = s
        # 去掉可能的尾部换行
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        try:
            out.append(zlib.decompress(raw).decode("latin-1"))
            continue
        except Exception:
            pass
        # 未压缩流：原样作为文本
        out.append(raw.decode("latin-1"))
    return out


def _sample_cubic(p0, ctrl, n=12):
    """三次贝塞尔曲线采样：p0=起点, ctrl=[c1,c2,p3]，返回 n 个中间点。"""
    (c1, c2, p3) = ctrl
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _point_in_poly_wn(px, py, poly):
    """非零环绕数：点在多边形内的环绕数贡献（-1/0/1），poly 是闭合点列。"""
    wn = 0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 <= py:
            if y2 > py and _is_left(x1, y1, x2, y2, px, py) > 0:
                wn += 1
        else:
            if y2 <= py and _is_left(x1, y1, x2, y2, px, py) < 0:
                wn -= 1
    return wn


def _is_left(x1, y1, x2, y2, px, py):
    return (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1)


class PDFPathRenderer:
    """极简 PDF 内容流渲染器（仅路径 + 偶奇规则填充，不含文本/图像）。"""

    def __init__(self, width=595, height=842, scale=3.0):
        self.width = width
        self.height = height
        self.scale = scale
        self.px_w = int(width * scale)
        self.px_h = int(height * scale)
        # 画布：黑色笔画在白色底上（初始全白）
        self.canvas = np.full((self.px_h, self.px_w), 255, dtype=np.uint8)
        self.ctm = [1, 0, 0, 1, 0, 0]  # 当前坐标变换矩阵
        self.stack = []
        # 当前填充组：[[path1], [path2], ...]（一个 f 前的所有子路径）
        self.cur_path = []       # 当前正在构建的子路径
        self.path_group = []     # 本填充组的子路径列表
        self.cur_color = (0, 0, 0)

    def _transform(self, x, y):
        a, b, c, d, e, f = self.ctm
        return a * x + c * y + e, b * x + d * y + f

    def _to_px(self, x, y):
        tx, ty = self._transform(x, y)
        px = tx * self.scale
        py = (self.height - ty) * self.scale
        return px, py

    def _fill_path_group(self, subpaths):
        """按偶奇规则填充一组子路径（支持孔洞），用行扫描线算法。

        汉字轮廓常用偶奇规则：像素在"奇数个"子路径内 → 填充。
        实现：对 path_group 联合 bbox 分配小掩码，逐子路径行扫描累加覆盖；
        覆盖奇数的像素涂黑。
        """
        if not subpaths:
            return
        # 联合 bbox（性能：只处理字形实际占用的区域）
        all_xs = [p[0] for poly in subpaths for p in poly]
        all_ys = [p[1] for poly in subpaths for p in poly]
        if not all_xs:
            return
        obx0 = int(max(0, min(all_xs) - 1))
        obx1 = int(min(self.px_w - 1, max(all_xs) + 1))
        oby0 = int(max(0, min(all_ys) - 1))
        oby1 = int(min(self.px_h - 1, max(all_ys) + 1))
        if obx1 <= obx0 or oby1 <= oby0:
            return
        w = obx1 - obx0 + 1
        h = oby1 - oby0 + 1
        cov = np.zeros((h, w), dtype=np.uint8)
        for poly in subpaths:
            n = len(poly)
            if n < 3:
                continue
            xs_p = [p[0] for p in poly]
            ys_p = [p[1] for p in poly]
            p_by0 = int(max(oby0, min(ys_p) - 1))
            p_by1 = int(min(oby1, max(ys_p) + 1))
            edges = []
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                if y1 == y2:
                    continue
                if y1 > y2:
                    x1, y1, x2, y2 = x2, y2, x1, y1
                edges.append((y1, y2, x1, x2, (x2 - x1) / (y2 - y1)))
            if not edges:
                continue
            ey1 = np.array([e[0] for e in edges])
            ey2 = np.array([e[1] for e in edges])
            ex1 = np.array([e[2] for e in edges])
            dxdy = np.array([e[4] for e in edges])
            for py in range(p_by0, p_by1 + 1):
                yc = py + 0.5
                active = (ey1 <= yc) & (yc < ey2)
                if not active.any():
                    continue
                xs_hit = ex1[active] + dxdy[active] * (yc - ey1[active])
                xs_hit.sort()
                row_cov = cov[py - oby0]
                for k in range(0, len(xs_hit) - 1, 2):
                    x_start = int(max(obx0, xs_hit[k])) - obx0
                    x_end = int(min(obx1, xs_hit[k + 1])) - obx0
                    if x_start < x_end:
                        row_cov[x_start:x_end] += 1
        inside = (cov % 2) == 1
        if inside.any():
            region = self.canvas[oby0:oby1 + 1, obx0:obx1 + 1]
            region[inside] = 0

    def render(self, text: str):
        # 基于行解析
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            op = parts[-1]
            if op == "cm" and len(parts) == 7:
                a, b, c_, d, e, f_ = (float(x) for x in parts[:6])
                pa, pb, pc, pd, pe, pf = self.ctm
                self.ctm = [
                    a * pa + b * pc, a * pb + b * pd,
                    c_ * pa + d * pc, c_ * pb + d * pd,
                    e * pa + f_ * pc + pe, e * pb + f_ * pd + pf,
                ]
            elif op == "rg" and len(parts) == 4:
                r, g, b = (float(x) for x in parts[:3])
                self.cur_color = (int(r * 255), int(g * 255), int(b * 255))
            elif op == "m" and len(parts) == 3:
                # 新子路径开始：若当前有未完成路径，先并入填充组
                if self.cur_path and len(self.cur_path) >= 2:
                    self.path_group.append(self.cur_path)
                self.cur_path = [self._to_px(float(parts[0]), float(parts[1]))]
            elif op == "l" and len(parts) == 3:
                self.cur_path.append(self._to_px(float(parts[0]), float(parts[1])))
            elif op == "c" and len(parts) == 7:
                pts = [
                    self._to_px(float(parts[0]), float(parts[1])),
                    self._to_px(float(parts[2]), float(parts[3])),
                    self._to_px(float(parts[4]), float(parts[5])),
                ]
                self.cur_path.extend(_sample_cubic(self.cur_path[-1] if self.cur_path else (0, 0), pts, 12))
            elif op == "v" and len(parts) == 5:
                pts = [
                    self.cur_path[-1] if self.cur_path else (0, 0),
                    self._to_px(float(parts[0]), float(parts[1])),
                    self._to_px(float(parts[2]), float(parts[3])),
                ]
                self.cur_path.extend(_sample_cubic(self.cur_path[-1] if self.cur_path else (0, 0), pts, 12))
            elif op == "y" and len(parts) == 5:
                end = self._to_px(float(parts[2]), float(parts[3]))
                pts = [
                    self._to_px(float(parts[0]), float(parts[1])),
                    end,
                    end,
                ]
                self.cur_path.extend(_sample_cubic(self.cur_path[-1] if self.cur_path else (0, 0), pts, 12))
            elif op == "q":
                self.stack.append(list(self.ctm))
            elif op == "Q":
                if self.stack:
                    self.ctm = self.stack.pop()
            elif op == "h":
                if self.cur_path:
                    self.cur_path.append(self.cur_path[0])  # 闭合
            elif op in ("f", "f*"):
                # 一个填充组结束：把当前子路径并入组，然后按偶奇规则填充。
                # 只填充深色笔画（亮度<128）；白色/浅灰背景矩形不涂黑
                if self.cur_path and len(self.cur_path) >= 3:
                    self.path_group.append(self.cur_path)
                if self.cur_color and min(self.cur_color) < 128:
                    self._fill_path_group(self.path_group)
                self.path_group = []
                self.cur_path = []
            elif op in ("n", "W", "W*"):
                # 结束路径（n）或设置裁剪路径（W/W*）：该路径不填充，
                # 清空当前路径，避免其坐标（如整页裁剪框）混入后续填充组
                self.cur_path = []
                self.path_group = []
            # 忽略其他操作符（w/Do/BI 等）

    def save(self, path):
        img = Image.fromarray(self.canvas, "L")
        img.save(path)
        return img


def render_pdf_to_image(pdf_path: str, out_path: str, scale=3.0) -> Image.Image:
    """把 PDF 渲染成高分辨率位图（纯 Python，无外部依赖）。

    out_path 传空串时只返回内存中的 PIL 图（供 OCR 等用，不落盘）。
    """
    with open(pdf_path, "rb") as f:
        data = f.read()
    streams = parse_pdf_streams(data)
    renderer = PDFPathRenderer(width=595, height=842, scale=scale)
    for s in streams:
        renderer.render(s)
    img = Image.fromarray(renderer.canvas, "L")
    if out_path:
        img.save(out_path)
    return img


if __name__ == "__main__":
    import sys
    img = render_pdf_to_image(sys.argv[1], sys.argv[2])
    print(f"渲染完成: {sys.argv[2]} 尺寸={img.size}")
