# -*- coding: utf-8 -*-
"""
数据获取相关的异常定义
"""

class DataFetchError(Exception):
    """数据获取异常基类"""
    pass


class RateLimitError(DataFetchError):
    """API 速率限制异常"""
    pass


class InsufficientQuotaError(DataFetchError):
    """积分不足或配额超限异常"""
    pass


class DataSourceUnavailableError(DataFetchError):
    """数据源不可用异常"""
    pass
