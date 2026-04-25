# 基金/股票估值监控推送 — 设计文档

Date: 2026-04-25
Status: Approved

---

## 1. 背景与目标

在 TrendRadar 现有热点新闻推送流程的基础上，附加一个基金估值监控区块。
每次热榜消息推送时，自动在消息末尾追加当前关注基金的今日估算涨跌情况。

**目标：**
- 用户通过一个独立配置文件管理关注的基金代码
- 每次热榜推送时顺带看到基金估值，无需额外操作
- 新增代码最小化，不破坏现有推送架构

---

## 2. 数据来源

**API：** 东方财富（天天基金）手机端接口

```
GET https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo
  ?pageIndex=1&pageSize=200
  &plat=Android&appType=ttjj&product=EFund&Version=1
  &deviceid={uuid}
  &Fcodes={基金代码列表,逗号分隔}
```

**使用字段：**

| 字段 | 含义 |
|---|---|
| `FUNDNAME` | 基金名称 |
| `GSZ` | 今日估算净值（盘中实时） |
| `GSZZL` | 今日估算涨跌幅（%） |
| `GZTIME` | 估算时间戳 |
| `NAV` | 上一日单位净值（非交易日回退使用） |

**UUID 策略：**
- 首次运行时随机生成一个 UUID（格式：`xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`）
- 持久化存储到 `output/.fund_uuid`
- 后续复用，模拟固定 Android 设备

**批量策略：**
- 所有基金代码一次性逗号拼接，单次 HTTP 请求取回全部数据

---

## 3. 新增文件

```
config/
  fund_watch.yaml          ← 新建：基金监控配置

trendradar/
  fund/
    __init__.py            ← 新建（空）
    fetcher.py             ← 新建：API请求 + 解析 + 格式化
```

### 3.1 `config/fund_watch.yaml`

```yaml
# 基金/股票估值监控配置
enabled: true

funds:
  - code: "110022"
    name: "易方达消费行业"
  - code: "161725"
    name: "招商中证白酒"

stocks: []   # 预留，本期暂不实现
```

### 3.2 `trendradar/fund/fetcher.py`

**对外接口：**

```python
def fetch_fund_block(
    config_path: str = "config/fund_watch.yaml",
    proxy_url: Optional[str] = None,
) -> Optional[str]:
    """
    返回值：
    - None         → enabled=false 或文件不存在，调用方不追加任何内容
    - 非空字符串   → 格式化好的 Markdown 块，直接追加到消息末尾
    """
```

**内部流程：**
1. 读取并解析 `fund_watch.yaml`
2. `enabled: false` 或文件不存在 → 返回 `None`
3. `funds` 列表为空 → 返回 `"📊 基金监控：未配置任何代码"`
4. 读取/生成 UUID（`output/.fund_uuid`）
5. 调用东方财富 API（超时 10s，失败返回错误提示字符串）
6. 解析响应，格式化输出

---

## 4. 消息格式

**正常交易日：**
```
---
📊 基金估值 · 14:30
🔴 易方达消费行业 (110022)　+1.23%
🟢 招商中证白酒 (161725)　-0.45%
⚪ 某某基金 (000001)　0.00%
```

**涨跌 emoji 规则：**
- 🔴 涨跌幅 > 0
- 🟢 涨跌幅 < 0
- ⚪ 涨跌幅 = 0.00

**非交易日（GSZ 为空）：**
```
🔴 易方达消费行业 (110022)　+1.23%（非交易日）
```
回退显示 `NAV`（上一交易日净值），并标注"（非交易日）"。

**异常情况：**

| 情况 | 显示内容 |
|---|---|
| 某只基金数据为空 | `⚠️ 易方达消费行业 (110022)　数据暂不可用` |
| API 整体失败 | `📊 基金估值：获取失败，请稍后重试` |
| funds 列表为空 | `📊 基金监控：未配置任何代码` |

---

## 5. 接入现有推送流程

### 5.1 数据流

```
__main__.py  (_send_notification_if_needed)
    │
    ├── fetch_fund_block(proxy_url=self.proxy_url)
    │       ↓ 返回 fund_block: Optional[str]
    │
    └── dispatcher.dispatch_all(..., fund_block=fund_block)
            │
            └── renderer.py 渲染时在消息末尾追加 fund_block
```

### 5.2 改动文件清单（最小化）

| 文件 | 改动内容 |
|---|---|
| `trendradar/notification/dispatcher.py` | `dispatch_all()` 新增 `fund_block: Optional[str] = None`，向下透传至各 `_send_*` 方法及 sender |
| `trendradar/notification/renderer.py` | 渲染末尾：若 `fund_block` 不为 `None`，追加分隔线 + fund_block |
| `trendradar/__main__.py` | `_send_notification_if_needed()` 中调用 `fetch_fund_block()` 并传入 dispatcher |

### 5.3 renderer 追加逻辑（伪代码）

```python
if fund_block is not None:
    content += "\n\n" + fund_block
```

---

## 6. 错误处理与边界情况

| 情况 | 行为 |
|---|---|
| `fund_watch.yaml` 不存在 | 返回 `None`，静默跳过，消息无变化 |
| `enabled: false` | 返回 `None`，静默跳过，消息无变化 |
| `funds` 列表为空 | 返回提示字符串，消息末尾显示提示 |
| API 超时 / 网络失败 | 返回错误提示字符串，不抛异常 |
| 某只基金代码无数据 | 该条目单独显示"数据暂不可用" |
| 非交易日（`GSZ` 为空） | 回退显示 `NAV` + "（非交易日）"标注 |
| `fund_block` 为 `None` | dispatcher / renderer 不做任何处理 |

---

## 7. 不在本期范围内

- 股票实时行情（`stocks` 字段预留，本期返回空）
- 历史净值走势图
- 基金涨跌阈值告警（超过 X% 才推送）
- 独立定时推送（不依赖热榜触发）
