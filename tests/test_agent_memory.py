from services.agent_memory import add_memory, clear_memory, get_memory


class Session(dict):
    modified = False


def test_memory_is_scoped_bounded_and_clearable():
    session = Session()
    for index in range(12):
        add_memory(session, 3, f"question {index}", f"answer {index}")

    memory = get_memory(session, 3)
    assert len(memory) == 8
    assert memory[0]["user"] == "question 4"
    assert get_memory(session, 4) == []

    clear_memory(session, 3)
    assert get_memory(session, 3) == []
