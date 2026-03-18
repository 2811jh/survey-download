#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动刷新网易问卷系统 Cookie

使用 Playwright 打开浏览器，自动获取登录态 Cookie 并保存。
- 首次运行：需要手动登录（登录后自动保存 session）
- 后续运行：复用已保存的 session，自动获取新 Cookie（无需重新登录）

使用前需安装 Playwright：
  pip install playwright
  playwright install chromium
"""

import json
import os
import sys
import time


SURVEY_URL = "https://survey-game.163.com/index.html#/surveylist"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
PROFILE_DIR = os.path.join(SCRIPT_DIR, ".browser_profile")

# 需要提取的 Cookie 名称
TARGET_COOKIES = {"SURVEY_TOKEN", "JSESSIONID", "P_INFO"}


def _log(msg):
    print(f"[refresh_cookie] {msg}", flush=True)


def refresh_cookie(timeout=300):
    """
    自动刷新 Cookie。
    1. 打开浏览器访问问卷系统
    2. 如果已有登录态，自动获取 Cookie
    3. 如果没有，等待用户手动登录
    4. 检测到 SURVEY_TOKEN 后保存到 config.json

    返回: True=成功, False=失败
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("ERROR: Playwright not installed.")
        _log("  pip install playwright")
        _log("  playwright install chromium")
        return False

    _log("Launching browser...")
    with sync_playwright() as p:
        # 使用持久化上下文，保留登录 session
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="msedge",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else context.new_page()
        _log(f"Navigating to {SURVEY_URL}")
        page.goto(SURVEY_URL, wait_until="domcontentloaded")

        _log("Waiting for login cookies...")
        _log("(If you see the login page, please log in manually. The script will auto-detect.)")

        start_time = time.time()
        while time.time() - start_time < timeout:
            cookies = context.cookies()
            cookie_dict = {}
            for c in cookies:
                if c["name"] in TARGET_COOKIES:
                    cookie_dict[c["name"]] = c["value"]

            if "SURVEY_TOKEN" in cookie_dict and "JSESSIONID" in cookie_dict:
                # 验证：尝试访问问卷列表 API
                _log("Detected cookies, verifying...")
                try:
                    resp = page.evaluate("""async () => {
                        const r = await fetch('/view/survey/list', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
                            body: JSON.stringify({pageNo:1,surveyName:"",status:"-1",deliveryRange:-1,type:-1,groupId:-1,groupUser:-1,gameName:""})
                        });
                        return await r.json();
                    }""")
                    if resp.get("resultCode") == 100:
                        _log("Cookies verified successfully!")
                        # 保存到 config.json
                        config = {
                            "cookies": cookie_dict,
                            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                            json.dump(config, f, ensure_ascii=False, indent=2)
                        _log(f"Cookies saved to {CONFIG_PATH}")
                        context.close()
                        return True
                    else:
                        _log("Cookie detected but verification failed, waiting...")
                except Exception:
                    pass

            time.sleep(2)
            elapsed = int(time.time() - start_time)
            if elapsed % 30 == 0 and elapsed > 0:
                _log(f"Still waiting... ({elapsed}s / {timeout}s)")

        _log(f"Timeout after {timeout}s. Failed to detect valid cookies.")
        context.close()
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="自动刷新网易问卷系统 Cookie")
    parser.add_argument("--timeout", type=int, default=300, help="等待登录超时（秒，默认300）")
    args = parser.parse_args()

    success = refresh_cookie(timeout=args.timeout)
    if success:
        _log("✓ Cookie refresh completed!")
        # 输出 JSON 结果
        print(json.dumps({"status": "success", "message": "Cookie 已自动刷新"}, ensure_ascii=False))
    else:
        _log("× Cookie refresh failed.")
        print(json.dumps({"status": "error", "message": "Cookie 刷新失败"}, ensure_ascii=False))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
