from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ChatRequest(BaseModel):
    session_id: str
    message: str
    agent: str = "claude"
    use_obsidian: bool = True
    attachments: Optional[List[dict]] = []
    voice: bool = False


class SessionCreate(BaseModel):
    name: str
    agent: str = "claude"


class SessionOut(BaseModel):
    id: str
    name: str
    agent: str
    created_at: str
    message_count: int = 0


class MessageOut(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    created_at: str


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    status: str = "todo"
    agent: Optional[str] = None
    assigned_agent_id: Optional[int] = None
    workspace_path: Optional[str] = ""
    position: Optional[int] = 0


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    agent: Optional[str] = None
    assigned_agent_id: Optional[int] = None
    position: Optional[int] = None
    workspace_path: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    title: str
    description: str
    status: str
    agent: Optional[str]
    assigned_agent_id: Optional[int] = None
    position: int
    workspace_path: str
    result: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    retry_count: int = 0
    created_at: str
    updated_at: str


class AgentProfileCreate(BaseModel):
    name: str
    model: str = "openrouter/meta-llama/llama-3.1-8b-instruct"
    system_prompt: Optional[str] = ""
    memory_session: Optional[str] = ""
    mcp_servers: Optional[List[str]] = []


class AgentProfileUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    memory_session: Optional[str] = None
    mcp_servers: Optional[List[str]] = None


class AgentProfileOut(BaseModel):
    id: int
    name: str
    model: str
    system_prompt: str
    memory_session: str
    mcp_servers: List[str]
    created_at: str


class LeadOut(BaseModel):
    id: str
    org_name: str
    website: str
    contacts: List[dict]
    summary: str
    relevance: str
    status: str
    search_query: str
    obsidian_path: str
    phone: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    postal_code: str = ""
    kvk_number: str = ""
    lead_type: str = "overig"
    enriched_at: str = ""
    score: int = 50
    tags: List[str] = []
    created_at: str
    updated_at: str


class LeadUpdate(BaseModel):
    status: str


class VacancyOut(BaseModel):
    id: str
    title: str
    organization: str = ""
    url: str
    source: str = "overig"
    role_query: str = ""
    location: str = ""
    hours_text: str = ""
    contract_type: str = ""
    description: str = ""
    fit_score: int = 50
    fit_rationale: str = ""
    posted_days_ago: int = -1
    status: str
    search_query: str = ""
    created_at: str
    updated_at: str


class VacancyUpdate(BaseModel):
    status: str


class ObsidianResult(BaseModel):
    file: str
    path: str
    score: float
    snippet: str


class ObsidianSearchOut(BaseModel):
    query: str
    results: List[ObsidianResult]
    vault_path: str
    total_files: int
