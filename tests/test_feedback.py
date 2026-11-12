import app.routes.api as api


def test_feedback_flow(client, db, monkeypatch):
    r = client.post("/api/feedback", json={"role": "teacher", "section": "Грамматика", "kind": "wish", "rating": 4,
                                           "comment": "Хотелось бы больше упражнений на времена.",
                                           "context": {"lang": "ru", "level": "B1.2"}})
    assert r.json()["saved"]
    assert client.post("/api/feedback", json={"section": "x", "comment": "ok", "rating": 9}).status_code == 422
    mine = client.get("/api/feedback/mine").json()["items"]
    assert mine[0]["section"] == "Грамматика" and mine[0]["rating"] == 4
    assert client.get("/api/admin/feedback").status_code == 403          # not an admin
    monkeypatch.setattr(api, "_is_admin", lambda user: True)
    a = client.get("/api/admin/feedback").json()
    assert a["summary"][0] == {"section": "Грамматика", "count": 1, "avg_rating": 4.0}
    fid = a["items"][0]["id"]
    client.post(f"/api/admin/feedback/{fid}", json={"status": "done"})
    assert db.tables["feedback"][0]["status"] == "done"
    csv = client.get("/api/admin/feedback.csv")
    text = csv.content.decode("utf-8-sig")
    assert csv.status_code == 200 and "Грамматика" in text and "B1.2" in text and text.startswith("Дата;")
