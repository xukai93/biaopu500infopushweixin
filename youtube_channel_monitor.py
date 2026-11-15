#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 频道更新监控 · 微信推送
================================================
功能：按给定的一批 YouTube 频道（@handle），检查是否有新视频发布；
      有更新时把「所有频道的新视频」汇总成一条消息推送到微信。

数据源：YouTube **官方 RSS** (feeds/videos.xml)
  - 无需 API Key、无需 OAuth、无需第三方解析服务
  - 每个频道返回最近约 15 条视频，含 videoId / 标题 / 发布时间 / 链接
  - 频道名直接从 RSS 的 <title> 读取，无需手工维护中文名

去重机制（两层，缺一不可）：
  1. state 文件记录每个频道已见过的 videoId  → 精确去重
  2. 只认 LOOKBACK_HOURS 内发布的视频        → 即使 state 丢失（缓存被清），
     也不会把几个月前的老视频当成「更新」批量推出去

【推送渠道（三选一，与 sp500_daily_report.py 完全一致）】
  1. serverchan     - Server酱 (sct.ftqq.com)  ← 当前启用
  2. wecom_app      - 企业微信自建应用（纯官方，微信插件纯文本）
  3. wecom_webhook  - 企业微信群机器人（纯官方）

【安全】本脚本不含任何明文密钥，全部从环境变量读取。
  本地：export SERVERCHAN_SENDKEY=xxx
  云端：GitHub Actions 用 secrets.SERVERCHAN_SENDKEY 注入
  切勿把 Key 写进脚本或提交到公开仓库。

用法：
  python3 youtube_channel_monitor.py                # 正式运行（有新视频才推送）
  python3 youtube_channel_monitor.py --dry-run      # 全流程跑一遍，只打印，不推送、不写 state
  python3 youtube_channel_monitor.py --test-push    # 抓真实数据，强制把每个频道最新 1 条当「新视频」推一条，用于手机看排版效果；不改 state
  python3 youtube_channel_monitor.py --init         # 重建基线：把当前视频全部记为已见，不推送（新增频道/校准时用）
  python3 youtube_channel_monitor.py --preview-all  # 打印三套渠道模板
  python3 youtube_channel_monitor.py --resolve-only # 连通性自检：只解析并打印各频道 RSS 地址，不推送、不写 state
"""

import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

# ==================== 配置区（按需修改） ====================

# --- 推送渠道 ---
PUSH_CHANNEL = "serverchan"          # "serverchan" / "wecom_app" / "wecom_webhook"
SERVERCHAN_SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "")
SERVERCHAN_API = "https://sctapi.ftqq.com"

# 企业微信自建应用（纯官方）
WECOM_CORPID = os.environ.get("WECOM_CORPID", "")
WECOM_CORPSECRET = os.environ.get("WECOM_CORPSECRET", "")
WECOM_AGENTID = os.environ.get("WECOM_AGENTID", "")
WECOM_TOUSER = "@all"

# 企业微信群机器人（纯官方）
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")

WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"

# --- 要监控的频道 ---
# 只写 @handle 即可，频道显示名会自动从 RSS 读取。
# 中文 handle 直接写中文，脚本会自动做 URL 编码。
CHANNELS = [
    "@JiangFengTimes",
    "@Tankman2020",
    "@wangzhian",
    "@realsert",
    "@xiaodaodalang",
    "@m2story",
    "@新官场",
    "@historyechoes",
    "@chaijing2023",
    "@dlczxxs",
    "@ma-siku",
]

# 兜底：若某频道页面反爬导致自动解析 channel_id 失败，可在这里手动填 RSS 地址。
# 获取方式：浏览器打开频道页 → 查看网页源代码 → 搜索 "channelId" 或 "rss+xml"
#   例："@somehandle": "https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxxxxxxxxxx",
FEED_URL_OVERRIDES = {}

# --- 行为参数 ---
STATE_FILE = "youtube_state.json"   # 状态文件（GitHub Actions 里由 cache 跨次运行保留）
LOOKBACK_HOURS = 48                 # 只把最近 N 小时内发布的视频视为「新更新」
MAX_SEEN = 30                       # 每个频道保留多少个历史 videoId
MAX_VIDEOS_PER_CHANNEL = 5          # 单条消息里每个频道最多列几条，防刷屏
NOTIFY_ON_INIT = True               # 首次建立基线时，推一条「监控已启动」
NOTIFY_WHEN_EMPTY = False           # 没有新视频时是否也推一条（默认不推，免打扰）
NOTIFY_ON_ERROR = True              # 全部频道抓取失败时，推一条告警
TIMEOUT = 20
TZ = ZoneInfo("Asia/Shanghai")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

YOUTUBE_BASE = "https://www.youtube.com"
RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================== HTTP ====================


def _get(url: str, **kwargs) -> requests.Response:
    """带一次重试的 GET，缓解云端偶发网络抖动。"""
    last = None
    for attempt in range(2):
        try:
            resp = SESSION.get(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:                       # noqa: BLE001
            last = e
            if attempt == 0:
                continue
    raise RuntimeError(f"GET 失败 {url}: {last}")


# ==================== 频道 / RSS ====================


def resolve_feed_url(handle: str, cached: str = "") -> str:
    """把 @handle 解析成 RSS feed 地址。

    优先用 state 里缓存的结果（之后每次运行只请求 RSS，不请求频道页）。
    首次解析：YouTube 频道页 HTML 里官方自带
      <link rel="alternate" type="application/rss+xml" href="...channel_id=UCxxx">
    直接提取这个链接最稳；另备几个正则兜底。
    """
    if handle in FEED_URL_OVERRIDES:
        return FEED_URL_OVERRIDES[handle]
    if cached:
        return cached

    base = f"{YOUTUBE_BASE}/{quote(handle, safe='@')}"
    patterns = [
        r"https://www\.youtube\.com/feeds/videos\.xml\?channel_id=(UC[\w-]+)",
        r'"channelId"\s*:\s*"(UC[\w-]+)"',
        r'"externalId"\s*:\s*"(UC[\w-]+)"',
        r'"browseId"\s*:\s*"(UC[\w-]+)"',
        r"/channel/(UC[\w-]+)",
    ]
    last_err = None
    # 频道页 /videos 与根路径都试一遍，任意一个返回可解析的 HTML 即可
    for page_url in (base + "/videos", base):
        try:
            text = _get(page_url).text
        except Exception as e:                       # noqa: BLE001
            last_err = e
            continue
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return RSS_BASE + m.group(1)

    raise RuntimeError(
        f"无法从频道页解析 channel_id（{handle}，{last_err}）。"
        f"请在 FEED_URL_OVERRIDES 中手动填入该频道的 RSS 地址。"
    )


def parse_feed(xml_text: str) -> tuple:
    """解析 Atom RSS，返回 (频道名, [video_dict, ...])。

    video_dict: {id, title, url, published(datetime|None)}
    """
    root = ET.fromstring(xml_text)
    channel_title = (root.findtext("a:title", default="", namespaces=ATOM_NS) or "").strip()

    videos = []
    for entry in root.findall("a:entry", ATOM_NS):
        vid = (entry.findtext("yt:videoId", default="", namespaces=ATOM_NS) or "").strip()
        if not vid:
            raw_id = entry.findtext("a:id", default="", namespaces=ATOM_NS) or ""
            m = re.search(r"video:([\w-]+)", raw_id)
            vid = m.group(1) if m else ""
        if not vid:
            continue

        title = html.unescape(
            (entry.findtext("a:title", default="", namespaces=ATOM_NS) or "").strip()
        ) or "(无标题)"

        link_el = entry.find("a:link[@rel='alternate']", ATOM_NS)
        url = (link_el.get("href") if link_el is not None else "") or \
              f"https://www.youtube.com/watch?v={vid}"

        raw_pub = (entry.findtext("a:published", default="", namespaces=ATOM_NS)
                   or entry.findtext("a:updated", default="", namespaces=ATOM_NS) or "")
        published = None
        if raw_pub:
            try:
                published = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        videos.append({"id": vid, "title": title, "url": url, "published": published})

    return channel_title, videos


def fetch_channel(handle: str, cached_feed: str = "") -> tuple:
    """返回 (频道名, 视频列表, feed_url)。"""
    feed_url = resolve_feed_url(handle, cached_feed)
    resp = _get(feed_url)
    resp.encoding = "utf-8"
    title, videos = parse_feed(resp.text)
    return (title or handle.lstrip("@")), videos, feed_url


def _fmt_time(dt) -> str:
    if not dt:
        return "时间未知"
    return dt.astimezone(TZ).strftime("%m-%d %H:%M")


# ==================== 状态 ====================


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("channels"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "channels": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ==================== 检查更新 ====================


def check_updates(state: dict, force_init: bool = False) -> dict:
    """逐频道检查，返回 {handle: {"name", "new": [video...], "init", "error"}} 及统计。

    副作用：更新 state 里的 feed_url / seen / last_check（调用方决定是否落盘）。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    results = {}

    for handle in CHANNELS:
        prev = state["channels"].get(handle, {})
        feed_url = prev.get("feed_url", "")
        try:
            name, videos, feed_url = fetch_channel(handle, feed_url)
        except Exception as e:                       # noqa: BLE001
            print(f"[错误] {handle}: {e}", file=sys.stderr)
            results[handle] = {"name": handle.lstrip("@"), "new": [],
                               "init": False, "error": str(e)}
            continue

        seen = list(prev.get("seen", []))
        # 首次见到该频道（或 --init）：只建基线，不推历史视频
        is_init = force_init or not seen

        if is_init:
            new = []
        else:
            new = [
                v for v in videos
                if v["id"] not in seen
                and (v["published"] is None or v["published"] >= cutoff)
            ]
            # 按发布时间倒序，最新的在前
            new.sort(key=lambda v: v["published"] or datetime.min.replace(tzinfo=timezone.utc),
                     reverse=True)

        # 更新 seen：新 id 在前，去重后截断
        merged = [v["id"] for v in new] + [v["id"] for v in videos] + seen
        uniq = list(dict.fromkeys(merged))[:MAX_SEEN]

        state["channels"][handle] = {
            "name": name,
            "feed_url": feed_url,
            "seen": uniq,
            "last_check": datetime.now(TZ).isoformat(timespec="seconds"),
        }
        results[handle] = {"name": name, "new": new, "init": is_init, "error": None}
        flag = "基线" if is_init else f"{len(new)} 条新视频"
        print(f"[检查] {name} ({handle}): {flag} / RSS 共 {len(videos)} 条")

    return results


def make_test_results(state: dict) -> dict:
    """--test-push：每个频道取最新 1 条，强制当作新视频，用于预览真实排版。"""
    results = {}
    for handle in CHANNELS:
        prev = state["channels"].get(handle, {})
        try:
            name, videos, _ = fetch_channel(handle, prev.get("feed_url", ""))
        except Exception as e:                       # noqa: BLE001
            print(f"[错误] {handle}: {e}", file=sys.stderr)
            results[handle] = {"name": handle.lstrip("@"), "new": [],
                               "init": False, "error": str(e)}
            continue
        latest = videos[:1]
        results[handle] = {"name": name, "new": latest, "init": False, "error": None}
        print(f"[测试] {name}: 取最新 1 条")
    return results


def resolve_all(state: dict) -> int:
    """连通性自检：解析每个频道的 RSS 地址并验证可拉取，输出可粘贴的 overrides。

    在 GitHub Actions 里手动跑一次本模式，把打印出来的 FEED_URL_OVERRIDES
    填回脚本，即可让后续运行完全跳过「抓取频道页」这一步（最不容易被反爬）。
    """
    print("=" * 56)
    print("频道连通性自检（不推送、不写状态）")
    print("=" * 56)
    ok = 0
    overrides = {}
    for handle in CHANNELS:
        cached = state["channels"].get(handle, {}).get("feed_url", "")
        try:
            feed_url = resolve_feed_url(handle, cached)
            name, videos, feed_url = fetch_channel(handle, feed_url)
            overrides[handle] = feed_url
            ok += 1
            print(f"[OK]   {handle:<22} {name}  ·  RSS {len(videos)} 条")
        except Exception as e:                       # noqa: BLE001
            print(f"[FAIL] {handle:<22} {e}")

    print("\n" + "=" * 56)
    print(f"成功 {ok}/{len(CHANNELS)} 个频道。可把下面这段贴进脚本的 FEED_URL_OVERRIDES：")
    print("=" * 56)
    print("FEED_URL_OVERRIDES = {")
    for h, u in overrides.items():
        print(f'    "{h}": "{u}",')
    print("}")
    return 0 if ok == len(CHANNELS) else 1


# ==================== 消息模板 ====================


def _collect(results: dict) -> tuple:
    """整理成 (有更新的频道列表, 新视频总数, 失败频道数)。"""
    updated, total = [], 0
    for handle, info in results.items():
        if info["new"]:
            updated.append((handle, info))
            total += len(info["new"])
    return updated, total, sum(1 for i in results.values() if i["error"])


def _stamp() -> str:
    return datetime.now(TZ).strftime("%m-%d %H:%M")


def render_markdown(results: dict, mode: str = "normal") -> str:
    """Server酱：标准 Markdown（支持表格、标题、链接、引用）。"""
    updated, total, failures = _collect(results)
    n_ch = len(results)
    lines = ["## 📺 YouTube 频道更新", ""]

    if mode == "init":
        lines += [
            f"> 监控已启动　·　{n_ch} 个频道　·　{_stamp()}",
            "",
            "---",
            "",
            "已为下列频道建立更新基线，**本次不推送历史视频**。",
            "此后只在这些频道发布新视频时通知你。",
            "",
        ]
        for _, info in list(results.items())[:n_ch]:
            if not info["error"]:
                lines.append(f"- **{info['name']}**")
        lines.append("")
    elif not updated:
        lines += [f"> 本次无新视频　·　{n_ch} 个频道　·　{_stamp()}", ""]
    else:
        ch_names = "、".join(dict.fromkeys(info["name"] for _, info in updated))
        head = ch_names if len(ch_names) <= 24 else f"{len(updated)} 个频道"
        lines += [
            f"> {head} 有更新　·　共 **{total}** 条新视频　·　{_stamp()}",
            "",
            "---",
            "",
        ]
        for handle, info in updated:
            lines += [f"### 🔴 {info['name']}", ""]
            show = info["new"][:MAX_VIDEOS_PER_CHANNEL]
            for i, v in enumerate(show, 1):
                lines += [
                    f"**{i}. [{v['title']}]({v['url']})**",
                    f"> 🕒 {_fmt_time(v['published'])} 发布",
                    "",
                ]
            rest = len(info["new"]) - len(show)
            if rest > 0:
                lines += [f"> …另有 {rest} 条，点频道页查看：`{handle}`", ""]

    if failures:
        lines += ["---", "", f"> ⚠️ 本次有 {failures} 个频道抓取失败，详情见 Actions 日志", ""]
    lines += ["---", "", f"*监控源 · YouTube 官方 RSS　|　更新 {_stamp()}*"]
    return "\n".join(lines)


def render_wecom_markdown(results: dict, mode: str = "normal") -> str:
    """企业微信群机器人：官方 markdown 子集（不支持表格，用 font 上色）。"""
    updated, total, failures = _collect(results)
    n_ch = len(results)
    lines = ["# 📺 YouTube 频道更新", ""]

    if mode == "init":
        lines += [
            f"> 监控已启动 · {n_ch} 个频道 · {_stamp()}",
            "> <font color=\"comment\">已建立基线，本次不推送历史视频</font>",
            "",
        ]
        for _, info in results.items():
            if not info["error"]:
                lines.append(f"> {info['name']}")
        lines.append("")
    elif not updated:
        lines += [f"> 本次无新视频 · {n_ch} 个频道 · {_stamp()}", ""]
    else:
        lines += [
            f"## <font color=\"warning\">共 {total} 条新视频</font>",
            f"> {_stamp()}",
            "",
        ]
        for _, info in updated:
            lines += [f"### {info['name']}", ""]
            for i, v in enumerate(info["new"][:MAX_VIDEOS_PER_CHANNEL], 1):
                lines += [
                    f"{i}. <font color=\"warning\">{v['title']}</font>",
                    f"> 🕒 {_fmt_time(v['published'])}",
                    f"> {v['url']}",
                    "",
                ]
    if failures:
        lines += [f"> <font color=\"comment\">⚠️ {failures} 个频道抓取失败</font>", ""]
    lines.append(f"<font color=\"comment\">监控源 · YouTube 官方 RSS　|　更新 {_stamp()}</font>")
    return "\n".join(lines)


def render_plain(results: dict, mode: str = "normal") -> str:
    """微信插件（微工作台）：纯文本，Markdown 语法无效，给裸链接。"""
    sep = "─" * 24
    updated, total, failures = _collect(results)
    n_ch = len(results)
    lines = ["📺 YouTube 频道更新", ""]

    if mode == "init":
        lines += [f"监控已启动 · {n_ch} 个频道 · {_stamp()}",
                  "已建立基线，本次不推送历史视频", "", sep]
        for _, info in results.items():
            if not info["error"]:
                lines.append(f"　{info['name']}")
        lines.append("")
    elif not updated:
        lines += [f"本次无新视频 · {n_ch} 个频道 · {_stamp()}", ""]
    else:
        lines += [f"共 {total} 条新视频 · {_stamp()}", "", sep]
        for _, info in updated:
            lines += ["", f"【{info['name']}】"]
            for i, v in enumerate(info["new"][:MAX_VIDEOS_PER_CHANNEL], 1):
                lines += [
                    f"　{i}. {v['title']}",
                    f"　　 🕒 {_fmt_time(v['published'])}",
                    f"　　 {v['url']}",
                ]
        lines += ["", sep]
    if failures:
        lines += ["", f"⚠️ {failures} 个频道抓取失败"]
    lines += ["", f"监控源 · YouTube 官方 RSS　|　更新 {_stamp()}"]
    return "\n".join(lines)


def build_message(results: dict, mode: str = "normal") -> dict:
    updated, total, _ = _collect(results)
    if mode == "init":
        title = f"📺 频道监控已启动 · {len(results)} 个频道"
    elif total:
        names = "、".join(dict.fromkeys(i["name"] for _, i in updated))
        title = (f"📺 {names} 更新 · {total} 条" if len(names) <= 20
                 else f"📺 {len(updated)} 个频道更新 · {total} 条")
    else:
        title = "📺 YouTube 频道 · 无新视频"
    return {
        "title": title,
        "markdown": render_markdown(results, mode),
        "wecom_markdown": render_wecom_markdown(results, mode),
        "plain": render_plain(results, mode),
    }


# ==================== 推送（与 sp500_daily_report.py 同逻辑） ====================


def push_serverchan(title: str, md: str) -> None:
    if not SERVERCHAN_SENDKEY:
        raise ValueError("未配置 SERVERCHAN_SENDKEY 环境变量，请到 https://sct.ftqq.com 获取")
    resp = SESSION.post(
        f"{SERVERCHAN_API}/{SERVERCHAN_SENDKEY}.send",
        data={"title": title, "desp": md},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise ValueError(f"Server酱推送失败: {result}")


def get_wecom_token() -> str:
    if not WECOM_CORPID or not WECOM_CORPSECRET:
        raise ValueError("未配置 WECOM_CORPID / WECOM_CORPSECRET")
    resp = _get(WECOM_TOKEN_URL,
                params={"corpid": WECOM_CORPID, "corpsecret": WECOM_CORPSECRET})
    result = resp.json()
    if result.get("errcode") != 0:
        raise ValueError(f"获取企业微信 access_token 失败: {result}")
    return result["access_token"]


def push_wecom_app(title: str, plain: str) -> None:
    """微工作台不支持 markdown，必须用 text 纯文本。"""
    if not WECOM_AGENTID:
        raise ValueError("未配置 WECOM_AGENTID")
    token = get_wecom_token()
    resp = SESSION.post(
        WECOM_SEND_URL,
        params={"access_token": token},
        json={
            "touser": WECOM_TOUSER or "@all",
            "msgtype": "text",
            "agentid": int(WECOM_AGENTID),
            "text": {"content": f"{title}\n\n{plain}"},
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 1800,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("errcode") != 0:
        raise ValueError(f"企业微信应用消息推送失败: {result}")


def push_wecom_webhook(title: str, md: str) -> None:
    if not WECOM_WEBHOOK:
        raise ValueError("未配置 WECOM_WEBHOOK")
    if not WECOM_WEBHOOK.startswith("https://qyapi.weixin.qq.com/"):
        raise ValueError("WECOM_WEBHOOK 不是企业微信官方地址，请检查是否被篡改")
    resp = SESSION.post(
        WECOM_WEBHOOK,
        json={"msgtype": "markdown", "markdown": {"content": md[:4096]}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("errcode") != 0:
        raise ValueError(f"群机器人推送失败: {result}")


def push_message(msg: dict) -> None:
    if PUSH_CHANNEL == "serverchan":
        push_serverchan(msg["title"], msg["markdown"])
    elif PUSH_CHANNEL == "wecom_app":
        push_wecom_app(msg["title"], msg["plain"])
    elif PUSH_CHANNEL == "wecom_webhook":
        push_wecom_webhook(msg["title"], msg["wecom_markdown"])
    else:
        raise ValueError(f"未知推送渠道: {PUSH_CHANNEL}")


# ==================== 主流程 ====================


PREVIEW_KEYS = {
    "serverchan": "markdown",
    "wecom_app": "plain",
    "wecom_webhook": "wecom_markdown",
}


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    preview_all = "--preview-all" in argv
    force_init = "--init" in argv
    test_push = "--test-push" in argv
    resolve_only = "--resolve-only" in argv

    state = load_state()
    is_first_run = not state["channels"]

    if resolve_only:
        return resolve_all(state)

    try:
        if test_push:
            results = make_test_results(state)
            mode = "test"
        else:
            results = check_updates(state, force_init=force_init)
            mode = "init" if (is_first_run or force_init) else "normal"

        updated, total, failures = _collect(results)
        msg = build_message(results, mode)

        print("=" * 56)
        print("标题:", msg["title"])
        if preview_all:
            for label, key in [
                ("Server酱 · 标准 Markdown", "markdown"),
                ("企业微信群机器人 · 官方 Markdown", "wecom_markdown"),
                ("微信插件 · 纯文本", "plain"),
            ]:
                print("\n" + "=" * 56)
                print(f"【{label}】")
                print("=" * 56)
                print(msg[key])
        else:
            print("-" * 56)
            print(msg[PREVIEW_KEYS.get(PUSH_CHANNEL, "markdown")])
        print("=" * 56)

        if dry_run or preview_all:
            print("\n[dry-run] 测试模式：不推送、不写状态文件。")
            return 0

        # 决定是否需要推送
        should_push = False
        if test_push:
            should_push = True
        elif mode == "init":
            should_push = NOTIFY_ON_INIT
        elif total:
            should_push = True
        elif NOTIFY_WHEN_EMPTY:
            should_push = True
        elif failures and failures == len(CHANNELS) and NOTIFY_ON_ERROR:
            # 全部频道都失败 → 推一条告警，避免静默失效
            push_message({"title": "⚠️ 频道监控异常", "plain": "所有频道抓取均失败，请查看 Actions 日志。",
                          "markdown": "## ⚠️ 频道监控异常\n\n所有频道抓取均失败，请查看 Actions 日志。",
                          "wecom_markdown": "# ⚠️ 频道监控异常\n\n所有频道抓取均失败，请查看 Actions 日志。"})
            print("\n[告警] 全部频道抓取失败，已推送异常通知。")
            should_push = False

        if should_push:
            push_message(msg)
            print(f"\n✔ 推送成功！（{total} 条新视频）")
        else:
            print("\n· 无新视频，本次不推送。")

        # --test-push 不落状态，避免污染基线
        if not test_push:
            save_state(state)
            print(f"· 状态已写入 {STATE_FILE}")
        return 0

    except Exception as e:                           # noqa: BLE001
        err_msg = f"YouTube 频道监控失败: {e}"
        print(f"✘ {err_msg}", file=sys.stderr)
        try:
            if not dry_run and not preview_all and (
                SERVERCHAN_SENDKEY or WECOM_CORPID or WECOM_WEBHOOK
            ):
                push_message({"title": "频道监控任务异常", "plain": err_msg,
                              "markdown": err_msg, "wecom_markdown": err_msg})
        except Exception:                            # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
