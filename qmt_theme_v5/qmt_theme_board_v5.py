# -*- coding: utf-8 -*-
"""迅投 QMT V5：自动题材维护 + 高质量打板升级版。

部署方式：把 qmt_theme_v5 目录整体复制到 Windows，例如 C:\\qmt_theme_v5，
在 QMT 中载入本文件并将 runtime_dir 设置为同一目录。首次运行会创建可编辑的
config/theme_catalog.json 与 config/theme_overrides.json。

默认 DRY RUN（live_order_enabled=False）：只生成审计结果，绝不委托。
人工覆盖不能绕过题材、共振、龙头、承接与封板稳定度硬风控。
"""
from __future__ import division

import datetime as dt
import json
import os
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from theme_engine import (  # noqa: E402
    DEFAULT_RULES,
    analyse_theme,
    atomic_dump_json,
    build_candidate_pool,
    build_effective_theme_basis,
    deep_merge,
    evaluate_board_gate,
    load_json,
    merge_components,
    normalise_stock,
    normalise_theme_catalog,
    normalise_overrides,
)


class G(object):
    pass


g = G()
_note = 0


DEFAULT_THEME_CATALOG = {
    "schema_version": 5,
    "description": "请将 sectors 填成当前 QMT 客户端可识别的行业/概念板块名称；一个题材可映射多个板块。",
    "themes": {
        "人工智能": {"sectors": ["人工智能", "AI应用"], "enabled": True, "notes": "示例：按本机 QMT 板块命名校正"},
        "机器人": {"sectors": ["机器人概念", "减速器"], "enabled": True, "notes": "示例"},
        "半导体": {"sectors": ["半导体", "芯片概念"], "enabled": True, "notes": "示例"}
    }
}

DEFAULT_OVERRIDES = {
    "schema_version": 5,
    "description": "人工覆盖只能禁用、观察、补充/排除成分及收紧阈值；不会绕过硬风控。",
    "global": {
        "rules": {
            "max_active_themes": 3
        }
    },
    "themes": {
        "示例禁用题材": {
            "action": "disable",
            "notes": "action 可为 auto/disable/watch/force_watch；force_watch 仅加入观察，不等于允许交易。",
            "include_stocks": [],
            "exclude_stocks": [],
            "min_strength": 70
        }
    }
}


def log(message):
    print("[QMT-V5] {0}".format(message))


def _field(value, key, default=None):
    if value is None:
        return default
    try:
        return value.get(key, default)
    except Exception:
        try:
            return getattr(value, key)
        except Exception:
            return default


def _now_time():
    return dt.datetime.now().time()


def _date_text():
    return dt.datetime.now().strftime("%Y%m%d")


def _path(name):
    return os.path.join(g.runtime_dir, name)


def _ensure_json(path, content):
    if not os.path.exists(path):
        atomic_dump_json(path, content)
        log("已创建配置模板：{0}".format(path))


def _runtime_paths():
    return {
        "catalog": _path(os.path.join("config", "theme_catalog.json")),
        "overrides": _path(os.path.join("config", "theme_overrides.json")),
        "membership": _path("output/theme_membership_{0}.json".format(_date_text())),
        "auto": _path("output/theme_auto_{0}.json".format(_date_text())),
        "basis": _path("output/effective_theme_basis_{0}.json".format(_date_text())),
        "audit": _path("output/board_gate_audit_{0}.jsonl".format(_date_text())),
    }


def _append_audit(payload):
    """以 jsonl 记录每次过闸/拒绝，盘后可完全复盘。"""
    try:
        path = _runtime_paths()["audit"]
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        log("审计写入失败：{0}".format(exc))


def _is_trading_day(now=None):
    now = now or dt.datetime.now()
    return now.weekday() < 5


def _within_buy_window(now=None):
    now = now or dt.datetime.now()
    current = now.time()
    return dt.time(9, 31) <= current <= g.buy_deadline


def init(C):
    """QMT 初始化入口。"""
    g.acct = "YOUR_QMT_ACCOUNT"        # 在本机部署后填写；不要提交真实账号。
    g.acct_type = "STOCK"
    g.strategy_name = "QMT_V5_ThemeBoard"
    g.live_order_enabled = False        # 安全默认：模拟/干跑。实盘前需人工改为 True。

    # Windows 推荐路径；可在 QMT 策略代码内按实际盘符修改。
    g.runtime_dir = os.environ.get("QMT_THEME_V5_HOME", r"C:\qmt_theme_v5")
    g.rules = deep_merge(DEFAULT_RULES, {
        "max_active_themes": 3,
        "min_theme_strength": 66,
        "min_theme_limit_ups": 2,
        "min_theme_breadth": 0.52,
        "min_leader_score": 72,
        "min_seal_amount_wan": 1800.0,
        "min_bid_ask_ratio": 1.35,
        "max_chase_time": "10:45",
    })
    g.max_holdings = 3
    g.single_stock_cash = 20000.0
    g.daily_max_new_orders = 3
    g.buy_deadline = dt.time(10, 45)
    g.force_sell_time = dt.time(14, 55)
    g.stop_loss_pct = -0.050
    g.trailing_activate_pct = 0.030
    g.trailing_ladder = [(0.15, 0.015), (0.10, 0.020), (0.06, 0.025), (0.03, 0.030)]

    g.day_buy = []
    g.day_sell = []
    g.trailing_state = {}
    g.synced_themes = {}
    g.auto_results = []
    g.effective_basis = {"rules": g.rules, "themes": [], "tradable_themes": []}
    g.candidate_pool = {}
    g.last_sync_day = ""
    g.last_gate_log = {}

    os.makedirs(os.path.join(g.runtime_dir, "config"), exist_ok=True)
    os.makedirs(os.path.join(g.runtime_dir, "output"), exist_ok=True)
    paths = _runtime_paths()
    _ensure_json(paths["catalog"], DEFAULT_THEME_CATALOG)
    _ensure_json(paths["overrides"], DEFAULT_OVERRIDES)

    # 盘前双次同步：首轮构建，开盘前复核覆盖文件及日线；handlebar 亦有缺失保护。
    C.run_time("pre_market_sync", "1nDay", "09:05:00")
    C.run_time("pre_open_refresh", "1nDay", "09:24:00")
    C.run_time("daily_reset", "1nDay", "09:00:00")
    log("初始化完成。模式={0}，运行目录={1}".format(
        "实盘委托" if g.live_order_enabled else "DRY RUN（不下单）", g.runtime_dir
    ))


def daily_reset(C):
    """每日状态清零，不清空盘前文件以保留审计。"""
    if not _is_trading_day():
        return
    g.day_buy = []
    g.day_sell = []
    g.trailing_state = {}
    g.last_gate_log = {}
    g.candidate_pool = {}
    g.effective_basis = {"rules": g.rules, "themes": [], "tradable_themes": []}
    g.auto_results = []
    g.last_sync_day = ""
    log("日内状态已重置")


def _safe_sector_members(C, sector_name):
    try:
        members = C.get_stock_list_in_sector(sector_name) or []
        values = []
        for stock in members:
            code = normalise_stock(stock)
            if code:
                values.append(code)
        return sorted(set(values)), ""
    except Exception as exc:
        return [], str(exc)


def _instrument_names(C, stocks):
    names = {}
    up_stops = {}
    for stock in stocks:
        try:
            detail = C.get_instrumentdetail(stock)
            names[stock] = str(_field(detail, "InstrumentName", ""))
            up_stops[stock] = _field(detail, "UpStopPrice", 0.0)
        except Exception:
            names[stock] = ""
            up_stops[stock] = 0.0
    return names, up_stops


def _frame_to_bars(frame):
    """把 QMT 返回的 pandas DataFrame 转为纯 dict 列表。"""
    if frame is None:
        return []
    try:
        records = frame.to_dict("records")
        return [dict(item) for item in records]
    except Exception:
        pass
    try:
        # 少数 QMT 版本返回 dict of arrays。
        keys = list(frame.keys())
        count = len(frame[keys[0]]) if keys else 0
        return [{key: frame[key][index] for key in keys} for index in range(count)]
    except Exception:
        return []


def _load_bars(C, stocks):
    if not stocks:
        return {}
    try:
        raw = C.get_market_data_ex(
            ["open", "high", "low", "close", "volume", "amount"],
            stocks,
            count=12,
            period="1d",
            dividend_type="front_ratio",
            subscribe=False,
        ) or {}
        return {stock: _frame_to_bars(raw.get(stock)) for stock in stocks}
    except Exception as exc:
        log("批量日线读取失败：{0}".format(exc))
        return {}


def _load_runtime_config():
    paths = _runtime_paths()
    catalog = normalise_theme_catalog(load_json(paths["catalog"], DEFAULT_THEME_CATALOG))
    overrides = normalise_overrides(load_json(paths["overrides"], DEFAULT_OVERRIDES))
    return catalog, overrides


def pre_market_sync(C):
    """步骤一：自动同步 QMT 板块成分，应用人工成分增删，生成题材扫描基础。"""
    if not _is_trading_day():
        return
    catalog, overrides = _load_runtime_config()
    synced = {}
    all_members = set()
    for theme, detail in catalog.items():
        if not detail.get("enabled", True):
            continue
        members = set()
        sector_details = []
        for sector_name in detail.get("sectors", []):
            current, error = _safe_sector_members(C, sector_name)
            sector_details.append({"sector": sector_name, "count": len(current), "error": error})
            members.update(current)
        merged = merge_components(theme, sorted(members), overrides)
        synced[theme] = {
            "sectors": sector_details,
            "synced_count": len(members),
            "effective_members": merged,
        }
        all_members.update(merged)

    payload = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme_count": len(synced),
        "themes": synced,
    }
    atomic_dump_json(_runtime_paths()["membership"], payload)
    g.synced_themes = synced
    log("题材成分同步完成：{0} 个题材，{1} 只去重成分股".format(len(synced), len(all_members)))
    _scan_and_build_basis(C, catalog, overrides)


def pre_open_refresh(C):
    """步骤二：开盘前复核，人工刚保存的 overrides 会在此轮生效。"""
    if not _is_trading_day():
        return
    pre_market_sync(C)


def _scan_and_build_basis(C, catalog, overrides):
    all_stocks = sorted(set(
        stock for item in g.synced_themes.values() for stock in item.get("effective_members", [])
    ))
    names, unused_up_stops = _instrument_names(C, all_stocks)
    bars_by_stock = _load_bars(C, all_stocks)
    # 先让全局人工阈值参与扫描（如龙头分、活跃题材数），再由引擎施加硬底线。
    scan_rules = deep_merge(g.rules, (overrides.get("global") or {}).get("rules") or {})
    auto_results = []
    for theme in sorted(g.synced_themes):
        members = g.synced_themes[theme]["effective_members"]
        result = analyse_theme(theme, members, bars_by_stock, names, scan_rules)
        result["sync"] = {
            "synced_count": g.synced_themes[theme]["synced_count"],
            "effective_count": len(members),
            "sectors": g.synced_themes[theme]["sectors"],
        }
        auto_results.append(result)

    basis = build_effective_theme_basis(auto_results, overrides, scan_rules)
    g.auto_results = auto_results
    g.effective_basis = basis
    g.candidate_pool = build_candidate_pool(basis)
    g.last_sync_day = _date_text()
    atomic_dump_json(_runtime_paths()["auto"], {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "themes": auto_results})
    atomic_dump_json(_runtime_paths()["basis"], basis)
    log("题材评估完成：可交易题材={0}，候选龙头={1}".format(
        basis.get("tradable_themes", []), list(g.candidate_pool.keys())
    ))
    for item in basis.get("themes", []):
        log("题材 {0} 强度={1:.1f} 状态={2} 原因={3}".format(
            item["theme"], item["strength"], item["status"], "；".join(item["reasons"]) or "通过"
        ))


def _get_positions():
    try:
        return get_trade_detail_data(g.acct, g.acct_type, "position") or []
    except Exception as exc:
        log("持仓查询失败：{0}".format(exc))
        return []


def _stock_from_position(position):
    return normalise_stock("{0}.{1}".format(
        _field(position, "m_strInstrumentID", ""), _field(position, "m_strExchangeID", "")
    ))


def _holding_map():
    result = {}
    for position in _get_positions():
        stock = _stock_from_position(position)
        quantity = int(float(_field(position, "m_nVolume", 0) or 0))
        if stock and quantity > 0:
            result[stock] = position
    return result


def _pending_orders(side):
    """过滤在途订单，避免重复委托。48=买，49=卖。"""
    flag = 48 if side == "buy" else 49
    result = set()
    try:
        orders = get_trade_detail_data(g.acct, g.acct_type, "ORDER", g.strategy_name) or []
        for order in orders:
            status = int(float(_field(order, "m_nOrderStatus", 0) or 0))
            offset = int(float(_field(order, "m_nOffsetFlag", 0) or 0))
            total = int(float(_field(order, "m_nVolumeTotal", 0) or 0))
            if offset == flag and total > 0 and status in (49, 50, 51, 52):
                result.add(normalise_stock("{0}.{1}".format(
                    _field(order, "m_strInstrumentID", ""), _field(order, "m_strExchangeID", "")
                )))
    except Exception as exc:
        log("在途委托查询失败：{0}".format(exc))
    return result


def _available_cash():
    try:
        accounts = get_trade_detail_data(g.acct, g.acct_type, "account") or []
        return float(_field(accounts[0], "m_dAvailable", 0.0) or 0.0) if accounts else 0.0
    except Exception:
        return 0.0


def _next_note(stock, action):
    global _note
    _note += 1
    return "{0}_{1}_{2}".format(stock, action, _note)


def _send_order(C, side, stock, volume, reason):
    """唯一委托出口：DRY RUN 下只审计；实盘前由人工明确打开开关。"""
    mode = "BUY" if side == "buy" else "SELL"
    event = {
        "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": "order_intent",
        "mode": mode,
        "stock": stock,
        "volume": int(volume),
        "reason": reason,
        "live_order_enabled": bool(g.live_order_enabled),
    }
    if not g.live_order_enabled:
        event["result"] = "DRY_RUN_NOT_SENT"
        _append_audit(event)
        log("[DRY RUN] {0} {1} {2}股：{3}".format(mode, stock, volume, reason))
        return False
    try:
        if side == "buy":
            passorder(23, 1102, g.acct, stock, 10, -1, int(volume), g.strategy_name, 2, _next_note(stock, "buy"), C)
        else:
            passorder(24, 1101, g.acct, stock, 10, -1, int(volume), g.strategy_name, 2, _next_note(stock, "sell"), C)
        event["result"] = "SENT"
        _append_audit(event)
        log("委托已发送：{0} {1} {2}股，{3}".format(mode, stock, volume, reason))
        return True
    except Exception as exc:
        event["result"] = "ERROR"
        event["error"] = str(exc)
        _append_audit(event)
        log("委托失败：{0}".format(exc))
        return False


def _trailing_should_exit(stock, profit_rate):
    state = g.trailing_state.setdefault(stock, {"peak": profit_rate, "activated": False})
    if not state["activated"]:
        if profit_rate >= g.trailing_activate_pct:
            state["activated"] = True
            state["peak"] = profit_rate
            return False, "trailing_activated"
        return profit_rate <= g.stop_loss_pct, "hard_stop_loss"
    if profit_rate > state["peak"]:
        state["peak"] = profit_rate
    drawdown = 0.03
    for threshold, tolerance in g.trailing_ladder:
        if state["peak"] >= threshold:
            drawdown = tolerance
            break
    if profit_rate <= state["peak"] - drawdown:
        return True, "trailing_drawdown_peak_{0:.2%}".format(state["peak"])
    return False, "hold"


def _manage_exits(C, now):
    """先卖后买；14:55 强制平仓优先于其他规则，策略不隔夜。"""
    positions = _holding_map()
    pending = _pending_orders("sell")
    for stock, position in positions.items():
        if stock in g.day_sell or stock in pending:
            continue
        can_use = int(float(_field(position, "m_nCanUseVolume", 0) or 0))
        if can_use <= 0:
            continue
        profit_rate = float(_field(position, "m_dProfitRate", 0.0) or 0.0)
        if now.time() >= g.force_sell_time:
            sell, reason = True, "force_flat_no_overnight"
        else:
            sell, reason = _trailing_should_exit(stock, profit_rate)
        if sell:
            _send_order(C, "sell", stock, can_use, reason)
            g.day_sell.append(stock)
            g.trailing_state.pop(stock, None)


def _up_stop_price(C, stock):
    try:
        detail = C.get_instrumentdetail(stock)
        return float(_field(detail, "UpStopPrice", 0.0) or 0.0)
    except Exception:
        return 0.0


def _board_lot(stock):
    return 200 if normalise_stock(stock).startswith(("688", "689")) else 100


def _allowed_to_attempt(holdings, stock, pending_buy):
    if g.last_sync_day != _date_text() or not g.candidate_pool:
        return False, "当日盘前题材依据不存在"
    if stock in holdings:
        return False, "已有持仓"
    if stock in g.day_buy or stock in pending_buy:
        return False, "今日已买或在途"
    if len(holdings) >= g.max_holdings:
        return False, "达到最大持仓数"
    if len(g.day_buy) >= g.daily_max_new_orders:
        return False, "达到每日新增仓位上限"
    return True, ""


def handlebar(C):
    """QMT 每根 1 分钟 bar 回调。最终风控通过才可进入唯一委托出口。"""
    now = dt.datetime.now()
    if not _is_trading_day(now):
        return
    try:
        if not C.is_last_bar():
            return
    except Exception:
        pass

    # 卖出风控任何时段优先执行；到点后不再买入。
    _manage_exits(C, now)
    if not _within_buy_window(now):
        return

    holdings = _holding_map()
    pending_buy = _pending_orders("buy")
    candidates = list(g.candidate_pool.keys())
    if not candidates:
        return
    try:
        ticks = C.get_full_tick(candidates) or {}
    except Exception as exc:
        log("实时行情读取失败：{0}".format(exc))
        return

    for stock, candidate in g.candidate_pool.items():
        ok, reason = _allowed_to_attempt(holdings, stock, pending_buy)
        if not ok:
            continue
        gate = evaluate_board_gate(
            candidate=candidate,
            theme_basis=g.effective_basis,
            quote=ticks.get(stock, {}),
            up_stop_price=_up_stop_price(C, stock),
            now=now,
        )
        # 相同股票每分钟最多打印一次同一拒绝集合，避免日志淹没关键事件。
        marker = "|".join(gate["failures"]) if gate["failures"] else "PASS"
        if g.last_gate_log.get(stock) != marker:
            _append_audit({"event": "board_gate", "candidate": candidate, "gate": gate})
            g.last_gate_log[stock] = marker
            log("打板闸门 {0}：{1}".format(stock, "通过" if gate["passed"] else "拒绝：" + "；".join(gate["failures"])))
        if not gate["passed"]:
            continue

        price = float(gate["metrics"].get("last_price", 0.0) or 0.0)
        lot = _board_lot(stock)
        volume = int(g.single_stock_cash / price / lot) * lot if price > 0 else 0
        cash = _available_cash()
        if volume < lot or cash < volume * price:
            _append_audit({"event": "board_gate", "stock": stock, "result": "资金或最小手数不足", "cash": cash})
            continue
        # 只要发起（含 DRY RUN）便锁定当日候选，防止一分钟内重复触发。
        _send_order(C, "buy", stock, volume, "四重硬闸通过:{0}".format(candidate.get("theme")))
        g.day_buy.append(stock)
        g.trailing_state[stock] = {"peak": 0.0, "activated": False}
        holdings[stock] = True
        if len(holdings) >= g.max_holdings or len(g.day_buy) >= g.daily_max_new_orders:
            break
        time.sleep(0.15)
