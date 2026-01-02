import unittest
from datetime import date, datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine

from app.models.models import Recording, SpeechBlock, Stream
from app.services.speech_blocks import build_speech_blocks


class SpeechBlockBuilderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        stream = Stream(name="Test Stream", url="http://example.com/stream")
        self.session.add(stream)
        self.session.commit()
        self.session.refresh(stream)
        self.stream_id = stream.id

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _add_recording(self, start_ts: datetime, duration: int, classification: str, transcript: str = "") -> Recording:
        rec = Recording(
            stream_id=self.stream_id,
            path=f"/tmp/{start_ts.timestamp()}.wav",
            start_ts=start_ts,
            duration_seconds=duration,
            classification=classification,
            status="completed",
            transcript=transcript,
        )
        self.session.add(rec)
        self.session.commit()
        self.session.refresh(rec)
        return rec

    def test_consecutive_speech_chunks_merge(self):
        base = datetime(2025, 1, 1, 10, 0, 0)
        first = self._add_recording(base, 30, "speech", "hello world")
        second = self._add_recording(base + timedelta(seconds=35), 30, "speech", "second chunk")

        blocks = build_speech_blocks(
            station_id=self.stream_id,
            target_date=date(2025, 1, 1),
            gap_threshold=5,
            min_duration=20,
            session=self.session,
        )

        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.chunk_ids, [first.id, second.id])
        self.assertAlmostEqual(block.duration_seconds, 65.0, places=1)
        self.assertIn("hello world", block.text)
        self.assertIn("second chunk", block.text)

    def test_blocks_split_by_music_and_gap(self):
        base = datetime(2025, 1, 2, 9, 0, 0)
        first = self._add_recording(base, 70, "speech", "segment one")
        self._add_recording(base + timedelta(seconds=120), 30, "music")
        second = self._add_recording(base + timedelta(seconds=180), 80, "speech", "segment two")
        third = self._add_recording(base + timedelta(seconds=360), 70, "speech", "segment three")

        blocks = build_speech_blocks(
            station_id=self.stream_id,
            target_date=date(2025, 1, 2),
            session=self.session,
        )

        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0].chunk_ids, [first.id])
        self.assertEqual(blocks[1].chunk_ids, [second.id])
        self.assertEqual(blocks[2].chunk_ids, [third.id])
        self.assertGreaterEqual(min(b.duration_seconds for b in blocks), 60)


if __name__ == "__main__":
    unittest.main()
