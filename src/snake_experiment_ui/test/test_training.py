import math
import unittest

from snake_experiment_ui.training import (
    TrainingError,
    build_trials,
    effective_thresholds,
    pose_error,
    validate_training_settings,
)


class TrainingHelpersTest(unittest.TestCase):
    def settings(self):
        return {
            "cycles": 2,
            "target_sequence": [1, 2, 3, 1],
            "trial_folder_pattern": (
                "{mode}_training_trial_{trial_id:03d}_{cycle:02d}_"
                "{target_start}_{target_end}"
            ),
            "attempt_pattern": "attempt_{attempt:03d}",
        }

    def pose(self, quaternion=None):
        return {
            "frame_id": "base_link",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "orientation": quaternion
            or {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        }

    def test_build_trials_uses_global_ids_and_cycles(self):
        settings = self.settings()
        validate_training_settings(settings)
        trials = build_trials("baseline", settings)
        self.assertEqual(len(trials), 6)
        self.assertEqual(trials[-1]["id"], 6)
        self.assertEqual(trials[-1]["cycle"], 2)
        self.assertEqual(trials[-1]["folder"], "baseline_training_trial_006_02_3_1")

    def test_threshold_units_convert_to_si(self):
        thresholds = effective_thresholds(
            {
                "provisional": True,
                "linear_mm": 5.0,
                "angular_deg": 5.0,
                "start_linear_mm": 5.0,
                "start_angular_deg": 5.0,
                "success_dwell_sec": 0.5,
                "pose_freshness_sec": 0.5,
            }
        )
        self.assertEqual(thresholds["linear_m"], 0.005)
        self.assertAlmostEqual(thresholds["angular_rad"], math.radians(5.0))

    def test_pose_error_handles_antipodal_quaternions(self):
        observed = self.pose({"x": 0.0, "y": 0.0, "z": 0.0, "w": -1.0})
        linear, angular = pose_error(observed, self.pose())
        self.assertEqual(linear, 0.0)
        self.assertEqual(angular, 0.0)

    def test_pose_error_rejects_frames_and_non_finite_values(self):
        observed = self.pose()
        observed["frame_id"] = "other"
        with self.assertRaisesRegex(TrainingError, "different reference frames"):
            pose_error(observed, self.pose())
        observed = self.pose()
        observed["position"]["x"] = float("nan")
        with self.assertRaisesRegex(TrainingError, "non-finite"):
            pose_error(observed, self.pose())
        observed = self.pose()
        observed["frame_id"] = ""
        with self.assertRaisesRegex(TrainingError, "require a reference frame"):
            pose_error(observed, self.pose())


if __name__ == "__main__":
    unittest.main()
