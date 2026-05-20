from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

try:
    import fitz
    import pdfplumber
    from PIL import Image, ImageChops, ImageDraw
except ModuleNotFoundError as exc:
    print("缺少运行依赖。请使用下面的命令重新运行：", file=sys.stderr)
    print(
        "uv run --with pymupdf --with pdfplumber --with pillow "
        "C:\\Users\\Administrator\\.codex\\skills\\review-word-format\\scripts\\review_word_format.py <文档.docx>",
        file=sys.stderr,
    )
    print(f"原始错误：{exc}", file=sys.stderr)
    raise SystemExit(2)


@dataclass
class Issue:
    severity: str
    stage: str
    page: int | None
    message: str
    detail: str = ""


def add_issue(
    issues: list[Issue],
    severity: str,
    stage: str,
    message: str,
    page: int | None = None,
    detail: str = "",
) -> None:
    issues.append(Issue(severity=severity, stage=stage, page=page, message=message, detail=detail))


def run_command(cmd: list[str], timeout: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "cmd": cmd,
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "命令不存在", "cmd": cmd}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": "命令超时",
            "cmd": cmd,
        }


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def collect_tool_versions() -> dict[str, str]:
    checks = {
        "pandoc": ["pandoc", "--version"],
        "pdftoppm": ["pdftoppm", "-v"],
    }
    versions: dict[str, str] = {}
    soffice_path = shutil.which("soffice")
    versions["soffice"] = f"命令可用：{soffice_path}" if soffice_path else "不可用"
    for name, cmd in checks.items():
        result = run_command(cmd, timeout=30)
        combined = "\n".join(part for part in [result["stdout"], result["stderr"]] if part)
        version = first_line(combined) if result["ok"] or combined else ""
        versions[name] = version or "不可用"
    versions["PyMuPDF"] = getattr(fitz, "version", ["未知"])[0]
    versions["pdfplumber"] = getattr(pdfplumber, "__version__", "未知")
    return versions


def inspect_docx_package(docx_path: Path, issues: list[Issue]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "paragraphs": 0,
        "tables": 0,
        "drawings": 0,
        "sections": 0,
        "comments_part": False,
        "comments": 0,
        "tracked_changes": False,
        "alt_chunks": 0,
    }
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }

    try:
        with zipfile.ZipFile(docx_path) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                add_issue(issues, "严重", "DOCX 包检查", "DOCX 缺少 word/document.xml，文件可能损坏。")
                return summary

            document_xml = archive.read("word/document.xml")
            root = ET.fromstring(document_xml)
            summary["paragraphs"] = len(root.findall(".//w:p", ns))
            summary["tables"] = len(root.findall(".//w:tbl", ns))
            summary["drawings"] = len(root.findall(".//w:drawing", ns))
            summary["sections"] = len(root.findall(".//w:sectPr", ns))
            summary["alt_chunks"] = len(root.findall(".//w:altChunk", ns))
            summary["comments_part"] = any(name.startswith("word/comments") for name in names)
            if "word/comments.xml" in names:
                comments_root = ET.fromstring(archive.read("word/comments.xml"))
                summary["comments"] = len(comments_root.findall(".//w:comment", ns))
            tracked_nodes = root.findall(".//w:ins", ns) + root.findall(".//w:del", ns) + root.findall(".//w:moveFrom", ns)
            summary["tracked_changes"] = bool(tracked_nodes)

            text = "".join(node.text or "" for node in root.findall(".//w:t", ns))
            if any(marker in text for marker in ["<<<<<<<", "=======", ">>>>>>>"]):
                add_issue(issues, "严重", "DOCX 包检查", "文档正文中出现疑似合并冲突标记。")
            if summary["tracked_changes"]:
                add_issue(issues, "警告", "DOCX 包检查", "文档包含未接受或未拒绝的修订痕迹。")
            if summary["comments"]:
                add_issue(issues, "提示", "DOCX 包检查", "文档包含批注部件，交付前应确认是否需要保留。")
            if summary["alt_chunks"]:
                add_issue(issues, "警告", "DOCX 包检查", "文档包含 altChunk 嵌入内容，转换结果可能不稳定。")
            if summary["paragraphs"] == 0:
                add_issue(issues, "严重", "DOCX 包检查", "未识别到正文段落，文档可能为空或结构异常。")
    except zipfile.BadZipFile:
        add_issue(issues, "严重", "DOCX 包检查", "DOCX 不是有效的压缩包，文件可能损坏。")
    except ET.ParseError as exc:
        add_issue(issues, "严重", "DOCX 包检查", "DOCX 主文档 XML 无法解析。", detail=str(exc))
    return summary


def convert_to_markdown(docx_path: Path, out_dir: Path, issues: list[Issue]) -> Path | None:
    md_path = out_dir / "pandoc.md"
    media_dir = out_dir / "pandoc-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    result = run_command(
        [
            "pandoc",
            str(docx_path),
            "-t",
            "gfm",
            "--wrap=none",
            f"--extract-media={media_dir}",
            "-o",
            str(md_path),
        ],
        timeout=180,
    )
    if not result["ok"]:
        add_issue(
            issues,
            "严重",
            "Pandoc Markdown 检查",
            "Pandoc 转 Markdown 失败。",
            detail=result["stderr"] or result["stdout"],
        )
        return None
    return md_path


def inspect_markdown(md_path: Path, issues: list[Issue]) -> dict[str, Any]:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    headings: list[int] = []
    image_refs: list[str] = []
    table_lines = 0
    raw_html_lines = 0

    for line in lines:
        match = re.match(r"^(#{1,6})\s+", line)
        if match:
            headings.append(len(match.group(1)))
        if re.search(r"!\[[^\]]*\]\(([^)]+)\)", line):
            image_refs.extend(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", line))
        if "|" in line and re.search(r"\|.*\|", line):
            table_lines += 1
        if re.search(r"</?(span|div|table|tr|td|p|br)\b", line, flags=re.IGNORECASE):
            raw_html_lines += 1

    if not text.strip():
        add_issue(issues, "严重", "Pandoc Markdown 检查", "Markdown 输出为空，结构转换失败或文档无正文。")
    for prev, current in zip(headings, headings[1:]):
        if current - prev > 1:
            add_issue(
                issues,
                "警告",
                "Pandoc Markdown 检查",
                f"标题层级从 H{prev} 跳到 H{current}，可能存在标题样式错误。",
            )
            break
    if raw_html_lines:
        add_issue(
            issues,
            "提示",
            "Pandoc Markdown 检查",
            "Markdown 中出现原始 HTML，可能来自复杂格式或无法自然表达的版面。",
            detail=f"涉及行数：{raw_html_lines}",
        )
    for ref in image_refs:
        ref_path = (md_path.parent / ref).resolve()
        if not ref_path.exists():
            add_issue(issues, "警告", "Pandoc Markdown 检查", "Markdown 图片引用缺失。", detail=ref)
            break
    if re.search(r"\b(TODO|FIXME|XXX)\b|待补充|占位", text, flags=re.IGNORECASE):
        add_issue(issues, "提示", "Pandoc Markdown 检查", "文档中存在疑似占位内容。")

    return {
        "line_count": len(lines),
        "heading_count": len(headings),
        "heading_levels": dict(Counter(headings)),
        "image_refs": len(image_refs),
        "table_like_lines": table_lines,
        "raw_html_lines": raw_html_lines,
    }


def convert_to_pdf(docx_path: Path, out_dir: Path, issues: list[Issue]) -> Path | None:
    pdf_dir = out_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    expected_pdf = pdf_dir / f"{docx_path.stem}.pdf"
    result = run_command(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(pdf_dir),
            str(docx_path),
        ],
        timeout=240,
    )
    if not result["ok"]:
        add_issue(
            issues,
            "严重",
            "LibreOffice PDF 转换",
            "LibreOffice 转 PDF 失败。",
            detail=result["stderr"] or result["stdout"],
        )
        return None
    if expected_pdf.exists():
        return expected_pdf

    candidates = sorted(pdf_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        add_issue(
            issues,
            "提示",
            "LibreOffice PDF 转换",
            "PDF 输出文件名与 DOCX 文件名不完全一致，已使用最近生成的 PDF。",
            detail=str(candidates[0]),
        )
        return candidates[0]

    add_issue(issues, "严重", "LibreOffice PDF 转换", "LibreOffice 返回成功但未找到 PDF 输出。")
    return None


def rect_outside(rect: tuple[float, float, float, float], width: float, height: float, tolerance: float = 1.0) -> bool:
    x0, y0, x1, y1 = rect
    return x0 < -tolerance or y0 < -tolerance or x1 > width + tolerance or y1 > height + tolerance


def rect_near_edge(rect: tuple[float, float, float, float], width: float, height: float, threshold: float = 6.0) -> bool:
    x0, y0, x1, y1 = rect
    return x0 < threshold or y0 < threshold or (width - x1) < threshold or (height - y1) < threshold


def inspect_pdf(pdf_path: Path, issues: list[Issue]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "page_count": 0,
        "page_sizes": [],
        "font_counter": {},
        "font_size_min": None,
        "font_size_max": None,
        "image_count": 0,
        "drawing_count": 0,
        "pages": [],
    }
    font_counter: Counter[str] = Counter()
    font_sizes: list[float] = []

    doc = fitz.open(pdf_path)
    summary["page_count"] = doc.page_count
    if doc.page_count == 0:
        add_issue(issues, "严重", "PDF 版面检查", "PDF 没有页面。")
        doc.close()
        return summary

    base_size: tuple[float, float] | None = None
    for index, page in enumerate(doc, start=1):
        width = float(page.rect.width)
        height = float(page.rect.height)
        size = (round(width, 2), round(height, 2))
        summary["page_sizes"].append(size)
        if base_size is None:
            base_size = size
        elif abs(base_size[0] - size[0]) > 2 or abs(base_size[1] - size[1]) > 2:
            add_issue(issues, "警告", "PDF 版面检查", "页面尺寸与首页不一致。", page=index, detail=f"{size}")
        if page.rotation:
            add_issue(issues, "提示", "PDF 版面检查", "页面设置了旋转角度。", page=index, detail=str(page.rotation))

        text = page.get_text("text").strip()
        page_dict = page.get_text("dict")
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())
        summary["image_count"] += image_count
        summary["drawing_count"] += drawing_count
        block_count = 0
        out_of_page_blocks = 0
        edge_blocks = 0

        for block in page_dict.get("blocks", []):
            bbox = tuple(float(v) for v in block.get("bbox", (0, 0, 0, 0)))
            block_count += 1
            if rect_outside(bbox, width, height):
                out_of_page_blocks += 1
            elif rect_near_edge(bbox, width, height):
                edge_blocks += 1
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("font"):
                        font_counter[str(span["font"])] += 1
                    if span.get("size"):
                        font_sizes.append(float(span["size"]))

        if not text and image_count == 0 and drawing_count == 0:
            add_issue(issues, "警告", "PDF 版面检查", "页面没有文本、图片或绘图对象，疑似空白页。", page=index)
        if out_of_page_blocks:
            add_issue(
                issues,
                "严重",
                "PDF 版面检查",
                "存在超出页面边界的文本或对象块。",
                page=index,
                detail=f"数量：{out_of_page_blocks}",
            )
        if edge_blocks:
            add_issue(
                issues,
                "提示",
                "PDF 版面检查",
                "部分内容距离页面边缘小于 6 pt，存在裁切风险。",
                page=index,
                detail=f"数量：{edge_blocks}",
            )

        summary["pages"].append(
            {
                "page": index,
                "width": width,
                "height": height,
                "text_chars": len(text),
                "blocks": block_count,
                "images": image_count,
                "drawings": drawing_count,
            }
        )

    if font_sizes:
        summary["font_size_min"] = round(min(font_sizes), 2)
        summary["font_size_max"] = round(max(font_sizes), 2)
        if min(font_sizes) < 5:
            add_issue(issues, "警告", "PDF 版面检查", "检测到小于 5 pt 的文字，可能不可读。", detail=str(round(min(font_sizes), 2)))
        if max(font_sizes) > 48:
            add_issue(issues, "提示", "PDF 版面检查", "检测到大于 48 pt 的文字，请确认是否为预期标题或封面。", detail=str(round(max(font_sizes), 2)))

    summary["font_counter"] = dict(font_counter.most_common(20))
    doc.close()

    try:
        with pdfplumber.open(pdf_path) as plumber_pdf:
            for page in plumber_pdf.pages:
                words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
                out_words = [
                    word
                    for word in words
                    if word["x0"] < -1
                    or word["top"] < -1
                    or word["x1"] > page.width + 1
                    or word["bottom"] > page.height + 1
                ]
                if out_words:
                    add_issue(
                        issues,
                        "严重",
                        "pdfplumber 坐标检查",
                        "存在超出页面边界的单词。",
                        page=page.page_number,
                        detail=f"数量：{len(out_words)}",
                    )
                overlap_count = count_suspicious_word_overlaps(words)
                if overlap_count:
                    add_issue(
                        issues,
                        "警告",
                        "pdfplumber 坐标检查",
                        "存在疑似文字重叠。",
                        page=page.page_number,
                        detail=f"数量：{overlap_count}",
                    )
    except Exception as exc:
        add_issue(issues, "警告", "pdfplumber 坐标检查", "pdfplumber 无法完成检查。", detail=str(exc))

    return summary


def count_suspicious_word_overlaps(words: list[dict[str, Any]], limit: int = 800) -> int:
    if len(words) > limit:
        words = words[:limit]
    by_line: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        key = int(round(float(word["top"]) / 3))
        by_line[key].append(word)

    count = 0
    for line_words in by_line.values():
        line_words = sorted(line_words, key=lambda item: item["x0"])
        for left, right in zip(line_words, line_words[1:]):
            horizontal = min(left["x1"], right["x1"]) - max(left["x0"], right["x0"])
            vertical = min(left["bottom"], right["bottom"]) - max(left["top"], right["top"])
            if horizontal > 1 and vertical > 1:
                count += 1
    return count


def render_pdf_to_png(pdf_path: Path, out_dir: Path, dpi: int, issues: list[Issue]) -> tuple[list[Path], str]:
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        prefix = pages_dir / "page"
        result = run_command(
            [
                "pdftoppm",
                "-r",
                str(dpi),
                "-png",
                "-aa",
                "yes",
                "-aaVector",
                "yes",
                str(pdf_path),
                str(prefix),
            ],
            timeout=300,
        )
        images = sorted(pages_dir.glob("page-*.png"))
        if result["ok"] and images:
            return images, "pdftoppm"
        add_issue(
            issues,
            "警告",
            "PDF 到 PNG 渲染",
            "pdftoppm 渲染失败，改用 PyMuPDF 渲染。",
            detail=result["stderr"] or result["stdout"],
        )
    else:
        add_issue(issues, "警告", "PDF 到 PNG 渲染", "未找到 pdftoppm，改用 PyMuPDF 渲染。")

    images: list[Path] = []
    doc = fitz.open(pdf_path)
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    for index, page in enumerate(doc, start=1):
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image_path = pages_dir / f"page-{index}.png"
        pixmap.save(image_path)
        images.append(image_path)
    doc.close()
    return images, "PyMuPDF"


def inspect_images(images: list[Path], out_dir: Path, dpi: int, issues: list[Issue]) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": len(images), "dpi": dpi, "pages": []}
    thumbs: list[Image.Image] = []

    for index, image_path in enumerate(images, start=1):
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            if width < 1000 and dpi >= 200:
                add_issue(issues, "警告", "PNG 视觉检查", "渲染宽度偏低，可能影响视觉复核。", page=index, detail=f"{width}x{height}")

            gray = rgb.convert("L")
            white = Image.new("L", gray.size, 255)
            diff = ImageChops.difference(gray, white)
            mask = diff.point(lambda value: 255 if value > 12 else 0)
            bbox = mask.getbbox()
            if bbox is None:
                add_issue(issues, "警告", "PNG 视觉检查", "页面渲染结果接近空白。", page=index, detail=image_path.name)
                content_box = None
            else:
                margin = max(8, math.floor(min(width, height) * 0.008))
                left, top, right, bottom = bbox
                if left < margin or top < margin or width - right < margin or height - bottom < margin:
                    add_issue(
                        issues,
                        "提示",
                        "PNG 视觉检查",
                        "可见内容贴近图片边缘，建议人工确认是否被裁切。",
                        page=index,
                        detail=image_path.name,
                    )
                content_box = [left, top, right, bottom]

            summary["pages"].append(
                {
                    "page": index,
                    "file": str(image_path),
                    "width": width,
                    "height": height,
                    "content_box": content_box,
                }
            )

            thumb = rgb.copy()
            thumb.thumbnail((220, 300))
            tile = Image.new("RGB", (240, 330), "white")
            x = (240 - thumb.width) // 2
            tile.paste(thumb, (x, 10))
            draw = ImageDraw.Draw(tile)
            draw.text((10, 308), f"第 {index} 页", fill=(0, 0, 0))
            thumbs.append(tile)

    if thumbs:
        columns = min(4, len(thumbs))
        rows = math.ceil(len(thumbs) / columns)
        sheet = Image.new("RGB", (columns * 240, rows * 330), "white")
        for idx, thumb in enumerate(thumbs):
            x = (idx % columns) * 240
            y = (idx // columns) * 330
            sheet.paste(thumb, (x, y))
        sheet_path = out_dir / "contact-sheet.png"
        sheet.save(sheet_path)
        summary["contact_sheet"] = str(sheet_path)

    return summary


def write_report(
    out_dir: Path,
    docx_path: Path,
    versions: dict[str, str],
    docx_summary: dict[str, Any],
    md_summary: dict[str, Any] | None,
    pdf_summary: dict[str, Any] | None,
    image_summary: dict[str, Any] | None,
    render_method: str | None,
    issues: list[Issue],
) -> Path:
    report_path = out_dir / "format-review-report.md"
    severity_order = {"严重": 0, "警告": 1, "提示": 2}
    sorted_issues = sorted(issues, key=lambda item: (severity_order.get(item.severity, 9), item.stage, item.page or 0))
    counts = Counter(issue.severity for issue in sorted_issues)

    lines: list[str] = [
        "# Word 格式审查报告",
        "",
        f"- 文档：`{docx_path}`",
        f"- 输出目录：`{out_dir}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 问题统计：严重 {counts.get('严重', 0)}，警告 {counts.get('警告', 0)}，提示 {counts.get('提示', 0)}",
        "",
        "## 工具版本",
        "",
    ]
    for name, version in versions.items():
        lines.append(f"- {name}: {version}")

    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "- Markdown：`pandoc.md`",
            "- PDF：`pdf/`",
            "- 页面 PNG：`pages/`",
            "- 联系表：`contact-sheet.png`",
            "- 结构化数据：`format-review-data.json`",
            "",
            "## 摘要",
            "",
            f"- DOCX：段落 {docx_summary.get('paragraphs', 0)}，表格 {docx_summary.get('tables', 0)}，图片/绘图 {docx_summary.get('drawings', 0)}，节 {docx_summary.get('sections', 0)}",
        ]
    )
    if md_summary:
        lines.append(
            f"- Markdown：行 {md_summary.get('line_count', 0)}，标题 {md_summary.get('heading_count', 0)}，图片引用 {md_summary.get('image_refs', 0)}，表格样式行 {md_summary.get('table_like_lines', 0)}"
        )
    if pdf_summary:
        lines.append(
            f"- PDF：页数 {pdf_summary.get('page_count', 0)}，图片 {pdf_summary.get('image_count', 0)}，绘图对象 {pdf_summary.get('drawing_count', 0)}，字号范围 {pdf_summary.get('font_size_min')} 到 {pdf_summary.get('font_size_max')}"
        )
        if pdf_summary.get("font_counter"):
            fonts = "；".join(f"{name}({count})" for name, count in list(pdf_summary["font_counter"].items())[:8])
            lines.append(f"- 常见字体：{fonts}")
    if image_summary:
        lines.append(f"- PNG：页数 {image_summary.get('count', 0)}，DPI {image_summary.get('dpi')}，渲染方式 {render_method}")

    lines.extend(["", "## 问题清单", ""])
    if not sorted_issues:
        lines.append("未发现脚本可识别的格式问题。仍建议按正式模板抽查 PDF 和单页 PNG。")
    else:
        lines.append("| 严重程度 | 阶段 | 页码 | 问题 | 细节 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for issue in sorted_issues:
            page = str(issue.page) if issue.page is not None else "-"
            detail = issue.detail.replace("|", "\\|") if issue.detail else "-"
            lines.append(f"| {issue.severity} | {issue.stage} | {page} | {issue.message} | {detail} |")

    lines.extend(
        [
            "",
            "## 人工复核建议",
            "",
            "- 先查看 `contact-sheet.png` 定位疑似空白页、横竖页异常、明显裁切或大面积错位。",
            "- 再打开对应 `pages/page-*.png` 单页检查页眉页脚、页码、表格边框、图片、公式和特殊字符。",
            "- 如果用户提供模板或排版规范，按规范复查本报告中的提示项；没有规范时不要把默认阈值当成正式标准。",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审查 Word 文档格式并输出 Markdown、PDF、PNG 和报告。")
    parser.add_argument("docx", type=Path, help="待审查的 .docx 文件")
    parser.add_argument("--out-dir", type=Path, default=None, help="输出目录，默认使用文档名加时间戳")
    parser.add_argument("--dpi", type=int, default=220, help="PNG 渲染 DPI，默认 220")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    docx_path = args.docx.resolve()
    if not docx_path.exists():
        print(f"文件不存在：{docx_path}", file=sys.stderr)
        return 2
    if docx_path.suffix.lower() != ".docx":
        print("只支持 .docx 文件。", file=sys.stderr)
        return 2

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir.resolve() if args.out_dir else docx_path.with_name(f"{docx_path.stem}-format-review-{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    issues: list[Issue] = []
    versions = collect_tool_versions()
    docx_summary = inspect_docx_package(docx_path, issues)
    md_path = convert_to_markdown(docx_path, out_dir, issues)
    md_summary = inspect_markdown(md_path, issues) if md_path else None
    pdf_path = convert_to_pdf(docx_path, out_dir, issues)
    pdf_summary: dict[str, Any] | None = None
    image_summary: dict[str, Any] | None = None
    render_method: str | None = None

    if pdf_path:
        pdf_summary = inspect_pdf(pdf_path, issues)
        images, render_method = render_pdf_to_png(pdf_path, out_dir, args.dpi, issues)
        image_summary = inspect_images(images, out_dir, args.dpi, issues)

    data = {
        "document": str(docx_path),
        "output_dir": str(out_dir),
        "versions": versions,
        "docx": docx_summary,
        "markdown": md_summary,
        "pdf": pdf_summary,
        "images": image_summary,
        "render_method": render_method,
        "issues": [asdict(issue) for issue in issues],
    }
    (out_dir / "format-review-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = write_report(
        out_dir,
        docx_path,
        versions,
        docx_summary,
        md_summary,
        pdf_summary,
        image_summary,
        render_method,
        issues,
    )
    print(f"审查完成：{report_path}")
    return 1 if any(issue.severity == "严重" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
