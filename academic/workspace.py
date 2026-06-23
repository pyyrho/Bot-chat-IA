from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("Revolutx.ResearchWorkspace")


@dataclass(slots=True)
class ResearchItem:
    id: int | None
    project_id: int
    item_type: str
    title: str
    content: str = ""
    url: str = ""
    created_at: datetime | None = None


@dataclass(slots=True)
class ResearchProject:
    id: int
    user_id: int
    guild_id: int | None
    name: str
    objective: str = ""
    status: str = "active"
    items: list[ResearchItem] = field(default_factory=list)
    created_at: datetime | None = None

    def as_context(self, max_chars: int = 2800) -> str:
        lines = [f"Projeto ativo #{self.id}: {self.name}"]
        if self.objective:
            lines.append("Objetivo: " + self.objective)
        for item in self.items[-12:]:
            label = {"note": "Nota", "source": "Fonte", "decision": "Decisão", "question": "Questão"}.get(item.item_type, item.item_type)
            body = item.content or item.url
            lines.append(f"- {label}: {item.title}" + (f" — {body[:350]}" if body else ""))
        return "\n".join(lines)[:max_chars]


class ResearchWorkspace:
    def __init__(self, pool: Any | None = None) -> None:
        self.pool = pool
        self.ready = False
        self._projects: dict[int, ResearchProject] = {}
        self._active: dict[tuple[int, int], int] = {}
        self._next_project = 1
        self._next_item = 1

    @staticmethod
    def _guild_key(guild_id: int | None) -> int:
        return int(guild_id or 0)

    async def prepare(self) -> None:
        if self.pool is None:
            return
        try:
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_research_projects (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    guild_key BIGINT NOT NULL DEFAULT 0,
                    name TEXT NOT NULL,
                    objective TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await self.pool.execute(
                """
                CREATE INDEX IF NOT EXISTS ai_research_projects_owner_idx
                ON ai_research_projects(user_id, guild_key, updated_at DESC)
                """
            )
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_research_items (
                    id BIGSERIAL PRIMARY KEY,
                    project_id BIGINT NOT NULL REFERENCES ai_research_projects(id) ON DELETE CASCADE,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_active_projects (
                    user_id BIGINT NOT NULL,
                    guild_key BIGINT NOT NULL DEFAULT 0,
                    project_id BIGINT NOT NULL REFERENCES ai_research_projects(id) ON DELETE CASCADE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(user_id, guild_key)
                )
                """
            )
            self.ready = True
        except Exception as exc:
            self.ready = False
            logger.warning("Projetos acadêmicos em modo de memória local: %s", exc)

    async def create(self, user_id: int, guild_id: int | None, name: str, objective: str = "") -> ResearchProject:
        name = " ".join(name.split())[:120]
        objective = " ".join(objective.split())[:800]
        if self.ready:
            row = await self.pool.fetchrow(
                """
                INSERT INTO ai_research_projects(user_id,guild_key,name,objective)
                VALUES($1,$2,$3,$4) RETURNING *
                """,
                user_id, self._guild_key(guild_id), name, objective,
            )
            project = self._from_row(row, guild_id)
        else:
            project = ResearchProject(self._next_project, user_id, guild_id, name, objective, created_at=datetime.now(timezone.utc))
            self._projects[project.id] = project
            self._next_project += 1
        await self.set_active(user_id, guild_id, project.id)
        return project

    async def list(self, user_id: int, guild_id: int | None, limit: int = 20) -> list[ResearchProject]:
        if self.ready:
            rows = await self.pool.fetch(
                "SELECT * FROM ai_research_projects WHERE user_id=$1 AND guild_key=$2 ORDER BY updated_at DESC LIMIT $3",
                user_id, self._guild_key(guild_id), limit,
            )
            return [self._from_row(row, guild_id) for row in rows]
        return [p for p in self._projects.values() if p.user_id == user_id and p.guild_id == guild_id][-limit:][::-1]

    async def get(self, user_id: int, guild_id: int | None, project_id: int, *, with_items: bool = True) -> ResearchProject | None:
        if self.ready:
            row = await self.pool.fetchrow(
                "SELECT * FROM ai_research_projects WHERE id=$1 AND user_id=$2 AND guild_key=$3",
                project_id, user_id, self._guild_key(guild_id),
            )
            if not row:
                return None
            project = self._from_row(row, guild_id)
            if with_items:
                item_rows = await self.pool.fetch(
                    "SELECT * FROM ai_research_items WHERE project_id=$1 ORDER BY created_at ASC LIMIT 100",
                    project_id,
                )
                project.items = [ResearchItem(
                    id=r["id"], project_id=r["project_id"], item_type=r["item_type"], title=r["title"],
                    content=r["content"], url=r["url"], created_at=r["created_at"],
                ) for r in item_rows]
            return project
        project = self._projects.get(project_id)
        return project if project and project.user_id == user_id and project.guild_id == guild_id else None

    async def add_item(
        self,
        user_id: int,
        guild_id: int | None,
        project_id: int,
        *,
        item_type: str,
        title: str,
        content: str = "",
        url: str = "",
    ) -> ResearchItem | None:
        project = await self.get(user_id, guild_id, project_id, with_items=False)
        if not project:
            return None
        title = " ".join(title.split())[:180]
        content = content.strip()[:6000]
        url = url.strip()[:1000]
        if self.ready:
            row = await self.pool.fetchrow(
                """
                INSERT INTO ai_research_items(project_id,item_type,title,content,url)
                VALUES($1,$2,$3,$4,$5) RETURNING *
                """,
                project_id, item_type[:30], title, content, url,
            )
            await self.pool.execute("UPDATE ai_research_projects SET updated_at=NOW() WHERE id=$1", project_id)
            return ResearchItem(row["id"], project_id, row["item_type"], row["title"], row["content"], row["url"], row["created_at"])
        item = ResearchItem(self._next_item, project_id, item_type[:30], title, content, url, datetime.now(timezone.utc))
        self._next_item += 1
        project.items.append(item)
        return item

    async def set_active(self, user_id: int, guild_id: int | None, project_id: int) -> bool:
        project = await self.get(user_id, guild_id, project_id, with_items=False)
        if not project:
            return False
        key = (user_id, self._guild_key(guild_id))
        if self.ready:
            await self.pool.execute(
                """
                INSERT INTO ai_active_projects(user_id,guild_key,project_id,updated_at)
                VALUES($1,$2,$3,NOW())
                ON CONFLICT(user_id,guild_key) DO UPDATE SET project_id=$3,updated_at=NOW()
                """,
                user_id, key[1], project_id,
            )
        else:
            self._active[key] = project_id
        return True

    async def get_active(self, user_id: int, guild_id: int | None) -> ResearchProject | None:
        key = (user_id, self._guild_key(guild_id))
        if self.ready:
            row = await self.pool.fetchrow(
                "SELECT project_id FROM ai_active_projects WHERE user_id=$1 AND guild_key=$2",
                user_id, key[1],
            )
            project_id = int(row["project_id"]) if row else None
        else:
            project_id = self._active.get(key)
        return await self.get(user_id, guild_id, project_id) if project_id else None


    async def delete(self, user_id: int, guild_id: int | None, project_id: int) -> bool:
        project = await self.get(user_id, guild_id, project_id, with_items=False)
        if not project:
            return False
        key = (user_id, self._guild_key(guild_id))
        if self.ready:
            result = await self.pool.execute(
                "DELETE FROM ai_research_projects WHERE id=$1 AND user_id=$2 AND guild_key=$3",
                project_id, user_id, key[1],
            )
            return str(result).endswith("1")
        self._projects.pop(project_id, None)
        if self._active.get(key) == project_id:
            self._active.pop(key, None)
        return True

    async def clear_active(self, user_id: int, guild_id: int | None) -> None:
        key = (user_id, self._guild_key(guild_id))
        if self.ready:
            await self.pool.execute(
                "DELETE FROM ai_active_projects WHERE user_id=$1 AND guild_key=$2",
                user_id, key[1],
            )
        else:
            self._active.pop(key, None)

    @staticmethod
    def export_markdown(project: ResearchProject) -> str:
        lines = [f"# {project.name}", ""]
        lines.append(f"Projeto #{project.id}")
        if project.objective:
            lines.extend(["", "## Objetivo", "", project.objective])
        grouped: dict[str, list[ResearchItem]] = {}
        for item in project.items:
            grouped.setdefault(item.item_type, []).append(item)
        labels = {"note": "Notas", "source": "Fontes", "decision": "Decisões", "question": "Questões de pesquisa"}
        for item_type, items in grouped.items():
            lines.extend(["", f"## {labels.get(item_type, item_type.title())}", ""])
            for item in items:
                lines.append(f"### {item.title}")
                if item.content:
                    lines.extend(["", item.content])
                if item.url:
                    lines.extend(["", item.url])
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _from_row(row: Any, guild_id: int | None) -> ResearchProject:
        return ResearchProject(
            id=int(row["id"]), user_id=int(row["user_id"]), guild_id=guild_id,
            name=str(row["name"]), objective=str(row["objective"] or ""), status=str(row["status"]),
            created_at=row["created_at"],
        )
