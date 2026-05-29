#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信读书全书内容爬取工具 - 导出为 Markdown 格式
基于 Playwright + Canvas fillText 拦截

使用前：
  pip install playwright
  playwright install chromium

用法：
  python weread_scraper.py <reader_url> [-c cookies.txt] [-o ./output]
  python weread_scraper.py https://weread.qq.com/web/reader/f0932fc... -o ./books
"""
import argparse
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CANVAS_PATCH_JS = """
window.__canvasChars = [];
const __origFT = CanvasRenderingContext2D.prototype.fillText;
CanvasRenderingContext2D.prototype.fillText = function(text, x, y) {
    if (text && text.trim().length > 0 && text.length <= 80) {
        window.__canvasChars.push({text, x: Math.round(x), y: Math.round(y)});
    }
    return __origFT.apply(this, arguments);
};
"""

REMOVE_MASKS_JS = """() => {
    document.querySelectorAll('.wr_mask, .double_btn_dialog_container, .readerMemberCardTipsNew, .wr_dialog').forEach(el => {
        el.style.display = 'none';
        el.style.pointerEvents = 'none';
        el.style.visibility = 'hidden';
    });
}"""


def emit(progress, message):
    if progress:
        progress(message)
    else:
        print(message)


def get_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "缺少 playwright 依赖，请先安装 requirements.txt"
        ) from exc
    return sync_playwright


def load_cookie_values(cookie_path):
    cookies = {}
    with open(cookie_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                cookies[key.strip()] = val.strip()
    if "wr_vid" not in cookies or "wr_skey" not in cookies:
        raise ValueError(f"{cookie_path} 中缺少 wr_vid 或 wr_skey")
    return cookies


def save_cookie_values(cookie_path, cookies):
    wr_vid = cookies.get("wr_vid", "")
    wr_skey = cookies.get("wr_skey", "")
    if not wr_vid or not wr_skey:
        raise ValueError("未获取到完整的 wr_vid / wr_skey")
    with open(cookie_path, "w", encoding="utf-8") as f:
        f.write("# 微信读书 Cookie 配置文件\n")
        f.write("# 由扫码登录自动生成；失效后可重新扫码刷新。\n\n")
        f.write(f"wr_vid={wr_vid}\n")
        f.write(f"wr_skey={wr_skey}\n")


def load_cookies(cookie_path):
    cookies = load_cookie_values(cookie_path)
    return [
        {"name": k, "value": v, "domain": ".weread.qq.com", "path": "/"}
        for k, v in cookies.items()
    ]


def launch_chromium(playwright, headless=True):
    try:
        return playwright.chromium.launch(headless=headless, channel="msedge")
    except Exception:
        return playwright.chromium.launch(headless=headless)


def get_context_cookie_values(context):
    values = {}
    for cookie in context.cookies("https://weread.qq.com"):
        if cookie["name"] in {"wr_vid", "wr_skey"}:
            values[cookie["name"]] = cookie["value"]
    return values


def login_with_qr(cookie_path="cookies.txt", timeout=180, progress=None, stop_event=None):
    sync_playwright = get_sync_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=False)
        context = browser.new_context(viewport={"width": 1100, "height": 760})
        page = context.new_page()
        emit(progress, "正在打开微信读书登录页，请在弹出的浏览器窗口中扫码登录...")
        page.goto("https://weread.qq.com/", wait_until="domcontentloaded", timeout=60000)

        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if stop_event and stop_event.is_set():
                    emit(progress, "已取消扫码登录")
                    return None
                values = get_context_cookie_values(context)
                if values.get("wr_vid") and values.get("wr_skey"):
                    save_cookie_values(cookie_path, values)
                    emit(progress, f"登录成功，Cookie 已保存到 {cookie_path}")
                    return values
                page.wait_for_timeout(1000)
        finally:
            browser.close()

        raise TimeoutError("扫码登录超时，请重新尝试")


def get_text(page):
    chars = page.evaluate("window.__canvasChars || []")
    if not chars:
        return ""
    lines = []
    current = [chars[0]]
    for c in chars[1:]:
        if abs(c["y"] - current[-1]["y"]) < 5:
            current.append(c)
        else:
            lines.append(current)
            current = [c]
    if current:
        lines.append(current)
    for line in lines:
        line.sort(key=lambda c: c["x"])
    return "\n".join("".join(c["text"] for c in line) for line in lines)


def scrape_book(url, cookies, output_dir, headless=True, progress=None, stop_event=None):
    sync_playwright = get_sync_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies(cookies)
            page = context.new_page()
            page.add_init_script(CANVAS_PATCH_JS)

            emit(progress, f"Loading page: {url}")
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(10)

            page.evaluate(REMOVE_MASKS_JS)
            time.sleep(2)

            title = page.evaluate("""() => {
                const og = document.querySelector('meta[property="og:title"]');
                return og ? og.content.replace(' - 微信读书', '').trim() : 'book';
            }""")
            emit(progress, f"书名: {title}")

            all_pages = []
            visited_urls = set()
            empty_count = 0

            for step in range(2000):
                if stop_event and stop_event.is_set():
                    emit(progress, "已停止导出")
                    break

                page.evaluate(REMOVE_MASKS_JS)

                current_url = page.evaluate("window.location.href")

                time.sleep(3)
                try:
                    page.wait_for_function(
                        "window.__canvasChars && window.__canvasChars.length > 0",
                        timeout=8000,
                    )
                except:
                    pass

                text = get_text(page)
                if text and len(text.strip()) > 5:
                    all_pages.append(text)
                    empty_count = 0
                    preview = text[:60].replace("\n", " | ")
                    emit(progress, f"  [{step+1}] {len(text):>5} 字 | {preview}")
                else:
                    empty_count += 1
                    if empty_count > 10:
                        emit(progress, f"  连续 {empty_count} 次空白，停止")
                        break

                page.evaluate("window.__canvasChars = []")

                navigated = False

                next_page = page.locator(".renderTarget_pager_button_right").first
                if next_page.is_visible():
                    try:
                        next_page.click(timeout=5000)
                        time.sleep(2)
                        navigated = True
                    except:
                        page.evaluate(REMOVE_MASKS_JS)

                if not navigated:
                    next_ch = page.locator(".readerFooter_button").first
                    if next_ch.is_visible():
                        try:
                            next_ch.click(timeout=5000)
                            time.sleep(3)
                            navigated = True
                        except:
                            page.evaluate(REMOVE_MASKS_JS)

                if not navigated:
                    page.keyboard.press("ArrowRight")
                    time.sleep(2)
                    if page.evaluate("window.location.href") != current_url:
                        navigated = True

                if not navigated:
                    page.mouse.click(1100, 400)
                    time.sleep(2)
                    if page.evaluate("window.location.href") != current_url:
                        navigated = True

                if not navigated:
                    if page.evaluate("window.location.href") == current_url:
                        emit(progress, f"\n导航结束 (step {step+1})")
                        break

                new_url = page.evaluate("window.location.href")
                visited_urls.add(new_url.split("reader/")[-1])

            if all_pages:
                full_text = "\n\n".join(all_pages)
                os.makedirs(output_dir, exist_ok=True)
                filename = re.sub(r'[<>:"/\\|?*]', "_", title).strip(" .")[:100] or "book"
                filepath = os.path.join(output_dir, f"{filename}.md")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(f"# {title}\n\n---\n\n{full_text}\n")
                emit(progress, f'\n{"="*60}')
                emit(progress, f"导出完成: {filepath}")
                emit(progress, f"  页面: {len(all_pages)}, 字数: {len(full_text)}")
                emit(progress, f"  大小: {os.path.getsize(filepath)/1024:.1f} KB")
                return filepath

            emit(progress, "\n未采集到内容")
            return None
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="微信读书全书内容爬取工具 - 导出为 Markdown"
    )
    parser.add_argument("url", help="微信读书阅读页面 URL")
    parser.add_argument("-c", "--cookies", default="cookies.txt", help="Cookie 文件路径")
    parser.add_argument("-o", "--output", default="./output", help="输出目录")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    args = parser.parse_args()

    try:
        cookies = load_cookies(args.cookies)
    except ValueError as exc:
        print(f"[错误] {exc}")
        sys.exit(1)
    print(f"[info] wr_vid={cookies[0]['value']}")
    scrape_book(args.url, cookies, args.output, headless=not args.no_headless)


if __name__ == "__main__":
    main()
