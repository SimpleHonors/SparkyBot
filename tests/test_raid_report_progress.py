from core.raid_report_progress import progress_state


def test_post_parse_stages_have_honest_monotonic_progress_and_messages():
    stages = ["collect", "resolve", "combine", "augment", "view", "bake"]

    states = [progress_state(stage, 1, 1) for stage in stages]

    assert [percent for percent, _ in states] == sorted(
        percent for percent, _ in states
    )
    assert all(message for _, message in states)
    assert progress_state("view", 1, 1)[1] == (
        "Building player and enemy analysis…"
    )


def test_parse_message_uses_whole_selected_fight_count():
    assert progress_state("parse", 23, 23)[1] == "Reading fight 23 of 23…"
