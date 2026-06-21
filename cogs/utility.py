"""
utility.py — Revolux · Utilidades em Discord Components V2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Este cog usa discord.py 2.7.1+ e o sistema Components V2 do Discord.
As respostas ricas são construídas com LayoutView, Container, Section,
TextDisplay, Separator, MediaGallery e ActionRow, sem embeds legados.

Variáveis de ambiente:
  GEMINI_API_KEY   — chave usada pelos comandos de IA
  UTIL_AI_MODEL    — modelo das utilidades (padrão: gemini-2.5-flash)
  SUPPORT_INVITE   — convite do servidor de suporte

Requisito recomendado no requirements.txt:
  discord.py>=2.7.1,<3.0
"""

from __future__ import annotations

import asyncio
import colorsys
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands
import google.generativeai as genai


# Components V2 chegaram ao discord.py na versão 2.6.
# A versão 2.7.1 corrige um vazamento relacionado a LayoutView.clear_items().
LayoutView = getattr(discord.ui, "LayoutView", None)
if LayoutView is None:
    raise RuntimeError(
        "Este arquivo exige discord.py 2.7.1 ou superior. "
        "Atualize o requirements.txt para: discord.py>=2.7.1,<3.0"
    )


AI_MODEL = os.getenv("UTIL_AI_MODEL", "gemini-2.5-flash")
SUPPORT_INVITE = os.getenv("SUPPORT_INVITE", "https://discord.gg/suporte")
_START_TIME = time.monotonic()


# Emojis já usados no projeto original.
EMOJI_OK = "<:1000032055:1507947171624910859>"
EMOJI_ERROR = "<:1000032056:1507947210057322637>"
EMOJI_LOADING = "<:1000032079:1507948213741813972>"
EMOJI_BOT = "<:1000032124:1508195012175728720>"
EMOJI_PING = "<:1000032077:1507948183290904736>"
EMOJI_AI = "<:1000032072:1507947958723809340>"
EMOJI_MOD = "<:1000032064:1507947590652526654>"
EMOJI_UTIL = "<:1000032067:1507947758638600353>"
EMOJI_CHANNEL = "<:1000032049:1507946904124919949>"
EMOJI_SERVER = "<:1000032076:1507948144011509820>"
EMOJI_SUPPORT = "<:1000032065:1507947691848630282>"
EMOJI_ADD = "<:1000032050:1507946943639191573>"
EMOJI_CLOCK = "<:1000032074:1507948021013549166>"
EMOJI_REMINDER = "<:1000032058:1507947336616251574>"


# ──────────────────────────────────────────────
# Helpers gerais
# ──────────────────────────────────────────────


def _uptime_str() -> str:
    elapsed = int(time.monotonic() - _START_TIME)
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    clean = hex_str.lstrip("#").strip()
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6 or not all(c in "0123456789abcdefABCDEF" for c in clean):
        raise ValueError("HEX inválido")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return round(h * 360, 1), round(s * 100, 1), round(l * 100, 1)


def _format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _truncate(text: str, limit: int, *, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def _split_text(text: str, limit: int = 4000) -> list[str]:
    """Divide TextDisplay sem cortar parágrafos quando possível."""
    text = text.strip()
    if not text:
        return ["\u200b"]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _safe_guild_name(interaction: discord.Interaction) -> str:
    return interaction.guild.name if interaction.guild else "mensagem direta"


def _footer_text(extra: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y às %H:%M UTC")
    if extra:
        return f"-# Revolux · {extra} · {now}"
    return f"-# Revolux · {now}"


# ──────────────────────────────────────────────
# Builder Components V2
# ──────────────────────────────────────────────


class V2Card(LayoutView):
    """Builder para mensagens Components V2 com controle do limite de texto."""

    # O Discord limita a soma do conteúdo de TextDisplay a 4.000 caracteres
    # por LayoutView. O builder corta o último bloco automaticamente para evitar
    # HTTP 400 quando uma resposta dinâmica fica maior do que o esperado.
    MAX_DISPLAY_CHARACTERS = 4000

    def __init__(
        self,
        *,
        accent_color: discord.Color | int = discord.Color.blurple(),
        timeout: Optional[float] = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.container = discord.ui.Container(accent_color=accent_color)
        self.add_item(self.container)
        self._display_characters = 0

    def _fit_display_text(self, content: str) -> Optional[str]:
        remaining = self.MAX_DISPLAY_CHARACTERS - self._display_characters
        if remaining <= 0:
            return None

        fitted = _truncate(content.strip() or "\u200b", remaining)
        self._display_characters += len(fitted)
        return fitted

    def add_text(self, content: str) -> "V2Card":
        fitted = self._fit_display_text(content)
        if fitted is None:
            return self

        # Cada TextDisplay também possui limite individual de 4.000 caracteres.
        for chunk in _split_text(fitted, self.MAX_DISPLAY_CHARACTERS):
            self.container.add_item(discord.ui.TextDisplay(chunk))
        return self

    def add_header(
        self,
        title: str,
        *,
        subtitle: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
    ) -> "V2Card":
        content = f"## {title}"
        if subtitle:
            content += f"\n{subtitle}"

        fitted = self._fit_display_text(content)
        if fitted is None:
            return self

        text = discord.ui.TextDisplay(fitted)
        if thumbnail_url:
            section = discord.ui.Section(
                text,
                accessory=discord.ui.Thumbnail(
                    thumbnail_url,
                    description=_truncate(title, 256),
                ),
            )
            self.container.add_item(section)
        else:
            self.container.add_item(text)
        return self

    def add_separator(
        self,
        *,
        large: bool = False,
        visible: bool = True,
    ) -> "V2Card":
        spacing = (
            discord.SeparatorSpacing.large
            if large
            else discord.SeparatorSpacing.small
        )
        self.container.add_item(
            discord.ui.Separator(visible=visible, spacing=spacing)
        )
        return self

    def add_gallery(
        self,
        *items: tuple[str, Optional[str]],
    ) -> "V2Card":
        gallery_items = [
            discord.MediaGalleryItem(
                url,
                description=_truncate(description, 256) if description else None,
            )
            for url, description in items
            if url
        ]
        if gallery_items:
            self.container.add_item(discord.ui.MediaGallery(*gallery_items))
        return self

    def add_buttons(self, *buttons: discord.ui.Button) -> "V2Card":
        if buttons:
            self.container.add_item(discord.ui.ActionRow(*buttons))
        return self

    def add_footer(self, extra: Optional[str] = None) -> "V2Card":
        fitted = self._fit_display_text(_footer_text(extra))
        if fitted is not None:
            self.container.add_item(discord.ui.TextDisplay(fitted))
        return self


# ──────────────────────────────────────────────
# /ajuda paginado em Components V2
# ──────────────────────────────────────────────


_HELP_PAGES: list[tuple[str, Callable[[], discord.Color], str]] = [
    (
        f"{EMOJI_AI} Inteligência Artificial",
        discord.Color.blurple,
        (
            "`/chat` · Conversa completa com o Revolux\n"
            "`/pergunte-ia` · Pergunta rápida com resposta direta\n"
            "`/resumir` · Resume textos longos\n"
            "`/traduzir` · Traduz textos para outros idiomas\n"
            "`/status-ia` · Consulta o estado dos serviços de IA\n"
            "`/canal-ia` · Configura a IA automática em um canal\n"
            "`/limpar-conversa` · Apaga o histórico de conversa"
        ),
    ),
    (
        f"{EMOJI_MOD} Moderação",
        discord.Color.red,
        (
            "`/ban` · Banimento permanente\n"
            "`/tempban` · Banimento temporário\n"
            "`/softban` · Banimento com limpeza de mensagens\n"
            "`/kick` · Expulsar um membro\n"
            "`/mute` · Silenciar temporariamente\n"
            "`/unmute` · Remover silenciamento\n"
            "`/warn` · Registrar um aviso\n"
            "`/remover-aviso` · Remover um aviso por ID\n"
            "`/avisos` · Consultar o histórico de avisos\n"
            "`/limpar-avisos` · Limpar avisos de um membro\n"
            "`/inspecionar` · Análise completa de moderação\n"
            "`/nota` · Adicionar uma nota interna\n"
            "`/purge` · Apagar mensagens com filtros\n"
            "`/lockdown` · Bloquear ou liberar um canal\n"
            "`/palavra-proibida` · Gerenciar o filtro de palavras\n"
            "`/config-mod` · Configurar o sistema de moderação"
        ),
    ),
    (
        f"{EMOJI_UTIL} Utilidades",
        discord.Color.green,
        (
            "`/userinfo` · Perfil detalhado de um usuário\n"
            "`/serverinfo` · Informações do servidor\n"
            "`/avatar` · Avatar em alta resolução\n"
            "`/banner` · Banner de um usuário\n"
            "`/roleinfo` · Informações de um cargo\n"
            "`/canalinfo` · Informações de um canal\n"
            "`/botinfo` · Apresentação e estatísticas do bot\n"
            "`/ping` · Latência e saúde dos serviços\n"
            "`/poll` · Enquete interativa\n"
            "`/lembrete` · Lembrete por mensagem direta\n"
            "`/timestamp` · Gerador de timestamps\n"
            "`/cor` · Conversor e visualizador de cores\n"
            "`/embed-custom` · Mensagem personalizada em Components V2\n"
            "`/ajuda` · Abre este painel"
        ),
    ),
]


class HelpView(LayoutView):
    def __init__(self, bot_user: discord.ClientUser) -> None:
        super().__init__(timeout=120)
        self.page = 0
        self.bot_user = bot_user
        self._render()

    def _render(self, *, closed: bool = False) -> None:
        self.clear_items()

        if closed:
            container = discord.ui.Container(
                discord.ui.TextDisplay(f"## {EMOJI_OK} Painel fechado\nUse `/ajuda` para abrir novamente."),
                accent_color=discord.Color.dark_grey(),
            )
            self.add_item(container)
            return

        title, color_fn, description = _HELP_PAGES[self.page]
        header = discord.ui.Section(
            discord.ui.TextDisplay(f"## {title}\nEscolha uma categoria usando os botões abaixo."),
            accessory=discord.ui.Thumbnail(
                self.bot_user.display_avatar.url,
                description="Avatar do Revolux",
            ),
        )

        previous_button = discord.ui.Button(
            label="Anterior",
            style=discord.ButtonStyle.secondary,
            emoji="<:1000032309:1508193593439949053>",
            disabled=self.page == 0,
            custom_id="utility_help_previous",
        )
        next_button = discord.ui.Button(
            label="Próxima",
            style=discord.ButtonStyle.secondary,
            emoji="<:1000032308:1508193552004546590>",
            disabled=self.page == len(_HELP_PAGES) - 1,
            custom_id="utility_help_next",
        )
        close_button = discord.ui.Button(
            label="Fechar",
            style=discord.ButtonStyle.danger,
            custom_id="utility_help_close",
        )

        previous_button.callback = self._previous
        next_button.callback = self._next
        close_button.callback = self._close

        container = discord.ui.Container(
            header,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(description),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"-# Revolux · Página {self.page + 1}/{len(_HELP_PAGES)}"
            ),
            discord.ui.ActionRow(previous_button, next_button, close_button),
            accent_color=color_fn(),
        )
        self.add_item(container)

    async def _previous(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        if self.page < len(_HELP_PAGES) - 1:
            self.page += 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def _close(self, interaction: discord.Interaction) -> None:
        self._render(closed=True)
        self.stop()
        await interaction.response.edit_message(view=self)


# ──────────────────────────────────────────────
# /poll em Components V2
# ──────────────────────────────────────────────


class PollView(LayoutView):
    def __init__(
        self,
        *,
        question: str,
        options: list[str],
        creator_id: int,
        creator_name: str,
        duration_minutes: int,
    ) -> None:
        super().__init__(timeout=duration_minutes * 60)
        self.question = question
        self.options = options
        self.creator_id = creator_id
        self.creator_name = creator_name
        self.duration_minutes = duration_minutes
        self.votes: dict[str, set[int]] = {option: set() for option in options}
        self.ended = False
        self._render()

    def _results_text(self) -> str:
        total = sum(len(voters) for voters in self.votes.values())
        lines: list[str] = []
        for option, voters in sorted(
            self.votes.items(), key=lambda item: -len(item[1])
        ):
            count = len(voters)
            percentage = (count / total * 100) if total else 0
            bar_length = int(percentage / 10)
            bar = "█" * bar_length + "░" * (10 - bar_length)
            lines.append(
                f"**{option}**\n`{bar}` {count} voto(s) · {percentage:.1f}%"
            )
        return "\n\n".join(lines) if lines else "Nenhum voto registrado."

    def _render(self) -> None:
        self.clear_items()
        total = sum(len(voters) for voters in self.votes.values())

        children: list[discord.ui.Item] = [
            discord.ui.TextDisplay(
                f"## {'Enquete encerrada' if self.ended else 'Enquete'}\n"
                f"### {_truncate(self.question, 300)}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(self._results_text()),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"-# Criada por {self.creator_name} · "
                f"{total} voto(s) · Duração: {self.duration_minutes} min"
            ),
        ]

        buttons: list[discord.ui.Button] = []
        for index, option in enumerate(self.options):
            button = discord.ui.Button(
                label=_truncate(option, 80),
                style=discord.ButtonStyle.primary,
                custom_id=f"utility_poll_{index}",
                disabled=self.ended,
            )
            button.callback = self._make_vote_callback(option)
            buttons.append(button)

        end_button = discord.ui.Button(
            label="Encerrar",
            style=discord.ButtonStyle.danger,
            emoji="<a:1000032057:1507947249873719497>",
            custom_id="utility_poll_end",
            disabled=self.ended,
        )
        end_button.callback = self._end_poll
        buttons.append(end_button)
        children.append(discord.ui.ActionRow(*buttons))

        self.add_item(
            discord.ui.Container(
                *children,
                accent_color=(
                    discord.Color.green()
                    if self.ended
                    else discord.Color.blurple()
                ),
            )
        )

    def _make_vote_callback(self, option: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.ended:
                await interaction.response.send_message(
                    f"{EMOJI_ERROR} Esta enquete já foi encerrada.",
                    ephemeral=True,
                )
                return

            user_id = interaction.user.id
            for voters in self.votes.values():
                voters.discard(user_id)
            self.votes[option].add(user_id)
            self._render()
            await interaction.response.edit_message(view=self)

        return callback

    async def _end_poll(self, interaction: discord.Interaction) -> None:
        can_manage = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_messages
        )
        if interaction.user.id != self.creator_id and not can_manage:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Apenas o criador ou um moderador pode encerrar a enquete.",
                ephemeral=True,
            )
            return

        self.ended = True
        self._render()
        self.stop()
        await interaction.response.edit_message(view=self)

    async def finish_automatically(self, interaction: discord.Interaction) -> None:
        await asyncio.sleep(self.duration_minutes * 60)
        if self.ended:
            return

        self.ended = True
        self._render()
        self.stop()
        try:
            await interaction.edit_original_response(view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────


class Utility(commands.Cog):
    """Cog de utilidades avançadas com respostas em Components V2."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self._gemini_ready = True
        else:
            self._gemini_ready = False

    async def _ai_complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 600,
    ) -> str:
        if not self._gemini_ready:
            return f"{EMOJI_LOADING} IA não configurada. A variável GEMINI_API_KEY está ausente."

        try:
            model_obj = genai.GenerativeModel(
                model_name=AI_MODEL,
                system_instruction=system,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.5,
                ),
            )
            response = await asyncio.to_thread(model_obj.generate_content, user)
            text = getattr(response, "text", "")
            return text.strip() or "A IA não retornou conteúdo."
        except Exception as exc:
            # Não expõe chave, prompt ou configuração interna. Apenas o tipo do erro.
            return f"{EMOJI_ERROR} Não foi possível concluir a solicitação ({type(exc).__name__})."

    # ── /ajuda ────────────────────────────────

    @app_commands.command(
        name="ajuda",
        description="Painel interativo com todos os comandos do Revolux.",
    )
    async def help(self, interaction: discord.Interaction) -> None:
        if not self.bot.user:
            await interaction.response.send_message(
                "Bot ainda inicializando.", ephemeral=True
            )
            return
        await interaction.response.send_message(view=HelpView(self.bot.user))

    # ── /ping ─────────────────────────────────

    @app_commands.command(
        name="ping",
        description="Mostra a latência e a saúde dos serviços.",
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        websocket_ms = round(self.bot.latency * 1000)

        start = time.perf_counter()
        await interaction.response.defer()
        api_ms = round((time.perf_counter() - start) * 1000)

        if websocket_ms < 100:
            color = discord.Color.green()
            status = "Excelente"
        elif websocket_ms < 200:
            color = discord.Color.gold()
            status = "Normal"
        else:
            color = discord.Color.red()
            status = "Alta latência"

        card = V2Card(accent_color=color)
        card.add_header(
            f"{EMOJI_PING} Status dos serviços",
            subtitle="Diagnóstico rápido da conexão do bot com o Discord.",
            thumbnail_url=(self.bot.user.display_avatar.url if self.bot.user else None),
        )
        card.add_separator()
        card.add_text(
            f"**WebSocket:** `{websocket_ms}ms`\n"
            f"**Resposta da API:** `{api_ms}ms`\n"
            f"**Estado:** **{status}**\n"
            f"**Uptime:** `{_uptime_str()}`"
        )
        card.add_footer()
        await interaction.followup.send(view=card)

    # ── /botinfo ──────────────────────────────

    @app_commands.command(
        name="botinfo",
        description="Apresentação, recursos e estatísticas do Revolux.",
    )
    async def botinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        bot = self.bot
        guild_count = len(bot.guilds)
        member_count = sum(guild.member_count or 0 for guild in bot.guilds)
        channel_count = sum(len(guild.channels) for guild in bot.guilds)
        command_count = len(bot.tree.get_commands())
        latency = round(bot.latency * 1000)
        bot_name = bot.user.name if bot.user else "Revolux"

        if latency < 100:
            status = "Excelente"
            color = discord.Color.green()
        elif latency < 200:
            status = "Normal"
            color = discord.Color.gold()
        else:
            status = "Alta latência"
            color = discord.Color.red()

        avatar_url = bot.user.display_avatar.with_size(256).url if bot.user else None
        banner_url: Optional[str] = None
        if bot.user:
            try:
                fetched_user = await bot.fetch_user(bot.user.id)
                if fetched_user.banner:
                    banner_url = fetched_user.banner.with_size(1024).url
            except (discord.HTTPException, discord.NotFound):
                pass

        card = V2Card(accent_color=color)
        card.add_header(
            f"{EMOJI_BOT} | {bot_name}",
            subtitle=(
                f"Olá! Eu sou o **{bot_name}**, uma inteligência artificial criada "
                "para conversar, auxiliar nos estudos e tornar servidores mais "
                "organizados e seguros."
            ),
            thumbnail_url=avatar_url,
        )

        if banner_url:
            card.add_gallery((banner_url, f"Banner oficial do {bot_name}"))

        card.add_separator(large=True)
        card.add_text(
            "### Um bot cotidiano e acadêmico\n"
            "Metade do meu trabalho reúne recursos comuns de servidor, como "
            "utilidades, lembretes, enquetes e informações de usuários. A outra "
            "metade é dedicada ao aprendizado e à pesquisa acadêmica.\n\n"
            "Posso ajudar em áreas como **programação, lógica, matemática, "
            "filosofia** e outros campos do conhecimento, incluindo explicação de "
            "conceitos, exercícios, revisão de textos e organização de estudos."
        )
        card.add_separator()
        card.add_text(
            "### Fontes e materiais acadêmicos\n"
            "Para enriquecer respostas de filosofia e áreas relacionadas, conto com "
            "referências de fontes especializadas, como a **Stanford Encyclopedia "
            "of Philosophy (SEP)**, o **PhilPapers** e outras plataformas relevantes.\n\n"
            "Minha base também inclui livros e materiais didáticos selecionados "
            "manualmente, entre eles obras usadas em cursos universitários e estudos "
            "de pós-graduação. As respostas servem como apoio, e trabalhos acadêmicos "
            "devem sempre conferir as fontes originais."
        )
        card.add_separator()
        card.add_text(
            "### Moderação auxiliada por IA\n"
            "Meu sistema de moderação pode analisar o contexto das mensagens e "
            "compará-lo com as regras configuradas no servidor. Isso auxilia na "
            "identificação de ofensas, ameaças, spam e outros conteúdos inadequados, "
            "sem depender apenas de palavras isoladas.\n\n"
            "As configurações e decisões finais permanecem sob o controle da equipe "
            "responsável pelo servidor."
        )
        card.add_separator()
        card.add_text(
            "### Desenvolvimento\n"
            "Fui criado e sou mantido por **Isabelle** 🤍, responsável pelo meu "
            "desenvolvimento, atualizações e expansão de recursos, com a ajuda de "
            "**Pedro** 🩵 e **Gustavo** 💚."
        )
        card.add_separator()
        card.add_text(
            "### Estatísticas\n"
            f"**Servidores:** `{_format_number(guild_count)}`\n"
            f"**Usuários alcançados:** `{_format_number(member_count)}`\n"
            f"**Canais monitorados:** `{_format_number(channel_count)}`\n"
            f"**Comandos disponíveis:** `{command_count}`\n"
            f"**Latência:** `{latency}ms`\n"
            f"**Status:** **{status}**\n"
            f"**Uptime:** `{_uptime_str()}`"
        )
        card.add_separator()
        card.add_text(
            "Precisa de ajuda, encontrou algum problema ou deseja enviar uma "
            "sugestão? Use os botões abaixo para acessar os canais oficiais."
        )

        support_button = discord.ui.Button(
            label="Servidor de suporte",
            style=discord.ButtonStyle.link,
            url=SUPPORT_INVITE,
            emoji=EMOJI_SUPPORT,
        )
        add_button = discord.ui.Button(
            label="Adicionar ao servidor",
            style=discord.ButtonStyle.link,
            url=(
                "https://discord.com/oauth2/authorize"
                f"?client_id={bot.user.id if bot.user else 0}"
                "&permissions=8&scope=bot%20applications.commands"
            ),
            emoji=EMOJI_ADD,
        )
        card.add_buttons(support_button, add_button)
        card.add_footer(
            f"ID {bot.user.id if bot.user else 'indisponível'}"
        )

        await interaction.followup.send(view=card)

    # ── /userinfo ─────────────────────────────

    @app_commands.command(name="userinfo", description="Perfil completo de um usuário.")
    @app_commands.describe(membro="Usuário a consultar (padrão: você)")
    async def userinfo(
        self,
        interaction: discord.Interaction,
        membro: Optional[discord.Member] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Este comando só pode ser usado em servidores.",
                ephemeral=True,
            )
            return

        member = membro or interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Não foi possível carregar esse membro.",
                ephemeral=True,
            )
            return

        roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
        roles_text = ", ".join(roles[:10]) if roles else "Nenhum cargo"
        if len(roles) > 10:
            roles_text += f" e mais `{len(roles) - 10}`"

        permissions = member.guild_permissions
        key_permissions: list[str] = []
        if permissions.administrator:
            key_permissions.append("Administrador")
        if permissions.manage_guild:
            key_permissions.append("Gerenciar servidor")
        if permissions.manage_channels:
            key_permissions.append("Gerenciar canais")
        if permissions.manage_messages:
            key_permissions.append("Gerenciar mensagens")
        if permissions.ban_members:
            key_permissions.append("Banir membros")
        if permissions.kick_members:
            key_permissions.append("Expulsar membros")
        if permissions.moderate_members:
            key_permissions.append("Moderar membros")

        flags = member.public_flags
        badges: list[str] = []
        if flags.staff:
            badges.append("Discord Staff")
        if flags.partner:
            badges.append("Parceiro do Discord")
        if flags.bug_hunter:
            badges.append("Bug Hunter")
        if flags.early_supporter:
            badges.append("Early Supporter")
        if flags.verified_bot_developer:
            badges.append("Desenvolvedor verificado")
        if flags.active_developer:
            badges.append("Active Developer")
        if member.premium_since:
            badges.append("Server Booster")

        created = int(member.created_at.timestamp())
        joined = int(member.joined_at.timestamp()) if member.joined_at else None
        boosting = (
            int(member.premium_since.timestamp()) if member.premium_since else None
        )

        details = (
            f"**Usuário:** {member.mention}\n"
            f"**Tag:** `{member}`\n"
            f"**ID:** `{member.id}`\n"
            f"**Tipo:** {'Bot' if member.bot else 'Pessoa'}\n"
            f"**Conta criada:** <t:{created}:D> · <t:{created}:R>\n"
        )
        if joined:
            details += f"**Entrou no servidor:** <t:{joined}:D> · <t:{joined}:R>\n"
        if boosting:
            details += f"**Impulsionando desde:** <t:{boosting}:D>\n"
        details += (
            f"**Cargo mais alto:** {member.top_role.mention}\n"
            f"**Cargos ({len(roles)}):** {_truncate(roles_text, 1200)}"
        )

        color = (
            member.color
            if member.color != discord.Color.default()
            else discord.Color.blurple()
        )
        card = V2Card(accent_color=color)
        card.add_header(
            f"{EMOJI_BOT} {member.display_name}",
            subtitle="Informações públicas do perfil neste servidor.",
            thumbnail_url=member.display_avatar.with_size(256).url,
        )
        card.add_separator()
        card.add_text(details)

        if key_permissions:
            card.add_separator()
            card.add_text(
                "### Permissões relevantes\n" + ", ".join(key_permissions)
            )

        if badges:
            card.add_separator()
            card.add_text("### Badges\n" + " · ".join(badges))

        if member.activities:
            activity = member.activities[0]
            activity_name = getattr(activity, "name", "Atividade desconhecida")
            activity_type = getattr(activity.type, "name", "atividade").replace("_", " ").title()
            card.add_separator()
            card.add_text(
                f"### Atividade\n**{activity_type}:** {_truncate(activity_name, 500)}"
            )

        card.add_footer()
        await interaction.response.send_message(view=card)

    # ── /serverinfo ───────────────────────────

    @app_commands.command(
        name="serverinfo",
        description="Painel completo de informações do servidor.",
    )
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Este comando só pode ser usado em servidores.",
                ephemeral=True,
            )
            return

        bots = sum(1 for member in guild.members if member.bot)
        humans = max(0, (guild.member_count or 0) - bots)
        online = sum(1 for member in guild.members if member.status == discord.Status.online)
        idle = sum(1 for member in guild.members if member.status == discord.Status.idle)
        dnd = sum(1 for member in guild.members if member.status == discord.Status.dnd)
        offline = max(0, (guild.member_count or 0) - online - idle - dnd)

        forum_count = sum(
            1 for channel in guild.channels if isinstance(channel, discord.ForumChannel)
        )
        owner_text = guild.owner.mention if guild.owner else "Indisponível"

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(
            f"{EMOJI_SERVER} {guild.name}",
            subtitle=f"Servidor criado em <t:{int(guild.created_at.timestamp())}:D>.",
            thumbnail_url=guild.icon.with_size(256).url if guild.icon else None,
        )

        if guild.banner:
            card.add_gallery((guild.banner.with_size(1024).url, f"Banner de {guild.name}"))

        card.add_separator()
        card.add_text(
            "### Identidade\n"
            f"**ID:** `{guild.id}`\n"
            f"**Proprietário:** {owner_text}\n"
            f"**Nível de verificação:** `{str(guild.verification_level).replace('_', ' ').title()}`\n"
            f"**2FA para moderadores:** `{'Ativada' if guild.mfa_level else 'Desativada'}`"
        )
        card.add_separator()
        card.add_text(
            "### Membros\n"
            f"**Total:** `{_format_number(guild.member_count or 0)}`\n"
            f"**Pessoas:** `{_format_number(humans)}`\n"
            f"**Bots:** `{_format_number(bots)}`\n"
            f"**Online:** `{online}` · **Ausentes:** `{idle}` · "
            f"**Não perturbe:** `{dnd}` · **Offline:** `{offline}`"
        )
        card.add_separator()
        card.add_text(
            "### Estrutura\n"
            f"**Canais de texto:** `{len(guild.text_channels)}`\n"
            f"**Canais de voz:** `{len(guild.voice_channels)}`\n"
            f"**Fóruns:** `{forum_count}`\n"
            f"**Palcos:** `{len(guild.stage_channels)}`\n"
            f"**Categorias:** `{len(guild.categories)}`\n"
            f"**Cargos:** `{len(guild.roles)}`\n"
            f"**Emojis:** `{len(guild.emojis)}`\n"
            f"**Stickers:** `{len(guild.stickers)}`"
        )
        card.add_separator()
        card.add_text(
            "### Impulsionamento\n"
            f"**Nível:** `{guild.premium_tier}`\n"
            f"**Boosts:** `{guild.premium_subscription_count or 0}`"
        )
        card.add_footer()
        await interaction.response.send_message(view=card)

    # ── /avatar ───────────────────────────────

    @app_commands.command(
        name="avatar",
        description="Exibe o avatar de um usuário em alta resolução.",
    )
    @app_commands.describe(
        membro="Usuário (padrão: você)",
        formato="jpg | png | webp | gif",
    )
    async def avatar(
        self,
        interaction: discord.Interaction,
        membro: Optional[discord.Member] = None,
        formato: Optional[str] = None,
    ) -> None:
        target = membro or interaction.user
        valid_formats = {"jpg", "png", "webp", "gif"}
        selected_format = (
            formato.lower()
            if formato and formato.lower() in valid_formats
            else None
        )

        try:
            asset = target.display_avatar.with_size(1024)
            if selected_format:
                asset = asset.with_format(selected_format)  # type: ignore[arg-type]
            url = asset.url
        except (ValueError, TypeError):
            url = target.display_avatar.url

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(
            f"Avatar de {target.display_name}",
            subtitle="Imagem em alta resolução.",
            thumbnail_url=target.display_avatar.with_size(256).url,
        )
        card.add_gallery((url, f"Avatar de {target.display_name}"))
        card.add_buttons(
            discord.ui.Button(
                label="Abrir imagem",
                style=discord.ButtonStyle.link,
                url=url,
            )
        )
        card.add_footer()
        await interaction.response.send_message(view=card)

    # ── /banner ───────────────────────────────

    @app_commands.command(name="banner", description="Exibe o banner de um usuário.")
    @app_commands.describe(membro="Usuário (padrão: você)")
    async def banner(
        self,
        interaction: discord.Interaction,
        membro: Optional[discord.Member] = None,
    ) -> None:
        target = membro or interaction.user
        await interaction.response.defer()

        try:
            user = await self.bot.fetch_user(target.id)
        except discord.NotFound:
            await interaction.followup.send(
                f"{EMOJI_ERROR} Usuário não encontrado.", ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                f"{EMOJI_ERROR} Não foi possível consultar o banner.", ephemeral=True
            )
            return

        if not user.banner:
            await interaction.followup.send(
                f"{EMOJI_ERROR} {target.display_name} não possui banner.",
                ephemeral=True,
            )
            return

        banner_url = user.banner.with_size(1024).url
        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(
            f"Banner de {target.display_name}",
            thumbnail_url=target.display_avatar.with_size(256).url,
        )
        card.add_gallery((banner_url, f"Banner de {target.display_name}"))
        card.add_buttons(
            discord.ui.Button(
                label="Abrir imagem",
                style=discord.ButtonStyle.link,
                url=banner_url,
            )
        )
        card.add_footer()
        await interaction.followup.send(view=card)

    # ── /roleinfo ─────────────────────────────

    @app_commands.command(name="roleinfo", description="Detalhes de um cargo.")
    @app_commands.describe(cargo="Cargo a inspecionar")
    async def roleinfo(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Este comando só pode ser usado em servidores.",
                ephemeral=True,
            )
            return

        permission_names = [
            name.replace("_", " ").title()
            for name, enabled in cargo.permissions
            if enabled
        ]
        member_count = sum(1 for member in interaction.guild.members if cargo in member.roles)
        role_color = (
            cargo.color
            if cargo.color != discord.Color.default()
            else discord.Color.dark_grey()
        )

        card = V2Card(accent_color=role_color)
        card.add_header(f"{EMOJI_MOD} Cargo · {cargo.name}")
        card.add_separator()
        card.add_text(
            f"**Menção:** {cargo.mention}\n"
            f"**ID:** `{cargo.id}`\n"
            f"**Posição:** `{cargo.position}`\n"
            f"**Membros:** `{member_count}`\n"
            f"**Cor:** `{cargo.color}`\n"
            f"**Mencionável:** `{'Sim' if cargo.mentionable else 'Não'}`\n"
            f"**Exibido separadamente:** `{'Sim' if cargo.hoist else 'Não'}`\n"
            f"**Gerenciado por integração:** `{'Sim' if cargo.managed else 'Não'}`\n"
            f"**Criado em:** <t:{int(cargo.created_at.timestamp())}:D>"
        )
        if permission_names:
            card.add_separator()
            shown = ", ".join(permission_names[:30])
            if len(permission_names) > 30:
                shown += f" e mais {len(permission_names) - 30}"
            card.add_text(
                f"### Permissões ({len(permission_names)})\n{_truncate(shown, 3000)}"
            )
        card.add_footer()
        await interaction.response.send_message(view=card)

    # ── /canalinfo ────────────────────────────

    @app_commands.command(
        name="canalinfo",
        description="Detalhes de um canal de texto.",
    )
    @app_commands.describe(canal="Canal a inspecionar (padrão: atual)")
    async def canalinfo(
        self,
        interaction: discord.Interaction,
        canal: Optional[discord.TextChannel] = None,
    ) -> None:
        channel = canal or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                f"{EMOJI_ERROR} O canal selecionado não é um canal de texto.",
                ephemeral=True,
            )
            return

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(f"{EMOJI_CHANNEL} Canal · #{channel.name}")
        card.add_separator()
        card.add_text(
            f"**Menção:** {channel.mention}\n"
            f"**ID:** `{channel.id}`\n"
            f"**Categoria:** `{channel.category.name if channel.category else 'Nenhuma'}`\n"
            f"**Criado em:** <t:{int(channel.created_at.timestamp())}:D>\n"
            f"**NSFW:** `{'Sim' if channel.is_nsfw() else 'Não'}`\n"
            f"**Modo lento:** `{channel.slowmode_delay}s`\n"
            f"**Posição:** `{channel.position}`"
        )
        if channel.topic:
            card.add_separator()
            card.add_text(f"### Tópico\n{_truncate(channel.topic, 3000)}")
        card.add_footer()
        await interaction.response.send_message(view=card)

    # ── /poll ─────────────────────────────────

    @app_commands.command(
        name="poll",
        description="Cria uma enquete interativa com botões.",
    )
    @app_commands.describe(
        pergunta="Pergunta da enquete",
        opcoes="Opções separadas por vírgula (2 a 4 opções)",
        duracao="Duração em minutos (padrão: 60)",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def poll(
        self,
        interaction: discord.Interaction,
        pergunta: str,
        opcoes: str,
        duracao: int = 60,
    ) -> None:
        option_list = [option.strip() for option in opcoes.split(",") if option.strip()]
        # Uma ActionRow suporta no máximo cinco botões; um deles é o botão Encerrar.
        if len(option_list) < 2 or len(option_list) > 4:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Forneça entre 2 e 4 opções separadas por vírgula.",
                ephemeral=True,
            )
            return

        duration = max(1, min(duracao, 10080))
        view = PollView(
            question=_truncate(pergunta, 300),
            options=[_truncate(option, 80) for option in option_list],
            creator_id=interaction.user.id,
            creator_name=interaction.user.display_name,
            duration_minutes=duration,
        )
        await interaction.response.send_message(view=view)
        asyncio.create_task(view.finish_automatically(interaction))

    # ── /lembrete ─────────────────────────────

    @app_commands.command(
        name="lembrete",
        description="Define um lembrete que chegará por mensagem direta.",
    )
    @app_commands.describe(
        minutos="Em quantos minutos lembrar",
        mensagem="O que lembrar",
    )
    async def lembrete(
        self,
        interaction: discord.Interaction,
        minutos: int,
        mensagem: str,
    ) -> None:
        if minutos < 1 or minutos > 10080:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Defina um período entre 1 minuto e 7 dias.",
                ephemeral=True,
            )
            return

        confirmation = V2Card(accent_color=discord.Color.green())
        confirmation.add_header(
            f"{EMOJI_CLOCK} Lembrete definido",
            subtitle=f"Vou enviar uma mensagem direta em **{minutos} minuto(s)**.",
        )
        confirmation.add_text(f"**Conteúdo:** {_truncate(mensagem, 3000)}")
        confirmation.add_footer()
        await interaction.response.send_message(view=confirmation, ephemeral=True)

        user = interaction.user
        guild_name = _safe_guild_name(interaction)

        async def _remind() -> None:
            await asyncio.sleep(minutos * 60)
            try:
                reminder = V2Card(accent_color=discord.Color.gold())
                reminder.add_header(f"{EMOJI_REMINDER} Lembrete")
                reminder.add_separator()
                reminder.add_text(_truncate(mensagem, 3900))
                reminder.add_footer(
                    f"Definido em {guild_name} há {minutos} minuto(s)"
                )
                await user.send(view=reminder)
            except discord.Forbidden:
                pass

        asyncio.create_task(_remind())

    # ── /timestamp ────────────────────────────

    @app_commands.command(
        name="timestamp",
        description="Gera timestamps formatados para o Discord.",
    )
    @app_commands.describe(
        ano="Ano",
        mes="Mês (1-12)",
        dia="Dia",
        hora="Hora UTC (0-23)",
        minuto="Minuto (0-59)",
    )
    async def timestamp(
        self,
        interaction: discord.Interaction,
        ano: int,
        mes: int,
        dia: int,
        hora: int = 0,
        minuto: int = 0,
    ) -> None:
        try:
            date = datetime(ano, mes, dia, hora, minuto, tzinfo=timezone.utc)
        except ValueError as exc:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Data inválida: {exc}", ephemeral=True
            )
            return

        timestamp_value = int(date.timestamp())
        formats = [
            ("Data curta", "d"),
            ("Data longa", "D"),
            ("Hora curta", "t"),
            ("Hora longa", "T"),
            ("Data e hora", "f"),
            ("Data e hora longa", "F"),
            ("Relativo", "R"),
        ]
        format_lines = [
            f"**{label} (`{code}`):** <t:{timestamp_value}:{code}> · "
            f"`<t:{timestamp_value}:{code}>`"
            for label, code in formats
        ]

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header("Gerador de timestamps")
        card.add_text(
            f"**Unix:** `{timestamp_value}`\n"
            f"**UTC:** `{date.strftime('%Y-%m-%d %H:%M')}`"
        )
        card.add_separator()
        card.add_text("\n".join(format_lines))
        card.add_footer()
        await interaction.response.send_message(view=card)

    # ── /cor ──────────────────────────────────

    @app_commands.command(
        name="cor",
        description="Exibe e converte uma cor HEX.",
    )
    @app_commands.describe(hex_code="Código HEX, por exemplo #FF5733")
    async def cor(self, interaction: discord.Interaction, hex_code: str) -> None:
        try:
            r, g, b = _hex_to_rgb(hex_code)
        except ValueError:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} HEX inválido. Use `#RRGGBB` ou `#RGB`.",
                ephemeral=True,
            )
            return

        clean = f"{r:02X}{g:02X}{b:02X}"
        h, s, l = _rgb_to_hsl(r, g, b)
        color = discord.Color.from_rgb(r, g, b)
        preview_url = f"https://singlecolorimage.com/get/{clean}/800x180"

        card = V2Card(accent_color=color)
        card.add_header(f"Cor · #{clean}")
        card.add_gallery((preview_url, f"Prévia da cor #{clean}"))
        card.add_separator()
        card.add_text(
            f"**HEX:** `#{clean}`\n"
            f"**RGB:** `rgb({r}, {g}, {b})`\n"
            f"**HSL:** `hsl({h}°, {s}%, {l}%)`\n"
            f"**Decimal:** `{color.value}`"
        )
        card.add_footer()
        await interaction.response.send_message(view=card)

    # ══════════════════════════════════════════
    # Comandos com IA
    # ══════════════════════════════════════════

    @app_commands.command(name="resumir", description="Resume um texto longo com IA.")
    @app_commands.describe(
        texto="Texto a ser resumido",
        idioma="Idioma do resumo (padrão: português)",
    )
    async def resumir(
        self,
        interaction: discord.Interaction,
        texto: str,
        idioma: str = "português",
    ) -> None:
        await interaction.response.defer()
        result = await self._ai_complete(
            system=(
                f"Resuma o texto em {idioma}, de forma clara e concisa, em no máximo "
                "três parágrafos. Preserve conceitos, ressalvas e conclusões importantes."
            ),
            user=texto[:4000],
            max_tokens=500,
        )

        card = V2Card(accent_color=discord.Color.green())
        card.add_header("Resumo")
        card.add_separator()
        card.add_text(_truncate(result, 4000))
        card.add_footer("Resposta gerada por IA")
        await interaction.followup.send(view=card)

    @app_commands.command(name="traduzir", description="Traduz um texto com IA.")
    @app_commands.describe(
        texto="Texto a traduzir",
        idioma_destino="Idioma de destino, como inglês ou espanhol",
    )
    async def traduzir(
        self,
        interaction: discord.Interaction,
        texto: str,
        idioma_destino: str = "inglês",
    ) -> None:
        await interaction.response.defer()
        result = await self._ai_complete(
            system=(
                f"Traduza o texto para {idioma_destino}. Retorne apenas a tradução, "
                "sem explicações, notas ou comentários adicionais."
            ),
            user=texto[:3000],
            max_tokens=700,
        )

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(f"Tradução para {idioma_destino.title()}")
        card.add_separator()
        card.add_text(f"### Original\n{_truncate(texto, 1500)}")
        card.add_separator()
        card.add_text(f"### Tradução\n{_truncate(result, 3500)}")
        card.add_footer("Resposta gerada por IA")
        await interaction.followup.send(view=card)

    @app_commands.command(
        name="pergunte-ia",
        description="Faça uma pergunta rápida para a IA do Revolux.",
    )
    @app_commands.describe(pergunta="Sua pergunta")
    async def pergunte_ia(
        self,
        interaction: discord.Interaction,
        pergunta: str,
    ) -> None:
        await interaction.response.defer()
        guild_context = (
            f"Você está no servidor Discord '{_safe_guild_name(interaction)}'. "
            "Responda em português de forma clara e objetiva, em no máximo três "
            "parágrafos. Quando houver incerteza, deixe-a explícita."
        )
        result = await self._ai_complete(
            system=guild_context,
            user=pergunta[:2000],
            max_tokens=700,
        )

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header("Resposta da IA")
        card.add_text(f"### Pergunta\n> {_truncate(pergunta, 1000)}")
        card.add_separator()
        card.add_text(f"### Resposta\n{_truncate(result, 4000)}")
        card.add_footer(f"Solicitado por {interaction.user.display_name}")
        await interaction.followup.send(view=card)

    # ── /embed-custom, agora Components V2 ───

    @app_commands.command(
        name="embed-custom",
        description="Cria uma mensagem personalizada em Components V2.",
    )
    @app_commands.describe(
        titulo="Título da mensagem",
        descricao="Descrição ou conteúdo",
        cor="Cor HEX, por exemplo #5865F2",
        imagem="URL de uma imagem de destaque",
        rodape="Texto do rodapé",
        canal="Canal onde enviar (padrão: atual)",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def embed_custom(
        self,
        interaction: discord.Interaction,
        titulo: str,
        descricao: str,
        cor: str = "#5865F2",
        imagem: Optional[str] = None,
        rodape: Optional[str] = None,
        canal: Optional[discord.TextChannel] = None,
    ) -> None:
        target = canal or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Canal inválido.", ephemeral=True
            )
            return

        try:
            r, g, b = _hex_to_rgb(cor)
            color = discord.Color.from_rgb(r, g, b)
        except ValueError:
            color = discord.Color.blurple()

        card = V2Card(accent_color=color)
        card.add_header(_truncate(titulo, 300))
        card.add_separator()
        card.add_text(_truncate(descricao, 12000))
        if imagem:
            card.add_separator()
            card.add_gallery((imagem, _truncate(titulo, 1024)))
        if rodape:
            card.add_separator()
            card.add_text(f"-# {_truncate(rodape, 1000)}")

        try:
            await target.send(view=card)
        except discord.HTTPException:
            await interaction.response.send_message(
                f"{EMOJI_ERROR} Não foi possível enviar a mensagem. Verifique a URL "
                "da imagem e o tamanho do conteúdo.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"{EMOJI_OK} Mensagem Components V2 enviada em {target.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
