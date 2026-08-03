from engine.dataframe import DataFrame
from services.agent_service import run_agent
from services.agent_tools import AgentToolRuntime
from services.llm_service import AgentFunctionCall, AgentModelTurn


SCHEMA = {"sales": {"types": {"amount": "float"}, "row_count": 2}}


class Audit:
    def record(self, *args):
        pass


class FakeSession:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.observations = []

    def send(self, message):
        return next(self.turns)

    def send_tool_results(self, results):
        self.observations.append(results)
        return next(self.turns)


class FakeModel:
    def __init__(self, turns):
        self.session = FakeSession(turns)

    def start_agent(self, system_prompt, declarations):
        assert any(tool["name"] == "finish" for tool in declarations)
        return self.session


def test_native_tool_loop_plans_executes_and_finishes():
    model = FakeModel([
        AgentModelTurn("", [
            AgentFunctionCall("record_plan", {"steps": ["Find top sales", "Return them"]}),
        ]),
        AgentModelTurn("", [
            AgentFunctionCall("top_rows", {
                "source": "sales",
                "sort_column": "amount",
                "limit": 2,
                "descending": True,
                "save_as": "top_sales",
            }),
        ]),
        AgentModelTurn("", [
            AgentFunctionCall("finish", {
                "source": "top_sales",
                "message": "Here are the top sales.",
            }),
        ]),
    ])
    runtime = AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda _: DataFrame([{"amount": 10}, {"amount": 20}]),
        request_id="r",
        user_id=1,
        audit=Audit(),
    )

    outcome = run_agent(model, "top sales", SCHEMA, [], [], runtime)

    assert outcome.status == "finished"
    assert outcome.result.name == "top_sales"
    assert outcome.plan == ["Find top sales", "Return them"]
    assert outcome.tool_calls == 3
    assert "20" not in str(model.session.observations)


def test_agent_can_pause_for_clarification():
    model = FakeModel([
        AgentModelTurn("", [
            AgentFunctionCall("ask_clarification", {
                "question": "Which amount column should I use?",
            }),
        ]),
    ])
    runtime = AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda _: DataFrame([]),
        request_id="r",
        user_id=1,
        audit=Audit(),
    )

    outcome = run_agent(model, "compare amounts", SCHEMA, [], [], runtime)
    assert outcome.status == "clarification"
    assert "Which amount" in outcome.message


def test_auto_mode_adds_autonomous_schema_instructions():
    class CapturingModel(FakeModel):
        def start_agent(self, system_prompt, declarations):
            self.system_prompt = system_prompt
            return self.session

    model = CapturingModel([AgentModelTurn("No tools needed.", [])])
    runtime = AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda _: DataFrame([]),
        request_id="r",
        user_id=1,
        audit=Audit(),
    )

    run_agent(model, "auto goal", SCHEMA, [], [], runtime, mode="auto")

    assert "autonomous schema-exploration mode" in model.system_prompt
    assert "do not stop at suggestions" in model.system_prompt


def test_agent_can_repair_a_result_rejected_by_deterministic_verification():
    model = FakeModel([
        AgentModelTurn("", [
            AgentFunctionCall("finish", {
                "message": "Done.",
            }),
        ]),
        AgentModelTurn("", [
            AgentFunctionCall("top_rows", {
                "source": "sales",
                "sort_column": "amount",
                "limit": 2,
                "descending": True,
                "save_as": "top_sales",
            }),
        ]),
        AgentModelTurn("", [
            AgentFunctionCall("finish", {
                "source": "top_sales",
                "message": "Top sales are ready.",
            }),
        ]),
    ])
    runtime = AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda _: DataFrame([{"amount": 10}, {"amount": 20}]),
        request_id="r",
        user_id=1,
        audit=Audit(),
    )

    def verifier(candidate):
        passed = candidate.result is not None
        return {
            "passed": passed,
            "score": 1.0 if passed else 0.0,
            "warnings": [] if passed else ["A named result is required."],
        }

    outcome = run_agent(
        model,
        "top sales",
        SCHEMA,
        [],
        [],
        runtime,
        verifier=verifier,
    )

    assert outcome.result.name == "top_sales"
    assert outcome.verification["passed"] is True
    assert model.session.observations[0][0]["response"]["status"] == "error"
    assert any(item["status"] == "warning" for item in outcome.trace)
