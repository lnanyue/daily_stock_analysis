# -*- coding: utf-8 -*-
"""Regression tests for shared data-processing helpers."""

import unittest

from src.utils.data_processing import extract_json_from_text


class TestDataProcessing(unittest.TestCase):
    def test_extract_json_from_text_reads_markdown_fence(self):
        text = """
        分析如下：

        ```json
        {"stock_name":"贵州茅台","sentiment_score":72}
        ```
        """

        parsed = extract_json_from_text(text)

        self.assertEqual(parsed["stock_name"], "贵州茅台")
        self.assertEqual(parsed["sentiment_score"], 72)


if __name__ == "__main__":
    unittest.main()
