import io
import wave

from app.services import listening_audio, ielts_pronunciation


def test_mp3_join_and_duration(fixture_bytes):
    seg = fixture_bytes("speech.mp3")
    one = listening_audio.mp3_duration(seg)
    assert one > 1
    joined = listening_audio.join_mp3([seg, seg])
    assert abs(listening_audio.mp3_duration(joined) - 2 * one) < 0.2


def test_pronunciation_sample_from_webm(fixture_bytes):
    wav, seconds = ielts_pronunciation.build_sample([fixture_bytes("answer.webm"), b"broken"])
    assert seconds > 1
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
