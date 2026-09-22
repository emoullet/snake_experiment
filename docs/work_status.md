# Development work status

## Active lot

- Feature: Cartesian Go-to controls for Panels B, F, and G
- Branch: `codex/go-to-pose`
- Base: validated Lot 7 implementation plus updated dependencies
- Status: implemented; 95 tests and isolated ROS 2 validation pass
- Physical robot motion: not authorised or executed

## Validation

- Python/unit/API suite: 95 tests passed.
- Isolated `colcon build --symlink-install`: passed.
- Isolated installed-package `colcon test`: 95 tests passed.
- Installed ROS 2 launch: the node and Uvicorn server reached ready state in
  simulation mode on port 18083; the stack and robot were not started.
- The sandbox denies DDS network socket discovery, so the launch emitted
  `getifaddrs`/UDP permission warnings; no stack or robot motion was requested.
- Hardware validation remains to be performed by the researcher.

## Decision register

| Decision | Author | Status | Rationale | Affected lots |
| --- | --- | --- | --- | --- |
| Use 5 mm and 5 degree development thresholds for start and success checks | Researcher | Approved for development | Enables technical validation without claiming scientific approval | 6, 7 |
| Require 0.5 seconds continuously inside both success thresholds | Researcher | Approved | Avoids transient false success | 6, 7 |
| Treat `target_out_i` as trial start and `target_j` as arrival success | Researcher | Approved | Matches the calibration semantics and approved workflow | 6, 7 |
| Use `/pose_target` for confirmed Cartesian Go-to motion | Researcher | Approved | The updated Cartesian manager provides the required dynamic target behavior | B, 6, 7 |
| Map Panel B to `target_1`–`target_3` and `starting_point` | Researcher | Approved | System check-up validates the global calibration before participant enrolment | B |
| Map Panel F/G target buttons to `target_out_1`–`target_out_3` | Researcher | Approved | These are the calibrated trial starting poses | 6, 7 |
| Suspend and restore the current mapper around Go-to motion | Researcher | Approved | Prevents joystick commands from competing with the Cartesian target behavior | 6, 7 |
| Require confirmation, expose Stop, and cancel after 30 seconds | Researcher | Approved | Keeps each motion explicit and bounded | B, 6, 7 |
| Cancel active Go-to when the last browser disconnects | Researcher | Approved | Avoids unattended Cartesian motion | B, 6, 7 |
| Manual stop invalidates an attempt; incidents alone do not | Researcher | Approved | Separates acquisition validity from informational annotations | 6, 7 |
| Invalid attempts require retry or explicit continuation with deviation | Researcher | Approved | Preserves operator control and protocol traceability | 6, 7 |
| Number official recording trials 1–30 independently in each mode block | Researcher | Approved | Block folders already separate both conditions and avoid ambiguity | 7 |
| End Lot 7 with the existing six-block completion behavior | Researcher | Approved | Pause, questionnaires, and advanced session closure remain future work | 7 |

## Repository note

The parent development instructions reference `docs/development_workflow.md`
and `protocol/session.md`, but those files were not present when Lot 6 opened.
The researcher-approved Lot 7 specification in the task conversation is the
implementation authority for this branch.
