"""Bankroll and ledger domain exceptions."""


class BankrollError(Exception):
    """Base exception for bankroll and ledger errors."""
    pass


class InsufficientFundsError(BankrollError):
    """Raised when available balance is insufficient for a bet stake."""
    def __init__(self, requested: float, available: float):
        super().__init__(
            f"Insufficient funds: requested stake {requested:.2f} RUB exceeds available balance {available:.2f} RUB."
        )
        self.requested = requested
        self.available = available


class ExposureLimitExceededError(BankrollError):
    """Raised when bet would breach portfolio exposure limit."""
    def __init__(self, requested_exposure: float, max_allowed: float):
        super().__init__(
            f"Exposure limit breached: projected exposure {requested_exposure:.2f} RUB exceeds max {max_allowed:.2f} RUB."
        )
        self.requested_exposure = requested_exposure
        self.max_allowed = max_allowed


class AccountNotFoundError(BankrollError):
    """Raised when virtual account does not exist."""
    pass


class BetNotFoundError(BankrollError):
    """Raised when bet does not exist."""
    pass


class InvalidSettlementError(BankrollError):
    """Raised when attempting an invalid settlement transition."""
    pass


class ImmutableLedgerViolation(BankrollError):
    """Raised when attempting an unauthorized modification or deletion of ledger entries."""
    pass
