import json

from src.sleeper_client import SleeperClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResponse(200, self.payload)


def test_get_caches_to_disk_and_avoids_second_network_call(tmp_path):
    session = FakeSession({"league_id": "abc", "name": "Test League"})
    client = SleeperClient(cache_dir=tmp_path, session=session)

    first = client.get_league("abc")
    second = client.get_league("abc")

    assert first == second == {"league_id": "abc", "name": "Test League"}
    assert session.calls == 1  # second call served from disk cache


def test_force_refresh_bypasses_cache(tmp_path):
    session = FakeSession({"v": 1})
    client = SleeperClient(cache_dir=tmp_path, session=session)
    client.get_league("abc")

    session.payload = {"v": 2}
    result = client.get_league("abc", force_refresh=True)

    assert result == {"v": 2}
    assert session.calls == 2


def test_cache_expires_after_ttl(tmp_path, monkeypatch):
    session = FakeSession({"v": 1})
    client = SleeperClient(cache_dir=tmp_path, session=session)

    import src.sleeper_client as sc

    fake_time = [1000.0]
    monkeypatch.setattr(sc.time, "time", lambda: fake_time[0])

    client._get("/state/nfl", ttl_seconds=10)
    assert session.calls == 1

    fake_time[0] += 5  # within TTL
    client._get("/state/nfl", ttl_seconds=10)
    assert session.calls == 1

    fake_time[0] += 20  # past TTL
    client._get("/state/nfl", ttl_seconds=10)
    assert session.calls == 2


def test_non_200_raises_sleeper_error(tmp_path):
    from src.sleeper_client import SleeperError

    class Fail500Session(FakeSession):
        def get(self, url, params=None, timeout=None):
            self.calls += 1
            return FakeResponse(500, None)

    client = SleeperClient(cache_dir=tmp_path, session=Fail500Session({}))
    try:
        client.get_league("abc")
        assert False, "expected SleeperError"
    except SleeperError:
        pass
