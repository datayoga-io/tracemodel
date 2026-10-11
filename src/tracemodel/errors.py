"""Errors raised by tracemodel. All derive from TraceModelError."""


class TraceModelError(Exception):
    """Base class for every tracemodel error."""


class DefinitionError(TraceModelError):
    """The model definition is invalid (bad dimension, item or check)."""


class UnknownItem(TraceModelError):
    """A line item name that the model does not declare."""


class UnknownCoord(TraceModelError):
    """A coord label, or a dimension name, that the model does not declare."""


class DimMismatch(TraceModelError):
    """Coords that don't give exactly one label per dim of the item."""


class MissingValue(TraceModelError):
    """No value has been set for this item and coords."""


class InvalidValue(TraceModelError):
    """A value that is not a plain number, or a missing source."""
