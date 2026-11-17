"""Filter the high-frequency status poll from Uvicorn access logs."""

import logging


class FinalizationAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access records carry client, method, path, HTTP version and status.
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _, method, path, _, status = args
        return not (
            method == "GET"
            and isinstance(path, str)
            and path.split("?", 1)[0] == "/api/experiment/finalization"
            and isinstance(status, int)
            and 200 <= status < 300
        )
