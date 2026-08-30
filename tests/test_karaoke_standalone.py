"""The single-file build must be the same karaoke OS, not a lookalike.

Builds karaoke_os.py into a temp dir, imports the artifact, and runs a
night through it -- generated code that parses but does not execute is a
failure, same rule as the Pine converter. No sockets, per the house rule;
the HTTP layer inside the artifact is the same thin shell already tested
via QueueRoom.
"""

import importlib.util
import random
import sys

import pytest

from tools.karaoke_server import build_standalone


@pytest.fixture(scope="module")
def artifact(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist") / "karaoke_os.py"
    out.write_text(build_standalone.build(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("karaoke_os", out)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve the __future__ annotations via sys.modules; a
    # real run is __main__ and is always registered, so register this too
    sys.modules["karaoke_os"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("karaoke_os", None)


def test_the_build_is_deterministic():
    assert build_standalone.build() == build_standalone.build()


def test_the_artifact_runs_a_night_without_the_repo(artifact):
    room = artifact.QueueRoom(None, artifact.RotationConfig(), rng=random.Random(1))
    ada = room.join({"name": "Ada"}, 0.0)["singer_id"]
    room.song({"singer_id": ada, "title": "Nine to Five"}, 0.0)
    state = room.state(1.0, singer_id=ada)
    assert state["called"]["singer_id"] == ada
    room.here({"singer_id": ada}, 10.0)
    room.retime({"singer_id": ada, "remaining_s": 5.0}, 20.0)
    state = room.state(30.0, singer_id=ada)
    assert state["singing"] is None
    assert state["you"]["needs_song"] is True
    assert state["house_on"] is True


def test_the_page_travels_inside_the_file(artifact):
    assert artifact.EMBEDDED_PAGE is not None
    doc = artifact.page_html("screen", artifact.EMBEDDED_PAGE)
    assert "<title>Karaoke Queue</title>" in doc
    assert 'id="youreUp"' in doc and 'id="stageCard"' in doc
    # the embedded copy is the real page, byte for byte
    assert artifact.EMBEDDED_PAGE == build_standalone.PAGE.read_text(encoding="utf-8")


def test_the_selfcheck_gate_passes(artifact):
    assert artifact._standalone_main(["--selfcheck"]) == 0


def test_the_artifact_carries_no_repo_paths_it_depends_on(artifact):
    # PAGE points into a repo that will not exist on the target machine;
    # the fallback must carry the page, so a missing file is never fatal
    assert artifact.page_source() is not None
