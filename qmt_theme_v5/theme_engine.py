# -*- coding: utf-8 -*-
"""QMT V5 题材维护、梯队识别及打板风控的纯逻辑层。

本模块不依赖 xtquant，可在普通 Python 环境中单元测试。QMT 脚本负责：
1) 从行情接口取数并归一化为 bars；2) 调用本模块；3) 仅在 gate 通过时委托。

重要原则：人工覆盖只能调整关注范围、阈值和禁用项，不能跳过题材共振、龙头、
资金承接、封板稳定度等硬风控。
"""
from __future__ import division

import copy
import datetime as dt
import json
import math
import os
import tempfile


DEFAULT_RULES = {
    "min_theme_strength": 66,
    "min_theme_limit_ups": 2,
    "min_theme_breadth": 0.52,
    "min_leader_score": 72,
    "leader_top_n": 2,
    "min_component_count": 3,
    "min_history_bars": 4,
    "min_theme_amount_yi": 5.0,
    "min_stock_amount_yi": 1.5,
    "min_ladder_for_leader": 1,
    "max_active_themes": 3,
    "near_limit_tolerance": 0.0015,
    "min_seal_amount_wan": 1800.0,
    "min_bid_ask_ratio": 1.35,
    "min_board_amount_yi": 1.2,
    "min_open_pct": -0.015,
    "max_open_pct": 0.065,
    "max_intraday_amplitude": 0.125,
    "max_chase_time": "10:45",
    "min_theme_leader_count": 1,
}


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def deep_merge(base, update):
    """递归合并字典，不修改输入。"""
    result = copy.deepcopy(base or {})
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return copy.deepcopy(default) if default is not None else {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_dump_json(path, payload):
    """原子写入 JSON，避免 QMT 运行中读到半截人工覆盖文件。"""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=parent or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def as_float(value, default=0.0):
    try:
        if value is None:
            return default
        converted = float(value)
        return default if math.isnan(converted) or math.isinf(converted) else converted
    except (TypeError, ValueError):
        return default


def row_value(row, key, default=0.0):
    """兼容 dict、pandas Series、numpy record。"""
    try:
        value = row[key]
    except Exception:
        try:
            value = getattr(row, key)
        except Exception:
            return default
    return as_float(value, default)


def normalise_stock(stock):
    return str(stock or "").strip().upper()


def stock_prefix(stock):
    return normalise_stock(stock).split(".")[0]


def is_st_name(name):
    text = str(name or "").upper()
    return "ST" in text or "退" in text or "*" + "ST" in text


def limit_ratio(stock, name=""):
    """返回常规涨跌停比例。ST 不参与策略，仍显式返回 5% 便于审计。"""
    code = stock_prefix(stock)
    if is_st_name(name):
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4")):
        return 0.30
    return 0.10


def is_allowed_board(stock, name, allow_cyb=True, allow_kcb=False, allow_bjs=False):
    code = stock_prefix(stock)
    if not code or is_st_name(name):
        return False
    if code.startswith(("8", "4")) and not allow_bjs:
        return False
    if code.startswith(("688", "689")) and not allow_kcb:
        return False
    if code.startswith(("300", "301")) and not allow_cyb:
        return False
    return True


def percent_change(close, previous_close):
    previous_close = as_float(previous_close)
    return 0.0 if previous_close <= 0 else as_float(close) / previous_close - 1.0


def close_position(row):
    high = row_value(row, "high")
    low = row_value(row, "low")
    close = row_value(row, "close")
    if high <= low:
        return 1.0 if close >= high and high > 0 else 0.5
    return clamp((close - low) / (high - low), 0.0, 1.0)


def bar_is_limit_close(current, previous, stock, name=""):
    """用收盘涨幅及收盘位置识别历史涨停，容忍除权、小数精度和数据源差异。"""
    prev_close = row_value(previous, "close")
    close = row_value(current, "close")
    if prev_close <= 0 or close <= 0:
        return False
    ratio = limit_ratio(stock, name)
    # 10%/20%/30% 板分别给予 0.6% 的数据容忍，且收盘接近日高。
    pct = percent_change(close, prev_close)
    tolerance = 0.006 if ratio <= 0.10 else 0.010
    return pct >= ratio - tolerance and close_position(current) >= 0.92


def consecutive_limit_ladder(bars, stock, name=""):
    """计算截至最后一根日线的连续收盘涨停天数。"""
    if not bars or len(bars) < 2:
        return 0
    count = 0
    for index in range(len(bars) - 1, 0, -1):
        if bar_is_limit_close(bars[index], bars[index - 1], stock, name):
            count += 1
        else:
            break
    return count


def to_yi(amount):
    return as_float(amount) / 100000000.0


def _return_score(pct):
    # 当日平均涨幅 0~8% 映射为 0~12 分；过高不额外加分，防范高位一致性。
    return clamp(as_float(pct) * 150.0, 0.0, 12.0)


def analyse_stock(stock, name, bars, rules=None):
    """计算一只股票的日线统计与预选龙头分数。"""
    rules = deep_merge(DEFAULT_RULES, rules or {})
    stock = normalise_stock(stock)
    bars = list(bars or [])
    result = {
        "stock": stock,
        "name": str(name or ""),
        "eligible": False,
        "reason": "",
        "pct": 0.0,
        "amount_yi": 0.0,
        "ladder": 0,
        "limit_close": False,
        "close_position": 0.0,
        "leader_score": 0.0,
        "last_close": 0.0,
    }
    if not is_allowed_board(stock, name):
        result["reason"] = "市场板块或ST过滤"
        return result
    if len(bars) < int(rules["min_history_bars"]):
        result["reason"] = "历史日线不足"
        return result
    last = bars[-1]
    prev = bars[-2]
    last_close = row_value(last, "close")
    prev_close = row_value(prev, "close")
    if last_close <= 0 or prev_close <= 0:
        result["reason"] = "收盘价缺失"
        return result

    pct = percent_change(last_close, prev_close)
    amount_yi = to_yi(row_value(last, "amount"))
    ladder = consecutive_limit_ladder(bars, stock, name)
    at_limit = bar_is_limit_close(last, prev, stock, name)
    cpos = close_position(last)
    # 预选龙头：连板高度为核心，配合日线强度、收盘质量和流动性。
    ladder_score = min(ladder * 22.0, 52.0)
    limit_bonus = 12.0 if at_limit else 0.0
    momentum_score = clamp((pct + 0.02) * 180.0, 0.0, 16.0)
    close_score = clamp(cpos * 8.0, 0.0, 8.0)
    liquidity_score = clamp(math.log10(max(amount_yi, 0.1)) * 8.0 + 8.0, 0.0, 12.0)
    leader_score = clamp(ladder_score + limit_bonus + momentum_score + close_score + liquidity_score)

    result.update({
        "eligible": True,
        "pct": round(pct, 6),
        "amount_yi": round(amount_yi, 4),
        "ladder": ladder,
        "limit_close": at_limit,
        "close_position": round(cpos, 4),
        "leader_score": round(leader_score, 2),
        "last_close": round(last_close, 4),
    })
    return result


def _theme_strength(stock_rows, rules):
    count = len(stock_rows)
    if not count:
        return 0.0, {}
    limit_count = sum(1 for item in stock_rows if item["limit_close"])
    up_count = sum(1 for item in stock_rows if item["pct"] > 0)
    breadth = float(up_count) / count
    avg_pct = sum(item["pct"] for item in stock_rows) / count
    total_amount = sum(item["amount_yi"] for item in stock_rows)
    max_ladder = max(item["ladder"] for item in stock_rows)
    multi_ladder_count = sum(1 for item in stock_rows if item["ladder"] >= 2)

    # 题材强度 0~100：涨停扩散(35)、梯队高度/完整度(25)、广度(16)、
    # 平均涨幅(12)、成交额(8)、非涨停强势股扩散(4)。
    limit_score = clamp(limit_count * 13.0, 0.0, 35.0)
    ladder_score = clamp(max_ladder * 8.0 + multi_ladder_count * 4.5, 0.0, 25.0)
    breadth_score = clamp((breadth - 0.35) / 0.55 * 16.0, 0.0, 16.0)
    return_score = _return_score(avg_pct)
    liquidity_score = clamp(math.log10(max(total_amount, 0.1)) * 5.0 + 3.0, 0.0, 8.0)
    follow_score = clamp((sum(1 for item in stock_rows if item["pct"] >= 0.03) - limit_count) * 1.4, 0.0, 4.0)
    strength = clamp(limit_score + ladder_score + breadth_score + return_score + liquidity_score + follow_score)
    metrics = {
        "component_count": count,
        "limit_up_count": limit_count,
        "up_count": up_count,
        "breadth": round(breadth, 4),
        "avg_pct": round(avg_pct, 6),
        "total_amount_yi": round(total_amount, 3),
        "max_ladder": max_ladder,
        "multi_ladder_count": multi_ladder_count,
    }
    return round(strength, 2), metrics


def analyse_theme(theme_name, components, bars_by_stock, names_by_stock=None, rules=None):
    """输出题材强度、涨停梯队、候选龙头和可交易前的自动判定。"""
    rules = deep_merge(DEFAULT_RULES, rules or {})
    names_by_stock = names_by_stock or {}
    seen = set()
    stock_rows = []
    skipped = []
    for raw_stock in components or []:
        stock = normalise_stock(raw_stock)
        if not stock or stock in seen:
            continue
        seen.add(stock)
        row = analyse_stock(stock, names_by_stock.get(stock, ""), bars_by_stock.get(stock, []), rules)
        if row["eligible"]:
            stock_rows.append(row)
        else:
            skipped.append({"stock": stock, "reason": row["reason"]})

    strength, metrics = _theme_strength(stock_rows, rules)
    leaders = sorted(stock_rows, key=lambda item: (
        item["leader_score"], item["ladder"], item["pct"], item["amount_yi"]
    ), reverse=True)
    leader_candidates = [
        item for item in leaders
        if item["leader_score"] >= float(rules["min_leader_score"])
        and item["ladder"] >= int(rules["min_ladder_for_leader"])
        and item["amount_yi"] >= float(rules["min_stock_amount_yi"])
    ][:max(1, int(rules["leader_top_n"]))]

    hard_failures = []
    if metrics.get("component_count", 0) < int(rules["min_component_count"]):
        hard_failures.append("有效成分股不足")
    if metrics.get("limit_up_count", 0) < int(rules["min_theme_limit_ups"]):
        hard_failures.append("涨停家数不足，疑似孤立板")
    if metrics.get("breadth", 0.0) < float(rules["min_theme_breadth"]):
        hard_failures.append("板块上涨广度不足")
    if metrics.get("total_amount_yi", 0.0) < float(rules["min_theme_amount_yi"]):
        hard_failures.append("板块成交额不足")
    if not leader_candidates:
        hard_failures.append("无合格龙头候选")
    if strength < float(rules["min_theme_strength"]):
        hard_failures.append("题材强度未达阈值")

    return {
        "theme": str(theme_name),
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strength": strength,
        "metrics": metrics,
        "leaders": leaders[:10],
        "leader_candidates": leader_candidates,
        "members_scanned": sorted(seen),
        "skipped": skipped,
        "auto_trade_allowed": not hard_failures,
        "auto_rejections": hard_failures,
    }


def normalise_theme_catalog(catalog):
    """兼容 {themes:{主题:{sectors:[]}}} 和 {主题:[sector]} 两种配置。"""
    raw_themes = (catalog or {}).get("themes", catalog or {})
    result = {}
    for theme, detail in raw_themes.items():
        if isinstance(detail, list):
            detail = {"sectors": detail}
        detail = detail or {}
        result[str(theme)] = {
            "sectors": list(detail.get("sectors") or [theme]),
            "enabled": bool(detail.get("enabled", True)),
            "notes": str(detail.get("notes", "")),
        }
    return result


def normalise_overrides(overrides):
    overrides = overrides or {}
    return {
        "global": dict(overrides.get("global") or {}),
        "themes": dict(overrides.get("themes") or {}),
    }


def merge_components(theme_name, synced_components, overrides):
    """先同步、再应用人工增删成分；新增成分仍须通过日线和实时硬风控。"""
    rule = normalise_overrides(overrides)["themes"].get(theme_name, {}) or {}
    current = set(normalise_stock(item) for item in (synced_components or []) if normalise_stock(item))
    include = set(normalise_stock(item) for item in (rule.get("include_stocks") or []) if normalise_stock(item))
    exclude = set(normalise_stock(item) for item in (rule.get("exclude_stocks") or []) if normalise_stock(item))
    return sorted((current | include) - exclude)


def _override_rule(overrides, theme_name):
    return normalise_overrides(overrides)["themes"].get(theme_name, {}) or {}


def build_effective_theme_basis(auto_results, overrides=None, rules=None):
    """将自动扫描结果与人工覆盖合并成唯一交易依据。

    action=disable 会禁用；action=watch/force_watch 只加入观察，不能强制交易。
    min_strength 可收紧或适度放宽主题阈值，但仍不能低于 global/hard_floor。
    """
    base_rules = deep_merge(DEFAULT_RULES, rules or {})
    override_data = normalise_overrides(overrides)
    global_cfg = override_data["global"]
    effective_rules = deep_merge(base_rules, global_cfg.get("rules") or {})
    # 人工配置不能把关键阈值放低到无纪律状态。
    hard_floor = {
        "min_theme_strength": 55,
        "min_theme_limit_ups": 2,
        "min_theme_breadth": 0.45,
        "min_leader_score": 65,
        "min_seal_amount_wan": 1000.0,
        "min_bid_ask_ratio": 1.15,
    }
    for key, floor in hard_floor.items():
        effective_rules[key] = max(as_float(effective_rules.get(key), floor), floor)

    items = []
    for result in auto_results or []:
        theme = result.get("theme", "")
        if not theme:
            continue
        override = _override_rule(override_data, theme)
        action = str(override.get("action", "auto")).lower()
        local_min = max(
            as_float(override.get("min_strength"), effective_rules["min_theme_strength"]),
            hard_floor["min_theme_strength"],
        )
        # 强度门槛可由覆盖文件收紧或在硬底线内调整，因此需以合并后的
        # 门槛重新判定；其他自动拒绝原因（共振、流动性、龙头）始终保留。
        reasons = [reason for reason in (result.get("auto_rejections") or [])
                   if reason not in ("题材强度未达阈值", "题材强度低于人工主题阈值")]
        if as_float(result.get("strength")) < local_min and "题材强度未达阈值" not in reasons:
            reasons.append("题材强度低于人工主题阈值")
        if action in ("disable", "disabled", "off"):
            reasons.append("人工覆盖禁用")
        is_watch = action in ("watch", "force_watch", "focus")
        if is_watch:
            reasons.append("人工覆盖仅观察")
        trade_allowed = not reasons and action not in ("disable", "disabled", "off")
        items.append({
            "theme": theme,
            "strength": as_float(result.get("strength")),
            "trade_allowed": trade_allowed,
            "watch_only": is_watch,
            "status": "可交易" if trade_allowed else ("观察" if is_watch else "不交易"),
            "reasons": reasons,
            "override": {
                "action": action,
                "min_strength": local_min,
                "notes": str(override.get("notes", "")),
            },
            "metrics": result.get("metrics", {}),
            "leader_candidates": result.get("leader_candidates", []),
            "leaders": result.get("leaders", []),
        })

    # 强度优先，只允许最多 N 个自动题材进入交易清单；其余保留在结果中但交易关闭。
    ranked = sorted(items, key=lambda item: item["strength"], reverse=True)
    allowed = 0
    max_active = int(effective_rules["max_active_themes"])
    for item in ranked:
        if item["trade_allowed"]:
            allowed += 1
            if allowed > max_active:
                item["trade_allowed"] = False
                item["status"] = "不交易"
                item["reasons"].append("超过当日活跃题材数量上限")

    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rules": effective_rules,
        "themes": ranked,
        "tradable_themes": [item["theme"] for item in ranked if item["trade_allowed"]],
        "watch_themes": [item["theme"] for item in ranked if item["watch_only"]],
    }


def build_candidate_pool(effective_basis):
    """将可交易题材的候选龙头展开为代码->题材映射，去重后保留最强题材。"""
    pool = {}
    for theme_data in (effective_basis or {}).get("themes", []):
        if not theme_data.get("trade_allowed"):
            continue
        for leader in theme_data.get("leader_candidates", []):
            stock = normalise_stock(leader.get("stock"))
            if not stock:
                continue
            candidate = copy.deepcopy(leader)
            candidate["theme"] = theme_data["theme"]
            candidate["theme_strength"] = theme_data["strength"]
            old = pool.get(stock)
            if old is None or candidate["theme_strength"] > old["theme_strength"]:
                pool[stock] = candidate
    return dict(sorted(pool.items(), key=lambda pair: (
        pair[1]["theme_strength"], pair[1]["leader_score"], pair[1]["ladder"]
    ), reverse=True))


def _array_value(source, index=0):
    if source is None:
        return 0.0
    try:
        return as_float(source[index])
    except Exception:
        return as_float(source)


def quote_book(quote):
    """兼容 QMT 常见 bidPrice/bidVol/bidVolume 字段，统一为资金承接指标。"""
    quote = quote or {}
    bid_prices = quote.get("bidPrice") or quote.get("bidPrices") or []
    ask_prices = quote.get("askPrice") or quote.get("askPrices") or []
    bid_volumes = quote.get("bidVol") or quote.get("bidVolume") or quote.get("bidVolumes") or []
    ask_volumes = quote.get("askVol") or quote.get("askVolume") or quote.get("askVolumes") or []
    bid_value = 0.0
    ask_value = 0.0
    for index in range(5):
        bid_value += _array_value(bid_prices, index) * _array_value(bid_volumes, index)
        ask_value += _array_value(ask_prices, index) * _array_value(ask_volumes, index)
    return {
        "bid1_price": _array_value(bid_prices, 0),
        "bid1_volume": _array_value(bid_volumes, 0),
        "ask1_price": _array_value(ask_prices, 0),
        "ask1_volume": _array_value(ask_volumes, 0),
        "bid_value": bid_value,
        "ask_value": ask_value,
        "has_depth": bid_value > 0 and _array_value(bid_volumes, 0) > 0,
    }


def evaluate_board_gate(candidate, theme_basis, quote, up_stop_price, now=None, rules=None):
    """最终打板判定：失败即拒绝，返回可审计的指标与拒绝理由。

    该函数只评估，不下单。所有条件均为 AND；严禁由人工覆盖绕过。
    """
    active_rules = deep_merge(DEFAULT_RULES, (theme_basis or {}).get("rules") or {})
    active_rules = deep_merge(active_rules, rules or {})
    now = now or dt.datetime.now()
    stock = normalise_stock((candidate or {}).get("stock"))
    theme = (candidate or {}).get("theme")
    failures = []

    theme_info = None
    for item in (theme_basis or {}).get("themes", []):
        if item.get("theme") == theme:
            theme_info = item
            break
    if not theme_info or not theme_info.get("trade_allowed"):
        failures.append("题材未进入有效交易依据")
    if as_float((candidate or {}).get("leader_score")) < as_float(active_rules["min_leader_score"]):
        failures.append("非高质量龙头候选")
    if int(as_float((candidate or {}).get("ladder"))) < int(active_rules["min_ladder_for_leader"]):
        failures.append("连板梯队高度不足")

    q = quote or {}
    last_price = as_float(q.get("lastPrice") or q.get("last_price"))
    last_close = as_float(q.get("lastClose") or q.get("last_close"))
    open_price = as_float(q.get("open") or q.get("openPrice"))
    high_price = as_float(q.get("high") or q.get("highPrice") or last_price)
    low_price = as_float(q.get("low") or q.get("lowPrice") or last_price)
    amount_yi = to_yi(q.get("amount"))
    up_stop_price = as_float(up_stop_price)
    book = quote_book(q)

    if not stock or last_price <= 0 or last_close <= 0 or up_stop_price <= 0:
        failures.append("实时行情或涨停价缺失")
    else:
        if last_price < up_stop_price * (1.0 - as_float(active_rules["near_limit_tolerance"])):
            failures.append("未封住涨停，拒绝追逐非板")
        if high_price > 0 and last_price < high_price * 0.996:
            failures.append("盘中回落，存在炸板风险")
        open_pct = percent_change(open_price, last_close) if open_price > 0 else 0.0
        if open_pct < as_float(active_rules["min_open_pct"]) or open_pct > as_float(active_rules["max_open_pct"]):
            failures.append("开盘幅度不在纪律区间")
        amplitude = (high_price - low_price) / last_close if high_price > low_price and last_close > 0 else 0.0
        if amplitude > as_float(active_rules["max_intraday_amplitude"]):
            failures.append("日内振幅过大，疑似换手炸板")

    if amount_yi < as_float(active_rules["min_board_amount_yi"]):
        failures.append("个股当日成交额不足")
    if not book["has_depth"]:
        failures.append("五档买盘缺失，无法验证资金承接")
    else:
        seal_amount_wan = book["bid1_price"] * book["bid1_volume"] / 10000.0
        bid_ask_ratio = book["bid_value"] / max(book["ask_value"], 1.0)
        if seal_amount_wan < as_float(active_rules["min_seal_amount_wan"]):
            failures.append("封单金额不足")
        if bid_ask_ratio < as_float(active_rules["min_bid_ask_ratio"]):
            failures.append("买卖盘承接比不足")

    if now.strftime("%H:%M") > str(active_rules["max_chase_time"]):
        failures.append("超过允许打板时间")

    metrics = {
        "last_price": round(last_price, 4),
        "up_stop_price": round(up_stop_price, 4),
        "amount_yi": round(amount_yi, 3),
        "seal_amount_wan": round(book["bid1_price"] * book["bid1_volume"] / 10000.0, 2),
        "bid_ask_ratio": round(book["bid_value"] / max(book["ask_value"], 1.0), 3),
        "theme_strength": as_float((candidate or {}).get("theme_strength")),
        "leader_score": as_float((candidate or {}).get("leader_score")),
        "ladder": int(as_float((candidate or {}).get("ladder"))),
    }
    return {
        "stock": stock,
        "theme": theme,
        "passed": not failures,
        "failures": failures,
        "metrics": metrics,
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
