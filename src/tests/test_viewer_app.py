from viewer_app import parse_args


def test_parse_args_without_startup_mod():
    args = parse_args([])

    assert args.mod_folder is None
    assert args.disabled_ini is False


def test_parse_args_accepts_path_with_spaces_and_disabled_ini():
    args = parse_args([r"fixture directory/source 01", "--disabled-ini"])

    assert args.mod_folder == r"fixture directory/source 01"
    assert args.disabled_ini is True
