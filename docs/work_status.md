# Development work status

## Active lot

- Lot: 7 — Panel G official recording workflow
- Branch: `codex/lot-07-recording`
- Base: validated Lot 6 commit `9cb5794`
- Status: implemented; 87 tests and isolated ROS 2 validation pass
- Physical robot motion: not authorised or executed

## Validation

- Python/unit/API suite: 87 tests passed.
- Isolated `colcon build --symlink-install`: passed.
- Isolated `colcon test`: 87 tests passed.
- Installed ROS 2 launch: the node and Uvicorn server reached ready state in
  simulation mode on port 18082.
- The sandbox denies DDS network socket discovery, so the launch emitted
  `getifaddrs`/UDP permission warnings; no stack or robot motion was requested.
- Hardware validation remains to be performed by the researcher.

## Decision register

| Decision | Author | Status | Rationale | Affected lots |
| --- | --- | --- | --- | --- |
| Use 5 mm and 5 degree development thresholds for start and success checks | Researcher | Approved for development | Enables technical validation without claiming scientific approval | 6, 7 |
| Require 0.5 seconds continuously inside both success thresholds | Researcher | Approved | Avoids transient false success | 6, 7 |
| Treat `target_out_i` as trial start and `target_j` as arrival success | Researcher | Approved | Matches the calibration semantics and approved workflow | 6, 7 |
| Keep Go-to unavailable in Lots 6 and 7 | Researcher | Approved | No validated ROS pose-motion interface exists in the current dependencies | 6, 7 |
| Manual stop invalidates an attempt; incidents alone do not | Researcher | Approved | Separates acquisition validity from informational annotations | 6, 7 |
| Invalid attempts require retry or explicit continuation with deviation | Researcher | Approved | Preserves operator control and protocol traceability | 6, 7 |
| Number official recording trials 1–30 independently in each mode block | Researcher | Approved | Block folders already separate both conditions and avoid ambiguity | 7 |
| End Lot 7 with the existing six-block completion behavior | Researcher | Approved | Pause, questionnaires, and advanced session closure remain future work | 7 |

## Repository note

The parent development instructions reference `docs/development_workflow.md`
and `protocol/session.md`, but those files were not present when Lot 6 opened.
The researcher-approved Lot 7 specification in the task conversation is the
implementation authority for this branch.
