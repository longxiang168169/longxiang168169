# -*- coding: utf-8 -*-
# ============================================================================
# QMT 自动题材维护与高质量打板策略 V5（单文件直接运行版）
# 平台：迅投 QMT / miniQMT；建议设置为 1 分钟 K 线。
#
# 核心纪律（全部 AND，任一失败即不买）：
# 1. 有有效题材：盘前同步 QMT 板块成分，并生成有效题材交易依据。
# 2. 有板块共振：至少 2 家涨停、足够上涨广度、成交额及梯队高度。
# 3. 有龙头地位：候选必须是题材内连板/强度/流动性均合格的预选龙头。
# 4. 有资金承接：盘中必须封板、封单金额和五档买卖盘承接比达标。
# 5. 严防孤立板和炸板：非封板、盘中回落、振幅过大、超时追板一律拒绝。
#
# 单文件说明：本文件可直接载入 QMT。首次运行会自动创建：
#   C:\qmt_theme_v5\theme_catalog.json
#   C:\qmt_theme_v5\theme_overrides.json
# 题材成分、交易依据及闸门审计结果会写入同一目录。
#
# 实盘安全：live_order_enabled 默认 False，仅输出审计，不会发出委托。
# 在模拟账户验证板块名称、五档字段、订单状态与成交表现后，才可人工改为 True。
# ============================================================================

import os
import json
import math
import time
import datetime as dt

import numpy as np
import pandas as pd


class G(object):
    pass


g = G()
note = 0


# ============================================================================
# 首次运行自动创建的配置模板
# ============================================================================
DEFAULT_THEME_CATALOG = {
    'schema_version': 5,
    'description': '请将 sectors 修改为本机 QMT 客户端可识别的概念/行业板块名称；同一题材可合并多个板块。',
    'themes': {
        '人工智能': {
            'sectors': ['人工智能', 'AI应用'],
            'enabled': True,
            'notes': '示例名称，部署前请按 QMT 板块树名称校正。'
        },
        '机器人': {
            'sectors': ['机器人概念', '减速器'],
            'enabled': True,
            'notes': '示例名称。'
        },
        '半导体': {
            'sectors': ['半导体', '芯片概念'],
            'enabled': True,
            'notes': '示例名称。'
        }
    }
}

DEFAULT_THEME_OVERRIDES = {
    'schema_version': 5,
    'description': '人工覆盖只能禁用、观察、调整成分或收紧阈值；不能跳过题材共振、龙头、封板和资金承接硬风控。',
    'global': {
        'rules': {
            'max_active_themes': 3
        }
    },
    'themes': {
        '示例禁用题材': {
            'action': 'disable',
            'notes': 'action 可为 auto、disable、watch 或 force_watch；watch 仅观察，不能强制交易。',
            'include_stocks': [],
            'exclude_stocks': [],
            'min_strength': 70
        }
    }
}


# ============================================================================
# 初始化与参数配置
# ============================================================================
def init(C):
    # -------------------- 必须配置 --------------------
    # 请在本机填写资金账号；不要把真实账号提交到公开仓库。
    g.acct = 'YOUR_QMT_ACCOUNT'
    g.acct_type = 'STOCK'
    g.strategy_name = 'theme_board_v5_single'
    g.live_order_enabled = False       # 默认 DRY RUN；False 时 passorder 永不执行。

    # -------------------- 运行目录及自动生成配置 --------------------
    g.work_dir = r'C:\qmt_theme_v5'
    g.theme_catalog_file = os.path.join(g.work_dir, 'theme_catalog.json')
    g.theme_overrides_file = os.path.join(g.work_dir, 'theme_overrides.json')
    g.audit_file = os.path.join(g.work_dir, 'board_gate_audit_%s.jsonl' % trade_date())

    # -------------------- 交易时段 --------------------
    g.time_pre_open = dt.time(9, 5)
    g.time_pre_open_end = dt.time(9, 28)
    g.time_open = dt.time(9, 30)
    g.time_buy_start = dt.time(9, 31)
    g.time_buy_stop = dt.time(10, 45)  # 只做早盘高质量板，不午后追板。
    g.time_force_sell = dt.time(14, 55)
    g.limit_monitor_interval_seconds = 5

    # -------------------- 股票板块权限 --------------------
    g.allow_cyb = True
    g.allow_kcb = False
    g.allow_bjs = False

    # -------------------- 盘前题材强度硬条件 --------------------
    g.min_history_bars = 4
    g.min_component_count = 3
    g.min_theme_strength = 66.0
    g.min_theme_limit_ups = 2
    g.min_theme_breadth = 0.52
    g.min_theme_amount_yi = 5.0
    g.min_leader_score = 72.0
    g.min_ladder_for_leader = 1
    g.min_stock_amount_yi = 1.5
    g.leader_top_n = 2
    g.max_active_themes = 3

    # -------------------- 盘中打板硬条件 --------------------
    g.near_limit_tolerance = 0.0015
    g.min_seal_amount_wan = 1800.0
    g.min_bid_ask_ratio = 1.35
    g.min_board_amount_yi = 1.2
    g.min_open_pct = -0.015
    g.max_open_pct = 0.065
    g.max_intraday_amplitude = 0.125

    # -------------------- 账户与组合风险 --------------------
    g.max_holdings = 3
    g.single_stock_cash = 20000.0
    g.daily_max_new_orders = 3
    g.daily_loss_limit = -0.018
    g.min_order_value = 10000.0
    g.market_indices = ['000001.SH', '399006.SZ', '000300.SH']
    g.market_regime = 'NEUTRAL'
    g.daily_regime = 'NEUTRAL'
    g.daily_risk_locked = False
    g.day_start_equity = 0.0

    # -------------------- 卖出保护 --------------------
    g.stop_loss_pct = -0.050
    g.trailing_activate_pct = 0.030
    g.trailing_ladder = [
        (0.150, 0.015),
        (0.100, 0.020),
        (0.060, 0.025),
        (0.030, 0.030),
    ]

    # -------------------- 日内状态 --------------------
    g.daylist_buy = []
    g.daylist_sell = []
    g.trailing_state = {}
    g.limit_hold_state = {}
    g.limit_price_cache = {}
    g.last_preopen_date = ''
    g.last_gate_log = {}
    g.synced_themes = {}
    g.auto_theme_results = []
    g.effective_basis = {'rules': {}, 'themes': [], 'tradable_themes': []}
    g.candidate_pool = {}

    try:
        if not os.path.exists(g.work_dir):
            os.makedirs(g.work_dir)
    except Exception as exc:
        print('[初始化] 无法创建 %s，改用当前目录：%s' % (g.work_dir, str(exc)))
        g.work_dir = '.'
        g.theme_catalog_file = os.path.join(g.work_dir, 'theme_catalog.json')
        g.theme_overrides_file = os.path.join(g.work_dir, 'theme_overrides.json')
        g.audit_file = os.path.join(g.work_dir, 'board_gate_audit_%s.jsonl' % trade_date())

    ensure_json_file(g.theme_catalog_file, DEFAULT_THEME_CATALOG)
    ensure_json_file(g.theme_overrides_file, DEFAULT_THEME_OVERRIDES)

    start_time = dt.datetime.now().strftime('%Y%m%d%H%M%S')
    C.run_time('check_time_and_run', '60nSecond', start_time)
    C.run_time('monitor_limit_hold', '%dnSecond' % g.limit_monitor_interval_seconds, start_time)
    print('[初始化] V5 单文件题材打板策略启动。模式=%s，配置目录=%s' % (
        '实盘委托' if g.live_order_enabled else 'DRY RUN（不下单）', g.work_dir))


# ============================================================================
# 定时调度与盘前题材自动维护
# ============================================================================
def check_time_and_run(C):
    now = dt.datetime.now()
    if now.weekday() >= 5:
        return
    today = trade_date()
    # 策略中途启动时，只要处于盘前窗口且当天未同步，仍会自动构建交易依据。
    if g.time_pre_open <= now.time() < g.time_pre_open_end and g.last_preopen_date != today:
        before_open(C)


def before_open(C):
    """同步板块成分 -> 分析强度/梯队/龙头 -> 合并人工覆盖 -> 生成有效交易依据。"""
    print('\n========== [盘前题材维护 V5] %s ==========' % dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    g.last_preopen_date = trade_date()
    g.daylist_buy = []
    g.daylist_sell = []
    g.trailing_state = {}
    g.limit_hold_state = {}
    g.limit_price_cache = {}
    g.last_gate_log = {}
    g.daily_risk_locked = False
    g.candidate_pool = {}

    catalog = normalize_theme_catalog(load_json_file(g.theme_catalog_file, DEFAULT_THEME_CATALOG))
    overrides = normalize_theme_overrides(load_json_file(g.theme_overrides_file, DEFAULT_THEME_OVERRIDES))
    scan_rules = get_effective_rules(overrides)

    # 1) 自动同步：每个题材可合并多个 QMT 概念/行业板块，人工增删在同步后应用。
    synced = {}
    all_members = set()
    for theme, detail in catalog.items():
        if not detail.get('enabled', True):
            continue
        raw_members = set()
        sector_logs = []
        for sector in detail.get('sectors', []):
            members, error = get_sector_members(C, sector)
            raw_members.update(members)
            sector_logs.append({'sector': sector, 'count': len(members), 'error': error})
        members = merge_theme_components(theme, sorted(raw_members), overrides)
        synced[theme] = {
            'sectors': sector_logs,
            'synced_count': len(raw_members),
            'effective_members': members,
        }
        all_members.update(members)

    g.synced_themes = synced
    write_json_file(os.path.join(g.work_dir, 'theme_membership_%s.json' % trade_date()), {
        'generated_at': time_text(), 'themes': synced
    })
    if not all_members:
        g.effective_basis = {'rules': scan_rules, 'themes': [], 'tradable_themes': []}
        write_json_file(os.path.join(g.work_dir, 'effective_theme_basis_%s.json' % trade_date()), g.effective_basis)
        print('[盘前] 未同步到题材成分股；今日仅执行持仓风控。请核对 theme_catalog.json 的 QMT 板块名称。')
        initialize_daily_market_state(C)
        return

    # 2) 批量读取日线。盘前只使用已完成交易日数据，避免把半根当日K线纳入强度。
    stocks = sorted(all_members)
    names = get_instrument_names(C, stocks)
    daily_data = get_daily_data(C, stocks, count=12)
    auto_results = []
    for theme in sorted(synced):
        result = analyze_theme(theme, synced[theme]['effective_members'], daily_data, names, scan_rules)
        result['sync'] = synced[theme]
        auto_results.append(result)

    # 3) 自动扫描与人工覆盖合并。人工覆盖不能移除硬风控失败原因。
    basis = build_effective_theme_basis(auto_results, overrides, scan_rules)
    g.auto_theme_results = auto_results
    g.effective_basis = basis
    g.candidate_pool = build_candidate_pool(basis)
    write_json_file(os.path.join(g.work_dir, 'theme_auto_%s.json' % trade_date()), {
        'generated_at': time_text(), 'themes': auto_results
    })
    write_json_file(os.path.join(g.work_dir, 'effective_theme_basis_%s.json' % trade_date()), basis)

    initialize_daily_market_state(C)
    print('[盘前] 同步题材=%d，去重成分=%d，可交易题材=%s，候选龙头=%s。' % (
        len(synced), len(all_members), str(basis.get('tradable_themes', [])), str(list(g.candidate_pool.keys()))))
    for item in basis.get('themes', []):
        reasons = '；'.join(item.get('reasons', [])) or '通过'
        print('[盘前] 题材=%s 强度=%.1f 状态=%s 原因=%s' % (
            item.get('theme'), safe_float(item.get('strength')), item.get('status'), reasons))


def initialize_daily_market_state(C):
    g.daily_regime = get_daily_market_regime(C)
    g.market_regime = g.daily_regime
    g.day_start_equity = get_account_equity({})
    print('[市场状态] 日线状态=%s，起始权益=%.2f。' % (g.daily_regime, g.day_start_equity))


# ============================================================================
# 题材数据同步、自动强度计算、人工覆盖合并
# ============================================================================
def get_effective_rules(overrides):
    """全局参数可调整，但以下硬底线不允许被覆盖文件放松。"""
    rules = {
        'min_component_count': g.min_component_count,
        'min_theme_strength': g.min_theme_strength,
        'min_theme_limit_ups': g.min_theme_limit_ups,
        'min_theme_breadth': g.min_theme_breadth,
        'min_theme_amount_yi': g.min_theme_amount_yi,
        'min_leader_score': g.min_leader_score,
        'min_ladder_for_leader': g.min_ladder_for_leader,
        'min_stock_amount_yi': g.min_stock_amount_yi,
        'leader_top_n': g.leader_top_n,
        'max_active_themes': g.max_active_themes,
        'min_seal_amount_wan': g.min_seal_amount_wan,
        'min_bid_ask_ratio': g.min_bid_ask_ratio,
    }
    user_rules = (overrides.get('global') or {}).get('rules') or {}
    for key, value in user_rules.items():
        if key in rules:
            rules[key] = safe_float(value, rules[key])
    floors = {
        'min_component_count': 3,
        'min_theme_strength': 55.0,
        'min_theme_limit_ups': 2,
        'min_theme_breadth': 0.45,
        'min_theme_amount_yi': 3.0,
        'min_leader_score': 65.0,
        'min_ladder_for_leader': 1,
        'min_stock_amount_yi': 1.0,
        'min_seal_amount_wan': 1000.0,
        'min_bid_ask_ratio': 1.15,
    }
    for key, floor in floors.items():
        rules[key] = max(safe_float(rules.get(key), floor), floor)
    rules['leader_top_n'] = max(1, safe_int(rules.get('leader_top_n'), g.leader_top_n))
    rules['max_active_themes'] = max(1, safe_int(rules.get('max_active_themes'), g.max_active_themes))
    return rules


def normalize_theme_catalog(payload):
    raw = (payload or {}).get('themes', payload or {})
    result = {}
    for theme, detail in raw.items():
        if isinstance(detail, list):
            detail = {'sectors': detail}
        detail = detail or {}
        result[str(theme)] = {
            'sectors': list(detail.get('sectors') or [theme]),
            'enabled': bool(detail.get('enabled', True)),
            'notes': str(detail.get('notes', '')),
        }
    return result


def normalize_theme_overrides(payload):
    payload = payload or {}
    return {'global': dict(payload.get('global') or {}), 'themes': dict(payload.get('themes') or {})}


def get_sector_members(C, sector):
    try:
        values = C.get_stock_list_in_sector(sector) or []
        members = []
        for value in values:
            stock = normalize_stock_code(value)
            if stock:
                members.append(stock)
        return sorted(set(members)), ''
    except Exception as exc:
        return [], str(exc)


def merge_theme_components(theme, synced_members, overrides):
    rule = (overrides.get('themes') or {}).get(theme, {}) or {}
    members = set(normalize_stock_code(item) for item in synced_members if normalize_stock_code(item))
    include = set(normalize_stock_code(item) for item in rule.get('include_stocks', []) if normalize_stock_code(item))
    exclude = set(normalize_stock_code(item) for item in rule.get('exclude_stocks', []) if normalize_stock_code(item))
    return sorted((members | include) - exclude)


def get_instrument_names(C, stocks):
    names = {}
    for stock in stocks:
        try:
            detail = C.get_instrumentdetail(stock)
            names[stock] = str(detail.get('InstrumentName', '')) if detail else ''
        except Exception:
            names[stock] = ''
    return names


def get_daily_data(C, stocks, count=12):
    try:
        raw = C.get_market_data_ex(
            ['open', 'high', 'low', 'close', 'volume', 'amount'], stocks,
            count=count, period='1d', dividend_type='front_ratio', subscribe=False) or {}
        result = {}
        for stock in stocks:
            result[stock] = completed_bars(raw.get(stock))
        return result
    except Exception as exc:
        print('[盘前] 批量日线读取失败：%s' % str(exc))
        return {}


def completed_bars(df):
    """删除可能存在的当日未完成K线；无日期索引时保守保留数据。"""
    if df is None or len(df) == 0:
        return None
    result = df.copy()
    try:
        last_day = pd.to_datetime(result.index[-1]).date()
        if last_day >= dt.datetime.now().date():
            result = result.iloc[:-1]
    except Exception:
        pass
    return result


def analyze_theme(theme, members, daily_data, names, rules):
    rows = []
    skipped = []
    for stock in members:
        item, reason = analyze_stock(stock, names.get(stock, ''), daily_data.get(stock), rules)
        if item is None:
            skipped.append({'stock': stock, 'reason': reason})
        else:
            rows.append(item)

    if not rows:
        metrics = {'component_count': 0, 'limit_up_count': 0, 'up_count': 0, 'breadth': 0.0,
                   'avg_pct': 0.0, 'total_amount_yi': 0.0, 'max_ladder': 0, 'multi_ladder_count': 0}
        return {'theme': theme, 'generated_at': time_text(), 'strength': 0.0, 'metrics': metrics,
                'leaders': [], 'leader_candidates': [], 'members_scanned': list(members), 'skipped': skipped,
                'auto_trade_allowed': False, 'auto_rejections': ['有效成分股不足']}

    count = len(rows)
    limit_count = sum(1 for item in rows if item['limit_close'])
    up_count = sum(1 for item in rows if item['pct'] > 0)
    breadth = float(up_count) / max(count, 1)
    avg_pct = sum(item['pct'] for item in rows) / float(count)
    total_amount_yi = sum(item['amount_yi'] for item in rows)
    max_ladder = max(item['ladder'] for item in rows)
    multi_ladder = sum(1 for item in rows if item['ladder'] >= 2)

    # 题材强度 100 分：涨停扩散 35、梯队 25、广度 16、动量 12、流动性 8、跟风扩散 4。
    limit_score = min(35.0, limit_count * 13.0)
    ladder_score = min(25.0, max_ladder * 8.0 + multi_ladder * 4.5)
    breadth_score = clamp((breadth - 0.35) / 0.55 * 16.0, 0.0, 16.0)
    return_score = clamp(avg_pct * 150.0, 0.0, 12.0)
    liquidity_score = clamp(math.log10(max(total_amount_yi, 0.1)) * 5.0 + 3.0, 0.0, 8.0)
    follow_score = clamp((sum(1 for item in rows if item['pct'] >= 0.03) - limit_count) * 1.4, 0.0, 4.0)
    strength = clamp(limit_score + ladder_score + breadth_score + return_score + liquidity_score + follow_score)

    leaders = sorted(rows, key=lambda item: (item['leader_score'], item['ladder'], item['pct'], item['amount_yi']), reverse=True)
    candidates = []
    for item in leaders:
        if (item['leader_score'] >= rules['min_leader_score'] and
                item['ladder'] >= rules['min_ladder_for_leader'] and
                item['amount_yi'] >= rules['min_stock_amount_yi']):
            candidates.append(item)
        if len(candidates) >= rules['leader_top_n']:
            break

    metrics = {
        'component_count': count,
        'limit_up_count': limit_count,
        'up_count': up_count,
        'breadth': round(breadth, 4),
        'avg_pct': round(avg_pct, 6),
        'total_amount_yi': round(total_amount_yi, 3),
        'max_ladder': max_ladder,
        'multi_ladder_count': multi_ladder,
    }
    rejections = []
    if count < rules['min_component_count']:
        rejections.append('有效成分股不足')
    if limit_count < rules['min_theme_limit_ups']:
        rejections.append('涨停家数不足，疑似孤立板')
    if breadth < rules['min_theme_breadth']:
        rejections.append('板块上涨广度不足')
    if total_amount_yi < rules['min_theme_amount_yi']:
        rejections.append('板块成交额不足')
    if not candidates:
        rejections.append('无合格龙头候选')
    if strength < rules['min_theme_strength']:
        rejections.append('题材强度未达阈值')

    return {
        'theme': theme,
        'generated_at': time_text(),
        'strength': round(strength, 2),
        'metrics': metrics,
        'leaders': leaders[:10],
        'leader_candidates': candidates,
        'members_scanned': list(members),
        'skipped': skipped,
        'auto_trade_allowed': not rejections,
        'auto_rejections': rejections,
    }


def analyze_stock(stock, name, df, rules):
    if not board_allowed(stock, name):
        return None, '市场板块或ST过滤'
    if df is None or len(df) < g.min_history_bars:
        return None, '历史日线不足'
    required = ['open', 'high', 'low', 'close', 'amount']
    if any(column not in df.columns for column in required):
        return None, '日线字段不足'
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = safe_float(last['close'])
        prev_close = safe_float(prev['close'])
        if close <= 0 or prev_close <= 0:
            return None, '收盘价异常'
        pct = close / prev_close - 1.0
        cpos = close_location(last)
        at_limit = is_limit_close(last, prev, stock, name)
        ladder = consecutive_limit_ladder(df, stock, name)
        amount_yi = safe_float(last['amount']) / 1e8
        ladder_score = min(52.0, ladder * 22.0)
        limit_bonus = 12.0 if at_limit else 0.0
        momentum_score = clamp((pct + 0.02) * 180.0, 0.0, 16.0)
        close_score = clamp(cpos * 8.0, 0.0, 8.0)
        liquidity_score = clamp(math.log10(max(amount_yi, 0.1)) * 8.0 + 8.0, 0.0, 12.0)
        leader_score = clamp(ladder_score + limit_bonus + momentum_score + close_score + liquidity_score)
        return {
            'stock': stock, 'name': name, 'pct': round(pct, 6), 'amount_yi': round(amount_yi, 4),
            'ladder': int(ladder), 'limit_close': bool(at_limit), 'close_position': round(cpos, 4),
            'leader_score': round(leader_score, 2), 'last_close': round(close, 4),
        }, ''
    except Exception as exc:
        return None, '日线计算异常:%s' % str(exc)


def build_effective_theme_basis(auto_results, overrides, rules):
    """生成当日唯一交易依据；watch/force_watch 只观察，绝不等于交易许可。"""
    themes = []
    override_themes = overrides.get('themes') or {}
    for result in auto_results:
        theme = result.get('theme', '')
        rule = override_themes.get(theme, {}) or {}
        action = str(rule.get('action', 'auto')).lower()
        local_min = max(safe_float(rule.get('min_strength'), rules['min_theme_strength']), 55.0)
        # 强度门槛由合并后参数重算；其他自动硬风控失败永远保留。
        reasons = [item for item in result.get('auto_rejections', [])
                   if item not in ('题材强度未达阈值', '题材强度低于人工题材阈值')]
        if safe_float(result.get('strength')) < local_min:
            reasons.append('题材强度低于人工题材阈值')
        if action in ('disable', 'disabled', 'off'):
            reasons.append('人工覆盖禁用')
        watch_only = action in ('watch', 'force_watch', 'focus')
        if watch_only:
            reasons.append('人工覆盖仅观察')
        trade_allowed = not reasons
        themes.append({
            'theme': theme,
            'strength': safe_float(result.get('strength')),
            'trade_allowed': bool(trade_allowed),
            'watch_only': bool(watch_only),
            'status': '可交易' if trade_allowed else ('观察' if watch_only else '不交易'),
            'reasons': reasons,
            'override': {'action': action, 'min_strength': local_min, 'notes': str(rule.get('notes', ''))},
            'metrics': result.get('metrics', {}),
            'leader_candidates': result.get('leader_candidates', []),
            'leaders': result.get('leaders', []),
        })

    themes.sort(key=lambda item: item['strength'], reverse=True)
    active = 0
    for item in themes:
        if item['trade_allowed']:
            active += 1
            if active > rules['max_active_themes']:
                item['trade_allowed'] = False
                item['status'] = '不交易'
                item['reasons'].append('超过当日活跃题材数量上限')
    return {
        'generated_at': time_text(),
        'rules': rules,
        'themes': themes,
        'tradable_themes': [item['theme'] for item in themes if item['trade_allowed']],
        'watch_themes': [item['theme'] for item in themes if item['watch_only']],
    }


def build_candidate_pool(basis):
    pool = {}
    for theme_data in basis.get('themes', []):
        if not theme_data.get('trade_allowed'):
            continue
        for leader in theme_data.get('leader_candidates', []):
            stock = normalize_stock_code(leader.get('stock'))
            if not stock:
                continue
            candidate = dict(leader)
            candidate['theme'] = theme_data['theme']
            candidate['theme_strength'] = theme_data['strength']
            old = pool.get(stock)
            if old is None or candidate['theme_strength'] > old['theme_strength']:
                pool[stock] = candidate
    return dict(sorted(pool.items(), key=lambda pair: (
        pair[1]['theme_strength'], pair[1]['leader_score'], pair[1]['ladder']), reverse=True))


# ============================================================================
# 市场状态、主循环、组合风险
# ============================================================================
def get_daily_market_regime(C):
    on_votes = 0
    off_votes = 0
    valid = 0
    try:
        data = C.get_market_data_ex(['close'], g.market_indices, count=30, period='1d',
                                    dividend_type='front_ratio', subscribe=False) or {}
        for stock in g.market_indices:
            df = completed_bars(data.get(stock))
            if df is None or len(df) < 21 or 'close' not in df.columns:
                continue
            closes = pd.to_numeric(df['close'], errors='coerce').values.astype(float)
            if len(closes) < 21 or not np.all(np.isfinite(closes[-21:])):
                continue
            close = closes[-1]
            ma5, ma10, ma20 = np.mean(closes[-5:]), np.mean(closes[-10:]), np.mean(closes[-20:])
            ret5 = close / closes[-6] - 1.0 if closes[-6] > 0 else 0.0
            valid += 1
            if close > ma20 and ma5 > ma10 and ret5 >= 0:
                on_votes += 1
            elif close < ma20 and ma5 < ma10 and ret5 < 0:
                off_votes += 1
    except Exception as exc:
        print('[市场状态] 指数日线读取失败：%s' % str(exc))
    if valid >= 2 and off_votes >= 2:
        return 'RISK_OFF'
    if valid >= 2 and on_votes >= 2:
        return 'RISK_ON'
    return 'NEUTRAL'


def update_intraday_market_guard(C):
    """指数同步转弱时当日仅卖不买，防止在情绪退潮中机械追板。"""
    if g.daily_risk_locked:
        g.market_regime = 'RISK_OFF'
        return
    try:
        ticks = C.get_full_tick(g.market_indices) or {}
        returns = []
        below_vwap = 0
        for stock in g.market_indices:
            tick = ticks.get(stock, {})
            last = safe_float(tick.get('lastPrice'))
            prev = safe_float(tick.get('lastClose'))
            if last <= 0 or prev <= 0:
                continue
            returns.append(last / prev - 1.0)
            vwap = get_tick_vwap(tick)
            if vwap > 0 and last < vwap:
                below_vwap += 1
        if len(returns) >= 2:
            average = float(np.mean(returns))
            if (below_vwap >= 2 and average <= -0.004) or average <= -0.010:
                if g.market_regime != 'RISK_OFF':
                    print('[市场状态] 盘中指数同步转弱，切换 RISK_OFF，仅卖不买。')
                g.market_regime = 'RISK_OFF'
    except Exception as exc:
        print('[市场状态] 盘中行情读取失败：%s' % str(exc))


def update_daily_loss_guard(ticks):
    if g.daily_risk_locked or g.day_start_equity <= 0:
        return
    current = get_account_equity(ticks)
    if current > 0 and current / g.day_start_equity - 1.0 <= g.daily_loss_limit:
        g.daily_risk_locked = True
        g.market_regime = 'RISK_OFF'
        print('[组合风控] 日内权益回撤触及 %.2f%%，今日仅卖不买。' % (g.daily_loss_limit * 100.0))


def handlebar(C):
    now = dt.datetime.now()
    if now.weekday() >= 5 or now.time() < g.time_open:
        return
    try:
        if not C.is_last_bar():
            return
    except Exception:
        pass

    accounts = get_trade_detail_data(g.acct, g.acct_type, 'account') or []
    if not accounts:
        print('[账户] 未读取到账户信息，请确认 QMT 登录状态及 g.acct。')
        return

    positions = get_positions()
    held = [position_stock(item) for item in positions]
    quote_stocks = list(dict.fromkeys(list(g.candidate_pool.keys()) + held))
    try:
        ticks = C.get_full_tick(quote_stocks) if quote_stocks else {}
    except Exception as exc:
        ticks = {}
        print('[行情] 实时行情读取失败：%s' % str(exc))

    if g.day_start_equity <= 0:
        g.day_start_equity = get_account_equity(ticks)
    update_intraday_market_guard(C)
    update_daily_loss_guard(ticks)

    # 卖出优先，候选池为空时仍执行全部持仓风控。
    manage_positions(C, positions, ticks, now.time())
    if not (g.time_buy_start <= now.time() <= g.time_buy_stop):
        return
    run_board_entry(C, positions, ticks, now)


# ============================================================================
# 盘中高质量打板：四重硬闸门 + 仓位约束
# ============================================================================
def run_board_entry(C, positions, ticks, now):
    if not g.candidate_pool:
        return
    if g.market_regime == 'RISK_OFF' or g.daily_risk_locked:
        entry_log(now.time(), '市场风险关闭，仅卖不买')
        return

    holdings = {position_stock(item) for item in positions if safe_int(getattr(item, 'm_nVolume', 0)) > 0}
    pending_buy = get_order_stocks('buy', include_completed=True)
    if len(holdings) >= g.max_holdings or len(g.daylist_buy) >= g.daily_max_new_orders:
        return
    cash = get_available_cash()
    if cash < g.min_order_value:
        return

    for stock, candidate in g.candidate_pool.items():
        if stock in holdings or stock in g.daylist_buy or stock in pending_buy:
            continue
        if len(holdings) >= g.max_holdings or len(g.daylist_buy) >= g.daily_max_new_orders:
            break

        tick = ticks.get(stock, {})
        up_stop = get_limit_up_price(stock, C)
        gate = evaluate_board_gate(stock, candidate, tick, up_stop, now)
        marker = '|'.join(gate['failures']) if gate['failures'] else 'PASS'
        if g.last_gate_log.get(stock) != marker:
            append_audit({'event': 'board_gate', 'candidate': candidate, 'gate': gate})
            g.last_gate_log[stock] = marker
            print('[打板闸门] %s %s' % (stock, '通过' if gate['passed'] else '拒绝：' + '；'.join(gate['failures'])))
        if not gate['passed']:
            continue

        price = safe_float(gate['metrics'].get('last_price'))
        lot = 200 if stock.split('.')[0].startswith(('688', '689')) else 100
        volume = int(g.single_stock_cash / max(price, 0.01) / lot) * lot
        value = volume * price
        if volume < lot or value < g.min_order_value or cash < value:
            append_audit({'event': 'board_gate', 'stock': stock, 'result': '资金或最小交易单位不足', 'cash': cash})
            continue

        sent = submit_order(C, 'buy', stock, volume, '四重硬闸通过:' + candidate.get('theme', ''))
        # DRY RUN 同样锁定一次，避免每分钟重复产生同一买入意图；次日自动重置。
        if sent or not g.live_order_enabled:
            g.daylist_buy.append(stock)
            g.trailing_state[stock] = {'peak': 0.0, 'activated': False}
            holdings.add(stock)
            cash -= value


def evaluate_board_gate(stock, candidate, tick, up_stop, now):
    """最终买入判定：题材、共振、龙头、资金承接及反炸板条件必须同时通过。"""
    failures = []
    theme = candidate.get('theme')
    theme_item = None
    for item in g.effective_basis.get('themes', []):
        if item.get('theme') == theme:
            theme_item = item
            break
    if not theme_item or not theme_item.get('trade_allowed'):
        failures.append('题材未进入有效交易依据')
    if safe_float(candidate.get('leader_score')) < safe_float(g.effective_basis.get('rules', {}).get('min_leader_score'), g.min_leader_score):
        failures.append('非高质量龙头候选')
    min_ladder = safe_int(g.effective_basis.get('rules', {}).get('min_ladder_for_leader'), g.min_ladder_for_leader)
    if safe_int(candidate.get('ladder')) < min_ladder:
        failures.append('连板梯队高度不足')

    last = safe_float(tick.get('lastPrice'))
    prev = safe_float(tick.get('lastClose'))
    opening = safe_float(tick.get('open'))
    high = safe_float(tick.get('high', tick.get('highPrice', last)))
    low = safe_float(tick.get('low', tick.get('lowPrice', last)))
    amount_yi = safe_float(tick.get('amount')) / 1e8
    up_stop = safe_float(up_stop)
    book = get_quote_book(tick)

    if min(last, prev, up_stop) <= 0:
        failures.append('实时行情或涨停价缺失')
    else:
        if last < up_stop * (1.0 - g.near_limit_tolerance):
            failures.append('未封住涨停，拒绝追逐非板')
        if high > 0 and last < high * 0.996:
            failures.append('盘中回落，存在炸板风险')
        open_pct = opening / prev - 1.0 if opening > 0 else 0.0
        if open_pct < g.min_open_pct or open_pct > g.max_open_pct:
            failures.append('开盘幅度不在纪律区间')
        amplitude = (high - low) / prev if high > low and prev > 0 else 0.0
        if amplitude > g.max_intraday_amplitude:
            failures.append('日内振幅过大，疑似换手炸板')

    if amount_yi < g.min_board_amount_yi:
        failures.append('个股当日成交额不足')
    if not book['has_depth']:
        failures.append('五档买盘缺失，无法验证资金承接')
    else:
        seal_amount_wan = book['bid1_price'] * book['bid1_volume'] / 10000.0
        bid_ask_ratio = book['bid_value'] / max(book['ask_value'], 1.0)
        min_seal = safe_float(g.effective_basis.get('rules', {}).get('min_seal_amount_wan'), g.min_seal_amount_wan)
        min_ratio = safe_float(g.effective_basis.get('rules', {}).get('min_bid_ask_ratio'), g.min_bid_ask_ratio)
        if seal_amount_wan < min_seal:
            failures.append('封单金额不足')
        if bid_ask_ratio < min_ratio:
            failures.append('买卖盘承接比不足')

    if now.time() > g.time_buy_stop:
        failures.append('超过允许打板时间')
    metrics = {
        'last_price': round(last, 4), 'up_stop_price': round(up_stop, 4),
        'amount_yi': round(amount_yi, 3),
        'seal_amount_wan': round(book['bid1_price'] * book['bid1_volume'] / 10000.0, 2),
        'bid_ask_ratio': round(book['bid_value'] / max(book['ask_value'], 1.0), 3),
        'theme_strength': safe_float(candidate.get('theme_strength')),
        'leader_score': safe_float(candidate.get('leader_score')),
        'ladder': safe_int(candidate.get('ladder')),
    }
    return {'stock': stock, 'theme': theme, 'passed': not failures, 'failures': failures,
            'metrics': metrics, 'checked_at': time_text()}


def get_quote_book(tick):
    bid_price = tick.get('bidPrice', tick.get('bidPrices', [])) or []
    ask_price = tick.get('askPrice', tick.get('askPrices', [])) or []
    bid_vol = tick.get('bidVol', tick.get('bidVolume', tick.get('bidVolumes', []))) or []
    ask_vol = tick.get('askVol', tick.get('askVolume', tick.get('askVolumes', []))) or []
    bid_value = 0.0
    ask_value = 0.0
    for index in range(5):
        bid_value += array_value(bid_price, index) * array_value(bid_vol, index)
        ask_value += array_value(ask_price, index) * array_value(ask_vol, index)
    return {
        'bid1_price': array_value(bid_price, 0), 'bid1_volume': array_value(bid_vol, 0),
        'ask1_price': array_value(ask_price, 0), 'ask1_volume': array_value(ask_vol, 0),
        'bid_value': bid_value, 'ask_value': ask_value,
        'has_depth': bid_value > 0 and array_value(bid_vol, 0) > 0,
    }


def submit_order(C, side, stock, volume, reason):
    """唯一委托出口。live_order_enabled=False 时仅记录意图，绝不调用 passorder。"""
    event = {'time': time_text(), 'event': 'order_intent', 'side': side, 'stock': stock,
             'volume': int(volume), 'reason': reason, 'live_order_enabled': bool(g.live_order_enabled)}
    if not g.live_order_enabled:
        event['result'] = 'DRY_RUN_NOT_SENT'
        append_audit(event)
        print('[DRY RUN] %s %s %d股，%s。' % (side.upper(), stock, volume, reason))
        return False
    try:
        if side == 'buy':
            passorder(23, 1102, g.acct, stock, 10, -1, int(volume),
                      g.strategy_name, 2, get_new_note(stock, 'buy'), C)
        else:
            passorder(24, 1101, g.acct, stock, 10, -1, int(volume),
                      g.strategy_name, 2, get_new_note(stock, 'sell'), C)
        event['result'] = 'SENT'
        append_audit(event)
        print('[委托] %s %s %d股，原因=%s。' % (side.upper(), stock, volume, reason))
        return True
    except Exception as exc:
        event['result'] = 'ERROR'
        event['error'] = str(exc)
        append_audit(event)
        print('[委托] %s 异常：%s' % (stock, str(exc)))
        return False


# ============================================================================
# 持仓风险管理：止损、追踪止盈、封板炸板与尾盘强平
# ============================================================================
def manage_positions(C, positions, ticks, current_time):
    pending_sell = get_order_stocks('sell', include_completed=False)
    for position in positions:
        stock = position_stock(position)
        can_use = safe_int(getattr(position, 'm_nCanUseVolume', 0))
        if can_use <= 0 or stock in g.daylist_sell or stock in pending_sell:
            continue
        profit = safe_float(getattr(position, 'm_dProfitRate', 0.0))
        tick = ticks.get(stock, {})
        reason = ''
        # 强平优先，避免因封板持有逻辑而隔夜。
        if current_time >= g.time_force_sell:
            reason = 'force_flat_no_overnight'
        elif g.daily_risk_locked and profit <= 0.015:
            reason = 'daily_loss_guard'
        elif g.market_regime == 'RISK_OFF' and profit <= 0.010:
            reason = 'market_risk_off'
        elif stock in g.limit_hold_state and g.limit_hold_state[stock].get('locked'):
            continue
        else:
            should_sell, detail = check_trailing_stop(stock, profit)
            if should_sell:
                reason = detail
        if reason:
            if submit_order(C, 'sell', stock, can_use, reason) or not g.live_order_enabled:
                g.daylist_sell.append(stock)
                g.trailing_state.pop(stock, None)


def check_trailing_stop(stock, profit):
    state = g.trailing_state.get(stock)
    if state is None:
        state = {'peak': profit, 'activated': False}
        g.trailing_state[stock] = state
    if not state.get('activated'):
        if profit >= g.trailing_activate_pct:
            state['activated'] = True
            state['peak'] = profit
            print('[追踪激活] %s 浮盈=%.2f%%。' % (stock, profit * 100.0))
            return False, 'trailing_activated'
        return profit <= g.stop_loss_pct, 'hard_stop_loss'
    state['peak'] = max(safe_float(state.get('peak')), profit)
    drawdown = 0.03
    for threshold, tolerance in g.trailing_ladder:
        if state['peak'] >= threshold:
            drawdown = tolerance
            break
    if profit <= state['peak'] - drawdown:
        return True, 'trailing_drawdown_peak_%.2f%%' % (state['peak'] * 100.0)
    return False, 'hold'


def monitor_limit_hold(C):
    """5 秒独立监控：已封板持仓不因普通回撤卖出，炸板后立即执行卖出风控。"""
    now = dt.datetime.now()
    if now.weekday() >= 5 or now.time() < g.time_open or now.time() >= dt.time(15, 0):
        return
    try:
        positions = get_positions()
        stocks = [position_stock(item) for item in positions if safe_int(getattr(item, 'm_nCanUseVolume', 0)) > 0]
        if not stocks:
            return
        ticks = C.get_full_tick(stocks) or {}
        pending_sell = get_order_stocks('sell', include_completed=False)
        for position in positions:
            stock = position_stock(position)
            can_use = safe_int(getattr(position, 'm_nCanUseVolume', 0))
            if can_use <= 0 or stock in g.daylist_sell or stock in pending_sell:
                continue
            last = safe_float(ticks.get(stock, {}).get('lastPrice'))
            limit_price = get_limit_up_price(stock, C)
            if last <= 0 or limit_price <= 0:
                continue
            state = g.limit_hold_state.setdefault(stock, {'locked': False, 'ever_locked': False})
            locked_now = last + 0.0001 >= limit_price
            if locked_now and not state.get('ever_locked'):
                state.update({'locked': True, 'ever_locked': True, 'first_lock_time': now.strftime('%H:%M:%S')})
                print('[封板持有] %s 涨停价=%.3f，仅在炸板或强平时卖出。' % (stock, limit_price))
            elif locked_now:
                state['locked'] = True
            elif state.get('locked'):
                state['locked'] = False
                profit = safe_float(getattr(position, 'm_dProfitRate', 0.0))
                print('[炸板] %s 跌离涨停，触发卖出风控。' % stock)
                if submit_order(C, 'sell', stock, can_use, 'limit_break') or not g.live_order_enabled:
                    g.daylist_sell.append(stock)
                    g.trailing_state.pop(stock, None)
    except Exception as exc:
        print('[封板监控] 异常：%s' % str(exc))


# ============================================================================
# QMT 交易、行情、日线与通用辅助函数
# ============================================================================
def get_positions():
    try:
        return get_trade_detail_data(g.acct, g.acct_type, 'position') or []
    except Exception:
        return []


def position_stock(position):
    return normalize_stock_code('%s.%s' % (getattr(position, 'm_strInstrumentID', ''),
                                           getattr(position, 'm_strExchangeID', '')))


def get_order_stocks(side, include_completed=False):
    try:
        orders = get_trade_detail_data(g.acct, g.acct_type, 'ORDER', g.strategy_name) or []
        offset = 48 if side == 'buy' else 49
        statuses = [49, 50, 51, 52]
        if include_completed:
            statuses.append(55)
        result = set()
        for order in orders:
            if (safe_int(getattr(order, 'm_nVolumeTotal', 0)) > 0 and
                    safe_int(getattr(order, 'm_nOffsetFlag', -1)) == offset and
                    safe_int(getattr(order, 'm_nOrderStatus', -1)) in statuses):
                result.add(normalize_stock_code('%s.%s' % (
                    getattr(order, 'm_strInstrumentID', ''), getattr(order, 'm_strExchangeID', ''))))
        return result
    except Exception:
        return set()


def get_available_cash():
    try:
        accounts = get_trade_detail_data(g.acct, g.acct_type, 'account') or []
        return safe_float(getattr(accounts[0], 'm_dAvailable', 0.0)) if accounts else 0.0
    except Exception:
        return 0.0


def get_account_equity(ticks):
    value = get_available_cash()
    for position in get_positions():
        stock = position_stock(position)
        volume = safe_int(getattr(position, 'm_nVolume', 0))
        price = safe_float((ticks or {}).get(stock, {}).get('lastPrice'))
        if price <= 0:
            price = safe_float(getattr(position, 'm_dSettlementPrice', 0.0))
        value += max(volume, 0) * max(price, 0.0)
    return value


def get_limit_up_price(stock, C):
    cached = g.limit_price_cache.get(stock, {})
    if cached.get('date') == trade_date() and safe_float(cached.get('price')) > 0:
        return safe_float(cached.get('price'))
    try:
        detail = C.get_instrumentdetail(stock)
        price = safe_float(detail.get('UpStopPrice')) if detail else 0.0
        if price > 0:
            g.limit_price_cache[stock] = {'date': trade_date(), 'price': price}
        return price
    except Exception:
        return 0.0


def normalize_stock_code(raw):
    code = str(raw or '').strip().upper().replace(' ', '')
    if not code:
        return ''
    if '.' in code:
        left, right = code.split('.', 1)
        if left.isdigit() and right in ('SH', 'SZ', 'BJ'):
            return left.zfill(6) + '.' + right
        return ''
    if not code.isdigit() or len(code) > 6:
        return ''
    code = code.zfill(6)
    if code.startswith('6'):
        return code + '.SH'
    if code.startswith(('0', '2', '3')):
        return code + '.SZ'
    if code.startswith(('4', '8')):
        return code + '.BJ'
    return ''


def board_allowed(stock, name):
    code = normalize_stock_code(stock).split('.')[0]
    text = str(name or '').upper()
    if not code or 'ST' in text or '退' in text:
        return False
    if code.startswith(('300', '301')) and not g.allow_cyb:
        return False
    if code.startswith(('688', '689')) and not g.allow_kcb:
        return False
    if code.startswith(('4', '8')) and not g.allow_bjs:
        return False
    return True


def limit_ratio(stock, name=''):
    code = normalize_stock_code(stock).split('.')[0]
    if 'ST' in str(name or '').upper():
        return 0.05
    if code.startswith(('300', '301', '688', '689')):
        return 0.20
    if code.startswith(('4', '8')):
        return 0.30
    return 0.10


def close_location(row):
    high = safe_float(row['high'])
    low = safe_float(row['low'])
    close = safe_float(row['close'])
    if high <= low:
        return 1.0 if close >= high and high > 0 else 0.5
    return clamp((close - low) / (high - low), 0.0, 1.0)


def is_limit_close(current, previous, stock, name):
    previous_close = safe_float(previous['close'])
    close = safe_float(current['close'])
    if previous_close <= 0 or close <= 0:
        return False
    ratio = limit_ratio(stock, name)
    tolerance = 0.006 if ratio <= 0.10 else 0.010
    return close / previous_close - 1.0 >= ratio - tolerance and close_location(current) >= 0.92


def consecutive_limit_ladder(df, stock, name):
    if df is None or len(df) < 2:
        return 0
    ladder = 0
    for index in range(len(df) - 1, 0, -1):
        if is_limit_close(df.iloc[index], df.iloc[index - 1], stock, name):
            ladder += 1
        else:
            break
    return ladder


def get_tick_vwap(tick):
    amount = safe_float((tick or {}).get('amount'))
    volume = safe_float((tick or {}).get('volume'))
    return amount / volume if amount > 0 and volume > 0 else 0.0


def array_value(values, index):
    if values is None:
        return 0.0
    try:
        return safe_float(values[index])
    except Exception:
        return safe_float(values)


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, safe_float(value)))


def safe_float(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def trade_date():
    return dt.datetime.now().strftime('%Y%m%d')


def time_text():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_new_note(stock, action):
    global note
    note += 1
    return '%s_%s_%d' % (stock, action, note)


def entry_log(current_time, text):
    if current_time.minute % 5 == 0:
        print('[开仓诊断] %s %s，题材=%s，候选=%d。' % (
            current_time.strftime('%H:%M:%S'), text,
            str(g.effective_basis.get('tradable_themes', [])), len(g.candidate_pool)))


# ============================================================================
# 文件与审计辅助函数
# ============================================================================
def ensure_json_file(path, default):
    if not os.path.exists(path):
        write_json_file(path, default)
        print('[初始化] 已创建配置模板：%s' % path)


def load_json_file(path, default):
    if not path or not os.path.exists(path):
        return default
    for encoding in ('utf-8-sig', 'gbk'):
        try:
            with open(path, 'r', encoding=encoding) as handle:
                return json.load(handle)
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            print('[配置] 读取 %s 失败：%s，使用默认配置。' % (path, str(exc)))
            return default
    return default


def write_json_file(path, payload):
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
        try:
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            os.rename(tmp_path, path)
    except Exception as exc:
        print('[文件] 写入 %s 失败：%s' % (path, str(exc)))


def append_audit(payload):
    try:
        g.audit_file = os.path.join(g.work_dir, 'board_gate_audit_%s.jsonl' % trade_date())
        with open(g.audit_file, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
    except Exception as exc:
        print('[审计] 写入失败：%s' % str(exc))
