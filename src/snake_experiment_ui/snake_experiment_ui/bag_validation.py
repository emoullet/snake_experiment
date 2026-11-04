"""Read-only bag validation with an isolated, bounded native reader process."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml


READ_TIMEOUT_SEC = 120


def validation_result(status="not_run", files=None, counts=None, code=None, message=None):
    """Return a fresh JSON-compatible validation diagnostic."""
    return {
        "status": status,
        "files": list(files or []),
        "message_counts": counts,
        "error": {"code": code, "message": message} if code else None,
    }


class BagValidationError(RuntimeError):
    """A validation failure with a stable diagnostic code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _counts(value):
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not name
        or type(count) is not int or count < 0
        for name, count in value.items()
    ):
        raise ValueError("Message counts must map topic names to non-negative integers.")
    return value


def _metadata(document):
    if not isinstance(document, dict):
        raise ValueError("Metadata must be a mapping.")
    info = document.get("rosbag2_bagfile_information")
    if not isinstance(info, dict):
        raise ValueError("Missing rosbag2_bagfile_information mapping.")
    storage = info.get("storage_identifier")
    if storage not in ("mcap", "sqlite3"):
        raise ValueError("Unsupported or missing storage_identifier.")
    files = info.get("relative_file_paths")
    if (
        not isinstance(files, list) or not files
        or any(
            not isinstance(name, str) or not name or "\0" in name or Path(name).is_absolute()
            or ".." in Path(name).parts or name == "."
            for name in files
        )
        or len({str(Path(name)) for name in files}) != len(files)
    ):
        raise ValueError("relative_file_paths must contain unique relative bag file paths.")
    topics = info.get("topics_with_message_count")
    if not isinstance(topics, list):
        raise ValueError("topics_with_message_count must be a list.")
    counts = {}
    for item in topics:
        if not isinstance(item, dict) or not isinstance(item.get("topic_metadata"), dict):
            raise ValueError("Malformed topic metadata.")
        name = item["topic_metadata"].get("name")
        count = item.get("message_count")
        _counts({name: count})
        if name in counts:
            raise ValueError(f"Duplicate topic in metadata: {name}")
        counts[name] = count
    total = info.get("message_count")
    if type(total) is not int or total < 0 or total != sum(counts.values()):
        raise ValueError("Total message_count must equal the sum of topic counts.")
    return storage, files, counts, total


def _read_counts(output, storage):
    """Run the reader in a separate process; never accept partial results."""
    with tempfile.TemporaryDirectory(prefix="snake-bag-validation-") as directory:
        result_path = Path(directory) / "result.json"
        with (Path(directory) / "reader.log").open("w+b") as log:
            try:
                process = subprocess.run(
                    [sys.executable, "-m", "snake_experiment_ui.bag_validation",
                     str(output), storage, str(result_path)],
                    stdout=log, stderr=log, timeout=READ_TIMEOUT_SEC, check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise BagValidationError(
                    "read_timeout", f"Bag reading exceeded {READ_TIMEOUT_SEC} seconds."
                ) from error
            except OSError as error:
                raise BagValidationError("reader_unavailable", str(error)) from error
            if process.returncode != 0:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 4096))
                detail = log.read().decode("utf-8", errors="replace").strip()
                raise BagValidationError(
                    "read_error", f"Bag reader exited with status {process.returncode}: {detail}"
                )
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError("Reader result must be a mapping.")
            error = result.get("error")
            if error is not None:
                if (
                    not isinstance(error, dict)
                    or error.get("code") not in ("reader_unavailable", "read_error")
                    or not isinstance(error.get("message"), str)
                ):
                    raise ValueError("Malformed reader error.")
                raise BagValidationError(error["code"], error["message"])
            return _counts(result["message_counts"])
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            raise BagValidationError("read_error", f"Invalid reader result: {error}") from error


def inspect_bag(output, storage):
    """Validate metadata, files and the complete message stream without modification."""
    files, expected, actual = [], {}, None
    metadata_error = None
    try:
        try:
            if output is None:
                raise ValueError("No rosbag output path is available.")
            output = Path(output).resolve()
            document = yaml.safe_load((output / "metadata.yaml").read_text(encoding="utf-8"))
            storage, files, expected, total = _metadata(document)
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as error:
            metadata_error = f"Unable to read rosbag metadata: {error}"
            raise BagValidationError("invalid_metadata", metadata_error) from error
        for name in files:
            path = (output / name).resolve()
            if not path.is_relative_to(output):
                raise BagValidationError("invalid_file", f"Bag file escapes output folder: {name}")
            if not path.exists():
                raise BagValidationError("missing_file", f"Bag file does not exist: {name}")
            if not path.is_file() or path.stat().st_size == 0:
                raise BagValidationError("invalid_file", f"Bag file is not regular or is empty: {name}")
        actual = _read_counts(output, storage)
        if sum(actual.values()) != total or any(
            actual.get(topic, 0) != expected.get(topic, 0)
            for topic in set(actual) | set(expected)
        ):
            raise BagValidationError(
                "count_mismatch", "Read message counts differ from rosbag metadata."
            )
        diagnostic = validation_result("passed", files, actual)
    except BagValidationError as error:
        diagnostic = validation_result("failed", files, actual, error.code, str(error))
    except (OSError, RuntimeError) as error:
        diagnostic = validation_result("failed", files, actual, "invalid_file", str(error))
    return {
        "storage": storage, "message_counts": expected,
        "metadata_error": metadata_error, "data_validation": diagnostic,
    }


def _worker(output, storage):
    try:
        import rosbag2_py
    except (ImportError, OSError) as error:
        return {"error": {"code": "reader_unavailable", "message": str(error)}}
    try:
        if storage not in rosbag2_py.get_registered_readers():
            return {"error": {"code": "reader_unavailable",
                              "message": f"No ROS bag reader plugin for {storage}."}}
        reader = rosbag2_py.SequentialReader()
        # Open the bag directory so the native reader traverses split files,
        # including SQLite files whose internal metadata references siblings.
        reader.open(
            rosbag2_py.StorageOptions(uri=output, storage_id=storage),
            rosbag2_py.ConverterOptions("", ""),
        )
        counts = Counter()
        while reader.has_next():
            topic, _data, _timestamp = reader.read_next()
            counts[topic] += 1
        return {"message_counts": dict(counts), "error": None}
    except Exception as error:
        return {"error": {"code": "read_error", "message": str(error)}}


if __name__ == "__main__":
    Path(sys.argv[3]).write_text(
        json.dumps(_worker(sys.argv[1], sys.argv[2])), encoding="utf-8"
    )
