# -*- coding: utf-8 -*-
"""
统一插件注册表
"""
import logging
from typing import List, Dict, Any, Optional

from data_provider.base import BaseFetcher

from .config import ConfigLoader
from .loader import scan_and_register
from .strategy_base import AnalysisStrategy, AnalysisContext, StrategyResult
from .plugin_context import PluginContext

logger = logging.getLogger(__name__)

SEARCH_PATHS = ["plugins/", "plugins.local/"]


class PluginRegistry:
    """统一插件注册表

    职责:
    1. 扫描 plugins/fetchers/ 和 plugins/strategies/ 目录
    2. 解析 plugins.yaml 配置
    3. 实例化并注册启用的插件
    4. 提供按类型查询插件的接口
    """

    def __init__(self, config_path: str = "plugins.yaml"):
        self.config_path = config_path
        self._config_loader = ConfigLoader(config_path)
        self._fetcher_factories: Dict[str, callable] = {}
        self._strategy_factories: Dict[str, callable] = {}
        self._fetchers: List[BaseFetcher] = []
        self._strategies: List[AnalysisStrategy] = []
        self._plugin_ctx: Optional[PluginContext] = None

    def load(self, plugin_ctx: PluginContext) -> None:
        """完整加载流程"""
        self._plugin_ctx = plugin_ctx
        self._scan_fetchers()
        self._scan_strategies()
        self._instantiate_fetchers()
        self._instantiate_strategies()

    def _scan_fetchers(self) -> None:
        paths = [f"{p}fetchers" for p in SEARCH_PATHS]
        factories = scan_and_register(paths, register_func_name="register")
        for name, factory in factories:
            self._fetcher_factories[name] = factory
            logger.info("[PluginRegistry] 注册 Fetcher 插件: %s", name)

    def _scan_strategies(self) -> None:
        paths = [f"{p}strategies" for p in SEARCH_PATHS]
        factories = scan_and_register(paths, register_func_name="register")
        for name, factory in factories:
            self._strategy_factories[name] = factory
            logger.info("[PluginRegistry] 注册 Strategy 插件: %s", name)

    def _instantiate_fetchers(self) -> None:
        for fetcher_cfg in self._config_loader.fetchers:
            name = fetcher_cfg.get("name", "")
            module = fetcher_cfg.get("module", name)
            enabled = fetcher_cfg.get("enabled", True)

            if not enabled:
                logger.info("[PluginRegistry] Fetcher '%s' 未启用，跳过", name)
                continue

            factory = self._fetcher_factories.get(module) or self._fetcher_factories.get(name)
            if factory is None:
                logger.warning("[PluginRegistry] Fetcher '%s' 的模块 '%s' 未找到", name, module)
                continue

            try:
                config = fetcher_cfg.get("config", {})
                fetcher = factory(config)
                if "priority" in fetcher_cfg:
                    fetcher.priority = fetcher_cfg["priority"]
                self._fetchers.append(fetcher)
                logger.info("[PluginRegistry] 实例化 Fetcher: %s (priority=%s)", name, fetcher.priority)
            except Exception as exc:
                logger.error("[PluginRegistry] 实例化 Fetcher '%s' 失败: %s", name, exc)

    def _instantiate_strategies(self) -> None:
        if self._plugin_ctx is None:
            return

        for strategy_cfg in self._config_loader.strategies:
            name = strategy_cfg.get("name", "")
            module = strategy_cfg.get("module", name)
            enabled = strategy_cfg.get("enabled", True)

            if not enabled:
                logger.info("[PluginRegistry] Strategy '%s' 未启用，跳过", name)
                continue

            factory = self._strategy_factories.get(module) or self._strategy_factories.get(name)
            if factory is None:
                logger.warning("[PluginRegistry] Strategy '%s' 的模块 '%s' 未找到", name, module)
                continue

            try:
                config = strategy_cfg.get("config", {})
                strategy = factory(config, self._plugin_ctx)
                self._strategies.append(strategy)
                logger.info("[PluginRegistry] 实例化 Strategy: %s", name)
            except Exception as exc:
                logger.error("[PluginRegistry] 实例化 Strategy '%s' 失败: %s", name, exc)

    def get_enabled_fetchers(self) -> List[BaseFetcher]:
        """返回已实例化的 Fetcher 列表"""
        return list(self._fetchers)

    def get_enabled_strategies(self) -> List[AnalysisStrategy]:
        """返回已实例化的 Strategy 列表"""
        return list(self._strategies)

    def execute_strategies(self, analysis_ctx: AnalysisContext) -> List[StrategyResult]:
        """批量执行已启用的策略，单个失败不阻塞其他"""
        results = []
        for strategy in self._strategies:
            try:
                result = strategy.execute(analysis_ctx)
                results.append(result)
                logger.info("[PluginRegistry] Strategy '%s' 执行成功", strategy.name)
            except Exception as exc:
                logger.error("[PluginRegistry] Strategy '%s' 执行失败: %s", strategy.name, exc)
                error_result = StrategyResult(
                    name=strategy.name,
                    title=strategy.name,
                    summary=f"执行失败: {exc}",
                    error=str(exc),
                )
                results.append(error_result)
        return results
