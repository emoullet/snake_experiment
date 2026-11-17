import logging
import unittest

from snake_experiment_ui.access_log import FinalizationAccessFilter


class FinalizationAccessFilterTest(unittest.TestCase):
    def test_only_successful_finalization_gets_are_hidden(self):
        access_filter = FinalizationAccessFilter()

        def allowed(method, path, status):
            record = logging.LogRecord(
                "uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                ("127.0.0.1:1234", method, path, "1.1", status), None,
            )
            return access_filter.filter(record)

        self.assertFalse(allowed("GET", "/api/experiment/finalization", 200))
        self.assertFalse(allowed("GET", "/api/experiment/finalization?x=1", 204))
        self.assertTrue(allowed("GET", "/api/experiment/finalization", 500))
        self.assertTrue(allowed("POST", "/api/experiment/finalization", 200))
        self.assertTrue(allowed("GET", "/api/state", 200))
        self.assertTrue(access_filter.filter(logging.LogRecord(
            "uvicorn.access", logging.INFO, "", 0, "other message", (), None,
        )))
