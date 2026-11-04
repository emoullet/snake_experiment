import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from snake_experiment_ui import bag_validation
from snake_experiment_ui.rosbag_manager import RosbagError, RosbagManager


REAL_READ_COUNTS = bag_validation._read_counts
try:
    import rosbag2_py
except ImportError:
    rosbag2_py = None


def write_metadata(output, counts=None, files=None):
    output.mkdir(exist_ok=True)
    counts = counts if counts is not None else {"/joy": 3, "/ee_pose": 2, "/joint_states": 4}
    files = files if files is not None else ["bag_0.mcap"]
    info = {
        "storage_identifier": "mcap", "relative_file_paths": files,
        "message_count": sum(counts.values()),
        "topics_with_message_count": [
            {"topic_metadata": {"name": topic}, "message_count": count}
            for topic, count in counts.items()
        ],
    }
    (output / "metadata.yaml").write_text(yaml.safe_dump({"rosbag2_bagfile_information": info}))
    return info


class FakeProcess:
    def __init__(self, command, start_new_session):
        self.command = command
        self.start_new_session = start_new_session
        self.pid = 4242
        self.return_code = None

    def poll(self):
        return self.return_code

    def wait(self, timeout):
        self.return_code = 0
        return 0


class RosbagManagerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.names = []
        self.processes = []
        def read_counts(output, storage):
            info = yaml.safe_load((output / "metadata.yaml").read_text())["rosbag2_bagfile_information"]
            return {item["topic_metadata"]["name"]: item["message_count"]
                    for item in info["topics_with_message_count"]}
        patcher = patch.object(bag_validation, "_read_counts", side_effect=read_counts)
        self.reader = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def create_process(self, command, start_new_session):
        process = FakeProcess(command, start_new_session)
        self.processes.append(process)
        self.names.append("/snake_experiment_discovery_recorder")
        output = Path(command[command.index("--output") + 1])
        write_metadata(output)
        (output / "bag_0.mcap").write_bytes(b"Mocked reader input")
        return process

    @patch("snake_experiment_ui.rosbag_manager.os.killpg")
    def test_record_command_and_valid_metadata(self, killpg):
        killpg.side_effect = lambda *_: self.names.clear()
        manager = RosbagManager(
            node_names=lambda: self.names,
            popen_factory=self.create_process,
            sleep=lambda _: None,
        )
        output = self.root / "rosbag_001"
        manager.start(output, ["/joy", "/ee_pose", "/joint_states"])
        result = manager.stop()
        self.assertTrue(result["valid"])
        self.assertEqual(result["message_counts"]["/joy"], 3)
        self.assertIn("--storage", self.processes[0].command)
        self.assertIn("mcap", self.processes[0].command)
        killpg.assert_called_once()

    def test_external_recorder_and_existing_output_are_rejected(self):
        manager = RosbagManager(
            node_names=lambda: ["/snake_experiment_discovery_recorder"]
        )
        with self.assertRaisesRegex(RosbagError, "outside this interface"):
            manager.start(self.root / "rosbag_001", ["/joy"])
        manager = RosbagManager(node_names=lambda: [])
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(RosbagError, "already exists"):
            manager.start(existing, ["/joy"])

    def test_missing_topic_makes_segment_invalid(self):
        output = self.root / "rosbag_001"
        write_metadata(output, {"/joy": 1})
        (output / "bag_0.mcap").write_bytes(b"Mocked reader input")
        result = RosbagManager.inspect(
            output, ["/joy", "/ee_pose", "/joint_states"], "mcap"
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["missing_topics"], ["/ee_pose", "/joint_states"])


    def test_optional_topic_absence_is_diagnostic_only(self):
        self.create_process([
            "ros2", "--output", str(self.root / "bag")
        ], True)
        topics = ["/joy", "/ee_pose", "/joint_states", "/mode_request"]
        result = RosbagManager.inspect(self.root / "bag", topics, "mcap", ["/mode_request"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["missing_topics"], ["/mode_request"])
        self.assertEqual(result["message_counts"]["/mode_request"], 0)
        self.assertEqual(result["missing_required_topics"], [])
        self.assertEqual(result["required_topics"], topics[:-1])
        strict = RosbagManager.inspect(self.root / "bag", topics, "mcap")
        self.assertFalse(strict["valid"])
        self.assertEqual(strict["missing_required_topics"], ["/mode_request"])
        required_missing = RosbagManager.inspect(
            self.root / "bag", topics + ["/required"], "mcap", ["/mode_request"]
        )
        self.assertFalse(required_missing["valid"])
        self.assertEqual(required_missing["missing_required_topics"], ["/required"])

    def test_optional_topics_do_not_allow_missing_or_unreadable_metadata(self):
        output = self.root / "bag"
        output.mkdir()
        for contents in (None, "[invalid yaml"):
            with self.subTest(contents=contents):
                if contents is not None:
                    (output / "metadata.yaml").write_text(contents)
                result = RosbagManager.inspect(output, ["/mode_request"], "mcap", ["/mode_request"])
                self.assertFalse(result["valid"])
                self.assertIsNotNone(result["metadata_error"])
                self.assertEqual(result["missing_required_topics"], [])

    @patch("snake_experiment_ui.rosbag_manager.os.killpg")
    def test_optional_policy_reaches_every_finalisation_path(self, killpg):
        killpg.side_effect = lambda *_: self.names.clear()
        manager = RosbagManager(node_names=lambda: self.names, popen_factory=self.create_process)
        topics = ["/joy", "/mode_request"]
        for action in ("stop", "shutdown", "exited"):
            with self.subTest(action=action):
                output = self.root / action
                manager.start(output, topics, optional_topics=["/mode_request"])
                self.assertIn("/mode_request", self.processes[-1].command)
                if action == "exited":
                    self.processes[-1].return_code = 1
                    self.names.clear()
                    result = manager.stop()
                else:
                    result = getattr(manager, action)()
                self.assertTrue(result["valid"])
                self.assertEqual(result["optional_topics"], ["/mode_request"])
        manager.start(self.root / "strict", topics)
        self.assertFalse(manager.stop()["valid"])

    def test_invalid_optional_topic_policy_is_rejected(self):
        for optional in (["/absent"], ["/joy", "/joy"], "/joy", [None], [[]]):
            with self.subTest(optional=optional):
                with self.assertRaises(RosbagError):
                    RosbagManager.inspect(self.root, ["/joy"], "mcap", optional)
                manager = RosbagManager(node_names=lambda: [], popen_factory=self.create_process)
                with self.assertRaises(RosbagError):
                    manager.start(self.root / "bad", ["/joy"], optional_topics=optional)
        self.assertFalse(self.processes)


    def test_files_must_exist_be_nonempty_and_stay_inside_bag(self):
        output = self.root / "bag"
        write_metadata(output)
        path = output / "bag_0.mcap"
        result = RosbagManager.inspect(output, ["/joy"], "mcap")
        self.assertEqual(result["data_validation"]["error"]["code"], "missing_file")
        self.assertIsNone(result["metadata_error"])
        for kind in ("empty", "directory", "symlink"):
            with self.subTest(kind=kind):
                if kind == "empty":
                    path.touch()
                elif kind == "directory":
                    path.mkdir()
                else:
                    (self.root / "outside.mcap").write_bytes(b"outside")
                    path.symlink_to(self.root / "outside.mcap")
                result = RosbagManager.inspect(output, ["/joy"], "mcap")
                self.assertFalse(result["valid"])
                self.assertEqual(result["data_validation"]["error"]["code"], "invalid_file")
                if kind == "directory":
                    path.rmdir()
                else:
                    path.unlink()
        self.reader.assert_not_called()

    def test_malformed_metadata_is_rejected_without_reader(self):
        output = self.root / "bag"
        valid = write_metadata(output)
        documents = [None, [], {"rosbag2_bagfile_information": []}]
        for key, values in {
            "storage_identifier": [None, "unknown"],
            "relative_file_paths": [[], ["../escape"], ["/absolute"], ["a", "./a"], [None], ["bad\0name"], "."],
            "message_count": [-1, True, 1.5, 100],
            "topics_with_message_count": [None, [None], [{"topic_metadata": None}]],
        }.items():
            for value in values:
                info = dict(valid, **{key: value})
                documents.append({"rosbag2_bagfile_information": info})
        for count in (-1, True, 1.5, "3", None):
            info = dict(valid, message_count=0, topics_with_message_count=[
                {"topic_metadata": {"name": "/joy"}, "message_count": count}
            ])
            documents.append({"rosbag2_bagfile_information": info})
        duplicate = valid["topics_with_message_count"] * 2
        documents.append({"rosbag2_bagfile_information": dict(valid, topics_with_message_count=duplicate)})
        for document in documents:
            with self.subTest(document=document):
                (output / "metadata.yaml").write_text(yaml.safe_dump(document))
                result = RosbagManager.inspect(output, ["/joy"], "mcap")
                self.assertFalse(result["valid"])
                self.assertEqual(result["data_validation"]["error"]["code"], "invalid_metadata")
                self.assertIsNotNone(result["metadata_error"])
        self.reader.assert_not_called()

    def test_count_mismatch_including_optional_and_unexpected_topics(self):
        output = self.root / "bag"
        write_metadata(output, {"/joy": 1, "/mode_request": 1})
        (output / "bag_0.mcap").write_bytes(b"Mocked reader input")
        self.reader.side_effect = None
        for counts in ({"/joy": 1}, {"/joy": 2}, {"/joy": 1, "/mode_request": 1, "/extra": 1}):
            with self.subTest(counts=counts):
                self.reader.return_value = counts
                result = RosbagManager.inspect(output, ["/joy", "/mode_request"], "mcap", ["/mode_request"])
                self.assertFalse(result["valid"])
                self.assertIsNone(result["metadata_error"])
                self.assertEqual(result["message_counts"]["/mode_request"], 1)
                self.assertEqual(result["data_validation"]["message_counts"], counts)
                self.assertEqual(result["data_validation"]["error"]["code"], "count_mismatch")

    def test_complete_actual_counts_and_all_announced_files_are_preserved(self):
        output = self.root / "bag"
        write_metadata(output, {"/joy": 1, "/extra": 2, "/mode_request": 0}, ["a.mcap", "b.mcap"])
        for name in ("a.mcap", "b.mcap"):
            (output / name).write_bytes(b"Mocked reader input")
        result = RosbagManager.inspect(output, ["/joy", "/mode_request"], "mcap", ["/mode_request"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["data_validation"]["files"], ["a.mcap", "b.mcap"])
        self.assertEqual(result["data_validation"]["message_counts"]["/extra"], 2)

    def test_reader_failures_keep_partial_counts_unknown(self):
        output = self.root / "bag"
        write_metadata(output)
        (output / "bag_0.mcap").write_bytes(b"Mocked reader input")
        for code in ("read_timeout", "reader_unavailable", "read_error"):
            with self.subTest(code=code):
                self.reader.side_effect = bag_validation.BagValidationError(code, "test failure")
                result = RosbagManager.inspect(output, ["/joy"], "mcap")
                self.assertFalse(result["valid"])
                self.assertIsNone(result["metadata_error"])
                self.assertIsNone(result["data_validation"]["message_counts"])
                self.assertEqual(result["data_validation"]["error"]["code"], code)

    def test_worker_reports_missing_ros_reader(self):
        with patch.dict(sys.modules, {"rosbag2_py": None}):
            result = bag_validation._worker(str(self.root), "mcap")
        self.assertEqual(result["error"]["code"], "reader_unavailable")
        self.assertNotIn("message_counts", result)
        plugin_missing = SimpleNamespace(get_registered_readers=lambda: set())
        with patch.dict(sys.modules, {"rosbag2_py": plugin_missing}):
            result = bag_validation._worker(str(self.root), "mcap")
        self.assertEqual(result["error"]["code"], "reader_unavailable")

    def test_real_subprocess_timeout(self):
        real_run = subprocess.run
        def sleeping_reader(command, **kwargs):
            return real_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        with patch.object(bag_validation, "READ_TIMEOUT_SEC", 0.1):
            with patch.object(bag_validation.subprocess, "run", side_effect=sleeping_reader):
                with self.assertRaises(bag_validation.BagValidationError) as caught:
                    REAL_READ_COUNTS(self.root, "mcap")
        self.assertEqual(caught.exception.code, "read_timeout")

    def test_subprocess_protocol_and_failures(self):
        def run(command, **kwargs):
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(kwargs["timeout"], 120)
            Path(command[-1]).write_text(json.dumps({"message_counts": {"/joy": 1}, "error": None}))
            return SimpleNamespace(returncode=0)
        with patch.object(bag_validation.subprocess, "run", side_effect=run):
            self.assertEqual(REAL_READ_COUNTS(self.root, "mcap"), {"/joy": 1})
        failures = [
            (subprocess.TimeoutExpired("reader", 120), "read_timeout"),
            (OSError("missing python"), "reader_unavailable"),
        ]
        for error, code in failures:
            with patch.object(bag_validation.subprocess, "run", side_effect=error):
                with self.assertRaises(bag_validation.BagValidationError) as caught:
                    REAL_READ_COUNTS(self.root, "mcap")
                self.assertEqual(caught.exception.code, code)
        for returncode in (0, -11):
            with patch.object(bag_validation.subprocess, "run", return_value=SimpleNamespace(returncode=returncode)):
                with self.assertRaises(bag_validation.BagValidationError) as caught:
                    REAL_READ_COUNTS(self.root, "mcap")
                self.assertEqual(caught.exception.code, "read_error")
        def unavailable(command, **kwargs):
            Path(command[-1]).write_text(json.dumps({"error": {"code": "reader_unavailable", "message": "no rosbag2_py"}}))
            return SimpleNamespace(returncode=0)
        with patch.object(bag_validation.subprocess, "run", side_effect=unavailable):
            with self.assertRaises(bag_validation.BagValidationError) as caught:
                REAL_READ_COUNTS(self.root, "mcap")
            self.assertEqual(caught.exception.code, "reader_unavailable")


@unittest.skipIf(rosbag2_py is None, "Source ROS to run real bag reader integration tests")
class RealBagValidationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def create_bag(self, storage="mcap", split=False):
        from rclpy.serialization import serialize_message
        from std_msgs.msg import String
        output = self.root / "bag"
        writer = rosbag2_py.SequentialWriter()
        writer.open(rosbag2_py.StorageOptions(uri=str(output), storage_id=storage),
                    rosbag2_py.ConverterOptions("", ""))
        writer.create_topic(rosbag2_py.TopicMetadata(0, "/joy", "std_msgs/msg/String", "cdr"))
        for i in range(4):
            if split and i == 2:
                writer.split_bagfile()
            writer.write("/joy", serialize_message(String(data=f"sample {i}")), 1000000000 + i)
        writer.close()
        return output

    def test_real_mcap_and_sqlite_bags(self):
        for storage in ("mcap", "sqlite3"):
            for split in (False, True):
                with self.subTest(storage=storage, split=split):
                    self.root = Path(self.directory.name) / f"{storage}-{split}"
                    self.root.mkdir()
                    output = self.create_bag(storage, split)
                    result = RosbagManager.inspect(output, ["/joy", "/mode_request"], storage, ["/mode_request"])
                    self.assertTrue(result["valid"], result)
                    self.assertEqual(result["data_validation"]["message_counts"], {"/joy": 4})
                    self.assertEqual(len(result["data_validation"]["files"]), 2 if split else 1)
                    self.assertEqual(result["missing_topics"], ["/mode_request"])

    def test_truncated_mcap_is_not_valid(self):
        output = self.create_bag()
        path = next(output.glob("*.mcap"))
        path.write_bytes(path.read_bytes()[:32])
        result = RosbagManager.inspect(output, ["/joy"], "mcap")
        self.assertFalse(result["valid"])
        self.assertIn(result["data_validation"]["error"]["code"], ("read_error", "count_mismatch"))

    def test_corrupt_second_file_is_read_and_rejected(self):
        output = self.create_bag(split=True)
        files = sorted(output.glob("*.mcap"))
        files[-1].write_bytes(files[-1].read_bytes()[:32])
        result = RosbagManager.inspect(output, ["/joy"], "mcap")
        self.assertFalse(result["valid"])
        self.assertIn(result["data_validation"]["error"]["code"], ("read_error", "count_mismatch"))

    def test_successful_validation_does_not_change_bag_files(self):
        for storage in ("mcap", "sqlite3"):
            with self.subTest(storage=storage):
                self.root = Path(self.directory.name) / storage
                self.root.mkdir()
                output = self.create_bag(storage, split=True)
                before = {path.name: path.read_bytes() for path in output.iterdir()}
                self.assertTrue(RosbagManager.inspect(output, ["/joy"], storage)["valid"])
                self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_missing_second_file_is_not_valid(self):
        output = self.create_bag(split=True)
        files = sorted(output.glob("*.mcap"))
        files[-1].unlink()
        result = RosbagManager.inspect(output, ["/joy"], "mcap")
        self.assertFalse(result["valid"])
        self.assertEqual(result["data_validation"]["error"]["code"], "missing_file")

    def test_real_bag_counter_mismatch_is_not_valid(self):
        output = self.create_bag()
        path = output / "metadata.yaml"
        document = yaml.safe_load(path.read_text())
        info = document["rosbag2_bagfile_information"]
        info["message_count"] += 1
        info["topics_with_message_count"][0]["message_count"] += 1
        path.write_text(yaml.safe_dump(document))
        result = RosbagManager.inspect(output, ["/joy"], "mcap")
        self.assertFalse(result["valid"])
        self.assertEqual(result["data_validation"]["error"]["code"], "count_mismatch")
        self.assertEqual(result["data_validation"]["message_counts"], {"/joy": 4})


if __name__ == "__main__":
    unittest.main()
