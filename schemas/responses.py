from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    status: str
    data: Any = None
    message: Optional[str] = None


class PaginatedResponse(BaseModel):
    status: str
    data: Any = None
    next_cursor: Optional[str] = None
    has_more: bool = False
