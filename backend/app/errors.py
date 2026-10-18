"""Application errors with stable codes for the UI and tests."""


class CraftsError(Exception):
    def __init__(self, message: str, *, code: str = "error", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class Cancelled(CraftsError):
    def __init__(self, message: str = "Cancelled."):
        super().__init__(message, code="cancelled", status=409)
