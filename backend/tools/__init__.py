from .obsidian_search import ObsidianSearchTool
from .obsidian_write import ObsidianWriteTool
from .web_search import WebSearchTool
from .task_manager import CreateTaskTool, ListTasksTool
from .financial_news import FinancialNewsTool
from .market_data import MarketDataTool
from .google_analytics import GoogleAnalyticsTool
from .delegate import DelegateTool, DelegationStatusTool

TOOLS = [
    ObsidianSearchTool(),
    ObsidianWriteTool(),
    WebSearchTool(),
    CreateTaskTool(),
    ListTasksTool(),
    FinancialNewsTool(),
    MarketDataTool(),
    GoogleAnalyticsTool(),
    DelegateTool(),
    DelegationStatusTool(),
]

TOOL_MAP = {t.name: t for t in TOOLS}
