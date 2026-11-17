#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
标普500每日行情 · 微信官方渠道推送
================================================
功能：获取标普500指数（^SPX）最近一个交易日的行情（涨跌幅等），
      并推送一条消息到微信；同时按「腾讯理财通每日 10 元定投」粗估持仓当日盈亏。

【推送渠道（三选一，通过 PUSH_CHANNEL 配置）】
  1. serverchan     - Server酱 (sct.ftqq.com)  ← 当前启用
                      配置最省事，消息直接进微信，但内容会经过第三方服务器
  2. wecom_app      - 企业微信「自建应用」消息（纯官方，无第三方中转）
                      消息经「微信插件」显示在【微信 App → 通讯录 → 我的企业】
  3. wecom_webhook  - 企业微信「群机器人」Webhook（纯官方）
                      消息显示在【企业微信 App 的群聊】里

【关于安全性】
  微信官方从未开放「给个人微信号主动发消息」的接口。Server酱这类服务的
  本质是替你托管了一个微信服务号，再用官方模板消息通道把消息转发给你。
  因此使用 serverchan 渠道时，消息内容与 SendKey 都会经过 sct.ftqq.com。
  想彻底不经第三方，把 PUSH_CHANNEL 改成 "wecom_app" 或 "wecom_webhook"，
  脚本便只会请求 qyapi.weixin.qq.com（腾讯官方域名）。
  折线图由 quickchart.io 按 URL 生成（只含公开行情），微信客户端会向该域名取图；
  脚本自身不请求它，不用图床、也不需要额外 Python 依赖。
  可用 grep 核对脚本里出现的所有域名：
    grep -oE 'https://[a-z0-9.]+' sp500_daily_report.py | sort -u

  注意：本脚本含有明文密钥，请勿提交到公开仓库或分享给他人。

用法：
  python3 sp500_daily_report.py                  # 正式运行（会实际推送）
  python3 sp500_daily_report.py --dry-run       # 只打印当前渠道的消息，不推送
  python3 sp500_daily_report.py --preview-all   # 预览全部三种渠道的模板效果
"""

import sys
import os
import json
import math
from datetime import date, datetime, timedelta
from urllib.parse import quote

import requests

# ==================== 配置区（请修改这里） ====================

# 推送渠道: "serverchan" / "wecom_app" / "wecom_webhook"
PUSH_CHANNEL = "serverchan"

# --- 方式一：Server酱（当前启用）---
# 从 https://sct.ftqq.com 微信扫码获取；此 Key 等同密码，勿外泄
SERVERCHAN_SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "")
SERVERCHAN_API = "https://sctapi.ftqq.com"

# --- 方式二：企业微信自建应用（纯官方，消息能到微信 App）---
# 注册：https://work.weixin.qq.com → 立即注册 → 选「个人组建团队」即可，免费
# 管理后台：https://work.weixin.qq.com/wework_admin/frame
WECOM_CORPID = ""       # 我的企业 → 企业信息 → 最底部「企业ID」
WECOM_CORPSECRET = ""   # 应用管理 → 自建 → 创建应用 → 查看 Secret
WECOM_AGENTID = ""      # 应用管理 → 点开该应用 → AgentId（纯数字）
WECOM_TOUSER = "@all"   # 接收人成员账号，自己用填 "@all" 即可

# --- 方式三：企业微信群机器人 Webhook ---
# 企业微信 App → 进入一个群 → 群设置 → 群机器人 → 添加 → 复制 Webhook 地址
# 注意：该地址等同密码，不要提交到公开仓库
WECOM_WEBHOOK = ""

# ===== 数据源（免费公开行情，无需配置）=====
TENCENT_URL = "https://qt.gtimg.cn/q=usINX"
# 日 K 线：返回近 10 个交易日，字段为 [日期, 开, 收, 高, 低, 量, ...]
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
TENCENT_KLINE_PARAMS = {"param": "usINX,day,,,10,qfq"}
HIST_DAYS = 10

# 折线图：QuickChart 把 Chart.js 配置编码进 URL 直接出图，无需本机装绘图库或图床
QUICKCHART_API = "https://quickchart.io/chart"
CHART_IMG_W = 640
CHART_IMG_H = 320
CHART_HEIGHT = 8     # 纯文本渠道兜底用的字符图行数
CHART_STEP = 4       # 字符图每个交易日占用的横向字符数
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
YAHOO_PARAMS = {"range": "5d", "interval": "1d"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# ===== 我的定投：腾讯理财通 · 标普500 =====
# 每个工作日早上自动买入 10 元。INVEST_BASE 是 INVEST_BASE_DATE 当天的累计投入。
# 脚本按「基准日 + 每工作日 10 元」自动推算之后的累计投入。
# 注意：仅排除周末，未排除法定节假日（长假会顺延扣款），建议每月对照
#       理财通账户核对一次，并更新下面两个基准值即可校准。
INVEST_ENABLED = True
INVEST_NAME = "腾讯理财通 · 标普500"
INVEST_PER_WORKDAY = 10.0
INVEST_BASE = 425.33
INVEST_BASE_DATE = "2026-11-16"

# 企业微信官方 API 固定地址
WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
TIMEOUT = 30

# ==================== 以下为逻辑代码，无需修改 ====================


# ---------------------------------------------------------------- 行情获取


def fetch_quote_tencent() -> dict:
    """从腾讯财经获取标普500最新行情。"""
    resp = requests.get(TENCENT_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "gbk"
    text = resp.text.strip()
    if '="' not in text:
        raise ValueError(f"腾讯财经返回异常: {text[:100]}")

    payload = text.split('="', 1)[1].rstrip('";\n ')
    fields = payload.split("~")
    if len(fields) < 35 or not fields[3]:
        raise ValueError(f"腾讯财经数据字段异常: {payload[:100]}")

    quote = {
        "name": fields[1],
        "close": float(fields[3]),       # 最新/收盘价
        "prev_close": float(fields[4]),  # 昨收
        "open": float(fields[5]) if fields[5] else None,
        "high": float(fields[33]) if fields[33] else None,
        "low": float(fields[34]) if fields[34] else None,
        "time_str": fields[30],          # 行情时间（美东时间）
    }
    quote["change"] = quote["close"] - quote["prev_close"]
    quote["pct"] = quote["change"] / quote["prev_close"] * 100
    return quote


def fetch_quote_yahoo() -> dict:
    """备用：从雅虎财经获取标普500行情。"""
    resp = requests.get(YAHOO_URL, params=YAHOO_PARAMS, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    if len(closes) < 2:
        raise ValueError("雅虎财经数据不足")
    quote = {
        "name": "标普500",
        "close": closes[-1],
        "prev_close": meta.get("previousClose") or closes[-2],
        "open": result["indicators"]["quote"][0]["open"][-1],
        "high": result["indicators"]["quote"][0]["high"][-1],
        "low": result["indicators"]["quote"][0]["low"][-1],
        "time_str": datetime.fromtimestamp(meta["regularMarketTime"]).strftime("%Y-%m-%d"),
    }
    quote["change"] = quote["close"] - quote["prev_close"]
    quote["pct"] = quote["change"] / quote["prev_close"] * 100
    return quote


def fetch_quote() -> dict:
    """获取行情：腾讯财经优先，失败时切换雅虎财经。"""
    try:
        return fetch_quote_tencent()
    except Exception as e:
        print(f"[提示] 腾讯财经获取失败({e})，尝试备用源雅虎财经...", file=sys.stderr)
        return fetch_quote_yahoo()


def fetch_history(q: dict, days: int = HIST_DAYS):
    """获取近 N 个交易日收盘价，用于价格折线图。

    失败时返回 None，只省略折线图，不影响当日行情的正常推送。
    """
    try:
        resp = requests.get(TENCENT_KLINE_URL, params=TENCENT_KLINE_PARAMS,
                            headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()["data"]["usINX"]["day"]
        hist = [(r[0], float(r[2])) for r in rows if len(r) >= 3]
        if len(hist) < 2:
            return None

        # 与实时行情对齐：K 线最后一天同日则覆盖收盘价，否则追加，
        # 避免早间 K 线尚未更新导致折线图与主数据不一致。
        date = (q.get("time_str") or "").split(" ")[0]
        if date:
            if hist[-1][0] == date:
                hist[-1] = (date, q["close"])
            else:
                hist.append((date, q["close"]))
        return hist[-days:]
    except Exception as e:
        print(f"[提示] 近{days}日价格折线图获取失败({e})，本次消息省略折线图", file=sys.stderr)
        return None


def _chart_image_url(hist) -> str:
    """把近 N 日收盘价编码成 QuickChart 图片地址（Chart.js 折线图）。"""
    values = [c for _, c in hist]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.18, hi * 0.002)
    rgb = "214,69,69" if values[-1] >= values[0] else "47,158,99"
    config = {
        "type": "line",
        "data": {
            "labels": [d[5:].replace("-", "/") for d, _ in hist],
            "datasets": [{
                "label": "Close",
                "data": values,
                "borderColor": f"rgb({rgb})",
                "backgroundColor": f"rgba({rgb},0.10)",
                "borderWidth": 2.5,
                "fill": True,
                "pointRadius": 3,
                "pointHoverRadius": 5,
                "pointBackgroundColor": "#fff",
                "pointBorderColor": f"rgb({rgb})",
                "pointBorderWidth": 2,
                "tension": 0.3,
            }],
        },
        "options": {
            "responsive": False,
            "plugins": {
                "title": {
                    "display": True,
                    "text": f"S&P 500 · Last {len(values)} Sessions",
                    "font": {"size": 18},
                    "padding": {"top": 8, "bottom": 12},
                },
                "legend": {"display": False},
            },
            "scales": {
                "x": {
                    "grid": {"display": False},
                    "ticks": {"maxRotation": 0, "autoSkip": True,
                              "maxTicksLimit": 5, "color": "#666666"},
                },
                "y": {
                    "min": math.floor(lo - pad),
                    "max": math.ceil(hi + pad),
                    "grid": {"color": "rgba(0,0,0,0.06)"},
                    "ticks": {"maxTicksLimit": 6, "color": "#666666"},
                },
            },
        },
    }
    encoded = quote(json.dumps(config, separators=(",", ":")), safe="")
    return (f"{QUICKCHART_API}?c={encoded}&w={CHART_IMG_W}"
            f"&h={CHART_IMG_H}&version=4&devicePixelRatio=2")


def _draw_line_chart(hist) -> str:
    """字符画折线图：仅作纯文本渠道（无法显示图片）的兜底。"""
    values = [c for _, c in hist]
    lo, hi = min(values), max(values)
    if hi <= lo:
        pad = abs(hi) * 0.005 or 1.0
        lo, hi = lo - pad, hi + pad
    else:
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad

    n = len(values)
    width = (n - 1) * CHART_STEP + 1
    grid = [[" "] * width for _ in range(CHART_HEIGHT)]

    def row_of(v: float) -> int:
        return round((hi - v) / (hi - lo) * (CHART_HEIGHT - 1))

    points = [(row_of(v), i * CHART_STEP) for i, v in enumerate(values)]

    # 先连线再标点，避免线段盖住数据点
    for (r1, c1), (r2, c2) in zip(points, points[1:]):
        for c in range(c1 + 1, c2 + 1):
            r = round(r1 + (r2 - r1) * (c - c1) / (c2 - c1))
            prev = round(r1 + (r2 - r1) * (c - 1 - c1) / (c2 - c1))
            grid[r][c] = "─" if r == prev else ("╱" if r < prev else "╲")
    for r, c in points:
        grid[r][c] = "●"

    label_w = len(f"{hi:,.0f}")
    lines = []
    for i, row in enumerate(grid):
        v = lo + (hi - lo) * (CHART_HEIGHT - 1 - i) / (CHART_HEIGHT - 1)
        lines.append(f"{v:>{label_w},.0f} ┤" + "".join(row).rstrip())

    x_labels = [" "] * width
    for i in (0, n // 2, n - 1):
        text = hist[i][0][5:].replace("-", "/")
        start = min(max(0, i * CHART_STEP - len(text) // 2), width - len(text))
        x_labels[start:start + len(text)] = list(text)
    lines.append(" " * (label_w + 1) + "└" + "─" * width)
    lines.append(" " * (label_w + 1) + "".join(x_labels).rstrip())
    return "\n".join(lines)


def chart_block(hist) -> dict | None:
    """把历史收盘价整理成模板可用的折线图信息。"""
    if not hist or len(hist) < 2:
        return None
    closes = [c for _, c in hist]
    pct = (closes[-1] / closes[0] - 1) * 100
    url = _chart_image_url(hist)
    return {
        "url": url,
        "image": f"![近 {len(hist)} 日收盘价折线图]({url})",
        "chart": _draw_line_chart(hist),
        "pct": pct,
        "start": hist[0][0][5:].replace("-", "/"),
        "end": hist[-1][0][5:].replace("-", "/"),
        "first": closes[0],
        "last": closes[-1],
        "n": len(hist),
        "color": "🔴" if pct > 0 else ("🟢" if pct < 0 else "⚪"),
        "wx_color": "warning" if pct > 0 else ("info" if pct < 0 else "comment"),
        "arrow": "▲" if pct > 0 else ("▼" if pct < 0 else "—"),
    }



def _workdays_after(start: date, end: date) -> int:
    """统计 (start, end] 内的工作日数（仅排除周末，不含法定节假日）。"""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def invest_block(q: dict) -> dict | None:
    """按定投计划与当日指数涨跌，粗估持仓当日盈亏。

    口径：QDII 基金净值确认滞后 1~2 个交易日，且人民币份额还受汇率影响；
    这里只用「累计投入 × 指数涨跌幅」给出量级参考，不等于理财通实际盈亏。
    410 元按当前持仓/投入的近似基数处理，若账户显示市值不同，改 INVEST_BASE 即可。
    """
    if not INVEST_ENABLED:
        return None
    try:
        base_date = datetime.strptime(INVEST_BASE_DATE, "%Y-%m-%d").date()
    except ValueError:
        return None

    invested = INVEST_BASE + INVEST_PER_WORKDAY * max(0, _workdays_after(base_date, date.today()))
    pnl = invested * q["pct"] / 100
    return {
        "name": INVEST_NAME,
        "invested": invested,
        "per": INVEST_PER_WORKDAY,
        "pnl": pnl,
        "color": "🔴" if pnl > 0 else ("🟢" if pnl < 0 else "⚪"),
        "wx_color": "warning" if pnl > 0 else ("info" if pnl < 0 else "comment"),
    }


# ---------------------------------------------------------------- 消息生成


def _f(v) -> str:
    """价格格式化，缺数据时用破折号占位。"""
    return f"{v:,.2f}" if v else "—"


def _style(q: dict) -> dict:
    """模板共用的视觉元素：涨跌方向、颜色、幅度描述、日期。"""
    change, pct = q["change"], q["pct"]

    if change > 0:
        s = dict(emoji="📈", trend="上涨", arrow="▲", color="🔴", wx_color="warning")
    elif change < 0:
        s = dict(emoji="📉", trend="下跌", arrow="▼", color="🟢", wx_color="info")
    else:
        s = dict(emoji="➖", trend="持平", arrow="—", color="⚪", wx_color="comment")

    ap = abs(pct)
    degree = "微" if ap < 0.3 else ("小幅" if ap < 1 else ("明显" if ap < 2 else "大幅"))
    s["degree"] = f"{degree}{s['trend']}" if change else "收平"

    date_str = (q["time_str"] or "").split(" ")[0]
    weekday = ""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = "（" + ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()] + "）"
        date_str = d.strftime("%Y年%m月%d日")
    except ValueError:
        date_str = q["time_str"] or datetime.now().strftime("%Y年%m月%d日")
    s["date"] = date_str
    s["weekday"] = weekday
    s["stamp"] = datetime.now().strftime("%m-%d %H:%M")
    return s


def _range_bar(q: dict, width: int = 12) -> tuple:
    """把收盘价在当日高低区间的位置画成进度条，返回 (条, 百分比)。"""
    high, low, close = q.get("high"), q.get("low"), q["close"]
    if not high or not low or high <= low:
        return "", None
    ratio = max(0.0, min(1.0, (close - low) / (high - low)))
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled), ratio * 100


def _footer(s: dict) -> str:
    return f"数据源 · 腾讯财经　|　更新 {s['stamp']}"


def render_markdown(q: dict, s: dict, bar: str, pos, t, inv) -> str:
    """Server酱：标准 Markdown，可用表格、标题、引用、行内代码。"""
    lines = [
        "## 📊 标普500 · 每日行情",
        f"> {s['date']}{s['weekday']}　·　美股收盘",
        "",
        "---",
        "",
        f"### {s['color']} {s['trend']} {q['pct']:+.2f}%",
        "",
        f"# {_f(q['close'])}",
        "",
        f"`收盘价`　·　{s['degree']}　·　相对前收 {_f(q['prev_close'])}",
        "",
        f"> **{q['change']:+,.2f} 点** {s['arrow']}",
        "",
        "---",
        "",
        "| 指标 | 价格 |",
        "| :--- | ---: |",
        f"| 开盘价 | {_f(q.get('open'))} |",
        f"| 最高价 | {_f(q.get('high'))} |",
        f"| 最低价 | {_f(q.get('low'))} |",
        f"| 前收盘 | {_f(q['prev_close'])} |",
        "",
    ]
    if bar:
        lines += [
            f"**日内区间位置**　`{bar}` {pos:.0f}%",
            "",
            f"> 收盘价位于 {_f(q.get('low'))} ~ {_f(q.get('high'))} 区间的 {pos:.0f}% 处",
            "",
        ]
    if t:
        lines += [
            "---",
            "",
            f"### 📈 近 {t['n']} 日价格折线图　{t['color']} {t['pct']:+.2f}%",
            "",
            t["image"],
            "",
            f"> {t['start']} {t['first']:,.2f}　→　{t['end']} {t['last']:,.2f}",
            "",
        ]
    if inv:
        lines += [
            "---",
            "",
            f"### 💰 我的定投（{inv['name']}）",
            "",
            "| 项目 | 金额 |",
            "| :--- | ---: |",
            f"| 累计投入 | {inv['invested']:,.2f} 元 |",
            f"| 定投计划 | 每工作日 {inv['per']:,.2f} 元 |",
            f"| 今日估算盈亏 | {inv['color']} **{inv['pnl']:+,.2f} 元** |",
            "",
            f"> 按当日 {q['pct']:+.2f}% × 累计投入粗估；QDII 净值确认滞后 1~2 个交易日、含汇率影响，仅作参考",
            "",
        ]
    lines += ["---", "", f"*{_footer(s)}*"]
    return "\n".join(lines)


def render_wecom_markdown(q: dict, s: dict, bar: str, pos, t, inv) -> str:
    """企业微信群机器人：官方 markdown 子集（不支持表格，支持 font 上色）。"""
    c = s["wx_color"]
    lines = [
        "# 📊 标普500 · 每日行情",
        f"> {s['date']}{s['weekday']} · 美股收盘",
        "",
        f"## 收盘价 <font color=\"{c}\">{_f(q['close'])}</font>",
        f"> <font color=\"{c}\">{s['arrow']} {q['change']:+,.2f} 点（{q['pct']:+.2f}%）</font>　{s['degree']}",
        "",
        f"> 开盘　{_f(q.get('open'))}",
        f"> 最高　{_f(q.get('high'))}",
        f"> 最低　{_f(q.get('low'))}",
        f"> 前收　{_f(q['prev_close'])}",
        "",
    ]
    if bar:
        lines += [f"日内位置 `{bar}` {pos:.0f}%", ""]
    if t:
        tc = t["wx_color"]
        # 企业微信群机器人 markdown 不支持图片语法，用字符图 + 图片链接代替
        lines += [
            f"## 📈 近 {t['n']} 日价格折线图",
            *[f"`{line}`" for line in t["chart"].splitlines()],
            f"> [查看清晰折线图]({t['url']})",
            f"> <font color=\"{tc}\">{t['arrow']} {t['pct']:+.2f}%</font>　{t['start']} {t['first']:,.2f} → {t['end']} {t['last']:,.2f}",
            "",
        ]
    if inv:
        ic = inv["wx_color"]
        lines += [
            f"## 💰 我的定投（{inv['name']}）",
            f"> 累计投入　{inv['invested']:,.2f} 元",
            f"> 定投计划　每工作日 {inv['per']:,.2f} 元",
            f"> 今日估算盈亏　<font color=\"{ic}\">{inv['pnl']:+,.2f} 元</font>",
            f"> 按当日 {q['pct']:+.2f}% 粗估 · 净值确认滞后 1~2 日，仅供参考",
            "",
        ]
    lines.append(f"<font color=\"comment\">{_footer(s)}</font>")
    return "\n".join(lines)


def render_plain(q: dict, s: dict, bar: str, pos, t, inv) -> str:
    """微信插件（微工作台）：纯文本，用分隔线与对齐排版保证可读性。"""
    sep = "─" * 24
    lines = [
        "📊 标普500 · 每日行情",
        f"{s['date']}{s['weekday']} · 美股收盘",
        "",
        sep,
        f"　收盘价　 {_f(q['close'])}",
        f"　{s['color']} {s['arrow']} {q['change']:+,.2f} 点（{q['pct']:+.2f}%）",
        f"　{s['degree']}　·　前收 {_f(q['prev_close'])}",
        sep,
        f"　开盘价　　　{_f(q.get('open'))}",
        f"　最高价　　　{_f(q.get('high'))}",
        f"　最低价　　　{_f(q.get('low'))}",
    ]
    if bar:
        lines += ["", f"　日内位置　{bar} {pos:.0f}%"]
    if t:
        lines += [
            "",
            sep,
            f"　近 {t['n']} 日价格折线图　{t['arrow']} {t['pct']:+.2f}%",
            t["chart"],
            f"　{t['start']} {t['first']:,.2f} → {t['end']} {t['last']:,.2f}",
        ]
    if inv:
        lines += [
            "",
            sep,
            f"💰 我的定投（{inv['name']}）",
            f"　累计投入　　　{inv['invested']:,.2f} 元",
            f"　定投计划　　　每工作日 {inv['per']:,.2f} 元",
            f"　今日估算盈亏　{inv['pnl']:+,.2f} 元",
            f"　按当日 {q['pct']:+.2f}% 粗估，净值确认滞后 1~2 日",
        ]
    lines += ["", _footer(s)]
    return "\n".join(lines)


def build_message(q: dict, hist=None) -> dict:
    """生成三种渠道各自适配的消息模板。"""
    s = _style(q)
    bar, pos = _range_bar(q)
    t = chart_block(hist)
    inv = invest_block(q)
    return {
        "title": f"{s['emoji']} 标普500 {q['pct']:+.2f}% · {_f(q['close'])}",
        "markdown": render_markdown(q, s, bar, pos, t, inv),
        "wecom_markdown": render_wecom_markdown(q, s, bar, pos, t, inv),
        "plain": render_plain(q, s, bar, pos, t, inv),
    }


# ------------------------------------------------------------- Server酱


def push_serverchan(title: str, md: str) -> None:
    """通过 Server酱推送到微信（第三方中转渠道）。"""
    key = SERVERCHAN_SENDKEY
    if not key:
        raise ValueError("未配置 SERVERCHAN_SENDKEY，请到 https://sct.ftqq.com 获取")
    resp = requests.post(
        f"{SERVERCHAN_API}/{key}.send",
        data={"title": title, "desp": md},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise ValueError(f"Server酱推送失败: {result}")


# ------------------------------------------------------- 企业微信官方 API


def get_wecom_token() -> str:
    """获取企业微信 access_token（官方接口）。"""
    if not WECOM_CORPID or not WECOM_CORPSECRET:
        raise ValueError("未配置 WECOM_CORPID / WECOM_CORPSECRET，请在企业微信管理后台获取")
    resp = requests.get(
        WECOM_TOKEN_URL,
        params={"corpid": WECOM_CORPID, "corpsecret": WECOM_CORPSECRET},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("errcode") != 0:
        raise ValueError(f"获取企业微信 access_token 失败: {result}")
    return result["access_token"]


def push_wecom_app(title: str, plain: str) -> None:
    """企业微信自建应用消息（官方 message/send 接口）。

    用 text 类型而非 markdown：官方说明「微工作台（原企业号）不支持展示
    markdown 消息」，而微信插件正是基于微工作台，所以用纯文本才能正常显示。
    """
    if not WECOM_AGENTID:
        raise ValueError("未配置 WECOM_AGENTID，请在应用详情页查看")
    token = get_wecom_token()
    resp = requests.post(
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
    """企业微信群机器人 Webhook（官方接口）。"""
    if not WECOM_WEBHOOK:
        raise ValueError("未配置 WECOM_WEBHOOK，请在企业微信群添加机器人后复制 Webhook 地址")
    if not WECOM_WEBHOOK.startswith("https://qyapi.weixin.qq.com/"):
        raise ValueError("WECOM_WEBHOOK 不是企业微信官方地址，请检查是否被篡改")
    resp = requests.post(
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


# ---------------------------------------------------------------- 主流程


PREVIEW_KEYS = {
    "serverchan": "markdown",
    "wecom_app": "plain",
    "wecom_webhook": "wecom_markdown",
}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    preview_all = "--preview-all" in sys.argv

    try:
        q = fetch_quote()
        hist = fetch_history(q)
        msg = build_message(q, hist)

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
            print("\n[dry-run] 测试模式，不实际推送。")
            print("提示：加 --preview-all 可一次查看全部三种渠道的模板。")
            return 0

        push_message(msg)
        print("\n✔ 推送成功！")
        return 0

    except Exception as e:
        err_msg = f"标普500行情推送失败: {e}"
        print(f"✘ {err_msg}", file=sys.stderr)
        # 推送失败也尝试用同一渠道通知一次，避免静默失败
        try:
            if not dry_run and not preview_all and (
                SERVERCHAN_SENDKEY or WECOM_CORPID or WECOM_WEBHOOK
            ):
                push_message({"title": "标普500行情任务异常", "plain": err_msg,
                              "markdown": err_msg, "wecom_markdown": err_msg})
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
