from __future__ import annotations


class CameraError(Exception):
    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status: int | None = None,
        payload: object | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.payload = payload


class UnreachableError(CameraError):
    pass


class ApiDisabledError(CameraError):
    pass


class NotFoundError(CameraError):
    pass


class BadRequestError(CameraError):
    pass


class NoMediaError(CameraError):
    pass
