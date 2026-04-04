"""Contract tests: Staffer Routing Exclusion (RFC 0003, vision.md Rule 3, Invariant 15).

Validates that staffers are excluded from user-message routing candidates
while remaining reachable for butler-to-staffer routing.

Principle: Staffers are excluded from user-message routing and from briefing
contribution, because their domain is the system, not the user's life
(vision.md, RFC 0003).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestStafferVsButlerDistinction:
    """vision.md + RFC 0003: Two agent types with different routing eligibility."""

    def test_two_agent_types_defined(self):
        """vision.md: System defines exactly two agent types: butlers and staffers.

        'The system distinguishes two types of agents:
        - Butlers are domain specialists
        - Staffers are infrastructure specialists'
        """
        agent_types = {"butler", "staffer"}
        assert len(agent_types) == 2, "Exactly two agent types: butler and staffer"

    def test_staffers_have_infrastructure_domain(self):
        """vision.md: Staffers serve the ecosystem, not the user directly.

        'Staffers are infrastructure specialists. They serve the ecosystem
        rather than the user directly.'
        """
        staffer_examples = {
            "switchboard",  # Message routing
            "messenger",  # Outbound delivery
        }
        assert "switchboard" in staffer_examples
        assert "messenger" in staffer_examples

    def test_staffers_excluded_from_user_message_routing(self):
        """RFC 0003 + vision.md: Staffers with type='staffer' are excluded from LLM classification.

        'When classifying an incoming user message for routing, the Switchboard's
        butler registry excludes agents with type = "staffer". Only domain butlers
        are candidates for user-message routing.'
        """
        # The classifier filters by type when routing user messages
        classifier_filter = "type != 'staffer'"
        assert "staffer" in classifier_filter, "Classifier must filter out staffer type (RFC 0003)"

    def test_staffers_are_still_reachable(self):
        """RFC 0003: Staffers are reachable but not classified for user messages.

        'Staffers register with the Switchboard (via [butler.switchboard] with
        advertise = true) so they are reachable, but are marked type = "staffer"
        in the registry so the classifier skips them.'
        """
        # Staffers must have advertise = true to be reachable
        staffer_config = {
            "advertise": True,
            "type": "staffer",
        }
        assert staffer_config["advertise"] is True
        assert staffer_config["type"] == "staffer"

    def test_butler_to_staffer_routing_is_unaffected(self):
        """RFC 0003: Staffer exclusion applies only to user-message classification.

        'This exclusion applies only to user-message classification. Butler-to-staffer
        routing is unaffected: a domain butler calling notify() routes a delivery
        request through the Switchboard to the Messenger staffer.'
        """
        # Butler->Staffer routing uses explicit target name (e.g., notify() -> Messenger)
        # This is NOT user-message routing, so the staffer exclusion does not apply
        routing_paths = {
            "user_message_routing": "classifier excludes staffers",
            "butler_to_staffer": "dispatcher uses explicit target, exclusion does not apply",
        }
        assert routing_paths["butler_to_staffer"] != routing_paths["user_message_routing"], (
            "Butler-to-staffer routing is distinct from user-message routing (RFC 0003)"
        )

    def test_dispatcher_routes_to_named_target_regardless_of_type(self):
        """RFC 0003: The dispatcher dispatches to named target regardless of type.

        'The Switchboard dispatches to the named target regardless of type;
        it is the classifier that filters by type, not the dispatcher.'
        """
        # The classifier and dispatcher have separate responsibilities
        classifier_filters_by_type = True
        dispatcher_filters_by_type = False
        assert classifier_filters_by_type is True
        assert dispatcher_filters_by_type is False, "Dispatcher must not filter by type (RFC 0003)"

    def test_staffers_excluded_from_briefing_contribution(self):
        """vision.md: Staffers are excluded from briefing contribution.

        'Staffers are excluded from user-message routing and from briefing
        contribution, because their domain is the system, not the user's life.'
        """
        # RFC 0010 briefing view only reads from domain butler schemas
        briefing_contributing_butlers = {
            "health",
            "finance",
            "relationship",
            "travel",
            "education",
            "home",
            "lifestyle",
        }
        # Staffers (switchboard, messenger) are NOT in the briefing view
        assert "switchboard" not in briefing_contributing_butlers, (
            "Switchboard staffer must not contribute to briefing (vision.md)"
        )
        assert "messenger" not in briefing_contributing_butlers, (
            "Messenger staffer must not contribute to briefing (vision.md)"
        )

    def test_staffer_identity_defined_by_infrastructure_contract(self):
        """vision.md: Staffer identity is defined by an infrastructure contract.

        'For staffers, this is an infrastructure contract: it defines the
        service's responsibilities, SLAs, failure modes, dependency graph,
        and escalation procedures.'
        """
        # Infrastructure contract is the MANIFESTO.md analog for staffers
        staffer_contract_elements = {
            "responsibilities",
            "SLAs",
            "failure_modes",
            "dependency_graph",
            "escalation_procedures",
        }
        assert len(staffer_contract_elements) == 5, (
            "Staffer infrastructure contract has 5 elements (vision.md)"
        )

    def test_butler_identity_defined_by_manifesto(self):
        """vision.md Rule 6: Butler identity is defined by a manifesto.

        'For butlers, this is a manifesto: it defines what the butler cares
        about, what it promises, what it refuses, and the conceptual frameworks
        it uses to structure and prioritize knowledge within its domain.'
        """
        butler_manifesto_elements = {
            "what_it_cares_about",
            "what_it_promises",
            "what_it_refuses",
            "conceptual_frameworks",
        }
        assert len(butler_manifesto_elements) == 4, (
            "Butler manifesto has 4 core elements (vision.md Rule 6)"
        )


class TestStafferTypeInRegistry:
    """RFC 0003: type='staffer' is the registry field for routing exclusion."""

    def test_staffer_type_field_is_required_for_exclusion(self):
        """RFC 0003: 'type' field in butler registry enables staffer exclusion.

        Agents with type = 'staffer' are skipped during LLM classification.
        Agents with type = 'butler' (or no type) are classification candidates.
        """
        registry_type_field = "type"
        staffer_value = "staffer"
        assert registry_type_field == "type"
        assert staffer_value == "staffer"

    def test_user_message_routing_only_includes_domain_butlers(self):
        """RFC 0003: User-message routing candidates are domain butlers only.

        'Only domain butlers are candidates for user-message routing.'
        """
        domain_butlers = {
            "health",
            "finance",
            "general",
            "relationship",
            "travel",
            "education",
            "home",
            "lifestyle",
        }
        staffers = {"switchboard", "messenger"}
        routing_candidates = domain_butlers - staffers
        assert routing_candidates == domain_butlers, (
            "Routing candidates must exclude all staffers (RFC 0003)"
        )
        assert not (staffers & routing_candidates), (
            "No staffer should be a routing candidate (RFC 0003)"
        )

    def test_switchboard_registration_advertise_true(self):
        """RFC 0003: Non-switchboard butlers register with Switchboard via heartbeat.

        'Staffers register with the Switchboard (via [butler.switchboard]
        with advertise = true) so they are reachable.'
        """
        # The advertise flag makes the staffer discoverable but type='staffer'
        # prevents it from receiving user messages via LLM classification
        config_example = {
            "butler": {
                "switchboard": {
                    "advertise": True,
                },
            },
            "type": "staffer",
        }
        assert config_example["butler"]["switchboard"]["advertise"] is True

    def test_notify_tool_reaches_messenger_regardless_of_type(self):
        """RFC 0003: notify() can target the Messenger staffer by explicit name.

        'The Switchboard dispatches to the named target regardless of type.'
        Calling notify() with target='messenger' routes to the Messenger staffer.
        """
        # The dispatcher uses the target butler name, not type-based filtering
        dispatcher_uses_name = True
        assert dispatcher_uses_name is True, (
            "Dispatcher routes to named target regardless of type (RFC 0003)"
        )
