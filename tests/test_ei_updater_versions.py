from core.ei_updater import EIUpdater


def test_patch_release_is_newer(tmp_path):
    updater = EIUpdater(tmp_path)

    assert updater._compare_versions("3.28.0.1", "3.28.0.0") == 1
    assert updater._compare_versions("3.28.0.0", "3.28.0.1") == -1


def test_versions_with_different_component_counts_compare_cleanly(tmp_path):
    updater = EIUpdater(tmp_path)

    assert updater._compare_versions("3.28", "3.28.0.0") == 0
    assert updater._compare_versions("v3.28.1", "3.28.0.9") == 1
