# Mixtape Bug Hunt — Submission

## AI Usage

I used Claude Code for most of this project, as a pair programmer rather than an answer machine.

- **Orientation:** I had it read through `models.py`, the routes, and the services and summarize what each piece does. Then I checked that summary against the README's two example call chains.
- **Reproducing:** before any code changed, it wrote a throwaway script that seeds a scratch DB and hits the real routes through Flask's test client. For issue #1 it patched `datetime.now` in the streak service to fake a Sunday. All five bugs reproduced this way.
- **Diagnosing:** for each bug, it traced the route → service → query path and named the exact line. I didn't take a diagnosis on faith. For each one I made sure there was a test that failed on the old code and passed on the new code.
- **Where the AI needed checking:** the issue #5 report says "added one more song and re-fetched". When we actually ran that step, `POST /playlists/<id>/songs` crashed with a 500 (NOT NULL on `position`). Reading the report alone, you'd assume adding a song worked. I logged that as a separate fix instead of folding it into #5.
- **Search (#3):** the AI's first instinct was to slap `.distinct()` on the query. Reading the query more closely showed the join to `song_tags` isn't used by the filter at all, so removing it fixes the cause rather than hiding it.

## Codebase Map

**app.py:** the Flask app factory. It configures SQLite (`mixtape.db` by default), sets up the shared `db` object, registers four blueprints under `/songs`, `/playlists`, `/users`, and `/feed`, and calls `create_all()`.

**models.py:** seven models: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, and `Notification`. There are also three association tables:
- `friendships`: user ↔ user, stored as two rows per friendship
- `song_tags`: song ↔ tag
- `playlist_entries`: playlist ↔ song, with extra columns `position`, `added_by`, and `added_at`. `position` is what gives a playlist its order.

Each model has a `to_dict()` that the routes return as JSON. `Song.to_dict()` pulls its tag names through the `Song.tags` relationship. All timestamps are UTC.

**routes/:** thin HTTP layer. Each route reads args or JSON, calls one service function, and turns `ValueError` into a 400 or 404.
- `songs.py`: search, song detail, rate, listen
- `playlists.py`: create, detail, list songs, add a song
- `users.py`: profile, streak, notifications, mark as read
- `feed.py`: listening-now and the activity feed

**services/:** all the actual logic.
- `streak_service.py`: records listening events and updates `User.listening_streak` and `last_listened_at` by comparing calendar dates.
- `feed_service.py`: listening-now (each friend's latest listen inside a time window) and the activity feed (the last N friend listens, with no time window).
- `search_service.py`: case-insensitive `ilike` match on title or artist.
- `notification_service.py`: creates and reads notifications. It also owns the two "social" actions that trigger notifications, `add_to_playlist` and `rate_song`.
- `playlist_service.py`: creates playlists and reads songs ordered by `playlist_entries.position`.

**seed_data.py:** drops and rebuilds the DB with 5 users, 13 songs with 0, 1, or 3 tags, 3 playlists, listening events at different ages, and one example notification.

**Data flow: a user rates a song.** `POST /songs/<id>/rate` with `{user_id, score}` → `routes/songs.py:rate()` → `notification_service.rate_song()`. That function:
1. checks that the score is 1–5
2. loads the Song and the User
3. updates the user's existing `Rating` for that song, or creates one (there's a unique constraint on user + song)
4. commits

The route then returns `rating.to_dict()` with a 201.

**Data flow: a user listens to a song.** `POST /songs/<id>/listen` → `streak_service.record_listening_event()`. It creates a `ListeningEvent` stamped with now (UTC), and `update_listening_streak()` compares today's date with the date of `last_listened_at`. That same event is what `feed_service` later reads for listening-now.

**Pattern I noticed:** routes never touch business rules; they only parse input and format output. Two services cross-import: `notification_service` imports from `playlist_service`, because adding to a playlist lives in the notification service.

## Root Cause Analyses

**Issue #1 — My listening streak keeps resetting**

1. **How I reproduced it:** I seeded the DB and set kenji's `last_listened_at` to Saturday 2026-10-24 at 22:00. With `datetime.now` patched to Sunday 2026-10-25 at 09:00, I hit `POST /songs/<id>/listen`. Then `GET /users/<kenji>/streak` returned `1` instead of `13`. The existing `test_streak_increments_on_sunday` also failed (`1 == 2`).
2. **How I found the root cause:** `/users/<id>/streak` just reads `User.listening_streak`, so the bad value has to be written earlier. Writing happens on the listen route: `routes/songs.py:listen` → `record_listening_event` → `update_listening_streak`. The issue saying "both times it was a Sunday" pointed straight at the weekday comparison.
3. **The root cause:** in `update_listening_streak`, the increment branch was `elif days_since_last == 1 and today.weekday() != 6:`. `weekday()` returns 6 for Sunday, so a Saturday → Sunday listen fails that condition even though the days are consecutive. It then falls into the `else` branch, which resets the streak to 1. The docstring's rules say nothing about weekdays.
4. **My fix:** removed `and today.weekday() != 6`, so the branch is just `days_since_last == 1`, which matches the documented rule.
5. **Side-effect check:** all of `tests/test_streaks.py` passes, including same-day (no change), a skipped day (reset), and a new user (starts at 1). Both sides of the boundary work: Sat → Sun increments now, and Sun → Mon was already fine.

**Issue #2 — Friends Listening Now shows people from yesterday**

1. **How I reproduced it:** on seeded data, I cleared the listening events and added one for darius at 23:00 yesterday (UTC). `GET /feed/<nova>/listening-now` still returned darius with `count: 1`.
2. **How I found the root cause:** `routes/feed.py:listening_now` → `feed_service.get_friends_listening_now`. The only thing that decides who counts as "now" is the `listened_at >= cutoff` filter, so I looked at how `cutoff` is built.
3. **The root cause:** `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD`, where `RECENT_THRESHOLD = timedelta(hours=24)`. That's a rolling 24-hour window, not "today". At 9am, a listen from 11pm the night before is only 10 hours old, so it passes the filter and hangs around until 11pm the next day.
4. **My fix:** the cutoff is now the start of the current UTC day, `now.replace(hour=0, minute=0, second=0, microsecond=0)`. I removed the unused `RECENT_THRESHOLD` constant. UTC matches how the streak service decides what "today" is.
5. **Side-effect check:** `get_activity_feed` doesn't use the cutoff and still returns the latest N events. One-per-friend dedup in listening-now is unchanged. I checked both sides of midnight: a listen from today still shows, and one at 23:00 yesterday doesn't. **Regression test:** `tests/test_feed.py::test_friend_who_listened_last_night_is_excluded` fails on the old code and passes on the fix.

**Issue #3 — The same song keeps showing up twice in search**

1. **How I reproduced it:** on seeded data, `GET /songs/search?q=Anthem` returned `Crown Heights Anthem` three times (`count: 3`). It has 3 tags. Songs with 0 or 1 tag only showed once, which is the "inconsistent" part of the report. `test_search_no_duplicates_multi_tag_song` also failed.
2. **How I found the root cause:** `routes/songs.py:search` → `search_service.search_songs`. Duplicates that scale with tag count meant something was joining on tags. The query has `.outerjoin(song_tags, ...)`. Git history showed that the last commit moved this from the legacy `Query` API to `select()`.
3. **The root cause:** the outer join to `song_tags` produces one row per (song, tag) pair, so a song with 3 tags comes back as 3 rows. The legacy `Query` API quietly de-duplicated entities, which hid this. `select(...).scalars().all()` doesn't. The join wasn't even needed, because the filter only checks `title` and `artist`, and `to_dict()` loads tags through `Song.tags` on its own.
4. **My fix:** removed the `.outerjoin(song_tags, ...)` line. One row per song, so nothing needs de-duplicating.
5. **Side-effect check:** all of `tests/test_search.py` passes, covering 0-, 1-, and 3-tag songs and an empty result. Results still include every tag name in `tags`, because that comes from the relationship.

**Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it**

1. **How I reproduced it:** on seeded data, kenji rated darius's song "Block Party" 5 stars via `POST /songs/<id>/rate`, which returned 201. Then `GET /users/<darius>/notifications` returned `count: 0`.
2. **How I found the root cause:** `routes/songs.py:rate` → `notification_service.rate_song`. I compared it side by side with `add_to_playlist` in the same file, since that path works.
3. **The root cause:** `add_to_playlist` ends by calling `create_notification(...)` for the song's sharer. `rate_song` saves or updates the `Rating`, commits, and returns, with no call to `create_notification` at all. The `song_rated` type is even listed in `create_notification`'s docstring, but nothing ever created one.
4. **My fix:** after the rating commits, `rate_song` now creates a `song_rated` notification for `song.shared_by`, for example "kenji rated your song 'Golden Hour' 5/5." It uses the same "not if it's your own song" guard as `add_to_playlist`.
5. **Side-effect check:** the rating is still returned and saved, and re-rating still updates the same row. Playlist-add notifications still work: the reproduction script got one after adding a song. Rating your own song creates nothing. **Regression test:** `tests/test_notifications.py::test_rating_notifies_song_sharer`.

**Issue #5 — The last song in a playlist never shows up**

1. **How I reproduced it:** the seeded playlist "Friday Energy" has 7 entries in `playlist_entries`, but `GET /playlists/<id>/songs` returned 6, and the missing one was the highest `position`. `test_playlist_returns_all_songs` also failed (4 of 5).
2. **How I found the root cause:** `routes/playlists.py:get_songs` → `playlist_service.get_playlist_songs`. The query (join on `playlist_entries`, ordered by `position`) returned all 7 rows when I ran it on its own, so the problem had to be after the query.
3. **The root cause:** the return line was `[song.to_dict() for song in songs[:-1]]`. `[:-1]` drops the last element, and since the list is sorted by position, that's always the newest song. Adding a song "freed" the previous one because it was no longer last. This contradicts the function's own note: "This function returns all songs in the playlist."
4. **My fix:** changed `songs[:-1]` to `songs`.
5. **Side-effect check:** all of `tests/test_playlists.py` passes, including order and the empty playlist (`[][:-1]` used to hide the bug there).

**Bonus — Adding a song to a playlist returned a 500**

Found while reproducing #5. `POST /playlists/<id>/songs` failed with `IntegrityError: NOT NULL constraint failed: playlist_entries.position`. `add_to_playlist` added the song with `playlist.songs.append(song)`, and the relationship only fills in the two key columns. That leaves `position` and `added_by` empty, and both are NOT NULL. I changed it to insert into `playlist_entries` directly with `position = max(position) + 1` and the real `added_by`. After the fix, the song gets added, shows up last, and the sharer still gets notified. Test: `tests/test_playlists.py::test_added_song_shows_up_last`.

## Regression Tests

- `tests/test_feed.py` — Issue #2
- `tests/test_notifications.py` — Issue #4
- `tests/test_playlists.py::test_added_song_shows_up_last` — the add-song fix

The existing tests for #1, #3, and #5 failed before the fixes and pass now. The full suite is 18 passing.
