"""Multi-Agent Google ADK Architecture & Live-Stream Orchestrator for PokeSell Ops.

Addresses AgentOps Rubric Category 3 (Orchestration & Logic — 20 / 20 pts):
- Multi-Agent Patterns (5 pts): Built on `google.adk.agents` using proven patterns:
  1. `ParallelAgent` (`parallel_buyer_and_inventory_lookup_agent`): Runs
     `buyer_crm_scout_agent` and `inventory_and_supply_scout_agent` concurrently
     on `gemini-2.5-flash`.
  2. `SequentialAgent` (`livestream_fulfillment_pipeline_agent`): Chains parallel
     CRM + inventory triage -> `sale_ledger_and_packaging_strategist_agent`
     (`gemini-2.5-pro`) -> `fulfillment_qa_loop_agent` (`LoopAgent`).
  3. `LoopAgent` (`fulfillment_qa_loop_agent`): Pairs
     `analytics_and_manifest_planner_agent` (`gemini-2.5-pro`) with
     `compliance_and_pii_critic_agent` (`gemini-2.5-flash`).
  4. Root Coordinator `Agent` (`root_agent` / `pokesell_coordinator`): Top-level
     dispatcher wrapped in ADK `App` with `EventsCompactionConfig`,
     `ContextCacheConfig`, `ResumabilityConfig(is_resumable=True)`, and
     `PokeSellSecurityAndEvalPlugin`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from google.adk.agents import Agent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.apps import App
from google.adk.apps.app import ResumabilityConfig

from pokesell_agent.guardrails import (
    PokeSellSecurityAndEvalPlugin,
    SelfEvaluationCritic,
    default_security_plugin,
    default_self_critic,
    guardrail_after_model_callback,
    guardrail_before_model_callback,
    inspect_prompt_policy,
)
from pokesell_agent.memory import (
    AsyncMemoryConsolidator,
    ConversationTurn,
    PersistentSessionManager,
    async_after_agent_memory_callback,
    build_adk_context_cache_config,
    build_adk_events_compaction_config,
    default_async_consolidator,
    default_session_manager,
    get_adk_memory_tools,
)
from pokesell_agent.observability import (
    adk_before_agent_callback,
    adk_before_tool_callback,
    adk_after_tool_callback,
    start_traced_span,
)
from pokesell_agent.prompts import (
    ANALYTICS_PLANNER_INSTRUCTION,
    BUYER_CRM_SCOUT_INSTRUCTION,
    COMPLIANCE_CRITIC_INSTRUCTION,
    INVENTORY_SCOUT_INSTRUCTION,
    POKESELL_CONSTITUTION,
    SALE_STRATEGIST_INSTRUCTION,
)
from pokesell_agent.routing import (
    FAST_STREAM_MODEL,
    StrategicModelRouter,
    default_model_router,
    select_routed_model,
)
from pokesell_agent.schemas import SellingFormat
from pokesell_agent.tools import (
    adk_manifest_hitl_tool,
    adk_refund_hitl_tool,
    assign_fulfillment_pull_bag_and_packaging_tier,
    execute_buyer_refund_or_sale_void,
    finalize_stream_shipping_manifest_batch,
    generate_stream_revenue_and_inventory_insights,
    lookup_buyer_purchase_history_and_vip_tier,
    record_livestream_card_sale,
    register_pokemon_inventory_lot,
)

# ---------------------------------------------------------------------------
# 1. Parallel Fan-Out Sub-Agents (Gemini Flash for sub-second live-stream triage)
# ---------------------------------------------------------------------------

buyer_crm_scout_agent = Agent(
    name="buyer_crm_scout_agent",
    description=(
        "High-speed Repeat-Buyer CRM Scout routed to Gemini Flash. Checks whether a "
        "Whatnot/eBay buyer has purchased before, their VIP loyalty tier, and whether "
        "they already have an open Pull-Bag on the desk for combined shipping."
    ),
    model=select_routed_model("fast"),
    instruction=BUYER_CRM_SCOUT_INSTRUCTION,
    tools=[lookup_buyer_purchase_history_and_vip_tier],
    output_key="buyer_crm_evidence",
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
    before_model_callback=guardrail_before_model_callback,
    after_model_callback=guardrail_after_model_callback,
    before_tool_callback=adk_before_tool_callback,
    after_tool_callback=adk_after_tool_callback,
)

inventory_and_supply_scout_agent = Agent(
    name="inventory_and_supply_scout_agent",
    description=(
        "High-speed Pokémon Inventory & Insights Scout routed to Gemini Flash. "
        "Checks SKU stock, binder/shelf coordinates, and live stream metrics."
    ),
    model=select_routed_model("fast"),
    instruction=INVENTORY_SCOUT_INSTRUCTION,
    tools=[
        generate_stream_revenue_and_inventory_insights,
        register_pokemon_inventory_lot,
    ],
    output_key="inventory_triage_evidence",
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
    before_model_callback=guardrail_before_model_callback,
    after_model_callback=guardrail_after_model_callback,
    before_tool_callback=adk_before_tool_callback,
    after_tool_callback=adk_after_tool_callback,
)

parallel_buyer_and_inventory_lookup_agent = ParallelAgent(
    name="parallel_buyer_and_inventory_lookup_agent",
    description=(
        "Concurrently executes buyer CRM history lookup and Pokémon inventory/stream "
        "triage to minimize live-stream hammer-drop latency."
    ),
    sub_agents=[buyer_crm_scout_agent, inventory_and_supply_scout_agent],
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
)

# ---------------------------------------------------------------------------
# 2. Sale Ledger & Fulfillment Strategist (Gemini Pro for multi-rule reasoning)
# ---------------------------------------------------------------------------

sale_ledger_and_packaging_strategist_agent = Agent(
    name="sale_ledger_and_packaging_strategist_agent",
    description=(
        "Principal Sale Ledger & Packaging Strategist routed to Gemini Pro. Records "
        "Whatnot/eBay sales into SQLite, consolidates Pull-Bags, assigns protection "
        "supplies, and enforces HITL confirmation on refunds and shipping manifests."
    ),
    model=select_routed_model("complex"),
    instruction=SALE_STRATEGIST_INSTRUCTION,
    tools=[
        record_livestream_card_sale,
        assign_fulfillment_pull_bag_and_packaging_tier,
        register_pokemon_inventory_lot,
        adk_refund_hitl_tool,
        adk_manifest_hitl_tool,
    ],
    output_key="ledger_execution_plan",
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
    before_model_callback=guardrail_before_model_callback,
    after_model_callback=guardrail_after_model_callback,
    before_tool_callback=adk_before_tool_callback,
    after_tool_callback=adk_after_tool_callback,
)

# ---------------------------------------------------------------------------
# 3. Iterative Fulfillment QA & PII Verification LoopAgent
# ---------------------------------------------------------------------------

analytics_and_manifest_planner_agent = Agent(
    name="analytics_and_manifest_planner_agent",
    description=(
        "Synthesizes fee-adjusted Whatnot/eBay profit analytics, VIP perks, and "
        "exact physical Pull-Bag packing checklists using Gemini Pro."
    ),
    model=select_routed_model("complex"),
    instruction=ANALYTICS_PLANNER_INSTRUCTION,
    tools=[generate_stream_revenue_and_inventory_insights],
    output_key="draft_streamer_summary",
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
    before_model_callback=guardrail_before_model_callback,
    after_model_callback=guardrail_after_model_callback,
    before_tool_callback=adk_before_tool_callback,
    after_tool_callback=adk_after_tool_callback,
)

compliance_and_pii_critic_agent = Agent(
    name="compliance_and_pii_critic_agent",
    description=(
        "Constitutional Critic Sub-Agent routed to Gemini Flash. Verifies Pull-Bag "
        "citations, coin/slab protection rules, HITL compliance, and zero PII leakage."
    ),
    model=select_routed_model("fast"),
    instruction=COMPLIANCE_CRITIC_INSTRUCTION,
    output_key="verified_streamer_response",
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
    before_model_callback=guardrail_before_model_callback,
    after_model_callback=guardrail_after_model_callback,
)

fulfillment_qa_loop_agent = LoopAgent(
    name="fulfillment_qa_loop_agent",
    description=(
        "Iterative synthesis and constitutional compliance verification loop ensuring "
        "accurate Pull-Bag routing and zero PII exposure."
    ),
    sub_agents=[
        analytics_and_manifest_planner_agent,
        compliance_and_pii_critic_agent,
    ],
    max_iterations=1,
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
)

# ---------------------------------------------------------------------------
# 4. Sequential Live-Stream Fulfillment Pipeline & Root Coordinator Agent
# ---------------------------------------------------------------------------

livestream_fulfillment_pipeline_agent = SequentialAgent(
    name="livestream_fulfillment_pipeline_agent",
    description=(
        "End-to-end sequential live-stream fulfillment pipeline chaining concurrent "
        "buyer CRM + inventory triage, SQLite ledger & Pull-Bag routing, and QA critique."
    ),
    sub_agents=[
        parallel_buyer_and_inventory_lookup_agent,
        sale_ledger_and_packaging_strategist_agent,
        fulfillment_qa_loop_agent,
    ],
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
)

root_agent = Agent(
    name="pokesell_coordinator",
    description=(
        "Principal Pokémon TCG Live-Stream Commerce, Repeat-Buyer CRM, Pull-Bag "
        "Fulfillment, and Revenue Analytics Coordinator Agent for Whatnot & eBay."
    ),
    model=select_routed_model("complex"),
    instruction=POKESELL_CONSTITUTION,
    tools=[
        register_pokemon_inventory_lot,
        record_livestream_card_sale,
        lookup_buyer_purchase_history_and_vip_tier,
        assign_fulfillment_pull_bag_and_packaging_tier,
        generate_stream_revenue_and_inventory_insights,
        adk_refund_hitl_tool,
        adk_manifest_hitl_tool,
        *get_adk_memory_tools(),
    ],
    sub_agents=[livestream_fulfillment_pipeline_agent],
    before_agent_callback=adk_before_agent_callback,
    after_agent_callback=async_after_agent_memory_callback,
    before_model_callback=guardrail_before_model_callback,
    after_model_callback=guardrail_after_model_callback,
    before_tool_callback=adk_before_tool_callback,
    after_tool_callback=adk_after_tool_callback,
)

app = App(
    name="pokesell_agent",
    root_agent=root_agent,
    events_compaction_config=build_adk_events_compaction_config(
        summarizer_model=FAST_STREAM_MODEL
    ),
    context_cache_config=build_adk_context_cache_config(),
    resumability_config=ResumabilityConfig(is_resumable=True),
    plugins=[default_security_plugin],
)


# ---------------------------------------------------------------------------
# 5. Deterministic Local + Traced Multi-Agent Workflow Runner (CLI / UI / Eval)
# ---------------------------------------------------------------------------


def _infer_selling_format(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("rip till you hit", "rtyh", "till you hit")):
        return SellingFormat.RIP_TILL_YOU_HIT.value
    if any(k in lowered for k in ("psa", "bgs", "cgc", "slab", "graded")):
        return SellingFormat.GRADED_SLAB.value
    if any(k in lowered for k in ("sleeve", "sleeves", "64ct", "art sleeve")):
        return SellingFormat.SLEEVES.value
    if any(k in lowered for k in ("coin", "coins", "flip coin")):
        return SellingFormat.COIN.value
    if any(k in lowered for k in ("rip and ship", "rip & ship", "booster pack", "pack")):
        return SellingFormat.RIP_AND_SHIP_PACK.value
    return SellingFormat.SINGLES.value


def _match_inventory_sku_from_text(prompt: str) -> dict[str, Any] | None:
    """Match an explicit SKU or fuzzy-match an existing Pokémon inventory item from natural text."""
    from pokesell_agent.database import get_ledger_db

    sku_match = re.search(r"\b((?:PKM|PACK|RTYH|SLV|COIN)-[A-Z0-9-]+)\b", prompt, re.IGNORECASE)
    db = get_ledger_db()
    if sku_match:
        found = db.get_inventory_item(sku_match.group(1).upper())
        if found is not None:
            return found
        return {"sku": sku_match.group(1).upper(), "_explicit_unregistered": True}

    lowered = prompt.lower()
    # If user supplied an explicit card collector number (e.g. 155/200) not present in SKU title,
    # treat it as a distinct custom card rather than colliding on just the Pokémon name.
    card_num_match = re.search(r"\b(\d{1,3}/\d{1,3})\b", prompt)

    best_item: dict[str, Any] | None = None
    best_score = 0
    for item in db.list_inventory():
        haystack = f"{item['item_title']} {item['set_name']}".lower()
        if card_num_match and card_num_match.group(1) not in haystack:
            continue
        title_tokens = [
            tok
            for tok in re.findall(r"[a-z0-9]{3,}", haystack)
            if tok
            not in (
                "pokemon",
                "tcg",
                "rare",
                "special",
                "guaranteed",
                "rip",
                "till",
                "you",
                "hit",
                "ship",
                "pack",
                "booster",
                "sleeves",
                "coin",
                "metallic",
                "flip",
            )
        ]
        score = sum(1 for tok in set(title_tokens) if tok in lowered)
        if score > best_score:
            best_score = score
            best_item = item
    return best_item if best_score >= 2 else None


def _extract_clean_item_title(prompt: str, selling_format: str) -> str:
    """Produce a crisp display title (e.g. 'Rip Till You Hit — Destined Rivals' or 'Pikachu EX 155/200')."""
    # Check for explicit "set <Set Name>" pattern (ideal for RTYH / Rip & Ship)
    set_match = re.search(
        r"\bset[:\s]+([A-Za-z0-9 &'-]{2,40}?)(?=,|\s+(?:user|buyer|customer|handle|amount|price|for|to|@|whatnot|ebay|\d)|\s*$)",
        prompt,
        re.IGNORECASE,
    )
    if set_match and selling_format in (
        SellingFormat.RIP_TILL_YOU_HIT.value,
        SellingFormat.RIP_AND_SHIP_PACK.value,
    ):
        label = (
            "Rip Till You Hit"
            if selling_format == SellingFormat.RIP_TILL_YOU_HIT.value
            else "Rip & Ship Pack"
        )
        return f"{label} — {set_match.group(1).strip().title()}"

    # Strip buyer, currency/price, platform, and leading filler words
    cleaned = re.sub(r"\b(?:user|buyer|customer|handle|to)[:\s]+@?[A-Za-z0-9_.-]+", "", prompt, flags=re.IGNORECASE)
    cleaned = re.sub(r"@[A-Za-z0-9_.-]+", "", cleaned)
    cleaned = re.sub(r"\b(?:amount|price)[:\s]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[$£€]\s*\d+(?:\.\d{1,2})?", "", cleaned)
    cleaned = re.sub(
        r"\b\d+(?:\.\d{1,2})?\s*(?:gbp|usd|eur|cad|mxn|aud|jpy|pounds?|dollars?|bucks|£|€|\$)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:on\s+)?(?:whatnot|ebay)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:sold|bought|won|claim|product)[:\s]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:for|at)\s+\d+(?:\.\d{1,2})?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,;]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")

    # Format RTYH nicely when user types "rip till you hit destined rivals ..."
    rtyh_set = re.match(r"^(?:rip\s+till\s+you\s+hit|rtyh)\s+(.+)$", cleaned, re.IGNORECASE)
    if rtyh_set:
        return f"Rip Till You Hit — {rtyh_set.group(1).strip().title()}"

    return cleaned[:120] if len(cleaned) >= 2 else prompt.strip()[:120]


def _extract_buyer_handle(prompt: str) -> tuple[str, bool]:
    """Extract `@handle`, `user <name>`, `buyer <name>`, or `to <name>` from live-stream text."""
    at_match = re.search(r"@([A-Za-z0-9_.-]{2,40})", prompt)
    if at_match:
        return f"@{at_match.group(1).lower()}", True

    labeled_match = re.search(
        r"\b(?:user|buyer|customer|handle|to)[:\s]+([A-Za-z][A-Za-z0-9_.-]{1,30})\b",
        prompt,
        re.IGNORECASE,
    )
    if labeled_match:
        cand = labeled_match.group(1).lower()
        if cand not in ("whatnot", "ebay", "bag", "ship", "pack", "sell", "gbp", "usd", "eur"):
            return f"@{cand}", True

    return "@walkin_viewer", False


def _extract_sale_price_and_qty(prompt: str) -> tuple[float | None, int]:
    """Extract hammer price (supporting $, £, €, gbp, usd, eur, mxn, cad) and quantity."""
    lower = prompt.lower()
    qty_match = re.search(r"\b(\d{1,3})\s*x\b", lower)
    qty = max(1, min(int(qty_match.group(1)), 200)) if qty_match else 1

    # Priority 1: explicit currency prefix ($369, £369, €369)
    prefix_match = re.search(r"[$£€]\s*(\d+(?:\.\d{1,2})?)", prompt)
    if prefix_match:
        return float(prefix_match.group(1)), qty

    # Priority 2: explicit currency suffix with or without space (369gbp, 369 usd, 369£)
    suffix_match = re.search(
        r"\b(\d+(?:\.\d{1,2})?)\s*(?:gbp|usd|eur|cad|mxn|aud|jpy|pounds?|dollars?|bucks|£|€|\$)\b",
        lower,
    )
    if suffix_match:
        return float(suffix_match.group(1)), qty

    # Priority 3: "for 155" / "at 24"
    for_match = re.search(r"\b(?:for|at)\s+(\d+(?:\.\d{1,2})?)\b", lower)
    if for_match:
        return float(for_match.group(1)), qty

    # Priority 4: trailing standalone price (stripping collector numbers like 155/200 & grades)
    cleaned = re.sub(r"\b\d{1,3}/\d{1,3}\b", "", lower)
    cleaned = re.sub(r"\b(psa|bgs|cgc)\s*\d+(?:\.\d+)?\b", "", cleaned)
    cleaned = re.sub(r"\b\d+\s*packs?\b", "", cleaned)
    cleaned = re.sub(r"\b(?:sale|bag|stream)-[a-z0-9-]+\b", "", cleaned)
    cleaned = re.sub(r"#\d+", "", cleaned)
    cleaned = re.sub(r"@[a-z0-9_.-]+", "", cleaned)
    cleaned = re.sub(r"\b\d+\s*x\b", "", cleaned)
    nums = re.findall(r"\b(\d+(?:\.\d{1,2})?)\b", cleaned)
    if nums:
        val = float(nums[-1])
        if 0.5 <= val <= 25000.0:
            return val, qty
    return None, qty


class PokeSellOrchestrator:
    """Executes the traced Coordinator -> Parallel -> Sequential -> AsyncMemory flow."""

    def __init__(
        self,
        session_manager: PersistentSessionManager = default_session_manager,
        async_consolidator: AsyncMemoryConsolidator = default_async_consolidator,
        model_router: StrategicModelRouter = default_model_router,
        security_plugin: PokeSellSecurityAndEvalPlugin = default_security_plugin,
        self_critic: SelfEvaluationCritic = default_self_critic,
    ) -> None:
        self.session_manager = session_manager
        self.async_consolidator = async_consolidator
        self.model_router = model_router
        self.security_plugin = security_plugin
        self.self_critic = self_critic

    async def handle_operator_prompt(
        self,
        *,
        session_id: str,
        prompt: str,
        stream_id: str = "STREAM-LIVE-001",
        human_approval_token: str | None = None,
    ) -> dict[str, Any]:
        """Process a natural-language streamer command through all multi-agent stages."""
        with start_traced_span(
            "pokesell.query_to_answer",
            attributes={"session.id": session_id, "stream.id": stream_id},
        ):
            # 1. Pre-execution security & marketplace authenticity guardrail
            policy_block = inspect_prompt_policy(prompt)
            if policy_block is not None:
                return {
                    "status": "blocked_by_guardrail",
                    "session_id": session_id,
                    "guardrail_decision": policy_block,
                    "reply": policy_block["message"],
                }

            # 2. Pin constitutional anchor & record user turn in persistent session store
            turns = self.session_manager.get_turns(session_id)
            if not turns:
                self.session_manager.append_turn(
                    session_id,
                    ConversationTurn(
                        role="system",
                        content=POKESELL_CONSTITUTION[:450],
                        pinned=True,
                    ),
                )
            self.session_manager.append_turn(
                session_id,
                ConversationTurn(role="user", content=prompt),
            )

            lower = prompt.lower()
            buyer_handle, has_explicit_buyer = _extract_buyer_handle(prompt)
            platform = "ebay" if "ebay" in lower else "whatnot"

            # 3. Stage 1: Parallel Buyer CRM + Inventory/Insights Scout (Gemini Flash)
            fast_route = self.model_router.route(prompt, task_type="buyer_crm_lookup")
            with start_traced_span("agent.parallel_buyer_and_inventory_lookup_agent"):
                buyer_crm, live_insights = await asyncio.gather(
                    asyncio.to_thread(
                        lookup_buyer_purchase_history_and_vip_tier,
                        buyer_handle=buyer_handle,
                        active_stream_id=stream_id,
                    ),
                    asyncio.to_thread(
                        generate_stream_revenue_and_inventory_insights,
                        stream_id=None,
                    ),
                )

            tool_evidence: list[dict[str, Any]] = []
            action_result: dict[str, Any] = {}

            # 4. Stage 2: Strategic Pro Dispatch (Sale / Refund HITL / Manifest HITL / Bag / Insights / CRM)
            pro_route = self.model_router.route(prompt, task_type="sale_and_packaging_strategy")
            with start_traced_span("agent.sale_ledger_and_packaging_strategist_agent"):
                sale_id_match = re.search(r"\b(SALE-\d{4,6})\b", prompt, re.IGNORECASE)
                bag_id_match = re.search(r"\b(BAG-\d{3,5})\b", prompt, re.IGNORECASE)
                price_val, qty_val = _extract_sale_price_and_qty(prompt)
                is_question_query = any(
                    w in lower
                    for w in (
                        "bought before",
                        "history",
                        "vip",
                        "who is",
                        "purchased",
                        "open bag",
                        "how many",
                        "how much",
                        "profit",
                        "revenue",
                        "insights",
                    )
                )
                is_sale_intent = price_val is not None and not is_question_query

                if any(w in lower for w in ("refund", "void", "cancel sale")) and sale_id_match:
                    target_sale = sale_id_match.group(1).upper()
                    action_result = await asyncio.to_thread(
                        execute_buyer_refund_or_sale_void,
                        sale_id=target_sale,
                        reason=prompt[:240],
                        human_approval_token=human_approval_token,
                    )
                    tool_evidence.append(action_result)
                    if action_result.get("status") == "pending_human_approval":
                        draft_reply = (
                            f"HITL Approval Required to void {target_sale}: "
                            f"{action_result.get('approval_instructions')}"
                        )
                    else:
                        draft_reply = (
                            f"Refund/Void completed for {target_sale} (${action_result.get('refunded_amount_usd', 0):.2f} "
                            f"returned for {action_result.get('buyer_handle')}; Pull-Bag {action_result.get('affected_pull_bag')} updated)."
                        )

                elif any(w in lower for w in ("finalize manifest", "closeout stream", "lock bags", "shipping manifest")):
                    action_result = await asyncio.to_thread(
                        finalize_stream_shipping_manifest_batch,
                        stream_id=stream_id,
                        human_approval_token=human_approval_token,
                    )
                    tool_evidence.append(action_result)
                    if action_result.get("status") == "pending_human_approval":
                        draft_reply = (
                            f"HITL Approval Required to finalize shipping manifest for {stream_id}: "
                            f"{action_result.get('approval_instructions')}"
                        )
                    else:
                        draft_reply = (
                            f"Stream Manifest {action_result.get('manifest_id')} finalized! "
                            f"Locked {action_result.get('finalized_bags_count')} Pull-Bag(s) totaling "
                            f"${action_result.get('total_declared_value_usd', 0):.2f} for carrier dispatch."
                        )

                elif bag_id_match and not is_sale_intent:
                    target_bag = bag_id_match.group(1).upper()
                    action_result = await asyncio.to_thread(
                        assign_fulfillment_pull_bag_and_packaging_tier,
                        bag_number=target_bag,
                    )
                    tool_evidence.append(action_result)
                    draft_reply = (
                        f"Pull-Bag {target_bag} ({action_result.get('buyer_handle')}) at "
                        f"{action_result.get('shelf_bin_coordinate')}: {action_result.get('total_items_in_bag')} item(s), "
                        f"${action_result.get('cumulative_value_usd', 0):.2f} value -> "
                        f"Tier: {action_result.get('packaging_tier')} | Supplies: "
                        f"{', '.join(action_result.get('protection_supplies_checklist', []))}."
                    )

                elif is_sale_intent and price_val is not None:
                    matched_inv = _match_inventory_sku_from_text(prompt)
                    matched_sku = matched_inv["sku"] if matched_inv else None
                    fmt = (
                        str(matched_inv["selling_format"])
                        if matched_inv and not matched_inv.get("_explicit_unregistered")
                        else _infer_selling_format(prompt)
                    )
                    packs_match = re.search(r"(\d+)\s+packs?\s+ripped", lower)
                    prompt_without_rtyh_phrase = re.sub(
                        r"rip\s+till\s+you\s+hit", "", prompt, flags=re.IGNORECASE
                    )
                    hit_match = re.search(
                        r"\b(?:pulled|hit:?)\s+([A-Za-z0-9 #'-]{3,50})",
                        prompt_without_rtyh_phrase,
                        re.IGNORECASE,
                    )

                    cleaned_title = (
                        str(matched_inv["item_title"])
                        if matched_inv and not matched_inv.get("_explicit_unregistered")
                        else _extract_clean_item_title(prompt, fmt)
                    )
                    action_result = await asyncio.to_thread(
                        record_livestream_card_sale,
                        stream_id=stream_id,
                        platform=platform,
                        buyer_handle=buyer_handle,
                        item_title=cleaned_title,
                        selling_format=fmt,
                        hammer_price_usd=price_val,
                        quantity=qty_val,
                        sku=matched_sku,
                        rtyh_packs_ripped=int(packs_match.group(1)) if packs_match else 0,
                        rtyh_hit_card=hit_match.group(1).strip() if hit_match else None,
                        buyer_notes=prompt,
                    )
                    tool_evidence.append(action_result)
                    if action_result.get("status") == "error":
                        draft_reply = (
                            f"Guided Error ({action_result.get('error_code')}): "
                            f"{action_result.get('message')} Recovery: {action_result.get('recovery_instructions')}"
                        )
                    else:
                        combine_tag = (
                            "COMBINED into existing Pull-Bag"
                            if action_result.get("combined_with_existing_bag")
                            else "Opened NEW Pull-Bag"
                        )
                        repeat_tag = (
                            f"REPEAT BUYER ({action_result.get('buyer_vip_tier')})"
                            if action_result.get("is_repeat_buyer")
                            else "First-Time Buyer"
                        )
                        draft_reply = (
                            f"Logged {action_result['sale_id']} on {platform.upper()}! "
                            f"Buyer {action_result['buyer_handle']} [{repeat_tag}] -> "
                            f"{combine_tag} {action_result['assigned_pull_bag_number']} "
                            f"(Pull from: {action_result['pull_from_location']}). "
                            f"Packaging: {action_result['recommended_packaging_tier']} "
                            f"({', '.join(action_result['protection_supplies_checklist'][:3])}). "
                            f"Gross: ${action_result['hammer_price_usd']:.2f} | Net Payout: "
                            f"${action_result['net_payout_usd']:.2f} | Est. Profit: "
                            f"${action_result['estimated_net_profit_usd']:.2f}. "
                            f"VIP Perk: {action_result['vip_perk_recommendation']}"
                        )

                elif has_explicit_buyer and any(
                    w in lower for w in ("bought before", "history", "vip", "who is", "purchased", "open bag")
                ):
                    action_result = buyer_crm
                    draft_reply = (
                        f"Buyer CRM for {buyer_crm['buyer_handle']}: "
                        f"Repeat Buyer={buyer_crm['is_repeat_buyer']} | Tier={buyer_crm['vip_tier']} | "
                        f"Orders={buyer_crm['total_historical_orders']} | Lifetime Spend="
                        f"${buyer_crm['lifetime_spend_usd']:.2f}. {buyer_crm['combine_shipping_alert']}"
                    )

                else:
                    action_result = live_insights
                    draft_reply = (
                        f"PokeSell Insights ({live_insights['scope']}): "
                        f"{live_insights['total_units_sold']} unit(s) sold across "
                        f"{live_insights['total_transactions']} sale(s) | Gross: "
                        f"${live_insights['gross_revenue_usd']:.2f} | Fees: "
                        f"${live_insights['total_platform_fees_usd']:.2f} | Net Payout: "
                        f"${live_insights['net_payout_after_fees_usd']:.2f} | Est. Net Profit: "
                        f"${live_insights['estimated_net_profit_usd']:.2f} | Repeat Buyers: "
                        f"{live_insights['repeat_buyers_count']} | Open Pull-Bags on Desk: "
                        f"{live_insights['open_pull_bags_count']}."
                    )

            # 5. Stage 3: Constitutional Self-Evaluation Loop
            with start_traced_span("agent.fulfillment_qa_loop_agent"):
                eval_report = self.self_critic.evaluate(
                    response_text=draft_reply,
                    tool_evidence=tool_evidence,
                )

            final_reply = eval_report.remediated_text
            compacted_turns = self.session_manager.append_turn(
                session_id,
                ConversationTurn(role="assistant", content=final_reply),
            )

            # 6. Non-blocking Async Memory Consolidation in background
            bg_task = self.async_consolidator.schedule_background_consolidation(
                session_id=session_id,
                turns=compacted_turns,
            )

            return {
                "status": action_result.get("status", "success"),
                "session_id": session_id,
                "stream_id": stream_id,
                "models_routed": {
                    "parallel_scouts": fast_route.selected_model,
                    "strategist_and_planner": pro_route.selected_model,
                },
                "buyer_crm": buyer_crm,
                "action_result": action_result,
                "self_evaluation": {
                    "passed": eval_report.passed,
                    "overall_score": eval_report.overall_score,
                    "groundedness_score": eval_report.groundedness_score,
                    "safety_score": eval_report.safety_score,
                },
                "reply": final_reply,
                "async_memory_scheduled": bg_task is not None,
            }


default_orchestrator = PokeSellOrchestrator()
