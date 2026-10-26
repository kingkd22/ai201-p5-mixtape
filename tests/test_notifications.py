"""
tests/test_notifications.py — Mixtape

Tests for notifications created when friends interact with a shared song.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def shared_song(app):
    with app.app_context():
        sharer = User(username="aaliya", email="aaliya@example.com")
        friend = User(username="kenji", email="kenji@example.com")
        db.session.add_all([sharer, friend])
        db.session.flush()

        song = Song(title="Golden Hour", artist="Solange K", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()
        yield {"sharer": sharer, "friend": friend, "song": song}


def test_rating_notifies_song_sharer(app, shared_song):
    """Regression test for Issue #4: rating a friend's song should notify them."""
    with app.app_context():
        rate_song(shared_song["friend"].id, shared_song["song"].id, 5)

        notifs = get_notifications(shared_song["sharer"].id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_rated"
        assert "kenji" in notifs[0]["body"]


def test_rating_own_song_does_not_notify(app, shared_song):
    with app.app_context():
        rate_song(shared_song["sharer"].id, shared_song["song"].id, 4)

        assert get_notifications(shared_song["sharer"].id) == []
