from .obsidian_search import ObsidianSearchTool
from .obsidian_write import ObsidianWriteTool
from .web_search import WebSearchTool
from .notebooklm import NotebookLMResearchTool
from .task_manager import CreateTaskTool, ListTasksTool
from .financial_news import FinancialNewsTool
from .market_data import MarketDataTool
from .google_analytics import GoogleAnalyticsTool
from .delegate import DelegateTool, DelegationStatusTool
from .calendar_tool import CalendarCreateTool

TOOLS = [
    ObsidianSearchTool(),
    ObsidianWriteTool(),
    WebSearchTool(),
    NotebookLMResearchTool(),
    CreateTaskTool(),
    ListTasksTool(),
    FinancialNewsTool(),
    MarketDataTool(),
    GoogleAnalyticsTool(),
    DelegateTool(),
    DelegationStatusTool(),
    CalendarCreateTool(),
]

TOOL_MAP = {t.name: t for t in TOOLS}
