# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_No unreleased changes._

## [1.0.1] - 2026-10-20

### Fixed

- **尺寸与清晰度面板空白**：部分机器上「尺寸预设」下拉框、「宽 / 高」输入框
  不显示当前值。原因是 Tk 9 运行环境缺少初始化脚本时，只读下拉框
  会让绑定在控件上的 Tcl 变量失效。现在下拉框与输入框直接读写控件文本，
  不再依赖这些变量，并在启动时检测 Tk 是否完整初始化，异常写入操作日志。
- **毫米输入行拥挤**：宽 / 高 两个单位标签不再被截断成 “m”，
  「高」标签与输入框间距放宽。
- **滑块标签被挤压**：「字迹微调」每行的提示文字在窄列里显示成 “细 ← →” 等
  被截断的样子，现在右列加宽、滑块加长，六个滑块的提示完整可见。

### Changed

- 尺寸预设文本改为「45 × 15 mm · 标准（合同）」这样以尺寸开头的写法，
  下拉框变窄时也不会把尺寸截掉。
- 选择预设时会同步刷新「宽 / 高」输入框，手动输入后会按当前尺寸重新匹配预设。

## [1.0.0] - 2026-10-20

### Added

- 首个正式版本 / First public release.
- **文本输入与语言识别**：输入框实时识别汉语、英语、数字与符号，中英混排自动分段排版，
  缺字（字体不含该字）会在状态栏提示。
- **透明背景 PNG 输出**：默认 45 × 15 mm @ 600 DPI，PNG 的 pHYs 块按目标毫米数
  重写，Word 插入后自带的尺寸就是界面里设定的尺寸；自动裁切空白边，
  并限制单图文件体积。
- **10 种手写字体**：标准楷体、行楷、霞鹜文楷（LXGW WenKai）、悠哉手写（Yozai）、
  新魏碑、舒体、隶书、仿宋、Ink Free、Segoe Script。
- **自备字体**：把 `.ttf / .otf` 放入 `fonts\` 或 `assets\fonts\custom\`，
  刷新后出现在字体列表，可使用毛泽东体等商业字体（用户自行提供）。
- **笔迹样式**：黑 / 红 / 蓝 × 钢笔 / 圆珠笔 / 铅笔，共 9 种组合，各有不同的笔锋、
  墨量、颗粒与毛边表现。
- **参数滑块**：粗细度、颜色深度、模糊度、笔画抖动、字体倾斜度、整体透明度。
  字体倾斜度以字迹底部为轴心旋转，左右各 24°，50 为端正，保持签名基线不动。
- **尺寸与输出**：30 × 10、45 × 15、55 × 18 mm 预设，自定义毫米尺寸，
  300 / 600 / 1200 DPI，透明背景或白底，自动裁切。
- **预览区**：按真实毫米比例模拟纸张与签字横线，所见即所得；「换个写法」
  重掷随机种子。
- **紧凑界面**：参数区分左右两列（输入 / 字体 / 笔型 | 尺寸 / 微调 / 操作），
  六个滑块两行三列排布；默认窗口 1180 × 760，小屏自动收缩到工作区内，
  左侧参数区可滚动，窗口可最大化 / 最小化并记忆大小。
- **保存与复制**：保存 PNG，或直接复制到剪贴板（PNG 格式）粘贴进 Word。
- **免安装单文件 exe**：Windows 10 / 11 即拷即用，标准可最大化 / 最小化窗口，
  记忆窗口大小与上次参数。
- **日志**：操作日志 `logs\hstool.log`（2 MB 滚动保留 3 份）、
  崩溃日志 `logs\crash.log`（含完整堆栈），程序目录不可写时自动切换到
  `%LOCALAPPDATA%\HandwrittenSignatureTool\logs\`。
- **命令行**：`--cli` 单次渲染、`--list-fonts`、`--selftest` 打包后自检、`--version`。
- **构建脚本**：`build.ps1` 一键完成字体子集化 → 单元测试 → PyInstaller 打包 →
  exe 自检 → 发布 zip。
- 24 项单元测试覆盖文本识别、渲染管线、尺寸与体积预算、字体解析、
  倾斜度方向与 PNG 物理尺寸。

### Licensing notes

- 项目代码 MIT；内置 LXGW WenKai、Yozai 依 SIL OFL 1.1 分发。
- 系统字体（楷体、行楷、新魏、舒体、隶书、仿宋、Ink Free、Segoe Script）不随仓库分发，
  仅在用户本机构建 exe 时打入私有副本。

[Unreleased]: https://github.com/wanwuyijian/Handwritten-font-Tool/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/wanwuyijian/Handwritten-font-Tool/releases/tag/v1.0.1
[1.0.0]: https://github.com/wanwuyijian/Handwritten-font-Tool/releases/tag/v1.0.0
