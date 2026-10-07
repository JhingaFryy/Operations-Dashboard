from pydantic import BaseModel


class DefectTypeOut(BaseModel):
    id: int
    code: str
    name: str
