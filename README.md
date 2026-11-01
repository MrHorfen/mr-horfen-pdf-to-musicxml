# Mr.Horfen — PDF to MusicXML

把扫描版 / 数字版五线谱 PDF 转成结构化 MusicXML 的命令行工具。核心是**「先规范图像，再交给 OMR，最后做结构后处理」**：OMR 引擎只负责认符号，页面级和乐谱级的结构问题由本仓库的规则层兜住。

> 面向钢琴、吉他、二胡等单声部/多声部乐谱。不依赖任何自训练模型，整条链路是图像处理 + Audiveris + MusicXML 规则修正。

<br>

## 中文

### 链路

```
PDF
 ├─ 渲染层       PyMuPDF 灰度渲染，DPI 可调，硬性压到 2000 万像素以内
 ├─ 预处理层     deskew 纠偏 / enhancement 增强 / region_cut 区域裁剪 /
 │               quality_check 质量闸门 / pdf_type_detector 扫描版判定
 ├─ OMR 层       Audiveris（Java，外部依赖）逐页识别 → .mxl
 ├─ 合并层       解压 mxl 取 XML，跨页 part 合并、小节号全局对齐
 └─ 后处理层     声部补全与登记、缺声部补休止、结构校验 → 交付 MusicXML
```

### 依赖

| 依赖 | 说明 |
|---|---|
| Python 3.10+ | |
| Audiveris 5.x + Java 17 | **必须单独安装**，本仓库不内置 |
| PyMuPDF / opencv-python / lxml / Pillow / numpy / scipy | `pip install -r requirements.txt` |

Audiveris 与 Java 路径通过环境变量传入，不写死在代码里：

```powershell
$env:AUDIVERIS = "C:\Program Files\Audiveris\Audiveris.exe"   # 或可执行文件名
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17"
```

### 用法

```powershell
pip install -r requirements.txt

python score_converter/main.py -i .\input\score.pdf -o .\output --dpi 300 --instrument piano -v
```

| 参数 | 说明 |
|---|---|
| `-i/--input` | 输入 PDF |
| `-o/--output` | 输出目录（默认与输入同目录） |
| `--dpi` | 扫描版渲染 DPI，默认 300（谱线间距约 20px 时识别最稳） |
| `--instrument` | 目标乐器，默认 `erhu` |
| `-v` | 详细日志 |

批处理另有 `batch_convert.py`、`batch_convert_piano_exam.py`、`batch_convert_abrsm.py`，目录通过 `INPUT_BASE` / `OUTPUT_BASE` / `TEMP_DIR` 环境变量指定。

### 关键模块

| 文件 | 职责 |
|---|---|
| `score_converter/main.py` | CLI 入口与流程编排 |
| `omr_engine.py` / `modules/omr_engine_batch.py` | Audiveris 调用、mxl 解包 |
| `modules/musicxml_merge.py` | 跨页合并、part-list 与 part 一一对应 |
| `modules/musicxml_postprocessor.py` | 声部补全、整小节休止补齐、小节号对齐 |
| `modules/postprocess_pipeline.py` | 后处理编排 + 结构校验报告 |
| `modules/pdf_preprocessor/` | 渲染、纠偏、增强、区域裁剪、质量闸门 |
| `musicxml_exporter.py` / `error_corrector.py` | 导出与规则级纠错 |

### 已知限制（不粉饰）

- 识别质量的上限由**原始扫描质量**决定。歪斜、糊边、手写批注会直接拉低音高与符杠的准确率。
- Audiveris 对 2000 万像素以上图像会直接失败，渲染 DPI 必须按纸张尺寸算，不是越高越好。
- 本仓库**不含**任何训练权重，也不含训练代码。想提升精度只能从图像预处理和结构后处理入手。
- 声部编号跨页不一致时，本仓库的策略是「补登记 + 补休止」，不做破坏性删除。

<br>

---

## English

A command-line tool that converts scanned or digital staff-notation PDFs into structured MusicXML. The idea is simple: **normalize the image first, let the OMR engine read symbols, then let a rule layer fix everything structural.** The OMR engine never decides page layout or part identity.

### Pipeline

1. **Render** — grayscale rendering via PyMuPDF, adjustable DPI, hard-capped at 20M pixels (Audiveris limit).
2. **Preprocess** — deskew, contrast enhancement, region cropping, quality gate, scanned-vs-digital detection.
3. **OMR** — Audiveris (Java, external) per page → `.mxl`.
4. **Merge** — unzip `.mxl`, merge parts across pages, globally align measure numbers.
5. **Post-process** — register missing parts, fill whole-measure rests, validate structure, emit MusicXML.

### Requirements

- Python 3.10+
- **Audiveris 5.x with Java 17** — installed separately, not bundled here
- `pip install -r requirements.txt`

Set the toolchain paths via environment variables (`AUDIVERIS`, `JAVA_HOME`).

### Usage

```powershell
python score_converter/main.py -i .\input\score.pdf -o .\output --dpi 300 --instrument piano -v
```

### Limitations

- Output quality is bounded by **input scan quality** — skew and smudges directly hurt pitch and beam accuracy.
- Above 20M pixels per page Audiveris fails outright; choose DPI based on paper size.
- No trained weights and no training code are included.
- Cross-page part-number mismatches are repaired by registering and filling rests — never by deleting parts.

<br>

## License / 许可

**PolyForm Noncommercial License 1.0.0** — 源码公开，允许个人学习、研究、非商业使用与修改；**禁止任何商业用途**（含内部商业业务、SaaS、收费分发）。

This repository is **source-available, not open source** in the OSI sense. Commercial use of any kind is prohibited. See [LICENSE](LICENSE).

商业授权请联系作者。/ For commercial licensing, contact the author.
