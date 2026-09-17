# Development work status

## Active lot

- Lot: 6 — Panel F training workflow
- Branch: `codex/lot-06-training`
- Base: local `codex/progressive-restart` at validated Lot 5 commit `b412ab8`
- Status: implemented; 80 tests and isolated `colcon` build pass
- Physical robot motion: not authorised or executed

## Validation

- Python/unit/API suite: 80 tests passed.
- Isolated `colcon build`: passed with `--symlink-install`.
- Installed ROS 2 launch: the node and Uvicorn server reached ready state in
  simulation mode on port 18081.
- The sandbox denies DDS network socket discovery, so the launch emitted
  `getifaddrs`/UDP permission warnings; no stack or robot motion was requested.
- Hardware validation remains to be performed by the researcher.

## Decision register

| Decision | Author | Status | Rationale | Affected lots |
| --- | --- | --- | --- | --- |
| Use 5 mm and 5 degree development thresholds for start and success checks | Researcher | Approved for development | Enables technical validation without claiming scientific approval | 6, 7 |
| Require 0.5 seconds continuously inside both success thresholds | Researcher | Approved | Avoids transient false success | 6, 7 |
| Treat `target_out_i` as trial start and `target_j` as arrival success | Researcher | Approved | Matches the calibration semantics and approved workflow | 6, 7 |
| Keep Go-to unavailable in Lot 6 | Researcher | Approved | No validated ROS pose-motion interface exists in the current dependencies | 6 |
| Manual stop invalidates an attempt; incidents alone do not | Researcher | Approved | Separates acquisition validity from informational annotations | 6, 7 |
| Invalid attempts require retry or explicit continuation with deviation | Researcher | Approved | Preserves operator control and protocol traceability | 6, 7 |

## Repository note

The parent development instructions reference `docs/development_workflow.md`
and `protocol/session.md`, but those files were not present when Lot 6 opened.
The researcher-approved Lot 6 specification in the task conversation is the
implementation authority for this branch.
