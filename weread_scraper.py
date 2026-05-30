#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信读书全书内容爬取工具 - 导出为 Markdown 格式
优先使用网页端章节接口直接解码正文；不可用时回退到 Canvas fillText 拦截

使用前：
  pip install playwright
  playwright install chromium

用法：
  python weread_scraper.py <reader_url> [-c cookies.txt] [-o ./output]
  python weread_scraper.py https://weread.qq.com/web/reader/f0932fc... -o ./books
"""
import argparse
import base64
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET

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

CHAPTER_INFO_JS = """async (bookId) => {
    const headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
    };
    for (const url of ["/web/book/publicchapterInfos", "/web/book/chapterInfos"]) {
        const resp = await fetch(url, {
            headers,
            body: JSON.stringify({bookIds: [bookId]}),
            method: "POST",
            credentials: "include",
        });
        const data = await resp.json();
        if (data && data.data && data.data[0] && data.data[0].updated && data.data[0].updated.length) {
            return data.data[0];
        }
    }
    return null;
}"""

FETCH_CHAPTER_JS = """async ({url, params}) => {
    const resp = await fetch(url, {
        headers: {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
        },
        body: JSON.stringify(params),
        method: "POST",
        credentials: "include",
    });
    return {status: resp.status, text: await resp.text()};
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


def md5_hex(value):
    return hashlib.md5(str(value).encode("utf-8")).hexdigest()


def weread_hash(value):
    value = str(value)
    digest = md5_hex(value)
    result = digest[:3]
    if value.isdigit():
        chunks = [format(int(value[i : i + 9]), "x") for i in range(0, len(value), 9)]
        type_flag = "3"
    else:
        chunks = ["".join(format(ord(c), "x") for c in value)]
        type_flag = "4"

    result += type_flag + "2" + digest[-2:]
    for idx, chunk in enumerate(chunks):
        result += f"{len(chunk):02x}{chunk}"
        if idx < len(chunks) - 1:
            result += "g"

    if len(result) < 20:
        result += digest[: 20 - len(result)]
    result += md5_hex(result)[:3]
    return result


def weread_signature(query):
    a = 0x15051505
    b = a
    i = len(query) - 1
    while i > 0:
        a = (a ^ (ord(query[i]) << ((len(query) - i) % 30))) & 0x7FFFFFFF
        b = (b ^ (ord(query[i - 1]) << (i % 30))) & 0x7FFFFFFF
        i -= 2
    return format(a + b, "x").lower()


def make_chapter_params(book_id, chapter_uid, pc, ps):
    params = {
        "b": weread_hash(book_id),
        "c": weread_hash(chapter_uid),
        "ct": str(int(time.time())),
        "pc": str(pc),
        "prevChapter": "false",
        "ps": str(ps or "11"),
        "r": str(random.randrange(10000) ** 2),
        "sc": 0,
        "st": 0,
    }
    query = "&".join(f"{key}={value}" for key, value in params.items())
    params["s"] = weread_signature(query)
    return params


def get_book_id_from_page(page):
    for _ in range(30):
        book_id = page.evaluate("""() => {
            const el = document.querySelector('script[type="application/ld+json"]');
            if (!el) return null;
            try {
                const data = JSON.parse(el.textContent);
                return data["@Id"] || data.bookId || null;
            } catch {
                return null;
            }
        }""")
        if book_id:
            return str(book_id)
        page.wait_for_timeout(500)
    return None


def get_book_info(page, book_id):
    return page.evaluate(CHAPTER_INFO_JS, book_id)


def fetch_chapter_fragments(page, book_format, params):
    if book_format == "epub":
        urls = ["/web/book/chapter/e_0", "/web/book/chapter/e_1", "/web/book/chapter/e_3"]
    else:
        urls = ["/web/book/chapter/t_0", "/web/book/chapter/t_1"]

    fragments = []
    for url in urls:
        result = page.evaluate(FETCH_CHAPTER_JS, {"url": url, "params": params})
        text = result.get("text", "")
        if result.get("status") != 200 or text.startswith("{"):
            raise RuntimeError(f"章节接口返回异常: {url} status={result.get('status')}")
        fragments.append(text)
    return fragments


def scramble_positions(value):
    length = len(value)
    if length < 4:
        return []
    if length < 11:
        return [0, 2]

    n = min(4, math.ceil(length / 10))
    tmp = ""
    for idx in range(length - 1, length - n - 1, -1):
        tmp += str(int(format(ord(value[idx]), "b"), 4))

    positions = []
    m = length - n - 2
    step = len(str(m))
    idx = 0
    while len(positions) < 10 and idx + step < len(tmp):
        positions.append(int(tmp[idx : idx + step]) % m)
        positions.append(int(tmp[idx + 1 : idx + 1 + step]) % m)
        idx += step
    return positions


def restore_scrambled(value, positions):
    chars = list(value)
    for idx in range(len(positions) - 1, 0, -2):
        for offset in (1, 0):
            left = positions[idx] + offset
            right = positions[idx - 1] + offset
            if left < len(chars) and right < len(chars):
                chars[left], chars[right] = chars[right], chars[left]
    return "".join(chars)


def decode_weread_fragments(fragments):
    fragments = [fragment for fragment in fragments if fragment]
    if len(fragments) == 4:
        fragments.pop(2)
    if not fragments:
        return ""

    payload = "".join(fragment[32:] for fragment in fragments if len(fragment) > 32)
    payload = payload[1:]
    if not payload:
        return ""

    encoded = restore_scrambled(payload, scramble_positions(payload))
    encoded = re.sub(r"[^A-Za-z0-9+/=_-]", "", encoded).replace("-", "+").replace("_", "/")
    encoded += "=" * (-len(encoded) % 4)
    return base64.b64decode(encoded).decode("utf-8", errors="replace")


def _mathml_tag_name(node):
    return node.tag.rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""


def _mathml_text(node):
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _mathml_render(node):
    tag = _mathml_tag_name(node)
    if tag in {"annotation", "annotation-xml"}:
        return ""
    if tag in {"math", "mrow", "semantics"}:
        if tag == "semantics":
            for child in node:
                rendered = _mathml_render(child)
                if rendered:
                    return rendered
            return _mathml_text(node)
        return "".join(_mathml_render(child) or (child.text or "") for child in node).strip()
    if tag in {"mi", "mn", "mo", "mtext"}:
        return _mathml_text(node)
    if tag == "msup":
        items = list(node)
        if len(items) >= 2:
            return f"{_mathml_render(items[0])}^{{{_mathml_render(items[1])}}}"
    if tag == "msub":
        items = list(node)
        if len(items) >= 2:
            return f"{_mathml_render(items[0])}_{{{_mathml_render(items[1])}}}"
    if tag == "msubsup":
        items = list(node)
        if len(items) >= 3:
            return f"{_mathml_render(items[0])}_{{{_mathml_render(items[1])}}}^{{{_mathml_render(items[2])}}}"
    if tag == "mfrac":
        items = list(node)
        if len(items) >= 2:
            return f"\\frac{{{_mathml_render(items[0])}}}{{{_mathml_render(items[1])}}}"
    if tag == "msqrt":
        return f"\\sqrt{{{''.join(_mathml_render(child) for child in node)}}}"
    if tag == "mroot":
        items = list(node)
        if len(items) >= 2:
            return f"\\sqrt[{_mathml_render(items[1])}]{{{_mathml_render(items[0])}}}"
    if tag == "mfenced":
        inner = "".join(_mathml_render(child) for child in node)
        open_ch = node.attrib.get("open", "(")
        close_ch = node.attrib.get("close", ")")
        return f"{open_ch}{inner}{close_ch}"
    if tag == "mphantom":
        return ""
    if tag in {"munder", "mover", "munderover"}:
        items = list(node)
        if tag == "munder" and len(items) >= 2:
            return f"{_mathml_render(items[0])}_{{{_mathml_render(items[1])}}}"
        if tag == "mover" and len(items) >= 2:
            return f"{_mathml_render(items[0])}^{{{_mathml_render(items[1])}}}"
        if tag == "munderover" and len(items) >= 3:
            return f"{_mathml_render(items[0])}_{{{_mathml_render(items[1])}}}^{{{_mathml_render(items[2])}}}"
    if tag == "mtable":
        rows = []
        for tr in node:
            if _mathml_tag_name(tr) != "mtr":
                continue
            cells = [_mathml_render(td) for td in tr if _mathml_tag_name(td) == "mtd"]
            rows.append(" | ".join(cell for cell in cells if cell))
        return " ; ".join(rows)
    if tag in {"mtr", "mtd"}:
        return "".join(_mathml_render(child) for child in node)
    rendered = "".join(_mathml_render(child) for child in node)
    if rendered:
        return rendered
    return _mathml_text(node)


def replace_mathml(content):
    if "<math" not in content:
        return content

    def replace_block(match):
        raw = match.group(0)
        try:
            root = ET.fromstring(raw)
            rendered = _mathml_render(root).strip()
            if rendered:
                return f"${rendered}$"
        except Exception:
            pass
        return re.sub(r"<[^>]+>", "", raw)

    return re.sub(r"<math\b[\s\S]*?</math>", replace_block, content, flags=re.IGNORECASE)


class MarkdownExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def append(self, value):
        if value:
            self.parts.append(value)

    def newline(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.newline()
            self.append("#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "table", "tr"}:
            self.newline()
        elif tag == "br":
            self.newline()
        elif tag == "li":
            self.newline()
            self.append("- ")
        elif tag == "img":
            alt = (attrs.get("alt") or attrs.get("title") or "").strip()
            src = (attrs.get("src") or "").strip()
            if alt:
                self.append(alt)
            elif src:
                self.append(f"![image]({src})")
        elif tag == "sup":
            self.append("^{")
        elif tag == "sub":
            self.append("_{")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "section", "article", "li", "tr", "table"}:
            self.newline()
        elif tag in {"sup", "sub"}:
            self.append("}")

    def handle_data(self, data):
        if not self.skip_depth:
            self.append(data)

    def markdown(self):
        text = html.unescape("".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(content):
    content = replace_mathml(content)
    parser = MarkdownExtractor()
    parser.feed(content)
    parser.close()
    return parser.markdown()


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


def scrape_book_direct(page, output_dir, chapter_request_params, progress=None, stop_event=None):
    emit(progress, "尝试使用章节接口直接获取内容...")

    book_id = get_book_id_from_page(page)
    if not book_id:
        raise RuntimeError("未在页面中找到 bookId")

    book_info = get_book_info(page, book_id)
    if not book_info:
        raise RuntimeError("未获取到章节目录")

    book = book_info.get("book", {})
    title = book.get("title") or "book"
    book_format = book.get("format") or "epub"
    chapters = [
        chapter
        for chapter in book_info.get("updated", [])
        if chapter.get("chapterUid") is not None
    ]
    if not chapters:
        raise RuntimeError("章节目录为空")

    for _ in range(30):
        if chapter_request_params.get("pc"):
            break
        page.wait_for_timeout(500)
    pc = chapter_request_params.get("pc")
    ps = chapter_request_params.get("ps") or "11"
    if not pc:
        raise RuntimeError("未捕获到章节接口参数 pc")

    emit(progress, f"书名: {title}")
    emit(progress, f"章节接口可用: {len(chapters)} 章, format={book_format}")

    all_chapters = []
    for idx, chapter in enumerate(chapters, start=1):
        if stop_event and stop_event.is_set():
            emit(progress, "已停止导出")
            break

        chapter_title = chapter.get("title") or f"Chapter {idx}"
        chapter_uid = chapter.get("chapterUid")
        params = make_chapter_params(book_id, chapter_uid, pc, ps)
        fragments = fetch_chapter_fragments(page, book_format, params)
        content = decode_weread_fragments(fragments).strip()
        if book_format == "epub":
            content = html_to_markdown(content)

        if content:
            if chapter_title and chapter_title not in content[:120]:
                content = f"## {chapter_title}\n\n{content}"
            all_chapters.append(content)
            preview = content[:60].replace("\n", " | ")
            emit(progress, f"  [{idx}/{len(chapters)}] {len(content):>5} 字 | {preview}")
        else:
            emit(progress, f"  [{idx}/{len(chapters)}] 空章节: {chapter_title}")

    if not all_chapters:
        raise RuntimeError("章节接口未解出正文")

    full_text = "\n\n".join(all_chapters)
    os.makedirs(output_dir, exist_ok=True)
    filename = re.sub(r'[<>:"/\\|?*]', "_", title).strip(" .")[:100] or "book"
    filepath = os.path.join(output_dir, f"{filename}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n---\n\n{full_text}\n")

    emit(progress, f'\n{"="*60}')
    emit(progress, f"导出完成: {filepath}")
    emit(progress, f"  章节: {len(all_chapters)}, 字数: {len(full_text)}")
    emit(progress, f"  大小: {os.path.getsize(filepath)/1024:.1f} KB")
    return filepath


def scrape_book(url, cookies, output_dir, headless=True, progress=None, stop_event=None):
    sync_playwright = get_sync_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies(cookies)
            page = context.new_page()
            chapter_request_params = {}

            def remember_chapter_params(request):
                if "/web/book/chapter/" not in request.url or request.method != "POST":
                    return
                try:
                    data = json.loads(request.post_data or "{}")
                    if data.get("pc"):
                        chapter_request_params.update(data)
                except Exception:
                    pass

            page.on("request", remember_chapter_params)
            page.add_init_script(CANVAS_PATCH_JS)

            emit(progress, f"Loading page: {url}")
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(10)

            page.evaluate(REMOVE_MASKS_JS)
            time.sleep(2)

            try:
                return scrape_book_direct(
                    page,
                    output_dir,
                    chapter_request_params,
                    progress=progress,
                    stop_event=stop_event,
                )
            except Exception as exc:
                emit(progress, f"章节接口不可用，回退到 Canvas 拦截: {exc}")

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
