MAX_MEMORY_ITEMS = 8
MAX_TEXT_LENGTH = 300


def _clean_text(value):
    return " ".join(str(value or "").split())[:MAX_TEXT_LENGTH]


def get_memory(session_store, project_id):
    memories = session_store.get("agent_memories", {})
    return list(memories.get(str(project_id), []))


def add_memory(session_store, project_id, user_text, assistant_text):
    memories = dict(session_store.get("agent_memories", {}))
    project_key = str(project_id)
    items = list(memories.get(project_key, []))
    items.append({
        "user": _clean_text(user_text),
        "assistant": _clean_text(assistant_text),
    })
    memories[project_key] = items[-MAX_MEMORY_ITEMS:]
    session_store["agent_memories"] = memories
    session_store.modified = True


def clear_memory(session_store, project_id):
    memories = dict(session_store.get("agent_memories", {}))
    memories.pop(str(project_id), None)
    session_store["agent_memories"] = memories
    session_store.pop("pending_agent", None)
    session_store.modified = True
