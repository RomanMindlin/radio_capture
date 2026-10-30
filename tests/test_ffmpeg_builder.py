from app.services.ffmpeg_builder import FfmpegBuilder


def _build(stream_overrides=None):
    base = {
        "url": "https://example.com/live.m3u8",
        "name": "Kan Bet",
        "mandatory_params": {"format": "wav", "segment_time": 30},
        "optional_params": {},
    }
    if stream_overrides:
        base.update(stream_overrides)
    return FfmpegBuilder(base).build_command()


def test_mp3_uses_transcoding_codec_not_copy():
    cmd = _build({"mandatory_params": {"format": "mp3", "segment_time": 30}})
    assert "-segment_format" in cmd
    assert cmd[cmd.index("-segment_format") + 1] == "mp3"
    assert "-c:a" in cmd
    assert cmd[cmd.index("-c:a") + 1] == "libmp3lame"


def test_copy_codec_skips_audio_shape_flags():
    cmd = _build(
        {
            "mandatory_params": {
                "format": "aac",
                "segment_time": 30,
                "channels": 1,
                "sample_rate": 16000,
            },
            "optional_params": {"codec": "copy"},
        }
    )
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-ac" not in cmd
    assert "-ar" not in cmd


def test_transcode_only_applies_explicit_channels_and_sample_rate():
    cmd = _build(
        {
            "mandatory_params": {
                "format": "mp3",
                "segment_time": 30,
                "channels": 2,
                "sample_rate": 48000,
            }
        }
    )
    assert cmd[cmd.index("-c:a") + 1] == "libmp3lame"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[cmd.index("-ar") + 1] == "48000"


def test_hls_input_skips_reconnect_flags():
    cmd = _build({"url": "https://example.com/live/playlist.m3u8?bitrate=192000"})
    assert "-reconnect" not in cmd
    assert "-reconnect_at_eof" not in cmd
    assert cmd[cmd.index("-timeout") + 1] == "30000000"


def test_progressive_http_input_keeps_reconnect_flags():
    cmd = _build({"url": "https://example.com/Live/icecast.audio"})
    assert cmd[cmd.index("-reconnect_at_eof") + 1] == "1"
    assert cmd[cmd.index("-timeout") + 1] == "30000000"
