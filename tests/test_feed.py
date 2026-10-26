"""
tests/test_feed.py — Mixtape

Tests for the Friends Listening Now feed.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def friends(app):
    """A user with two friends and one song."""
    with app.app_context():
        me = User(username="nova", email="nova@example.com")
        early_bird = User(username="simone", email="simone@example.com")
        night_owl = User(username="darius", email="darius@example.com")
        db.session.add_all([me, early_bird, night_owl])
        db.session.flush()

        for friend in (early_bird, night_owl):
            db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))

        song = Song(title="Block Party", artist="Street Collective", shared_by=me.id)
        db.session.add(song)
        db.session.commit()
        yield {"me": me, "early_bird": early_bird, "night_owl": night_owl, "song": song}


def test_friend_who_listened_today_appears(app, friends):
    with app.app_context():
        now = datetime.now(timezone.utc)
        db.session.add(ListeningEvent(user_id=friends["early_bird"].id,
                                      song_id=friends["song"].id, listened_at=now))
        db.session.commit()

        feed = get_friends_listening_now(friends["me"].id)
        assert [e["friend"]["username"] for e in feed] == ["simone"]


def test_friend_who_listened_last_night_is_excluded(app, friends):
    """
    Regression test for Issue #2: a listen from 11pm yesterday is inside a
    rolling 24h window but is not "today", so it shouldn't show up.
    """
    with app.app_context():
        start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        db.session.add(ListeningEvent(user_id=friends["night_owl"].id,
                                      song_id=friends["song"].id,
                                      listened_at=start_of_today - timedelta(hours=1)))
        db.session.commit()

        assert get_friends_listening_now(friends["me"].id) == []
