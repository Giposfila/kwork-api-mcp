"""MCP-инструмент для поиска публичных проектов Kwork."""

from argparse import Namespace
from pathlib import Path
import sys
from typing import Annotated, TypedDict

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

# MCP-клиент может запустить сервер из любой рабочей папки.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kwork_projects


class SearchResult(TypedDict):
    count: int
    projects: list[dict[str, object]]


mcp = MCPServer("kwork-projects", instructions="Поиск публичных проектов на бирже Kwork.")


@mcp.tool(
    title="Поиск проектов Kwork",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
def kwork_search_projects(
    category: Annotated[int | None, Field(ge=1, description="ID рубрики из параметра c в URL Kwork.")] = None,
    keywords: Annotated[list[str] | None, Field(description="Слова и фразы для поиска. Достаточно одного совпадения.")] = None,
    exclude_keywords: Annotated[list[str] | None, Field(description="Исключить проект при совпадении с любым из этих слов или фраз.")] = None,
    min_budget: Annotated[int | None, Field(ge=0, description="Нижняя граница желаемого бюджета в рублях.")] = None,
    max_budget: Annotated[int | None, Field(ge=0, description="Верхняя граница желаемого бюджета в рублях.")] = None,
    max_offers: Annotated[int | None, Field(ge=0, description="Максимальное число откликов.")] = None,
    pages: Annotated[int, Field(ge=1, description="Максимум просматриваемых страниц, начиная с первой.")] = 3,
    limit: Annotated[int, Field(ge=1, description="Максимум проектов после фильтрации.")] = 20,
) -> SearchResult:
    """Найти проекты на публичной бирже Kwork и вернуть карточки в JSON.

    Ключевые слова ищутся в названии и описании, без учёта регистра.
    """
    options = Namespace(
        category=category,
        keyword=keywords or [],
        exclude=exclude_keywords or [],
        min_budget=min_budget,
        max_budget=max_budget,
        max_offers=max_offers,
        pages=pages,
        limit=limit,
        html=None,
    )
    try:
        kwork_projects.validate_options(options)
    except ValueError as error:
        raise ToolError(str(error)) from None

    try:
        projects = kwork_projects.collect(options)
    except (OSError, ValueError, LookupError) as error:
        raise ToolError(kwork_projects.error_message(error)) from None
    return {"count": len(projects), "projects": projects}


if __name__ == "__main__":
    mcp.run(transport="stdio")
