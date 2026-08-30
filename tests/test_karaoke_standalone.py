"""The single-file build must be the same karaoke OS, not a lookalike.

Builds karaoke_os.py into a temp dir, imports the artifact, and runs a
night through it -- generated code that parses but does not execute is a
failure, same rule as the Pine converter. No sockets, per the house rule;
the HTTP layer inside the artifact is the same thin shell already tested
via QueueRoom.
"""

import importlib.util
import os
import random
import shutil
import sys
from pathlib import Path

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


def test_the_qr_encoder_travels_too(artifact):
    """The single file has to draw its own QR on a router with no uplink."""
    assert "// ==== qr, drawn here" in artifact.EMBEDDED_PAGE
    assert "function qrEncode(" in artifact.EMBEDDED_PAGE
    assert "cdnjs" not in artifact.EMBEDDED_PAGE


def test_the_selfcheck_gate_passes(artifact):
    assert artifact._standalone_main(["--selfcheck"]) == 0


def test_the_artifact_carries_no_repo_paths_it_depends_on(artifact):
    # PAGE points into a repo that will not exist on the target machine;
    # the fallback must carry the page, so a missing file is never fatal
    assert artifact.page_source() is not None


# ---------- what the adversarial review of 2026-08-30 convicted ----------


def test_it_runs_from_a_shallow_path():
    """C:\\karaoke\\karaoke_os.py is a plausible spot and used to crash.

    queue_server computed the repo root as parents[2] at import time, so a
    file fewer than three directories deep died with IndexError before the
    launcher, the selfcheck, or the embedded page could run. Neither CI nor
    the other tests caught it -- pytest's tmpdir and dist/ are both deep, so
    this one writes to a genuinely root-adjacent directory on purpose.
    """
    shallow_dir = Path("/kqtest")  # /kqtest/k.py has 2 parents, not 3
    shallow_dir.mkdir(exist_ok=True)
    shallow = shallow_dir / "k.py"
    try:
        assert len(shallow.resolve().parents) < 3, "path is not actually shallow"
        shallow.write_text(build_standalone.build(), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("karaoke_shallow", shallow)
        module = importlib.util.module_from_spec(spec)
        sys.modules["karaoke_shallow"] = module
        try:
            spec.loader.exec_module(module)  # used to raise IndexError here
            assert module.page_source() is not None
            assert module._standalone_main(["--selfcheck"]) == 0
        finally:
            sys.modules.pop("karaoke_shallow", None)
    finally:
        shutil.rmtree(shallow_dir, ignore_errors=True)


def test_the_repo_page_lookup_survives_a_root_adjacent_file():
    from tools.karaoke_server import queue_server

    assert queue_server._repo_page() is not None  # in the repo, it resolves
    # the guard itself: a path with too few parents returns None, not IndexError
    assert len(queue_server.Path("/k.py").resolve().parents) < 3


def test_the_screen_opens_on_an_address_phones_can_reach(artifact, monkeypatch):
    """The page builds its QR from location.origin.

    A screen opened at localhost therefore shows a QR that every phone in
    the room fails to reach -- on the default double-click path, which is
    the entire product. Blocker, found 2026-08-30.
    """
    opened = []

    class FakeTimer:
        def __init__(self, delay, fn, args=None):
            opened.append(args[0])

        def start(self):
            pass

    monkeypatch.setattr(artifact.threading, "Timer", FakeTimer)
    monkeypatch.setattr(artifact, "lan_address", lambda: "192.168.1.50")
    monkeypatch.setattr(artifact, "serve", lambda *a, **k: None)
    artifact._standalone_main(["--port", "8772"])
    assert opened == ["http://192.168.1.50:8772/screen"]
    assert "localhost" not in opened[0]

    opened.clear()
    monkeypatch.setattr(artifact, "lan_address", lambda: None)
    artifact._standalone_main(["--port", "8772"])
    assert opened == ["http://localhost:8772/screen"]  # only as a fallback


def test_memory_lands_next_to_the_program_not_the_working_directory(
    artifact, monkeypatch, tmp_path
):
    """cwd is wrong for a double-clicked exe (System32 as admin, temp in a zip)."""
    seen = {}
    monkeypatch.setattr(artifact, "serve", lambda h, p, prof: seen.update(path=prof))
    monkeypatch.setattr(artifact, "_home_dir", lambda: tmp_path / "venue")
    artifact._standalone_main(["--no-browser"])
    assert seen["path"] == str(tmp_path / "venue" / "karaoke-profiles.json")
    assert os.path.isabs(seen["path"])


def test_an_unwritable_location_costs_the_memory_not_the_night(artifact, tmp_path):
    """An exe in Program Files, or a USB stick pulled mid-night, must not
    take the room down with it.

    mkstemp sat outside the try in _save_profiles, so any OSError from it
    raised straight out of join() through the HTTP handler: every phone's
    join died and the night was over. A read-only directory is the Windows
    version; here the folder simply is not there, which convicts as any
    user (root ignores a read-only bit, so that variant proves nothing).
    """
    gone = tmp_path / "unplugged-usb" / "profiles.json"  # parent never created
    room = artifact.QueueRoom(str(gone), artifact.RotationConfig())
    ada = room.join({"name": "Ada"}, 0.0)["singer_id"]  # must not raise
    room.song({"singer_id": ada, "title": "Nine to Five"}, 0.0)
    state = room.state(1.0, singer_id=ada)
    assert state["called"]["singer_id"] == ada  # the night runs regardless
    assert not gone.exists()  # memory really was lost, not silently relocated


def test_the_firewall_hint_is_printed_where_the_operator_will_see_it(capsys):
    from tools.karaoke_server import queue_server

    src = build_standalone.module_body("queue_server.py")
    assert "public networks" in src.lower()
    assert "firewall" in src.lower()
