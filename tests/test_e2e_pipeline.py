"""End-to-end pipeline tests with VCR-recorded HTTP interactions."""

import logging
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.network

logger = logging.getLogger(__name__)


class TestFullPipeline:
    """Test full analysis pipeline with recorded HTTP."""

    @pytest.fixture
    def stocks_config(self):
        """Load test stock config."""
        import yaml

        with open(FIXTURES_DIR / "stocks_test.yaml") as f:
            return yaml.safe_load(f)

    @pytest.fixture
    def fetcher_manager(self):
        from data_provider import DataFetcherManager

        return DataFetcherManager()

    @pytest.mark.vcr
    @pytest.mark.asyncio
    async def test_kline_fetch(self, fetcher_manager):
        """Verify k-line data can be fetched from network."""
        df, source = await fetcher_manager.get_daily_data("600519", days=30)
        assert df is not None
        assert not df.empty
        assert len(df) > 0
        close_col = "close" if "close" in df.columns else "收盘"
        assert close_col in df.columns
        logger.info("K-line fetched from %s: %d rows", source, len(df))

    @pytest.mark.vcr
    @pytest.mark.asyncio
    async def test_full_analysis_flow(self, stocks_config, fetcher_manager):
        """Verify full collection pipeline produces structured output."""
        from src.core.pipeline_data_collector import StockDataCollector
        from src.search_service import SearchService
        from src.stock_analyzer import StockTrendAnalyzer
        from src.analyzer.core import GeminiAnalyzer
        from src.config import get_config

        config = get_config()
        search = SearchService()
        trend_analyzer = StockTrendAnalyzer()
        analyzer = GeminiAnalyzer(config=config)

        collector = StockDataCollector(
            config=config,
            fetcher_manager=fetcher_manager,
            search_service=search,
            analyzer=analyzer,
            trend_analyzer=trend_analyzer,
        )

        code = stocks_config["stocks"][0]["code"]
        result = await collector.collect(code)

        # Verify key data fields are populated
        assert result.realtime_quote is not None
        assert result.trend_result is not None
        assert result.final_news is not None
        assert len(result.final_news) > 0

    @pytest.mark.vcr
    def test_search_stock_news_with_extraction(self):
        """Verify search tool returns full-text extraction results."""
        from src.agent.tools.search_tools import _handle_search_stock_news

        result = _handle_search_stock_news("600519", "贵州茅台")
        assert result.get("success", False)
        assert len(result.get("results", [])) > 0

        # Verify extraction was attempted on first result
        first_result = result["results"][0]
        assert "extracted" in first_result
        assert "full_text_snippet" in first_result

        # Verify llm_analysis field is present (may be None if API not configured)
        assert "llm_analysis" in result

    @pytest.mark.vcr
    @pytest.mark.asyncio
    async def test_market_review(self):
        """Verify market review generates a report covering configured sectors."""
        from src.config import get_config
        from src.core.market_review import run_market_review

        config = get_config()

        # Create a no-op notifier that does not send notifications
        class _NoopNotifier:
            async def send_notification(self, title="", content="", msg_type="market_review"):
                return True
            async def send_market_review(self, content: str):
                logger.info("Market review length: %d chars", len(content) if content else 0)
                return True

        notifier = _NoopNotifier()
        report = await run_market_review(
            notifier=notifier,
            send_notification=False,
            merge_notification=True,
        )

        assert report is not None
        assert len(report) > 100
        logger.info("Market review report generated: %d chars", len(report))
