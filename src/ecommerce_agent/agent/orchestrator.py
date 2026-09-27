import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ecommerce_agent.agent.trace import AgentRunRecord, TraceRecorder
from ecommerce_agent.config import get_settings
from ecommerce_agent.llm.client import ImageMediaType, LLMClient, LLMClientError
from ecommerce_agent.llm.client import SearchProvider
from ecommerce_agent.models.product import FieldConfidence, FieldSource, Product, ProductDraft
from ecommerce_agent.tools.duplicates import DuplicateCheckResult, check_duplicate_product
from ecommerce_agent.tools.extraction import ProductExtractionError, extract_product_attributes
from ecommerce_agent.tools.persistence import flag_for_human_review, save_product
from ecommerce_agent.tools.search import SEARCHABLE_FIELDS, search_product_info
from ecommerce_agent.tools.validation import ValidationResult, validate_product

# A call is "repeated" if it has the same name AND arguments as the immediately preceding
# call. This many consecutive repeats are tolerated (the model gets one chance to notice
# an error tool_result and try something else) before the run is aborted.
_MAX_IDENTICAL_TOOL_CALLS = 2

_REQUIRED_FOR_VALIDITY = ("category", "price", "currency", "gtin")

AGENT_SYSTEM_PROMPT = """\
You are an autonomous e-commerce product data agent. A vision model has already extracted an \
initial product draft from a photograph; your job is to decide, one tool call at a time, what \
to do with it until it is either saved or flagged for human review.

Price and currency are business-controlled inputs already present on the draft. Never search \
the web for price or currency, never change those values, and never treat a price visible in \
the photo as the selling price.

You have five tools. Call exactly one tool per turn - never reply with plain text only, and \
never request more than one tool call at once.

There is no fixed order - decide based on what the current draft actually needs:
- If a field that matters for validity (category, gtin) is missing or has low \
  confidence, and you have not already searched for it in this run, search_product_info can \
  look it up. search_product_info cannot look up price or currency.
- validate_product runs deterministic rule checks (required fields, price/currency \
  consistency, GTIN format and checksum, confidence thresholds) against the CURRENT draft. Call \
  it whenever you want an authoritative read on whether the draft is currently valid - including \
  again after search_product_info changes something. It still validates the business-provided \
  price and currency.
- check_duplicate_product looks up whether a product with the same GTIN already exists.
- save_product persists the draft as a final, valid product. It is a terminal action.
- flag_for_human_review persists the draft marked for review with a short reason you provide. \
  It is also terminal. Use it when validation reports errors that more searching will not fix, \
  when the product turns out to be a duplicate, or when you cannot reach a confident decision.

Before calling save_product, you must have already called validate_product (with a valid \
result) and check_duplicate_product (with a not-a-duplicate result) in this run. If you try to \
save without that, or while a blocking error or duplicate is outstanding, the system will refuse \
the call and tell you why - read that message and choose a different tool.

Do not call the same tool with the same arguments repeatedly if it is not changing anything -\
if a tool result does not help, try a different tool or make a final decision instead.
"""

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_product_info",
        "description": (
            "Search the web for one or more product attributes that are currently missing or "
            "have low confidence in the draft. Only request fields that are actually absent or "
            "uncertain - do not re-request a field you already have confidently, and do not "
            "request a field you have already searched for earlier in this run if it did not "
            "help. Price and currency are business-provided and cannot be searched."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(SEARCHABLE_FIELDS)},
                    "description": "Which product fields to search for.",
                    "minItems": 1,
                }
            },
            "required": ["fields"],
        },
    },
    {
        "name": "validate_product",
        "description": (
            "Run deterministic business-rule validation against the current draft: required "
            "fields, price/currency consistency, GTIN format and checksum, and confidence "
            "thresholds. Returns a status plus separate blocking errors and non-blocking "
            "warnings. Safe to call as many times as useful - re-run it after the draft changes."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_duplicate_product",
        "description": (
            "Check the product database for an existing product with the same GTIN as the "
            "current draft. Returns whether a duplicate was found and, if so, its id."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_product",
        "description": (
            "Persist the current draft as a final, valid product record. Terminal action - ends "
            "the run. Requires that validate_product has already reported a valid result and "
            "check_duplicate_product has already reported no duplicate; the system will reject "
            "the call otherwise."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "flag_for_human_review",
        "description": (
            "Persist the current draft marked NEEDS_REVIEW instead of saving it as final. "
            "Terminal action - ends the run. Use this for unresolved validation errors, "
            "duplicates, or when you cannot reach a confident decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "A short explanation for the human reviewer.",
                }
            },
            "required": ["reason"],
        },
    },
]


class AgentOutcome(str, Enum):
    EXTRACTION_FAILED = "extraction_failed"
    AGENT_ERROR = "agent_error"
    DUPLICATE = "duplicate"
    SAVED = "saved"
    NEEDS_REVIEW = "needs_review"


class AgentRunResult(BaseModel):
    outcome: AgentOutcome
    reason: str
    iterations_used: int
    draft: ProductDraft | None = None
    product: Product | None = None
    duplicate_of_product_id: str | None = None
    validation: ValidationResult | None = None
    trace: AgentRunRecord


@dataclass
class _AgentState:
    draft: ProductDraft
    validation: ValidationResult | None = None
    duplicate_result: DuplicateCheckResult | None = None
    searched_fields: set[str] = field(default_factory=set)


def _apply_business_pricing(draft: ProductDraft, *, price: float, currency: str) -> ProductDraft:
    """Stamp business-provided selling price/currency onto the draft. These values are never
    taken from vision extraction or web search."""
    updated = draft.model_copy(deep=True)
    updated.price = FieldConfidence(value=price, confidence=1.0, source=FieldSource.BUSINESS)
    updated.currency = FieldConfidence(
        value=currency.strip().upper(),
        confidence=1.0,
        source=FieldSource.BUSINESS,
    )
    return updated


def _draft_summary(draft: ProductDraft, threshold: float) -> dict[str, Any]:
    def field_view(name: str) -> dict[str, Any] | None:
        fc = getattr(draft, name)
        if fc is None:
            return None
        return {"value": fc.value, "confidence": fc.confidence, "source": fc.source.value}

    return {
        name: field_view(name)
        for name in (
            "title",
            "description",
            "category",
            "brand",
            "manufacturer",
            "color",
            "material",
            "dimensions",
            "weight",
            "price",
            "currency",
            "gtin",
        )
    } | {
        "tags": draft.tags,
        "missing_fields_relevant_to_validity": draft.missing_fields(_REQUIRED_FOR_VALIDITY),
        "low_confidence_fields": draft.low_confidence_fields(threshold),
    }


def _precondition_error(tool_name: str, state: _AgentState) -> str | None:
    """Hard safety gates Python enforces regardless of what the LLM decides.

    These implement the non-negotiable rules: never save without a passing validation, never
    save a known duplicate. This is state bookkeeping, not "when to call which tool" policy -
    the LLM is still the one that decides to attempt save_product in the first place.
    """
    if tool_name != "save_product":
        return None
    if state.validation is None:
        return "Cannot save: validate_product has not been called yet in this run. Call it first."
    if not state.validation.is_valid:
        return f"Cannot save: validate_product reported blocking errors: {state.validation.errors}"
    if state.duplicate_result is None:
        return (
            "Cannot save: check_duplicate_product has not been called yet in this run. "
            "Call it first."
        )
    if state.duplicate_result.is_duplicate:
        return "Cannot save: this product duplicates an existing record. Call flag_for_human_review instead."
    return None


class ProductAgent:
    """Runs an LLM-driven tool-calling loop over a single product image.

    The LLM decides which of the five tools to call, in which order, and when to stop; this
    class only executes whatever it asks for, enforces the safety gates above, tracks state,
    and records the trace. It contains no logic like "if category is missing, call search".
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        search_provider: SearchProvider,
        session: Session,
        max_iterations: int | None = None,
        confidence_threshold: float | None = None,
        extraction_model_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self._llm_client = llm_client
        self._search_provider = search_provider
        self._session = session
        self._max_iterations = (
            max_iterations if max_iterations is not None else settings.max_agent_iterations
        )
        self._threshold = (
            confidence_threshold if confidence_threshold is not None else settings.confidence_threshold
        )
        self._extraction_model_name = extraction_model_name or settings.anthropic_model

    def run(
        self,
        *,
        image_bytes: bytes,
        image_media_type: ImageMediaType,
        source_image_url: str,
        price: float,
        currency: str,
    ) -> AgentRunResult:
        trace = TraceRecorder(source_image_url=source_image_url)
        iteration = 1

        extraction_input_summary = {
            "image_media_type": image_media_type,
            "image_size_bytes": len(image_bytes),
        }
        try:
            draft = extract_product_attributes(
                image_bytes=image_bytes,
                image_media_type=image_media_type,
                source_image_url=source_image_url,
                llm_client=self._llm_client,
            )
        except (LLMClientError, ProductExtractionError) as exc:
            trace.log_tool_call(
                iteration=iteration,
                tool_name="extract_product_attributes",
                input_summary=extraction_input_summary,
                error=str(exc),
            )
            record = trace.finish(outcome=AgentOutcome.EXTRACTION_FAILED, iterations_used=iteration)
            return AgentRunResult(
                outcome=AgentOutcome.EXTRACTION_FAILED,
                reason=f"Image extraction failed: {exc}",
                iterations_used=iteration,
                trace=record,
            )

        trace.log_tool_call(
            iteration=iteration,
            tool_name="extract_product_attributes",
            input_summary=extraction_input_summary,
            output_summary=_draft_summary(draft, self._threshold),
        )

        draft = _apply_business_pricing(draft, price=price, currency=currency)

        state = _AgentState(draft=draft)
        history: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    "Here is the product draft extracted from the image:\n\n"
                    + json.dumps(_draft_summary(draft, self._threshold), indent=2)
                    + "\n\nNo tools have been called yet in this run. Decide what to do next."
                ),
            }
        ]

        last_signature: tuple[str, str] | None = None
        repeat_count = 0

        while True:
            if iteration >= self._max_iterations:
                return self._force_flag(
                    state,
                    trace,
                    iteration=iteration + 1,
                    reason="Iteration limit reached before the agent reached a final decision.",
                )

            iteration += 1

            try:
                turn = self._llm_client.send_agent_turn(
                    system_prompt=AGENT_SYSTEM_PROMPT, messages=history, tools=AGENT_TOOLS
                )
            except LLMClientError as exc:
                trace.log_tool_call(iteration=iteration, tool_name="agent_decision", error=str(exc))
                record = trace.finish(outcome=AgentOutcome.AGENT_ERROR, iterations_used=iteration)
                return AgentRunResult(
                    outcome=AgentOutcome.AGENT_ERROR,
                    reason=f"Agent decision call failed: {exc}",
                    draft=state.draft,
                    iterations_used=iteration,
                    trace=record,
                )

            history.append({"role": "assistant", "content": turn.assistant_content})

            if turn.tool_use is None:
                trace.log_tool_call(
                    iteration=iteration,
                    tool_name="agent_turn_no_tool_call",
                    output_summary={"text": turn.text, "stop_reason": turn.stop_reason},
                    llm_initiated=True,
                )
                history.append(
                    {"role": "user", "content": "You must call exactly one of the available tools."}
                )
                continue

            tool_name = turn.tool_use.name
            tool_args = turn.tool_use.input
            signature = (tool_name, json.dumps(tool_args, sort_keys=True))

            if signature == last_signature:
                repeat_count += 1
            else:
                repeat_count = 1
                last_signature = signature

            if repeat_count > _MAX_IDENTICAL_TOOL_CALLS:
                trace.log_tool_call(
                    iteration=iteration,
                    tool_name=tool_name,
                    input_summary=tool_args,
                    llm_initiated=True,
                    error="aborted: identical tool call repeated without making progress",
                )
                return self._force_flag(
                    state,
                    trace,
                    iteration=iteration,
                    reason=(
                        f"Aborted after '{tool_name}' was called repeatedly with the same "
                        "arguments without changing the outcome."
                    ),
                )

            blocking_reason = _precondition_error(tool_name, state)
            if blocking_reason is not None:
                trace.log_tool_call(
                    iteration=iteration,
                    tool_name=tool_name,
                    input_summary=tool_args,
                    llm_initiated=True,
                    error=blocking_reason,
                )
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": turn.tool_use.id,
                                "content": blocking_reason,
                                "is_error": True,
                            }
                        ],
                    }
                )
                continue

            result_summary, is_error, terminal_outcome, product = self._execute_tool(
                tool_name, tool_args, state
            )
            trace.log_tool_call(
                iteration=iteration,
                tool_name=tool_name,
                input_summary=tool_args,
                output_summary=result_summary,
                llm_initiated=True,
                error=result_summary.get("error") if is_error else None,
            )

            if terminal_outcome is not None:
                record = trace.finish(outcome=terminal_outcome, iterations_used=iteration)
                duplicate_id = (
                    str(state.duplicate_result.matched_product_id)
                    if state.duplicate_result and state.duplicate_result.matched_product_id
                    else None
                )
                return AgentRunResult(
                    outcome=terminal_outcome,
                    reason=result_summary.get("reason", f"Agent called {tool_name}."),
                    draft=state.draft,
                    product=product,
                    validation=state.validation,
                    duplicate_of_product_id=duplicate_id,
                    iterations_used=iteration,
                    trace=record,
                )

            history.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": turn.tool_use.id,
                            "content": json.dumps(result_summary),
                            "is_error": is_error,
                        }
                    ],
                }
            )

    def _execute_tool(
        self, name: str, args: dict[str, Any], state: _AgentState
    ) -> tuple[dict[str, Any], bool, AgentOutcome | None, Product | None]:
        """Execute one LLM-requested tool. Returns (result_summary, is_error, outcome, product).

        `outcome`/`product` are non-None only for the two terminal tools.
        """
        if name == "search_product_info":
            fields = args.get("fields") or []
            invalid = [f for f in fields if f not in SEARCHABLE_FIELDS]
            if not fields or invalid:
                return (
                    {"error": f"'fields' must be a non-empty subset of {list(SEARCHABLE_FIELDS)}."},
                    True,
                    None,
                    None,
                )
            try:
                state.draft = search_product_info(
                    draft=state.draft, fields=fields, search_provider=self._search_provider
                )
            except LLMClientError as exc:
                return {"error": str(exc)}, True, None, None
            state.searched_fields.update(fields)
            return (
                {"searched_fields": fields, "updated_draft": _draft_summary(state.draft, self._threshold)},
                False,
                None,
                None,
            )

        if name == "validate_product":
            state.validation = validate_product(state.draft, confidence_threshold=self._threshold)
            return (
                {
                    "status": state.validation.status.value,
                    "errors": state.validation.errors,
                    "warnings": state.validation.warnings,
                },
                False,
                None,
                None,
            )

        if name == "check_duplicate_product":
            state.duplicate_result = check_duplicate_product(self._session, state.draft)
            return (
                {
                    "is_duplicate": state.duplicate_result.is_duplicate,
                    "matched_product_id": (
                        str(state.duplicate_result.matched_product_id)
                        if state.duplicate_result.matched_product_id
                        else None
                    ),
                },
                False,
                None,
                None,
            )

        if name == "save_product":
            product = save_product(
                self._session, state.draft, extraction_model=self._extraction_model_name
            )
            return (
                {"product_id": str(product.id), "reason": "Product passed validation and was saved."},
                False,
                AgentOutcome.SAVED,
                product,
            )

        if name == "flag_for_human_review":
            reason = args.get("reason") or "No reason given."
            validation = state.validation or validate_product(
                state.draft, confidence_threshold=self._threshold
            )
            validation = validation.model_copy(update={"errors": [*validation.errors, f"Agent note: {reason}"]})
            state.validation = validation
            product = flag_for_human_review(
                self._session, state.draft, validation, extraction_model=self._extraction_model_name
            )
            outcome = (
                AgentOutcome.DUPLICATE
                if state.duplicate_result and state.duplicate_result.is_duplicate
                else AgentOutcome.NEEDS_REVIEW
            )
            return {"product_id": str(product.id), "reason": reason}, False, outcome, product

        return {"error": f"Unknown tool '{name}'."}, True, None, None

    def _force_flag(
        self, state: _AgentState, trace: TraceRecorder, *, iteration: int, reason: str
    ) -> AgentRunResult:
        validation = state.validation or validate_product(state.draft, confidence_threshold=self._threshold)
        product = flag_for_human_review(
            self._session, state.draft, validation, extraction_model=self._extraction_model_name
        )
        outcome = (
            AgentOutcome.DUPLICATE
            if state.duplicate_result and state.duplicate_result.is_duplicate
            else AgentOutcome.NEEDS_REVIEW
        )
        trace.log_tool_call(
            iteration=iteration,
            tool_name="flag_for_human_review",
            input_summary={"forced_by_orchestrator": True},
            output_summary={"product_id": str(product.id), "errors": validation.errors},
            llm_initiated=False,
        )
        record = trace.finish(outcome=outcome, iterations_used=iteration)
        return AgentRunResult(
            outcome=outcome,
            reason=reason,
            draft=state.draft,
            product=product,
            validation=validation,
            iterations_used=iteration,
            trace=record,
        )
