---
name: review-word-format
description: "审查 Word 文档（.docx）的格式正确性和版面风险。用于检查 DOCX 是否存在标题层级异常、表格或图片丢失、分页或边距异常、空白页、文字越界、字体异常、修订痕迹、PDF 转换问题，或需要按 Pandoc 转 Markdown、LibreOffice 转 PDF、PyMuPDF 与 pdfplumber 分析 PDF、pdftoppm 或 PyMuPDF 渲染 PNG 的流水线输出可执行审查报告。也用于在完成 Word 文档创建、修改或编辑后自动触发一次格式复查。"
---

# Word 格式审查

## 总览

使用三层证据审查 `.docx` 格式：先用 Pandoc 转 Markdown 检查逻辑结构，再用 LibreOffice 转 PDF 并用 PyMuPDF 与 pdfplumber 检查版面对象，最后把 PDF 渲染为高保真 PNG 做视觉复核。

当本轮任务刚完成 Word 文档创建、修改或编辑时，自动使用本 skill 对最终 `.docx` 执行一次格式复查；除非用户明确跳过格式检查。

优先输出可执行的问题清单和证据路径；没有明确模板或排版规范时，只能报告“未发现脚本可识别的问题”，不要承诺“格式绝对无误”。

## 快速执行

1. 确认输入是 `.docx`，并询问或读取用户给出的格式标准、模板、页边距、字体、字号、标题层级、页眉页脚、页码、表格样式等要求。
2. 先确认外部工具可用：

```powershell
pandoc --version
soffice --headless --help
pdftoppm -v
```

3. 运行审查脚本：

```powershell
uv run --with pymupdf --with pdfplumber --with pillow C:\Users\Administrator\.codex\skills\review-word-format\scripts\review_word_format.py <文档.docx>
```

4. 打开输出目录中的 `format-review-report.md`、`contact-sheet.png` 和 `pages/*.png`，结合用户标准进行人工复核。

如果缺少 `pdftoppm`，脚本会优先记录该事实，并使用 PyMuPDF 渲染 PNG 作为降级方案。若用户明确要求必须使用 Poppler 渲染结果，则把缺少 `pdftoppm` 作为阻塞问题报告。

## 审查流水线

### 1. DOCX 到 Markdown

使用 Pandoc 将 `.docx` 转成 Markdown，检查语义结构：

- 标题层级是否跳级或缺失。
- 表格、图片、列表是否在转换后仍可识别。
- Markdown 是否为空、是否出现异常原始 HTML、缺失媒体文件或明显占位符。
- DOCX 包内是否存在未接受修订、批注、`altChunk`、损坏的主文档 XML。

这一层适合发现结构问题，不适合判断页边距、换行、字体和分页。

### 2. DOCX 到 PDF 并分析版面

使用 `soffice --headless --convert-to pdf:writer_pdf_Export` 生成 PDF。只使用命令名 `soffice`，不要追加可执行文件扩展名。

使用 PyMuPDF 检查页面、字体、图片、绘图对象和页面框；使用 pdfplumber 检查文字坐标、边界、疑似重叠和表格文本。重点报告：

- 页面尺寸不一致、旋转异常、空白页或近空白页。
- 文字块或单词超出页面边界。
- 正文内容贴近页面边缘，存在裁切风险。
- 极小或极大的字号、字体族异常、图片数量异常。
- PDF 页面文本为空但 DOCX/Markdown 中有内容，提示转换失败风险。

### 3. PDF 到 PNG 视觉复核

优先使用 `pdftoppm -r <dpi> -png -aa yes -aaVector yes` 生成页面 PNG；未安装 Poppler 时使用 PyMuPDF 以相同 DPI 渲染。

视觉复核时检查：

- 页面是否空白、截断、内容贴边或被裁切。
- 页眉页脚、页码、表格边框、图片、公式和特殊字符是否正常显示。
- 横向/纵向页面、章节分页、图表和表格跨页是否符合要求。
- `contact-sheet.png` 只用于快速定位问题；最终判断以单页 PNG 和 PDF 为准。

## 工具取舍

需要解释工具适配性、阈值或降级逻辑时，读取 `references/tool-selection.md`。

## 报告规则

- 按严重程度输出：`严重`、`警告`、`提示`。
- 每个问题写明阶段、页码、证据文件和建议动作。
- 区分“脚本发现的客观问题”和“需要人工按模板判断的问题”。
- 如果用户给出正式模板或规范，按该规范复查脚本输出；如果没有规范，避免把默认阈值当成正式标准。
