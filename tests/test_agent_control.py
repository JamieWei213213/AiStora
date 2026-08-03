from services.agent_control import CancellationRegistry


def test_cancellation_registry_lifecycle(tmp_path):
    registry = CancellationRegistry(str(tmp_path))
    event = registry.register("request")

    assert event.is_set() is False
    assert registry.cancel("request") is True
    assert event.is_set() is True

    registry.clear("request")
    assert registry.cancel("request") is False


def test_cancellation_is_visible_across_worker_registries(tmp_path):
    first_worker = CancellationRegistry(str(tmp_path))
    second_worker = CancellationRegistry(str(tmp_path))
    signal = first_worker.register("shared-request")

    assert second_worker.cancel("shared-request") is True
    assert signal.is_set() is True

    first_worker.clear("shared-request")
