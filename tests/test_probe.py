import json

from pycrestron_cip import probe
from pycrestron_cip.testing import FakeProcessor


async def test_probe_records_dump_and_sends_no_joins(tmp_path, capsys):
    async with FakeProcessor(ipids={0x03}) as proc:
        proc.digital[350] = True
        proc.analog[361] = 1000
        args = probe.parse_args(["127.0.0.1", "0x03", "--port", str(proc.port), "--seconds", "0.2",
                                 "--log", str(tmp_path / "p.log"), "--json", str(tmp_path / "p.json")])  # fmt: skip
        result = await probe.probe(args)
        assert proc.joins_received() == []  # read-only: no presses, no join values
    joins = {(e["type"], e["join"], e["value"]) for e in result["events"] if "join" in e}
    assert {("d", 350, True), ("a", 361, 1000)} <= joins
    assert json.loads((tmp_path / "p.json").read_text())["stats"]["connects"] == 1
    assert "initial dump 2 joins" in capsys.readouterr().out


def test_main_reports_connection_error(tmp_path, capsys):
    # nothing listens on port 1: a clean error and exit code 1, no traceback
    code = probe.main(["127.0.0.1", "0x03", "--port", "1", "--seconds", "0",
                       "--log", str(tmp_path / "p.log"), "--json", str(tmp_path / "p.json")])  # fmt: skip
    assert code == 1
    assert capsys.readouterr().err.startswith("error: ")
