from types import SimpleNamespace
import unittest

from snake_experiment_ui.calibration import (
    CalibrationError,
    CalibrationState,
    POSE_IDS,
)


def pose_message(frame="base_link", orientation=(0.0, 0.0, 0.0, 2.0)):
    return SimpleNamespace(
        header=SimpleNamespace(
            frame_id=frame,
            stamp=SimpleNamespace(sec=12, nanosec=345),
        ),
        pose=SimpleNamespace(
            position=SimpleNamespace(x=1.0, y=2.0, z=3.0),
            orientation=SimpleNamespace(
                x=orientation[0],
                y=orientation[1],
                z=orientation[2],
                w=orientation[3],
            ),
        ),
    )


class CalibrationStateTest(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.mode = "baseline"
        self.state = CalibrationState(
            max_pose_age_sec=1.0,
            mode_provider=lambda: self.mode,
            monotonic_clock=lambda: self.now,
            utc_clock=lambda: "2026-09-14T12:00:00Z",
        )

    def test_capture_requires_a_mode_and_a_fresh_pose(self):
        with self.assertRaisesRegex(CalibrationError, "No end-effector"):
            self.state.capture("target_1")
        self.state.update_live_pose(pose_message())
        self.now = 11.1
        with self.assertRaisesRegex(CalibrationError, "stale"):
            self.state.capture("target_1")
        self.now = 10.5
        self.mode = None
        with self.assertRaisesRegex(CalibrationError, "Activate"):
            self.state.capture("target_1")

    def test_capture_normalises_quaternion(self):
        self.state.update_live_pose(pose_message())
        record = self.state.capture("target_1")
        self.assertEqual(record["orientation"]["w"], 1.0)

    def test_snapshot_exposes_the_current_pose(self):
        self.state.update_live_pose(pose_message())

        stream = self.state.snapshot()["pose_stream"]

        self.assertTrue(stream["fresh"])
        self.assertEqual(stream["current_pose"]["frame_id"], "base_link")
        self.assertEqual(
            stream["current_pose"]["position"],
            {"x": 1.0, "y": 2.0, "z": 3.0},
        )
        self.assertEqual(stream["current_pose"]["orientation"]["w"], 2.0)
        self.assertEqual(stream["current_pose"]["stamp_nanosec"], 345)

    def test_invalid_pose_id_and_quaternion_are_rejected(self):
        self.state.update_live_pose(pose_message())
        with self.assertRaisesRegex(CalibrationError, "Unknown"):
            self.state.capture("target_4")
        self.state.update_live_pose(pose_message(orientation=(0.0, 0.0, 0.0, 0.0)))
        with self.assertRaisesRegex(CalibrationError, "quaternion"):
            self.state.capture("target_1")

    def test_all_seven_poses_are_required(self):
        self.state.update_live_pose(pose_message())
        for pose_id in POSE_IDS[:-1]:
            self.state.capture(pose_id)
        self.assertFalse(self.state.snapshot()["can_save"])
        with self.assertRaisesRegex(CalibrationError, "seven"):
            self.state.calibration_document()

        self.state.capture("starting_point")
        document = self.state.calibration_document()
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["reference_frame"], "base_link")
        self.assertEqual(tuple(document["poses"]), POSE_IDS)

    def test_mixed_frames_block_save(self):
        for index, pose_id in enumerate(POSE_IDS):
            frame = "map" if index == len(POSE_IDS) - 1 else "base_link"
            self.state.update_live_pose(pose_message(frame=frame))
            self.state.capture(pose_id)
        with self.assertRaisesRegex(CalibrationError, "same frame"):
            self.state.calibration_document()


if __name__ == "__main__":
    unittest.main()
