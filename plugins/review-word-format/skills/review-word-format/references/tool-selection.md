# 工具适配说明

## 角色分工

- Pandoc：适合把 `.docx` 转成 Markdown，检查标题、列表、表格、图片引用和正文结构。它不是版面引擎，不能代表 Word 或 PDF 的最终视觉效果。
- LibreOffice Writer：适合在无界面环境中把 `.docx` 转成 PDF，并保留大部分分页、字体、表格和图片布局。它和 Microsoft Word 的排版引擎不同，复杂文档仍需要人工抽查。
- PyMuPDF：适合快速读取 PDF 页面框、旋转、字体、图片、绘图对象，并可作为 PNG 渲染降级方案。
- pdfplumber：适合检查 PDF 文本坐标、单词边界、表格文本和疑似越界/重叠问题；它和 PyMuPDF 互补。
- pdftoppm：Poppler 的 PDF 到图片渲染工具，优先用于高保真 PNG。建议使用 `-r 200` 到 `-r 300`，并启用文字和矢量抗锯齿。
- Pillow：用于检查 PNG 是否空白、内容是否贴边，并生成联系表图片。

## 推荐顺序

1. 用 Pandoc 做结构层检查，避免只看 PDF 而漏掉标题层级、媒体引用或修订痕迹。
2. 用 LibreOffice 生成 PDF，固定一个可复核的版面证据。
3. 用 PyMuPDF 和 pdfplumber 同时检查 PDF，因为两者暴露的信息不同。
4. 用 pdftoppm 渲染 PNG；如果不可用，使用 PyMuPDF 渲染并在报告中标明降级。
5. 对复杂文档抽查单页 PNG，不只依赖自动阈值。

## 常见阈值

- 页面尺寸差异超过 2 pt：报告页面尺寸不一致。
- 文本或块边界超出页面 1 pt：报告越界。
- 内容距离页面边缘小于 6 pt：报告裁切风险。
- 字号小于 5 pt 或大于 48 pt：报告异常字号。
- 200 DPI 渲染宽度小于 1000 px：报告渲染分辨率过低。

这些阈值只用于发现风险，不等同于正式排版规范。用户给出模板或规范时，以用户规范为准。

## 失败处理

- Pandoc 缺失：无法完成 Markdown 结构检查，报告为严重问题。
- LibreOffice 缺失：无法生成 PDF，停止后续 PDF 和 PNG 检查。
- pdftoppm 缺失：记录警告并使用 PyMuPDF 渲染；若用户要求必须使用 Poppler，则报告为阻塞。
- Python 依赖缺失：使用 `uv run --with pymupdf --with pdfplumber --with pillow ...` 重新运行，不要使用常规安装命令。
