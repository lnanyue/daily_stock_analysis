# -*- coding: utf-8 -*-
"""
示例 Fetcher 插件 — 展示如何编写自定义数据源插件
"""
import pandas as pd
from data_provider.base import BaseFetcher


class ExampleFetcher(BaseFetcher):
    name = "example_api"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise NotImplementedError("ExampleFetcher._fetch_raw_data 未实现")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        df = df.copy()
        df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        return df


def register(config: dict) -> ExampleFetcher:
    return ExampleFetcher(**config)
