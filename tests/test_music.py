"""Music: parsing, source policy and failure tolerance.

Nothing here touches real audio. The point is that the *logic* is right - which
source is showing, when a player gets paused, how a cava frame is read - and that
a broken environment degrades instead of raising.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from omapoker.music import LOCAL, MPRIS_PREFIX, OFF, MusicController
from omapoker.music.cava import ASCII_MAX, Visualiser, config_text, parse_frame
from omapoker.music.library import Library, LocalTrack, read_tags
from omapoker.music.local import LocalPlayer, LocalStatus, socket_path_for
from omapoker.music.mpris import MprisBackend, Track, describe_source


# --------------------------------------------------------------------------
# mpris parsing
# --------------------------------------------------------------------------

class FakePlayerctl:
    """Stands in for the playerctl binary."""

    def __init__(self, players=(), metadata="", status="Playing"):
        self.players = list(players)
        self.metadata = metadata
        self.status = status
        self.calls: list[tuple[str, ...]] = []

    def run(self, *args):
        self.calls.append(args)
        if args[:1] == ("-l",):
            return 0, "\n".join(self.players)
        if "metadata" in args:
            return (0, self.metadata) if self.metadata else (1, "")
        if "status" in args:
            return 0, self.status
        return 0, ""


def make_backend(fake: FakePlayerctl) -> MprisBackend:
    backend = MprisBackend(binary="playerctl")
    backend._run = fake.run  # type: ignore[assignment]
    backend.available = lambda: True  # type: ignore[assignment]
    return backend


def test_track_parsing_from_tab_separated_output():
    fake = FakePlayerctl(metadata="firefox\tPlaying\tCareless Whisper\tGeorge Michael\tMake It Big\t278000000")
    backend = make_backend(fake)
    track = backend.track()
    assert track is not None
    assert track.player == "firefox"
    assert track.is_playing
    assert track.title == "Careless Whisper"
    assert track.artist == "George Michael"
    assert track.duration_seconds == 278
    assert track.label == "Careless Whisper - George Michael"


def test_track_parsing_survives_a_broken_title():
    fake = FakePlayerctl(metadata="spotify\tPaused\tA\tB")
    backend = make_backend(fake)
    track = backend.track()
    assert track is not None
    assert track.album == ""
    assert not track.is_playing


def test_playerctld_is_hidden_from_the_source_list():
    fake = FakePlayerctl(players=["playerctld", "firefox", "spotify"])
    backend = make_backend(fake)
    assert backend.players() == ["firefox", "spotify"]


def test_best_track_prefers_a_playing_player():
    tracks = {
        "vlc": "vlc\tPaused\tIdle Song\tSomeone\t\t0",
        "firefox": "firefox\tPlaying\tLive Song\tSomeone\t\t0",
    }

    class Multi(FakePlayerctl):
        def run(self, *args):
            if args[:1] == ("-l",):
                return 0, "vlc\nfirefox"
            if "metadata" in args and "-p" in args:
                player = args[args.index("-p") + 1]
                return 0, tracks[player]
            if "metadata" in args:
                return 0, tracks["vlc"]
            return 0, "Stopped"

    backend = make_backend(Multi())
    best = backend.best_track()
    assert best is not None and best.player == "firefox"


def test_short_labels_are_trimmed_with_an_ellipsis():
    track = Track(title="A" * 60, artist="B", status="Playing")
    short = track.short(20)
    assert len(short) == 20
    assert short.endswith("\u2026")


def test_describe_source_names_the_obvious_cases():
    assert describe_source("off") == "Off"
    assert describe_source("local") == "OmaPoker Local"


def test_mpris_control_is_silent_when_playerctl_is_missing():
    backend = MprisBackend(binary="definitely-not-a-real-playerctl")
    assert not backend.available()
    assert backend.players() == []
    assert backend.track() is None
    assert backend.play_pause() is False
    assert backend.volume() is None


# --------------------------------------------------------------------------
# cava
# --------------------------------------------------------------------------

def test_cava_config_is_raw_ascii():
    text = config_text(bars=8, framerate=24, input_method="pulse", source="auto")
    assert "method = raw" in text
    assert "data_format = ascii" in text
    assert "bars = 8" in text
    assert f"ascii_max_range = {ASCII_MAX}" in text


def test_parsing_a_cava_frame():
    assert parse_frame("0;50;100;", bars=3) == [0.0, 0.5, 1.0]
    # Values beyond the range are clamped rather than blowing out the bar.
    assert parse_frame("150;", bars=1) == [1.0]
    # Junk and short frames still produce the right number of bars.
    assert parse_frame("", bars=4) == [0.0, 0.0, 0.0, 0.0]
    assert parse_frame("10;10;", bars=4) == [0.1, 0.1, 0.0, 0.0]


def test_visualiser_resamples_to_any_bar_count(tmp_path):
    vis = Visualiser(bars=8, state_dir=tmp_path)
    vis.levels_data = [0.0, 0.25, 0.5, 0.75, 1.0, 0.75, 0.5, 0.25]
    assert len(vis.levels(4)) == 4
    assert len(vis.levels(16)) == 16
    assert vis.levels(0) == []
    assert max(vis.levels(4)) <= 1.0


def test_visualiser_reports_a_missing_binary_instead_of_raising(tmp_path):
    vis = Visualiser(binary="definitely-not-a-real-cava", state_dir=tmp_path)
    assert vis.available() is False
    assert vis.start() is False
    assert vis.error
    assert vis.levels(4) == [0.0, 0.0, 0.0, 0.0]
    assert not vis.alive()


def test_visualiser_writes_a_config_file(tmp_path):
    vis = Visualiser(binary="definitely-not-a-real-cava", state_dir=tmp_path / "cava", bars=6)
    path = vis.write_config()
    assert path.is_file()
    assert "bars = 6" in path.read_text(encoding="utf-8")


def test_idle_pattern_is_small_and_quiet():
    vis = Visualiser(state_dir=Path("/tmp"))
    pattern = vis.idle_pattern(6, phase=1.0)
    assert len(pattern) == 6
    assert all(0.0 <= value <= 0.2 for value in pattern)


# --------------------------------------------------------------------------
# local library
# --------------------------------------------------------------------------

def make_audio(path: Path, payload: bytes = b"not really audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_read_tags_falls_back_to_the_filename(tmp_path):
    path = make_audio(tmp_path / "03 - Blue Monday.mp3")
    track = read_tags(path)
    assert track.title == "Blue Monday"
    assert track.path == path


def test_scan_finds_audio_and_skips_everything_else(tmp_path):
    make_audio(tmp_path / "song.mp3")
    make_audio(tmp_path / "nested/deep/song.flac")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "cover.jpg").write_bytes(b"x")

    library = Library([tmp_path])
    tracks = library.scan()
    assert len(tracks) == 2
    assert {t.path.suffix for t in tracks} == {".mp3", ".flac"}
    assert library.count() == 2


def test_scan_ignores_hidden_and_missing_folders(tmp_path):
    make_audio(tmp_path / ".hidden/song.mp3")
    library = Library([tmp_path, tmp_path / "does-not-exist"])
    assert library.scan() == []


def test_playlist_reading_handles_comments_and_relative_paths(tmp_path):
    song = make_audio(tmp_path / "music/song.mp3")
    other = make_audio(tmp_path / "music/other.mp3")
    playlist = tmp_path / "music/list.m3u"
    playlist.write_text(
        "#EXTM3U\n\nsong.mp3\n  other.mp3  \n# a comment\nmissing.mp3\n",
        encoding="utf-8",
    )
    entries = Library.read_playlist(playlist)
    assert entries == [song, other]

    library = Library([tmp_path])
    assert set(library.playlists()) == {"list"}


def test_search_matches_title_artist_and_album():
    library = Library([])
    library.tracks = [
        LocalTrack(Path("/x/a.mp3"), title="Alpha", artist="Zed", album="One"),
        LocalTrack(Path("/x/b.mp3"), title="Beta", artist="Ada", album="Two"),
    ]
    library.scanned = True
    assert [t.title for t in library.search("ada")] == ["Beta"]
    assert [t.title for t in library.search("one")] == ["Alpha"]
    assert len(library.search("")) == 2


def test_default_folders_is_just_home_music():
    folders = Library.default_folders()
    assert all(isinstance(f, Path) for f in folders)
    assert len(folders) <= 1


# --------------------------------------------------------------------------
# local player
# --------------------------------------------------------------------------

def test_socket_path_stays_within_the_unix_limit():
    long_home = Path("/home") / ("x" * 120)
    path = socket_path_for(long_home)
    assert len(str(path)) < 100


def test_local_player_is_honest_when_mpv_is_missing(tmp_path):
    player = LocalPlayer(socket_path=tmp_path / "mpv.sock", binary="definitely-not-a-real-mpv")
    assert player.available() is False
    assert player.is_running() is False
    assert player.start() is False
    assert player.play([tmp_path / "song.mp3"]) is False
    assert player.command("get_property", "pause") is None
    status = player.status()
    assert isinstance(status, LocalStatus)
    assert not status.is_playing


def test_local_status_helpers():
    status = LocalStatus(filename="/music/song.mp3", title="Song", paused=False, position=30, duration=60)
    assert status.is_playing
    assert status.progress == pytest.approx(0.5)
    assert status.label == "Song"
    assert not LocalStatus().is_playing
    assert LocalStatus().label == "(nothing queued)"


def test_local_player_volume_is_clamped(tmp_path):
    player = LocalPlayer(socket_path=tmp_path / "mpv.sock", binary="definitely-not-a-real-mpv")
    assert player.set_volume(500) is False  # no mpv running, but no exception either
    assert player.volume == 100.0
    player.set_volume(-20)
    assert player.volume == 0.0


# --------------------------------------------------------------------------
# the controller's source policy
# --------------------------------------------------------------------------

class FakeMpris:
    def __init__(self, players=("firefox",), playing=True):
        self._players = list(players)
        self.playing = {player: playing for player in self._players}
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.tracks = {
            player: Track(player=player, status="Playing" if playing else "Paused",
                          title=f"Song on {player}", artist="Someone")
            for player in self._players
        }

    def players(self):
        return list(self._players)

    def track(self, player=None):
        track = self.tracks.get(player or "")
        if track is not None:
            track.status = "Playing" if self.playing.get(track.player) else "Paused"
        return track

    def status(self, player=None):
        return "Playing" if self.playing.get(player or "") else "Paused"

    def pause(self, player=None):
        self.playing[player or ""] = False
        self.paused.append(player)
        return True

    def play(self, player=None):
        self.playing[player or ""] = True
        self.resumed.append(player)
        return True

    def play_pause(self, player=None):
        key = player or ""
        self.playing[key] = not self.playing.get(key, False)
        if self.playing[key]:
            self.resumed.append(player)
        else:
            self.paused.append(player)
        return True

    def next_track(self, player=None):
        return True

    def previous_track(self, player=None):
        return True

    def set_volume(self, level, player=None):
        return True


def make_controller(tmp_path, mpris_playing: bool = True, **settings) -> MusicController:
    base = {
        "visualiser": False,
        "music_folders": [],
        "music_volume": 55,
    }
    base.update(settings)
    controller = MusicController(base, state_dir=tmp_path)
    controller.mpris = FakeMpris(playing=mpris_playing)  # type: ignore[assignment]
    controller.player = LocalPlayer(  # type: ignore[assignment]
        socket_path=tmp_path / "mpv.sock", binary="definitely-not-a-real-mpv", volume=55
    )
    return controller


def test_source_list_off_first_then_players_then_local(tmp_path):
    controller = make_controller(tmp_path)
    assert controller.available_sources() == [OFF, f"{MPRIS_PREFIX}firefox", LOCAL]
    labels = dict(controller.source_labels())
    assert labels[OFF] == "Off"
    assert "Apple Music" in labels[f"{MPRIS_PREFIX}firefox"]
    assert labels[LOCAL] == "OmaPoker Local"


def test_muted_volume_is_preserved_on_startup(tmp_path):
    controller = MusicController({"visualiser": False, "music_volume": 0}, state_dir=tmp_path)
    assert controller.volume == 0
    assert controller.player.volume == 0


def test_cycling_sources_wraps_around(tmp_path):
    controller = make_controller(tmp_path, music_source=OFF)
    assert controller.cycle_source() == f"{MPRIS_PREFIX}firefox"
    assert controller.cycle_source() == LOCAL
    assert controller.cycle_source() == OFF


def test_switching_away_pauses_only_what_omapoker_started(tmp_path):
    # The player is already going and we did not start it, so leaving it alone
    # is the polite thing to do.
    controller = make_controller(tmp_path, music_source=f"{MPRIS_PREFIX}firefox")
    controller.refresh()
    controller.set_source(LOCAL)
    assert controller.mpris.paused == []

    # Same controller, but now the player is paused until *we* press play.
    owned = make_controller(
        tmp_path, mpris_playing=False, music_source=f"{MPRIS_PREFIX}firefox"
    )
    owned.refresh()
    assert owned.play() is True
    owned.set_source(OFF)
    assert owned.mpris.paused == ["firefox"]


def test_off_never_kills_a_player_you_manage(tmp_path):
    controller = make_controller(tmp_path, music_source=f"{MPRIS_PREFIX}firefox")
    controller.refresh()
    assert controller.state().title == "Song on firefox"
    controller.set_source(OFF)
    assert controller.mpris.paused == []
    assert controller.state().source == OFF
    assert controller.chip_text() == "Off"


def test_a_player_that_disappears_falls_back_to_off(tmp_path):
    controller = make_controller(tmp_path, music_source=f"{MPRIS_PREFIX}firefox")
    controller.refresh()
    assert controller.state().title == "Song on firefox"
    controller.mpris.tracks.clear()  # type: ignore[attr-defined]
    controller.refresh()
    assert controller.state().source == OFF


def test_chip_text_reports_play_state(tmp_path):
    controller = make_controller(tmp_path, music_source=f"{MPRIS_PREFIX}firefox")
    controller.refresh()
    assert "Song on firefox" in controller.chip_text()
    assert "\u25b6" in controller.chip_text()


def test_levels_are_flat_when_the_visualiser_is_off(tmp_path):
    controller = make_controller(tmp_path)
    levels, live = controller.levels(6)
    assert levels == [0.0] * 6
    assert live is False


def test_volume_is_clamped_and_persisted_in_settings(tmp_path):
    controller = make_controller(tmp_path)
    assert controller.set_volume(999) == 100
    assert controller.set_volume(-5) == 0
    updates = controller.settings_updates()
    assert updates["music_volume"] == 0
    assert updates["music_source"] == OFF
    assert "visualiser" in updates


def test_toggles_are_reported(tmp_path):
    controller = make_controller(tmp_path, music_source=LOCAL)
    assert controller.set_shuffle(True) is True
    assert controller.set_repeat(True) is True
    assert controller.settings_updates()["music_shuffle"] is True
    assert controller.settings_updates()["music_repeat"] is True


def test_playing_local_files_without_mpv_fails_cleanly(tmp_path):
    controller = make_controller(tmp_path)
    song = make_audio(tmp_path / "songs/song.mp3")
    assert controller.play_local([song]) is False
    # And the local track helper still packages the song correctly.
    track = read_tags(song)
    assert isinstance(track, LocalTrack)
    assert controller.enqueue_local([track]) is False


def test_local_source_label_and_status_line(tmp_path):
    controller = make_controller(tmp_path, music_source=LOCAL)
    assert controller.source_label(LOCAL) == "OmaPoker Local"
    line = controller.status_line()
    assert "OmaPoker Local" in line
    assert "mpv" in line  # honest about local playback being unavailable


def test_shutdown_never_raises(tmp_path):
    controller = make_controller(tmp_path)
    controller.shutdown()


def test_no_real_subprocesses_are_spawned_by_a_broken_environment(tmp_path):
    """A guard against accidental process spawning in tests."""
    controller = make_controller(tmp_path)
    controller.refresh()
    controller.levels(4)
    controller.shutdown()
    assert not controller.player.is_running()
