from enum import StrEnum


class InvoiceStatus(StrEnum):
    PENDING = "pending"
    ISSUED = "issued"
    FAILED = "failed"


class AttemptKind(StrEnum):
    ISSUE = "issue"
    LOOKUP = "lookup"


class AttemptOutcome(StrEnum):
    IN_FLIGHT = "in_flight"
    SUCCESS = "success"
    RETRYABLE_ERROR = "retryable_error"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
