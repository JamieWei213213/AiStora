from services.schema_agent import build_auto_analysis_goal, build_query_suggestions


SCHEMA = {
    "sales": {
        "types": {
            "sale_id": "int",
            "region": "str",
            "amount": "float",
            "sale_date": "str",
        },
        "row_count": 100,
    }
}


def test_suggestions_are_derived_from_column_roles():
    suggestions = build_query_suggestions(SCHEMA)
    questions = [item["question"] for item in suggestions]

    assert any("highest amount" in question for question in questions)
    assert any("amount" in question and "region" in question for question in questions)
    assert all("highest sale_id" not in question for question in questions)


def test_relationships_create_join_suggestions():
    suggestions = build_query_suggestions(SCHEMA, [{
        "from_table": "sales",
        "from_column": "customer_id",
        "to_table": "customers",
        "to_column": "id",
    }], limit=10)

    assert any(item["label"] == "Join sales + customers" for item in suggestions)


def test_auto_goal_contains_profiles_and_execution_instruction():
    goal = build_auto_analysis_goal(SCHEMA)

    assert "AUTONOMOUS SCHEMA EXPLORATION GOAL" in goal
    assert '"numeric": ["amount"]' in goal
    assert "execute the analysis" in goal
