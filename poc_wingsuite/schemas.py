from pydantic import BaseModel
from datetime import date

class ExtractionRequest(BaseModel):
    client_name: str
    date_from: date
    date_to: date
    timeout_ms: int = 90_000

class ExtractionArtifact(BaseModel):
    local_path: str
    source: str
    product: str
    client_name: str