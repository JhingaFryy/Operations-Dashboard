from pydantic import BaseModel


class LocomotiveOut(BaseModel):
    loco_number: str
    loco_type: str
