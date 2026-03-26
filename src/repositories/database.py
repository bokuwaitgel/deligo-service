from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Union

from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class DatabaseRepository(ABC):
    def __init__(self, db_session: Session):
        self.db_session = db_session

    @abstractmethod
    def get_by_id(self, record_id: str) -> Any:
        pass

    @abstractmethod
    def get_all(self) -> List[Any]:
        pass

    @abstractmethod
    def create(self, obj_in: BaseModel) -> Any:
        pass

    @abstractmethod
    def update(self, record_id: str, obj_in: BaseModel) -> Any:
        pass

    @abstractmethod
    def delete(self, record_id: str) -> None:
        pass

