from blackmagic_pineapple_service.service import ensure_dot, normalize_command, parse_command


def test_parse_plain_set_name():
    cmd = parse_command("SetName TAKE_001")

    assert cmd.type == "SetName"
    assert cmd.value == "TAKE_001"


def test_parse_pipeline_aliases():
    assert normalize_command("recordStart").type == "Start"
    assert normalize_command("recordStop").type == "Stop"
    assert normalize_command("fileName", "TAKE_002").type == "SetName"


def test_parse_json_command():
    cmd = parse_command('{"type": "fileName", "value": "TAKE_003"}')

    assert cmd.type == "SetName"
    assert cmd.value == "TAKE_003"


def test_ensure_dot():
    assert ensure_dot("_mocap._tcp.local") == "_mocap._tcp.local."
    assert ensure_dot("_mocap._tcp.local.") == "_mocap._tcp.local."
