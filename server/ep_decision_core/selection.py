"""Immutable, explicit single-model selection. Configuration never loads a model."""
from dataclasses import dataclass
from types import MappingProxyType
from .decision_contracts import identity, text


@dataclass(frozen=True)
class ModelSelection:
    epoch: str
    model_identity: dict

    def __post_init__(self):
        # Snapshot caller-owned input; a frozen dataclass alone does not freeze a dict.
        object.__setattr__(self, 'model_identity', MappingProxyType(identity(self.model_identity)))

    @classmethod
    def clef(cls, epoch='clef-bootstrap-v1'):
        # Unresolved pins are explicit placeholders. They do not claim a model run.
        return cls(epoch, dict(model='Clef', revision='UNRESOLVED',
                               checkpoint='UNRESOLVED', template='choice-scoring-UNRESOLVED',
                               runtime='NOT_LOADED', adapter_version='bootstrap-v1'))

    def validate(self):
        text(self.epoch, 128)
        return {'epoch': self.epoch, 'identity': identity(dict(self.model_identity))}
