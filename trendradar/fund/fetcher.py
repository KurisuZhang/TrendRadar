# coding=utf-8
"""
基金/股票估值监控模块

从东方财富 API 获取基金今日估算涨跌幅和股票实时行情，
格式化为 Markdown 字符串块，附加在推送消息末尾。
"""

import uuid
from pathlib import Path
from typing import Optional

import requests
import yaml


_UUID_FILE = Path("output/.fund_uuid")
_FUND_API_URL = (
    "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo"
    "?pageIndex=1&pageSize=200"
    "&plat=Android&appType=ttjj&product=EFund&Version=1"
    "&deviceid={deviceid}&Fcodes={fcodes}"
)
_STOCK_API_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?fltt=2&invt=2&fields=f2,f3,f12,f14&secids={secids}"
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
        parts = change_str.split(' ', 1)
        emoji = parts[0]
        pct_text = parts[1] if len(parts) > 1 else '--'
        return f"{emoji} {name} ({code})　{pct_text}（非交易日）"

    change_str = _fmt_change(gszzl)
    # 提取时间部分（GZTIME 格式如 "2026-04-25 14:30:00"，只取 HH:MM）
    time_part = ""
    if gztime and len(gztime) >= 16:
        time_part = gztime[11:16]

    line = f"{change_str} {name} ({code})"
    if time_part:
        line += f"　{time_part}"
    return line


def _get_secid(code: str) -> str:
    """根据股票代码推断东方财富 secid 前缀（沪=1, 深/北=0）"""
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def _build_stock_line(stock_cfg: dict, api_item: Optional[dict]) -> str:
    """为单只股票构造一行展示文本。"""
    code = stock_cfg.get("code", "")
    name = stock_cfg.get("name", code)

    if api_item is None:
        return f"⚠️ {name} ({code})　数据暂不可用"

    price = api_item.get("f2")
    chg = api_item.get("f3")

    change_str = _fmt_change(str(chg) if chg is not None else "")
    price_str = f"{price:.2f}" if isinstance(price, (int, float)) else "--"
    return f"{change_str} {name} ({code})　{price_str}"


def fetch_fund_block(
    config_path: str = "config/fund_watch.yaml",
    proxy_url: Optional[str] = None,
) -> Optional[str]:
    """
    获取基金/股票行情 Markdown 块，用于追加到推送消息末尾。

    返回值语义：
    - None            → enabled=false 或配置文件不存在，调用方不追加任何内容
    - 非空字符串      → 格式化好的 Markdown 块，直接追加
    """
    cfg = _load_config(config_path)
    if cfg is None:
        return None

    if not cfg.get("enabled", True):
        return None

    funds = cfg.get("funds") or []
    stocks = cfg.get("stocks") or []

    if not funds and not stocks:
        return "📊 基金/股票监控：未配置任何代码"

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

    lines = []

    # ── 基金部分 ──────────────────────────────────────────
    if funds:
        fcodes = ",".join(f["code"] for f in funds if f.get("code"))
        if fcodes:
            deviceid = _get_or_create_uuid()
            url = _FUND_API_URL.format(deviceid=deviceid, fcodes=fcodes)
            try:
                resp = requests.get(url, headers=headers, proxies=proxies, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                items_list = data.get("Datas") or []
                code_map = {item["FCODE"]: item for item in items_list if item.get("FCODE")}

                sample_time = ""
                for item in items_list:
                    gztime = item.get("GZTIME", "")
                    if gztime and len(gztime) >= 16:
                        sample_time = gztime[11:16]
                        break

                fund_header = "📊 基金估值"
                if sample_time:
                    fund_header += f" · {sample_time}"
                lines.append(fund_header)
                for fund_cfg in funds:
                    code = fund_cfg.get("code", "")
                    lines.append(_build_fund_line(fund_cfg, code_map.get(code)))
            except Exception as e:
                print(f"[基金] API 请求失败: {e}")
                lines.append("📊 基金估值：获取失败")

    # ── 股票部分 ──────────────────────────────────────────
    if stocks:
        secids = ",".join(_get_secid(s["code"]) for s in stocks if s.get("code"))
        if secids:
            url = _STOCK_API_URL.format(secids=secids)
            try:
                resp = requests.get(url, headers=headers, proxies=proxies, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                diff = (data.get("data") or {}).get("diff") or []
                code_map = {item["f12"]: item for item in diff if item.get("f12")}

                if lines:
                    lines.append("")  # 基金和股票之间空一行
                lines.append("📈 股票行情")
                for stock_cfg in stocks:
                    code = stock_cfg.get("code", "")
                    lines.append(_build_stock_line(stock_cfg, code_map.get(code)))
            except Exception as e:
                print(f"[股票] API 请求失败: {e}")
                lines.append("📈 股票行情：获取失败")

    return "\n".join(lines) if lines else None
