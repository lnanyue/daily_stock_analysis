# -*- coding: utf-8 -*-
"""
PluginContext — 提供给策略插件的内部服务访问
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Config
    from src.storage import Database
    from src.search_service import SearchService
    from data_provider.base import DataFetcherManager


@dataclass
class PluginContext:
    """策略插件可访问的内部服务引用"""
    config: "Config"
    db: "Database"
    search_service: "SearchService"
    fetcher_manager: "DataFetcherManager"
