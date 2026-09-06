"""Tests for tools/content_signal.py and the content adapter in tools/awareness.py.

The claim this file exists to keep honest is that **the two halves never vouch
for each other**. The render half is read off the owner's machine; the platform
half is an MCP read a Claude session performs. They are captured at different
moments by different things, and a signal with one fresh half and one missing
half must report exactly that -- not a pipeline that looks fine because the part
somebody looked at was fine.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from tools import awareness as aw
from tools import content_signal as cs

NOW = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def signal(**kw) -> cs.ContentSignal:
    base = dict(taken=NOW.isoformat())
    base.update(kw)
    return cs.ContentSignal(**base)


def rendered(hours=2.0, segments=5, description=True) -> dict:
    return dict(
        render_taken=NOW.isoformat(),
        render_age_hours=hours,
        render_segments=segments,
        render_has_description=description,
    )


# ---------------------------------------------------------------------------
# The two halves
# ---------------------------------------------------------------------------


def test_a_capture_with_no_platform_half_says_nothing_about_publishing():
    """Not one content:publish or content:analytics observation, at any severity."""
    obs = aw.observe_content(signal(**rendered()), NOW)
    assert [o.entity for o in obs] == ["content:render"]


def test_a_capture_with_no_render_half_says_nothing_about_the_render():
    obs = aw.observe_content(
        signal(
            platform_taken=NOW.isoformat(),
            publish_subscription="active",
            publish_accounts=1,
            publish_posts_7d=3,
        ),
        NOW,
    )
    assert not [o for o in obs if o.entity == "content:render"]


def test_each_half_is_seen_only_when_it_carries_its_own_stamp():
    assert not signal().render_seen
    assert not signal().platform_seen
    assert signal(**rendered()).render_seen
    assert signal(platform_taken=NOW.isoformat()).platform_seen


def test_no_signal_at_all_produces_no_observation():
    assert aw.observe_content(None, NOW) == []


# ---------------------------------------------------------------------------
# The render half: convict and acquit
# ---------------------------------------------------------------------------


def test_a_render_missing_for_a_whole_session_is_convicted():
    stale = rendered(hours=24 * 5)  # last written on the Friday
    render = [
        o
        for o in aw.observe_content(signal(**stale), NOW)
        if o.entity == "content:render"
    ][0]
    assert render.severity == "act" and render.trigger == "stopped"


def test_a_render_produced_this_session_is_acquitted():
    render = [
        o
        for o in aw.observe_content(signal(**rendered()), NOW)
        if o.entity == "content:render"
    ][0]
    assert render.severity == "info" and not render.trigger


def test_an_empty_render_folder_is_convicted_even_when_freshly_touched():
    stale = rendered(hours=1.0, segments=0)
    render = [
        o
        for o in aw.observe_content(signal(**stale), NOW)
        if o.entity == "content:render"
    ][0]
    assert render.trigger == "stopped"


# ---------------------------------------------------------------------------
# The platform half: convict and acquit
# ---------------------------------------------------------------------------


def platform(**kw) -> dict:
    base = dict(
        platform_taken=NOW.isoformat(),
        publish_subscription="active",
        publish_accounts=1,
        publish_posts_7d=3,
        analytics_plan="paid",
        analytics_accounts=1,
    )
    base.update(kw)
    return base


def test_renders_that_were_never_posted_are_convicted():
    obs = aw.observe_content(signal(**rendered(), **platform(publish_posts_7d=0)), NOW)
    publish = [o for o in obs if o.entity == "content:publish"][0]
    assert publish.trigger == "stopped"
    assert "made but not posted" in publish.detail


def test_renders_that_were_posted_are_acquitted():
    obs = aw.observe_content(signal(**rendered(), **platform()), NOW)
    publish = [o for o in obs if o.entity == "content:publish"][0]
    assert publish.severity == "info" and not publish.trigger


def test_a_lapsed_subscription_is_convicted():
    obs = aw.observe_content(
        signal(**rendered(), **platform(publish_subscription="inactive")), NOW
    )
    publish = [o for o in obs if o.entity == "content:publish"][0]
    assert publish.trigger == "stopped"


def test_no_connected_account_is_convicted():
    obs = aw.observe_content(signal(**rendered(), **platform(publish_accounts=0)), NOW)
    publish = [o for o in obs if o.entity == "content:publish"][0]
    assert publish.trigger == "stopped"


def test_an_unpaid_analytics_trial_is_watched_but_never_interrupts():
    """A trial goes quiet rather than failing loudly. Worth seeing, not worth waking anyone."""
    obs = aw.observe_content(
        signal(**rendered(), **platform(analytics_plan="trial")), NOW
    )
    analytics = [o for o in obs if o.entity == "content:analytics"][0]
    assert analytics.severity == "watch" and not analytics.trigger


def test_a_paid_analytics_plan_is_silent():
    obs = aw.observe_content(signal(**rendered(), **platform()), NOW)
    assert not [o for o in obs if o.entity == "content:analytics"]


# ---------------------------------------------------------------------------
# Reduction and redaction
# ---------------------------------------------------------------------------


def test_the_live_connector_shapes_reduce_to_the_facts_they_carry():
    """The payload here is the shape the real Blotato and Windsor.ai reads returned."""
    out = cs.reduce_platform(
        {
            "blotato": {
                "subscription": "active",
                "accounts": 1,
                "last_post": (NOW - dt.timedelta(days=2)).isoformat(),
                "posts_7d": 4,
            },
            "windsor": {"is_paid": False, "connectors": 1, "accounts": 1},
        },
        NOW,
    )
    assert out["publish_subscription"] == "active"
    assert out["analytics_plan"] == "trial"
    assert out["publish_last_post_days"] == 2.0


def test_an_unrecognised_subscription_word_becomes_unknown_rather_than_a_guess():
    out = cs.reduce_platform({"blotato": {"subscription": "grandfathered-pro"}}, NOW)
    assert out["publish_subscription"] == "unknown"


def test_a_missing_is_paid_is_unknown_not_a_trial():
    out = cs.reduce_platform({"windsor": {"connectors": 1}}, NOW)
    assert out["analytics_plan"] == "unknown"


def test_nothing_a_platform_read_holds_survives_into_the_signal():
    """Handles, captions and video titles are all free text. None may reach git."""
    payload = {
        "blotato": {
            "subscription": "active",
            "accounts": 1,
            "username": "jayshong6",
            "last_caption": "Market close: NVDA rips 6%",
            "posts_7d": 2,
        },
        "windsor": {"is_paid": False, "accounts": 1, "account_name": "AlaskaM"},
    }
    emitted = json.dumps(
        cs.derive(None, cs.reduce_platform(payload, NOW), NOW).as_dict()
    )
    for leak in ("jayshong6", "AlaskaM", "NVDA", "Market close"):
        assert leak not in emitted, f"{leak} reached the signal"


def test_a_field_outside_the_schema_is_refused():
    with pytest.raises(cs.Unpublishable):
        cs.validate({"taken": NOW.isoformat(), "caption": "hello"})


def test_render_reads_shape_and_age_but_never_text(tmp_path):
    (tmp_path / "01-open.txt").write_text(
        "Good afternoon, NVDA closed up", encoding="utf-8"
    )
    (tmp_path / "02-tape.txt").write_text("The S and P five hundred", encoding="utf-8")
    (tmp_path / "description.txt").write_text("Subscribe", encoding="utf-8")
    out = cs.read_render(tmp_path, NOW)
    assert out["render_segments"] == 2
    assert out["render_has_description"] is True
    assert "NVDA" not in json.dumps(out)


def test_a_render_directory_that_does_not_exist_is_not_captured(tmp_path):
    assert cs.read_render(tmp_path / "nope", NOW) is None


def test_a_signal_round_trips_through_disk(tmp_path):
    original = signal(**rendered(), **platform())
    path = cs.write_signal(original, tmp_path / "content.json")
    assert cs.load_signal(path) == original


# ---------------------------------------------------------------------------
# The capture's own age: the same rule the desk bridge gets
# ---------------------------------------------------------------------------


def test_a_stale_platform_capture_is_convicted_before_its_facts_are_read():
    """A capture from a fortnight ago reporting no posts is a fact about then.

    Same failure as a desk signal nobody rewrote: the numbers look current
    because the file is present, and nothing in them says how old they are.
    """
    old = dict(platform(), platform_taken="2026-08-20T12:00:00+00:00")
    obs = aw.observe_content(signal(**rendered(), **old), NOW)
    capture = [o for o in obs if o.entity == "content:capture"][0]
    assert capture.severity == "act" and capture.trigger == "stopped"
    assert dict(capture.metrics)["sessions_since_capture"] > 5


def test_a_capture_from_this_session_is_acquitted():
    obs = aw.observe_content(signal(**rendered(), **platform()), NOW)
    assert not [o for o in obs if o.entity == "content:capture"]


def test_a_capture_over_a_weekend_is_not_stale():
    """Friday's capture read on Monday. A weekend is not a missed session."""
    monday = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)
    friday = dict(platform(), platform_taken="2026-08-28T12:00:00+00:00")
    obs = aw.observe_content(signal(**rendered(), **friday), monday)
    assert not [o for o in obs if o.entity == "content:capture"]


# ---------------------------------------------------------------------------
# Whether the two connectors describe the same channel
#
# Confirmed by the owner on 2026-09-03: AlaskaM is the real channel, and Blotato
# publishes to @jayshong6. Two true numbers, one false picture -- the failure the
# content adapter could not see until it carried this field.
# ---------------------------------------------------------------------------


def test_two_different_channels_are_convicted_as_blocking():
    obs = aw.observe_content(
        signal(**rendered(), **platform(channel_match="different")), NOW
    )
    channel = [o for o in obs if o.entity == "content:channel"][0]
    assert channel.severity == "act" and channel.trigger == "blocking"


def test_one_channel_on_both_sides_is_acquitted():
    obs = aw.observe_content(
        signal(**rendered(), **platform(channel_match="same")), NOW
    )
    assert not [o for o in obs if o.entity == "content:channel"]


def test_an_unknown_match_says_nothing_rather_than_assuming_agreement():
    """`unknown` must be earned out of, never read as `same`."""
    obs = aw.observe_content(
        signal(**rendered(), **platform(channel_match="unknown")), NOW
    )
    assert not [o for o in obs if o.entity == "content:channel"]


def test_the_verdict_is_taken_and_never_derived_from_the_handles():
    """A TikTok handle and a Windsor account label are different kinds of string.

    Comparing them would manufacture a mismatch for one channel that happens to
    carry two names -- so the reduction refuses to compare, and reports unknown
    when nobody supplied a verdict.
    """
    out = cs.reduce_platform(
        {
            "blotato": {"subscription": "active", "username": "jayshong6"},
            "windsor": {"is_paid": False, "account_name": "AlaskaM"},
        },
        NOW,
    )
    assert out["channel_match"] == "unknown"


def test_an_unrecognised_verdict_word_becomes_unknown():
    out = cs.reduce_platform({"channel_match": "probably-ish"}, NOW)
    assert out["channel_match"] == "unknown"


def test_the_verdict_carries_no_handle_into_the_signal():
    payload = {
        "channel_match": "different",
        "blotato": {"subscription": "active", "accounts": 1, "username": "jayshong6"},
        "windsor": {"is_paid": False, "accounts": 1, "account_name": "AlaskaM"},
    }
    emitted = json.dumps(
        cs.derive(None, cs.reduce_platform(payload, NOW), NOW).as_dict()
    )
    assert '"channel_match": "different"' in emitted
    for leak in ("jayshong6", "AlaskaM"):
        assert leak not in emitted


def test_an_illegal_verdict_is_refused_before_it_is_written():
    payload = signal(channel_match="same").as_dict()
    payload["channel_match"] = "AlaskaM"
    with pytest.raises(cs.Unpublishable):
        cs.validate(payload)
