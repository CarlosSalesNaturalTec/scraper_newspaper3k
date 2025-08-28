from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class SystemLog(BaseModel):
    task: str
    start_time: datetime
    end_time: Optional[datetime] = None
    status: str
    processed_count: int = 0
    error_message: Optional[str] = None
    message: Optional[str] = None
