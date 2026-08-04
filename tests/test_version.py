from command_center import __version__


def test_application_version_matches_current_release_line():
    assert __version__ == "2.0.0"
