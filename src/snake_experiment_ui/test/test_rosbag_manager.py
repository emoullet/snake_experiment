from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from snake_experiment_ui.rosbag_manager import RosbagError, RosbagManager


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

    def tearDown(self):
        self.temporary_directory.cleanup()

    def create_process(self, command, start_new_session):
        process = FakeProcess(command, start_new_session)
        self.processes.append(process)
        self.names.append("/snake_experiment_discovery_recorder")
        output = Path(command[command.index("--output") + 1])
        output.mkdir()
        (output / "metadata.yaml").write_text(
            """rosbag2_bagfile_information:
  storage_identifier: mcap
  topics_with_message_count:
    - topic_metadata: {name: /joy}
      message_count: 3
    - topic_metadata: {name: /ee_pose}
      message_count: 2
    - topic_metadata: {name: /joint_states}
      message_count: 4
""",
            encoding="utf-8",
        )
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
        output.mkdir()
        (output / "metadata.yaml").write_text(
            """rosbag2_bagfile_information:
  storage_identifier: mcap
  topics_with_message_count:
    - topic_metadata: {name: /joy}
      message_count: 1
""",
            encoding="utf-8",
        )
        result = RosbagManager.inspect(
            output, ["/joy", "/ee_pose", "/joint_states"], "mcap"
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["missing_topics"], ["/ee_pose", "/joint_states"])


if __name__ == "__main__":
    unittest.main()
