# -*- coding: utf-8 -*-
import datetime as dt
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from theme_engine import (  # noqa: E402
    analyse_theme,
    build_candidate_pool,
    build_effective_theme_basis,
    consecutive_limit_ladder,
    evaluate_board_gate,
    merge_components,
)


def make_bars(closes, amount=200000000):
    bars = []
    for close in closes:
        bars.append({
            "open": close * 0.98,
            "high": close,
            "low": close * 0.96,
            "close": close,
            "amount": amount,
            "volume": 1000000,
        })
    return bars


class ThemeEngineTests(unittest.TestCase):
    def setUp(self):
        self.names = {
            "000001.SZ": "龙头A",
            "000002.SZ": "龙头B",
            "000003.SZ": "跟风C",
            "000004.SZ": "跟风D",
            "000005.SZ": "跟风E",
        }
        self.bars = {
            "000001.SZ": make_bars([10.0, 11.0, 12.1, 13.31]),
            "000002.SZ": make_bars([10.0, 10.5, 11.55, 12.705]),
            "000003.SZ": make_bars([10.0, 10.1, 10.2, 10.65]),
            "000004.SZ": make_bars([10.0, 10.1, 10.2, 10.51]),
            "000005.SZ": make_bars([10.0, 10.1, 10.2, 10.31]),
        }
        self.components = list(self.names.keys())

    def _good_result(self):
        result = analyse_theme("高质量题材", self.components, self.bars, self.names)
        self.assertTrue(result["auto_trade_allowed"], result)
        self.assertGreaterEqual(result["metrics"]["limit_up_count"], 2)
        self.assertGreaterEqual(result["metrics"]["max_ladder"], 2)
        self.assertTrue(result["leader_candidates"])
        return result

    def test_consecutive_ladder(self):
        self.assertEqual(consecutive_limit_ladder(self.bars["000001.SZ"], "000001.SZ", "龙头A"), 3)
        self.assertEqual(consecutive_limit_ladder(self.bars["000002.SZ"], "000002.SZ", "龙头B"), 2)

    def test_theme_strength_and_candidate_pool(self):
        basis = build_effective_theme_basis([self._good_result()])
        self.assertEqual(basis["tradable_themes"], ["高质量题材"])
        pool = build_candidate_pool(basis)
        self.assertIn("000001.SZ", pool)
        self.assertEqual(pool["000001.SZ"]["theme"], "高质量题材")

    def test_override_disable_and_force_watch_cannot_bypass_risk(self):
        good = self._good_result()
        disabled = build_effective_theme_basis([good], {"themes": {"高质量题材": {"action": "disable"}}})
        self.assertFalse(disabled["themes"][0]["trade_allowed"])
        self.assertIn("人工覆盖禁用", disabled["themes"][0]["reasons"])

        observed = build_effective_theme_basis([good], {"themes": {"高质量题材": {"action": "watch"}}})
        self.assertFalse(observed["themes"][0]["trade_allowed"])
        self.assertIn("人工覆盖仅观察", observed["themes"][0]["reasons"])

        weak = {
            "theme": "孤立板题材",
            "strength": 91,
            "metrics": {"limit_up_count": 1},
            "leaders": [],
            "leader_candidates": [],
            "auto_rejections": ["涨停家数不足，疑似孤立板", "无合格龙头候选"],
        }
        watched = build_effective_theme_basis([weak], {"themes": {"孤立板题材": {"action": "force_watch"}}})
        self.assertFalse(watched["themes"][0]["trade_allowed"])
        self.assertTrue(watched["themes"][0]["watch_only"])

    def test_component_override_union_minus_exclusion(self):
        overrides = {"themes": {"题材": {"include_stocks": ["000003.SZ"], "exclude_stocks": ["000002.SZ"]}}}
        members = merge_components("题材", ["000001.SZ", "000002.SZ"], overrides)
        self.assertEqual(members, ["000001.SZ", "000003.SZ"])

    def test_high_quality_board_gate_and_missing_depth(self):
        basis = build_effective_theme_basis([self._good_result()])
        candidate = build_candidate_pool(basis)["000001.SZ"]
        good_quote = {
            "lastPrice": 11.00,
            "lastClose": 10.00,
            "open": 10.20,
            "high": 11.00,
            "low": 10.15,
            "amount": 220000000,
            "bidPrice": [11.00, 10.99, 10.98, 10.97, 10.96],
            "bidVol": [2100000, 200000, 100000, 50000, 50000],
            "askPrice": [0, 0, 0, 0, 0],
            "askVol": [0, 0, 0, 0, 0],
        }
        passed = evaluate_board_gate(candidate, basis, good_quote, 11.00, dt.datetime(2026, 10, 9, 10, 0))
        self.assertTrue(passed["passed"], passed)

        shallow_quote = dict(good_quote)
        shallow_quote["bidVol"] = []
        failed = evaluate_board_gate(candidate, basis, shallow_quote, 11.00, dt.datetime(2026, 10, 9, 10, 0))
        self.assertFalse(failed["passed"])
        self.assertIn("五档买盘缺失，无法验证资金承接", failed["failures"])

    def test_open_board_is_rejected(self):
        basis = build_effective_theme_basis([self._good_result()])
        candidate = build_candidate_pool(basis)["000001.SZ"]
        quote = {
            "lastPrice": 10.70,
            "lastClose": 10.00,
            "open": 10.20,
            "high": 11.00,
            "low": 10.10,
            "amount": 220000000,
            "bidPrice": [10.70],
            "bidVol": [3000000],
            "askPrice": [10.71],
            "askVol": [100000],
        }
        failed = evaluate_board_gate(candidate, basis, quote, 11.00, dt.datetime(2026, 10, 9, 10, 0))
        self.assertFalse(failed["passed"])
        self.assertIn("未封住涨停，拒绝追逐非板", failed["failures"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
