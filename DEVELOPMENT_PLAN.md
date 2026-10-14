# Gig Pilot — Four-week development plan

Prepared October 14, 2026. This is a proposed sequence of improvements, not a
background automation or a promise to ship a feature every week.

## Objective

Help Ogbuefi make a clear predeparture decision and avoid unnecessary driving.
Preserve the mobile graphite interface, delivery-first strategy, current calendar
exports, private records, and Gridwise's role as the source of actual earnings.

## October 14–20: Make the existing plan easier to interpret

- Distinguish upcoming/open driving windows, rest, and optional continuation in
  the existing Drive headline. Explain whether timing is a usual dinner schedule
  or adjusted around a confirmed Michigan kickoff.
- Preserve driving times, game-day preferences, exports, and the personal $3 per
  estimated total mile goal. Do not imply measured demand or guaranteed earnings.
- During ordinary use, note whether the first screen answers: What phase is this?
  When does it start? What is it based on? No new logging routine is required.
- Review checkpoint: can the driver understand the plan in roughly 30 seconds,
  including a football rest period, without opening advanced tools?

## October 21–27: Decide whether source freshness needs more visibility

- Review actual cases where weather or event information was unclear or stale.
- If that causes hesitation, add restrained checked-at/partial-coverage wording
  using existing timestamps. Explain that checked-at is a retrieval time, not
  necessarily the source's publication time.
- Keep failed sources visibly unknown. Avoid new polling services or extra cards.
- Ship only if this improves trust or departure decisions in observed use.

## October 28–November 3: Reduce effort in the existing learning loop

- Assess whether the current decision journal gets used and what makes it tedious.
- If needed, simplify the existing Worth it / Mixed / Not worth it / Skipped
  review with an optional explanation. Reuse existing Decisions fields first.
- Look for avoidable departures, unnecessary positioning, and windows worth
  repeating. Do not request per-trip mileage or duplicate Gridwise accounting.
- Explicitly approve any proposed worksheet-schema change before implementing it.

## November 4–10: Evaluate evidence before adding comparisons

- Review only verified recent Ann Arbor/Ypsilanti results relevant to the current
  Telluride operation. Imported archives remain archive-only unless a separate,
  explicitly approved cohort/provenance design is implemented.
- Keep 2022 records out of current benchmarks and recommendations.
- Before a comparison feature, define the outcome, comparable windows, minimum
  sample and limitations. Show counts, dates, and missing information; small
  samples do not establish a reliable pattern or causal earnings improvement.
- If there is insufficient comparable evidence, continue observation rather than
  adding rankings, demand forecasts, or an earnings prediction.

## Each development checkpoint

1. Review the driver's observations and choose the single most useful problem.
2. Check current GitHub main, implementation and tests before proposing a change.
3. Keep the scope small; use a feature branch, regression tests and a reviewed PR.
4. Merge after checks pass; verify post-merge CI separately from live deployment.
5. Check mobile usefulness, and retain or revise the change based on actual use.

Success means clearer decisions with less effort, not more dashboard features.
No reminders, automatic coding, private-data collection, or recurring deployments
are scheduled by this document. Future phases remain subject to the user's
priorities and evidence from use.
