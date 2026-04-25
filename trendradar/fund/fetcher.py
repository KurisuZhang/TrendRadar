# coding=utf-8
"""
基金估值监控模块

从东方财富（天天基金）API 获取基金今日估算涨跌幅，
格式化为 Markdown 字符串块，附加在推送消息末尾。
"""

import random
import string
import uuid
from pathlib import Path
from typing import Optional

import requests
import yaml


_UUID_FILE = Path("output/.fund_uuid")
_API_URL = (
    "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo"
    "?pageIndex=1&pageSize=200"
    "&plat=Android&appType=ttjj&product=EFund&Version=1"
    "&deviceid={deviceid}&Fcodes={fcodes}"
)


def _get_or_create_uuid() -> str:
    """读取或生成持久化 UUID（模拟固定 Android 设备）"""
    if _UUID_FILE.exists():
        uid = _UUID_FILE.read_text(encoding="utf-8").strip()
        if uid:
            return uid

    # 生成格式：xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
    uid = str(uuid.uuid4())
    _UUID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _UUID_FILE.write_text(uid, encoding="utf-8")
    return uid


def _load_config(config_path: str) -> Optional[dict]:
    """读取 fund_watch.yaml，文件不存在返回 None"""
    p = Path(config_path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _fmt_change(gszzl_str: str) -> str:
    """
    格式化涨跌幅字符串，返回带 emoji 的文本。

    Args:
        gszzl_str: API 返回的 GSZZL 字段字符串，如 "1.23" / "-0.45" / "0.00" / ""

    Returns:
        如 "🔴 +1.23%" / "🟢 -0.45%" / "⚪ 0.00%"
    """
    try:
        val = float(gszzl_str)
    except (ValueError, TypeError):
        return "⚪ --%"

    if val > 0:
        return f"🔴 +{val:.2f}%"
    elif val < 0:
        return f"🟢 {val:.2f}%"
    else:
        return "⚪ 0.00%"


def _build_fund_line(fund_cfg: dict, api_item: Optional[dict]) -> str:
    """
    为单只基金构造一行展示文本。

    Args:
        fund_cfg: fund_watch.yaml 中的单条配置 {"code": ..., "name": ...}
        api_item: API 返回的对应基金数据，None 表示接口未返回该基金

    Returns:
        如 "🔴 易方达消费行业 (110022)　+1.23%"
    """
    code = fund_cfg.get("code", "")
    name = fund_cfg.get("name", code)

    if api_item is None:
        return f"⚠️ {name} ({code})　数据暂不可用"

    gsz = api_item.get("GSZ", "")
    gszzl = api_item.get("GSZZL", "")
    gztime = api_item.get("GZTIME", "")

    # 非交易日：GSZ 为空或 "0.0000"
    is_non_trading = not gsz or gsz.strip() in ("", "0.0000", "0")
    if is_non_trading:
        # 回退显示昨日净值
        nav = api_item.get("NAV", "")
        navchgrt = api_item.get("NAVCHGRT", "")
        change_str = _fmt_change(navchgrt)
        return f"{change_str.split(' ')[0]} {name} ({code})　{change_str.split(' ', 1)[1] if ' ' in change_str else '--'}（非交易日）"

    change_str = _fmt_change(gszzl)
    # 提取时间部分（GZTIME 格式如 "2026-04-25 14:30:00"，只取 HH:MM）
    time_part = ""
    if gztime and len(gztime) >= 16:
        time_part = gztime[11:16]

    line = f"{change_str} {name} ({code})"
    if time_part:
        line += f"　{time_part}"
    return line


def fetch_fund_block(
    config_path: str = "config/fund_watch.yaml",
    proxy_url: Optional[str] = None,
) -> Optional[str]:
    """
    获取基金估值 Markdown 块，用于追加到推送消息末尾。

    返回值语义：
    - None            → enabled=false 或配置文件不存在，调用方不追加任何内容
    - 非空字符串      → 格式化好的 Markdown 块（含标题行 + 各基金行），直接追加
    """
    cfg = _load_config(config_path)
    if cfg is None:
        # 配置文件不存在，静默跳过
        return None

    if not cfg.get("enabled", True):
        # enabled: false，静默跳过
        return None

    funds = cfg.get("funds") or []
    if not funds:
        return "📊 基金监控：未配置任何代码"

    # 构造请求
    fcodes = ",".join(f["code"] for f in funds if f.get("code"))
    if not fcodes:
        return "📊 基金监控：未配置任何代码"

    deviceid = _get_or_create_uuid()
    url = _API_URL.format(deviceid=deviceid, fcodes=fcodes)

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; Pixel 3) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.120 Mobile Safari/537.36"
        ),
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[基金] API 请求失败: {e}")
        return "📊 基金估值：获取失败，请稍后重试"

    # 解析：构建 code → item 映射
    items_list = data.get("Datas") or []
    code_map: dict = {}
    for item in items_list:
        fcode = item.get("FCODE", "")
        if fcode:
            code_map[fcode] = item

    # 格式化标题行（取任意一条的估算时间作为整体时间）
    sample_time = ""
    for item in items_list:
        gztime = item.get("GZTIME", "")
        if gztime and len(gztime) >= 16:
            sample_time = gztime[11:16]
            break

    header = f"📊 基金估值"
    if sample_time:
        header += f" · {sample_time}"

    lines = [header]
    for fund_cfg in funds:
        code = fund_cfg.get("code", "")
        api_item = code_map.get(code)
        lines.append(_build_fund_line(fund_cfg, api_item))

    return "\n".join(lines)
