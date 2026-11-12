from app.services import storage_cleanup

OLD, NEW = "2020-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"


def test_old_audio_removed_bank_kept(db):
    db.tables["ielts_mock_tests"] = [
        {"id": "old", "status": "completed", "started_at": OLD, "completed_at": OLD, "metadata": {}},
        {"id": "pending", "status": "in_progress", "started_at": OLD, "completed_at": None,
         "metadata": {"speaking_assessment": "pending"}},
        {"id": "new", "status": "completed", "started_at": NEW, "completed_at": NEW, "metadata": {}}]
    db.tables["ielts_mock_sections"] = []
    db.tables["tests"] = []
    db.store.update({p: b"x" for p in ("ielts-mock/old/part1.mp3", "ielts-mock/pending/part1.mp3",
                                       "ielts-mock/new/part1.mp3", "ielts-bank/b1/1.mp3")})
    storage_cleanup.run()
    assert "ielts-mock/old/part1.mp3" not in db.store
    assert {"ielts-mock/pending/part1.mp3", "ielts-mock/new/part1.mp3", "ielts-bank/b1/1.mp3"} <= set(db.store)
