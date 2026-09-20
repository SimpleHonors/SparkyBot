# SparkyBot Wall of Fame commentary

The Wall records **generated final commentary**, not delivery receipts, scheduled
callouts, combat awards, or a retrospective AI review. End-of-run AI review is
deliberately not implemented.

## Data path

* `main.process_log_file` calls `FightAnalyst.analyze`; the analyst owns provider
  retries and response postprocessing. After the existing Discord-length
  truncation, `record_final_comment` captures the exact text shared by Discord,
  Twitch and TTS. Recording happens once before transport fanout. Failed
  transport does not erase commentary already generated.
* `core.sparky_wall.CommentaryStore` stores minimal JSON records transactionally
  in `app_dir()/sparkybot_commentary.sqlite3`. Existing outcome/streak history
  does not store quotes and cannot reconstruct them. The SQLite primary key
  deduplicates identical final text for the same fight, including retries and
  concurrent writers. Different final comments on one fight remain distinct.
* Fight identity hashes EI start timestamp, duration, trigger ID and map ID.
  Missing start/duration is unavailable, not guessed. Character names map to
  stable normalized account IDs using that fight's roster. Exact name boundaries,
  longest-name matching and ambiguous-name abstention prevent substring and
  duplicate-name attribution. Missing account identity is not invented.
* Category labels describe topics explicitly present in the named sentence,
  not praise/blame or verified combat performance. Multiple named people in a
  sentence receive `unknown` rather than inheriting every category. Planned
  outliers never count as mentions. Pronouns, nicknames and inferred sentiment
  are intentionally unsupported.
  Commander/tag introductions alone are excluded. Tagged players still count
  when their own name segment contains an explicit performance topic, event,
  praise or roast cue; another player's subsequent performance cannot qualify
  the commander. Prefix cues (e.g. `Excellent healing from Alice`) also count
  when the commander is the sentence's only named player. This deliberately
  conservative matcher can miss novel phrasing.
* The existing `RunSession` start timestamp identifies an open explicit run.
  It is captured before generation, so ending a run while the provider is busy
  cannot rebind the completed comment. Manual mode has a null session ID;
  fight identity/time remains available.
* GUI/manual report and End Run use `make_runner`; the headless construction
  also passes the existing `enable_ai_analysis` master switch. The report runner
  freezes commentary for selected, successfully parsed JSON inputs in the
  `$:/sparkybot/commentary` tiddler in its exported report JSON. `build_night_model`
  intersects this snapshot with unambiguous Overview fight timestamps before
  aggregating `model.sparky_wall`. Selected-but-combiner-excluded fights do not
  contribute. Timestamp collisions and unmatched parser formats fail closed.
  Coverage uses **EI `timeEnd`**, retaining its literal local date/clock (not
  `timeEndStd`, UTC conversion, or start time). Upstream
  [parser_functions.py](https://github.com/Drevarr/GW2_EI_log_combiner/blob/main/parser_functions.py)
  splits `timeEnd` into `fight_date`, `fight_end`, `fight_utc`; its
  [output_functions.py](https://github.com/Drevarr/GW2_EI_log_combiner/blob/main/output_functions.py)
  builds Overview labels from the first two fields. The snapshot declares
  `coverage_basis: "ei.timeEnd"`; missing `timeEnd` never falls back to start or
  standard time. Multiple candidate IDs or multiple Overview rows at the same
  date/clock abstain, including clock collisions across offsets.
  Earlier local snapshots without this coverage marker used start times: model
  rebuilds return an enabled but empty wall for them. Regenerate the report from
  its selected EI JSON inputs to recover coverage; never infer end times from a
  broad range or relabel an old snapshot. Existing SQLite comments remain usable
  because start-based fight IDs are unchanged. Existing baked HTML stays frozen.
* The existing viewer conversion embeds the model; no separate viewer pipeline
  or additional AI call is introduced. Rebuilding with AI disabled yields
  `{enabled: false, players: []}` and reads no commentary history. Already
  exported offline files remain snapshots, not live settings subscribers.

## Model contract

`sparky_wall = {enabled, players: [{id, name, mentions, categories,
comments: [{text, fight_id, timestamp, categories, session_id, fight_timestamp,
fight_end_timestamp}]}]}`

Each mention is one distinct final comment naming that account, not each repeated
name within the comment. Each category counts comments carrying that topic.
Comments preserve full final text and UTC recording time (`timestamp`).
`fight_timestamp` remains the original start (`timeStartStd` preferred, otherwise
`timeStart`) for compatibility; it is not the Overview join key.
`fight_end_timestamp` preserves the full original `timeEnd`, including its offset,
from the selected EI input snapshot, also for older database comments lacking end
metadata. Only the coverage key drops the offset, exactly as Overview does.
Missing historical commentary produces an empty list, never invented
quotes. Renderer code must escape text and display unknown/missing data honestly.

## Verification

`python -m pytest tests/test_sparky_wall.py tests/test_sparky_wall_pipeline.py -q`

Tests use temporary stores and stub AI/transport/combiner boundaries: no production
AI requests, parser execution, Discord/Twitch posts or production state writes.
The real processing function is loaded via AST to avoid requiring Qt; the actual
report runner, converter, model and packed-model readback are exercised.