# 微信读书 Markdown 导出工具

一个轻量的微信读书导出工具，提供桌面 GUI 和命令行两种入口。工具使用 Playwright 打开微信读书网页，通过扫码登录获取本地会话，并将阅读页内容导出为 Markdown 文件。

## 功能

- 桌面 GUI：粘贴阅读页链接后即可导出
- 扫码登录：自动保存 `wr_vid` 和 `wr_skey` 到本地 `cookies.txt`
- Markdown 导出：按书名生成 `.md` 文件
- 直接文本源：优先调用微信读书网页端章节接口解码正文，尽量保留图片 `alt/title` 和公式文本
- 进度日志：导出过程中实时显示采集状态
- 可停止任务：长书导出时可以中途停止
- 分辨率适配：窗口会按屏幕尺寸和 DPI 自动调整
- 命令行模式：保留脚本化调用方式

## 环境要求

- Windows、macOS 或 Linux
- Python 3.10+
- Playwright Chromium
- Tkinter

Windows 官方 Python 通常自带 Tkinter。如果启动 GUI 时出现 Tcl/Tk 相关错误，请重新安装带 Tcl/Tk 组件的 Python。

## 安装

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

建议始终使用 `python -m pip`，这样可以确保依赖安装到当前正在运行的 Python 环境中。

## 使用 GUI

```powershell
python weread_gui.py
```

使用步骤：

1. 点击「扫码登录」。
2. 在弹出的微信读书浏览器窗口中使用微信扫码。
3. 登录成功后，程序会在项目目录生成 `cookies.txt`。
4. 打开微信读书网页阅读器，复制阅读页 URL。
5. 将 URL 粘贴到 GUI，选择输出目录。
6. 点击「开始导出」。

默认输出目录是 `output/`。导出完成后会生成以书名命名的 Markdown 文件。

## 命令行用法

```powershell
python weread_scraper.py "https://weread.qq.com/web/reader/..." -c cookies.txt -o output
```

显示采集浏览器窗口：

```powershell
python weread_scraper.py "https://weread.qq.com/web/reader/..." --no-headless
```

参数说明：

- `url`：微信读书网页阅读器 URL
- `-c, --cookies`：Cookie 文件路径，默认 `cookies.txt`
- `-o, --output`：导出目录，默认 `./output`
- `--no-headless`：显示浏览器窗口，便于排查采集问题

## 本地文件

以下文件只保存在本地，不会提交到 Git：

- `cookies.txt`：微信读书登录态
- `output/`：导出的 Markdown 文件
- `__pycache__/`：Python 缓存

如果登录失效，重新点击「扫码登录」即可刷新 `cookies.txt`。

## 注意事项

本工具只负责把你已能在微信读书网页端正常阅读的内容导出到本地。请遵守微信读书服务条款和相关版权要求，导出的内容仅用于个人备份、学习和整理。

如果章节接口不可用，程序会自动回退到原来的 Canvas 文字拦截方案。若采集过程中出现空白页、会员弹窗或翻页失败，可以尝试勾选「显示采集浏览器」观察页面状态，再重新导出。
