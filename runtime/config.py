import yaml
from pydantic import BaseModel, model_validator

class Service(BaseModel):
    id: str; name: str; duration_min: int; price: float
class Staff(BaseModel):
    id: str; name: str; services: list[str]; start: str = "09:00"; end: str = "18:00"
class Rules(BaseModel):
    buffer_min: int = 0; cancel_window_hours: int = 2; hold_offer_minutes: int = 15
class Business(BaseModel):
    name: str; type: str; timezone: str = "Asia/Kolkata"
    services: list[Service]; staff: list[Staff]; rules: Rules = Rules()
    @model_validator(mode="after")
    def check(self):
        ids = {s.id for s in self.services}
        for st in self.staff:
            bad = set(st.services) - ids
            if bad: raise ValueError(f"staff '{st.id}' offers unknown services: {sorted(bad)}")
        return self

def load(path):
    with open(path, encoding="utf-8") as f: return Business(**yaml.safe_load(f))
