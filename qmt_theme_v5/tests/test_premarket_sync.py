# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import qmt_theme_board_v5 as strategy  # noqa: E402


def make_bars(closes, amount=200000000):
    return [{
        "open": value * 0.98,
        "high": value,
        "low": value * 0.96,
        "close": value,
        "amount": amount,
        "volume": 1000000,
    } for value in closes]


class FakeFrame(object):
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return list(self.rows)


class FakeQmtContext(object):
    def __init__(self):
        self.schedules = []
        self.members = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]
        self.bars = {
            "000001.SZ": make_bars([10.0, 11.0, 12.1, 13.31]),
            "000002.SZ": make_bars([10.0, 10.5, 11.55, 12.705]),
            "000003.SZ": make_bars([10.0, 10.1, 10.2, 10.65]),
            "000004.SZ": make_bars([10.0, 10.1, 10.2, 10.51]),
            "000005.SZ": make_bars([10.0, 10.1, 10.2, 10.31]),
        }

    def run_time(self, name, interval, at):
        self.schedules.append((name, interval, at))

    def get_stock_list_in_sector(self, sector):
        return self.members if sector == "测试概念" else []

    def get_instrumentdetail(self, stock):
        return {"InstrumentName": "测试股", "UpStopPrice": 11.0}

    def get_market_data_ex(self, fields, stocks, count, period, dividend_type, subscribe):
        self.assert_fields = fields
        return {stock: FakeFrame(self.bars[stock]) for stock in stocks}


class PremarketSyncIntegrationTests(unittest.TestCase):
    def test_sync_scan_override_and_effective_basis_are_generated(self):
        context = FakeQmtContext()
        old_home = os.environ.get("QMT_THEME_V5_HOME")
        try:
            with tempfile.TemporaryDirectory() as runtime:
                os.environ["QMT_THEME_V5_HOME"] = runtime
                strategy.init(context)
                catalog_path = os.path.join(runtime, "config", "theme_catalog.json")
                override_path = os.path.join(runtime, "config", "theme_overrides.json")
                with open(catalog_path, "w", encoding="utf-8") as handle:
                    json.dump({"themes": {"测试主线": {"sectors": ["测试概念"], "enabled": True}}}, handle)
                with open(override_path, "w", encoding="utf-8") as handle:
                    json.dump({"global": {"rules": {"max_active_themes": 1}}, "themes": {}}, handle)

                strategy.pre_market_sync(context)

                self.assertEqual(len(context.schedules), 3)
                self.assertEqual(strategy.g.synced_themes["测试主线"]["synced_count"], 5)
                self.assertEqual(strategy.g.effective_basis["tradable_themes"], ["测试主线"])
                self.assertIn("000001.SZ", strategy.g.candidate_pool)
                self.assertTrue(os.path.exists(os.path.join(runtime, "output", "effective_theme_basis_{0}.json".format(strategy._date_text()))))
        finally:
            if old_home is None:
                os.environ.pop("QMT_THEME_V5_HOME", None)
            else:
                os.environ["QMT_THEME_V5_HOME"] = old_home


if __name__ == "__main__":
    unittest.main(verbosity=2)
