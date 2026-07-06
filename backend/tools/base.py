from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ToolResult:
    tool_name: str
    output: str
    error: bool = False


class Tool:
    name: str = ""
    description: str = ""
    input_schema: Dict = field(default_factory=dict)

    async def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def to_anthropic(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai(self) -> Dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
