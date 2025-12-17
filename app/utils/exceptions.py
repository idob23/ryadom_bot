"""
Custom exceptions for the application.
"""


class RyadomError(Exception):
    """Base exception for Ryadom bot."""
    pass


class UserNotFoundError(RyadomError):
    """User not found in database."""
    pass


class RateLimitExceededError(RyadomError):
    """User exceeded their rate limit."""
    pass


class SubscriptionRequiredError(RyadomError):
    """Action requires active subscription."""
    pass


class PaymentError(RyadomError):
    """Payment processing error."""
    pass


class ClaudeAPIError(RyadomError):
    """Error communicating with Claude API."""
    def __init__(self, message: str, status_code: int = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
