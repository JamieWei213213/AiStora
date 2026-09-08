from dataclasses import dataclass
import time

from config import Config
from services.logger import get_logger


logger = get_logger(__name__)
model = None
model_pool = {}


GEMINI_QUOTA_MESSAGE = (
    "Gemini quota is exhausted for the Google AI project connected to this API "
    "key. The key and model are working, but Google is not allowing another AI "
    "request. Check Usage and Billing in Google AI Studio, or try again after "
    "the quota resets."
)


def is_gemini_quota_error(exc):
    """Recognize quota errors without depending on one SDK exception class."""
    current = exc
    for _ in range(4):
        if current is None:
            break
        code = getattr(current, "code", None)
        status = getattr(current, "status", None)
        text = " ".join(
            str(value)
            for value in (type(current).__name__, code, status, current)
            if value is not None
        ).upper()
        if (
            "RESOURCE_EXHAUSTED" in text
            or "QUOTA_EXCEEDED" in text
            or ("429" in text and ("QUOTA" in text or "RESOURCE" in text))
        ):
            return True
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )
    return False


def is_transient_gemini_error(exc):
    if is_gemini_quota_error(exc):
        return False
    current = exc
    for _ in range(4):
        if current is None:
            break
        code = getattr(current, "code", None)
        status = getattr(current, "status", None)
        text = " ".join(
            str(value)
            for value in (type(current).__name__, code, status, current)
            if value is not None
        ).upper()
        if any(marker in text for marker in (
            "500 INTERNAL",
            "502",
            "503",
            "504",
            "CONNECTION RESET",
            "CONNECTIONERROR",
            "DEADLINE_EXCEEDED",
            "SERVICE_UNAVAILABLE",
            "TIMEOUT",
            "TIMED OUT",
            "UNAVAILABLE",
        )):
            return True
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )
    return False


SYSTEM_PROMPT = """
You are AIStora's private data-analysis agent.
Follow the task-specific instructions and use only the provided tools.
Never request raw database rows. AIStora executes tools locally and sends you
only schemas, safe metadata, and abbreviated errors.
"""


@dataclass
class AgentFunctionCall:
    name: str
    arguments: dict


@dataclass
class AgentModelTurn:
    text: str
    calls: list


class GeminiAgentSession:
    def __init__(self, chat, types_module, request_with_retry):
        self.chat = chat
        self.types = types_module
        self.request_with_retry = request_with_retry
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}
        self.retry_count = 0

    def _normalize(self, response):
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        self.usage["input_tokens"] += input_tokens
        self.usage["output_tokens"] += output_tokens
        self.usage["total_tokens"] += input_tokens + output_tokens
        input_rate = float(getattr(Config, "AGENT_INPUT_COST_PER_MILLION", 0.0))
        output_rate = float(getattr(Config, "AGENT_OUTPUT_COST_PER_MILLION", 0.0))
        self.usage["estimated_cost_usd"] = round(
            (self.usage["input_tokens"] * input_rate + self.usage["output_tokens"] * output_rate) / 1_000_000,
            8,
        )
        calls = [
            AgentFunctionCall(call.name, dict(call.args or {}))
            for call in (response.function_calls or [])
        ]
        return AgentModelTurn(response.text or "", calls)

    def send(self, message):
        return self._normalize(
            self.request_with_retry(lambda: self.chat.send_message(message))
        )

    def send_tool_results(self, results):
        parts = [
            self.types.Part.from_function_response(
                name=result["name"],
                response={"result": result["response"]},
            )
            for result in results
        ]
        return self._normalize(
            self.request_with_retry(lambda: self.chat.send_message(parts))
        )


class GeminiModel:
    def __init__(
        self,
        api_key,
        model_name="gemini-3.5-flash-lite",
        max_retries=None,
        retry_base_seconds=None,
        request_timeout_seconds=None,
        max_output_tokens=None,
    ):
        from google import genai
        from google.genai import types

        self.types = types
        self.model_name = model_name
        self.request_timeout_seconds = (
            int(getattr(Config, "AGENT_LLM_TIMEOUT_SECONDS", 30))
            if request_timeout_seconds is None
            else max(int(request_timeout_seconds), 1)
        )
        self.max_output_tokens = (
            int(getattr(Config, "AGENT_LLM_MAX_OUTPUT_TOKENS", 2048))
            if max_output_tokens is None
            else max(int(max_output_tokens), 1)
        )
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=self.request_timeout_seconds * 1000,
            ),
        )
        self.max_retries = (
            int(getattr(Config, "AGENT_LLM_MAX_RETRIES", 2))
            if max_retries is None
            else max(int(max_retries), 0)
        )
        self.retry_base_seconds = (
            float(getattr(Config, "AGENT_LLM_RETRY_BASE_SECONDS", 0.5))
            if retry_base_seconds is None
            else max(float(retry_base_seconds), 0.0)
        )
        self.retry_count = 0

    def _request(self, operation):
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                if attempt >= self.max_retries or not is_transient_gemini_error(exc):
                    raise
                self.retry_count += 1
                delay = min(self.retry_base_seconds * (2 ** attempt), 4.0)
                logger.warning(
                    "Retrying transient Gemini failure for %s in %.2fs (%s/%s)",
                    self.model_name,
                    delay,
                    attempt + 1,
                    self.max_retries,
                )
                if delay:
                    time.sleep(delay)

    def generate_content(self, prompt):
        return self._request(
            lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=self.max_output_tokens,
                ),
            )
        )

    def start_agent(self, system_prompt, tool_declarations):
        tool = self.types.Tool(function_declarations=tool_declarations)
        config = self.types.GenerateContentConfig(
            system_instruction=f"{SYSTEM_PROMPT}\n\n{system_prompt}",
            tools=[tool],
            tool_config=self.types.ToolConfig(
                function_calling_config=self.types.FunctionCallingConfig(
                    mode=self.types.FunctionCallingConfigMode.VALIDATED,
                    allowed_function_names=[
                        declaration["name"] for declaration in tool_declarations
                    ],
                ),
            ),
            max_output_tokens=self.max_output_tokens,
            automatic_function_calling=self.types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        chat = self.client.chats.create(model=self.model_name, config=config)
        session = GeminiAgentSession(chat, self.types, self._request)
        original_request = session.request_with_retry
        def request_with_retry(operation):
            before = self.retry_count
            response = original_request(operation)
            session.retry_count += self.retry_count - before
            return response
        session.request_with_retry = request_with_retry
        return session


def configure_llm():
    global model, model_pool
    try:
        if Config.GEMINI_API_KEY:
            standard = GeminiModel(Config.GEMINI_API_KEY, Config.GEMINI_MODEL)
            advanced_name = getattr(
                Config,
                "GEMINI_ADVANCED_MODEL",
                Config.GEMINI_MODEL,
            )
            advanced = (
                GeminiModel(Config.GEMINI_API_KEY, advanced_name)
                if getattr(Config, "AGENT_MODEL_ROUTING", True)
                and advanced_name != Config.GEMINI_MODEL
                else standard
            )
            model = standard
            model_pool = {"standard": standard, "advanced": advanced}
            logger.info(
                "Gemini AI configured with standard=%s advanced=%s",
                standard.model_name,
                advanced.model_name,
            )
        else:
            model = None
            model_pool = {}
            logger.warning("GEMINI_API_KEY not found in environment")
    except Exception as exc:
        model = None
        model_pool = {}
        logger.error("Error configuring Gemini API: %s", exc)


def get_model(tier="standard"):
    return model_pool.get(tier) or model
