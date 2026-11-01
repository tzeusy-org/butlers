## 1. Shared trail policy

- [x] 1.1 Add `owntracks_trail.classify_trail` (impossible-jump rule, re-anchor run, cross-batch anchors) with synthetic-trace tests.
- [x] 1.2 Point projection flags outlier point events (`payload.trail_outlier`) and excludes them from movement segmentation.
- [x] 1.3 Place-cluster projection drops outliers before clustering and away detection.
- [x] 1.4 Dashboard Map trail skips point events carrying `trail_outlier`.

## 2. Movement quality

- [x] 2.1 No movement episode below two accepted fixes; pending segments carry over.
- [x] 2.2 Point count spans the whole episode across batches.
- [x] 2.3 A fresh segment overlapping a stored same-endpoint episode widens it instead of duplicating it.
- [x] 2.4 Segment per endpoint so interleaved devices cannot fragment each other.

## 3. Place naming and away spans

- [x] 3.1 Label precedence: owner reference, then dominant OwnTracks region, else `place_unknown`, with `label_source`.
- [x] 3.2 Evidence-layer `away_episode` from the owner `home` reference (50 km, 3 h minimum, 36 h gap split), mapped to the Travel lane.
- [x] 3.3 Record the external-data decision: no geocoder. City naming of unnamed places is an owner follow-up.
