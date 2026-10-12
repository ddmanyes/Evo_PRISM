"""Adapters expose native proposals. P1 cannot load or invoke real models."""

from abc import ABC, abstractmethod
import json
from .decision_contracts import identity, PROPOSAL_FIELDS, exact


class ModelAdapter(ABC):
    engineering_only = False

    @abstractmethod
    def declaration(self):
        """Identity, task families, formats, native format, conformance receipt."""

    @abstractmethod
    def render(self, request):
        """Convert bounded evidence to a fixed native template without execution."""

    @abstractmethod
    def propose(self, native_input):
        """Return native output; runtime loading is outside P1 authority."""

    @abstractmethod
    def parse(self, native_output):
        """Produce DecisionProposal; parsing cannot grant tool authority."""


def declaration(adapter):
    value = adapter.declaration()
    exact(
        value,
        {"identity", "families", "formats", "native_format", "conformance_receipt"},
        "adapter_declaration",
    )
    identity(value["identity"])
    if (
        not value["families"]
        or not value["formats"]
        or not value["native_format"]
        or not value["conformance_receipt"]
    ):
        raise ValueError("adapter_conformance_required")
    return value


def parse_json(native_output):
    if not isinstance(native_output, str) or len(native_output.encode()) > 16384:
        raise ValueError("native_output_limit")

    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("duplicate_json_key")
            result[k] = v
        return result

    value = json.loads(
        native_output,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")),
    )
    exact(value, PROPOSAL_FIELDS, "proposal_schema")
    return value
