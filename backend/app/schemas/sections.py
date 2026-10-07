from pydantic import BaseModel


class SectionOut(BaseModel):
    id: int
    code: str
    name: str
