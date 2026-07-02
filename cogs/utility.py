"""
utility.py — Revolux · Utilidades em Discord Components V2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Arquitetura híbrida:
  • Slash commands mantidos: /ajuda, /ping, /botinfo
  • Todo o resto funciona por MENÇÃO, estilo Jarvis:
      "@RevolutX avatar do Pedro"
      "@RevolutX info do servidor"
      "@RevolutX traduz isso para inglês: [texto]"
      "@RevolutX crie uma enquete sobre X com opções A, B, C"
      "@RevolutX me lembra daqui 30 minutos de tomar água"
      "@RevolutX cor #FF5733"
      "@RevolutX timestamp 2026 12 31 23 59"
      "@RevolutX resume: [texto]"
      "@RevolutX info do cargo @Staff"
      "@RevolutX info do canal #geral"
  • Se o pedido não for reconhecido, cai no ai_chat normalmente (sem dupla resposta).
  • Moderação continua 100% em slash commands (outro cog).

Variáveis de ambiente:
  GEMINI_API_KEY   — chave usada pelos comandos de IA
  UTIL_AI_MODEL    — modelo das utilidades (padrão: gemini-2.5-flash)
  SUPPORT_INVITE   — convite do servidor de suporte

Requisitos recomendados no requirements.txt:
  discord.py>=2.7.1,<3.0
  google-genai>=2.9.0,<3.0
"""

from __future__ import annotations

import asyncio
import colorsys
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import types

from utils.mention_gate import mark_handled


logger = logging.getLogger("Revolux.Utility")


# Components V2 chegaram ao discord.py na versão 2.6.
# A versão 2.7.1 corrige um vazamento relacionado a LayoutView.clear_items().
LayoutView = getattr(discord.ui, "LayoutView", None)
if LayoutView is None:
    raise RuntimeError(
        "Este arquivo exige discord.py 2.7.1 ou superior. "
        "Atualize o requirements.txt para: discord.py>=2.7.1,<3.0"
    )


AI_MODEL = os.getenv("UTIL_AI_MODEL", "gemini-2.5-flash")
AI_TIMEOUT_SECONDS = max(5.0, float(os.getenv("UTIL_AI_TIMEOUT", "45")))
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
    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return round(hue * 360, 1), round(saturation * 100, 1), round(lightness * 100, 1)


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
            "A IA do Revolux funciona por **menção direta**, basta me citar e escrever.\n\n"
            f"**Me mencione e pergunte qualquer coisa:**\n"
            f"→ {EMOJI_AI} Conversa geral, dúvidas, raciocínio, código\n"
            f"→ {EMOJI_AI} Explicações acadêmicas, resumos, quizzes\n"
            f"→ {EMOJI_AI} Análise de argumentos e lógica\n"
            f"→ {EMOJI_AI} Pesquisa com fontes e referências\n\n"
            "**Exemplos de uso:**\n"
            "`@Revolux explica o que é epistemologia`\n"
            "`@Revolux me ajuda com esse código Python`\n"
            "`@Revolux analisa esse argumento: [texto]`\n"
            "`@Revolux pesquise sobre inteligência artificial`\n\n"
            "**Slash commands de IA disponíveis:**\n"
            "`/perfil-ia` · Define estilo e nível acadêmico\n"
            "`/memoria-ia` · Consulta e controla a memória semântica\n"
            "`/status-ia` · Estado dos serviços de IA\n"
            "`/canal-ia` · Configura a IA automática em um canal\n"
            "`/limpar-conversa` · Apaga o histórico de conversa"
        ),
    ),
    (
        f"{EMOJI_MOD} Moderação",
        discord.Color.red,
        (
            "Ações diretas funcionam por **menção**, comandos de consulta e "
            "configuração continuam como **slash commands** (`/`).\n\n"
            "**Ações por menção:**\n"
            "`@Revolux bane @usuário [motivo]`, banimento permanente\n"
            "`@Revolux tempban @usuário [X] minutos [motivo]`, banimento temporário\n"
            "`@Revolux softban @usuário [motivo]`, banimento com limpeza de mensagens\n"
            "`@Revolux expulsa @usuário [motivo]`, expulsar um membro\n"
            "`@Revolux muta @usuário [X] minutos [motivo]`, silenciar temporariamente\n"
            "`@Revolux desmuta @usuário`, remover silenciamento\n"
            "`@Revolux avisa @usuário [motivo]`, registrar um aviso\n\n"
            "**Slash commands de consulta e configuração:**\n"
            "`/avisos` · Consultar o histórico de avisos\n"
            "`/remover-aviso` · Remover um aviso por ID\n"
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
        f"{EMOJI_UTIL} Utilidades por Menção",
        discord.Color.green,
        (
            "As utilidades funcionam por **menção**, me cite e descreva o que quer.\n\n"
            f"**{EMOJI_BOT} Informações de usuário:**\n"
            "→ `@Revolux info do [usuário]` · Perfil completo\n"
            "→ `@Revolux avatar do [usuário]` · Avatar em alta resolução\n"
            "→ `@Revolux banner do [usuário]` · Banner do perfil\n\n"
            f"**{EMOJI_SERVER} Informações do servidor:**\n"
            "→ `@Revolux info do servidor` · Painel do servidor\n"
            "→ `@Revolux info do cargo @Cargo` · Detalhes do cargo\n"
            "→ `@Revolux info do canal #canal` · Detalhes do canal\n\n"
            f"**{EMOJI_UTIL} Ferramentas:**\n"
            "→ `@Revolux enquete Pergunta? op1, op2, op3` · Enquete\n"
            "→ `@Revolux me lembra em [X] minutos de [mensagem]` · Lembrete\n"
            "→ `@Revolux timestamp [ano] [mês] [dia]` · Timestamps\n"
            "→ `@Revolux cor #RRGGBB` · Preview e conversão de cor\n\n"
            f"**{EMOJI_AI} IA rápida:**\n"
            "→ `@Revolux resume: [texto]` · Resumo com IA\n"
            "→ `@Revolux traduz para [idioma]: [texto]` · Tradução com IA\n\n"
            f"**{EMOJI_MOD} Moderação por menção:**\n"
            "→ `@Revolux bane`, `muta`, `desmuta`, `expulsa @usuário`, entre outras\n"
            "→ Veja a página de Moderação para a lista completa\n\n"
            f"**Slash commands disponíveis:** `/ajuda` · `/ping` · `/botinfo`"
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
            emoji=discord.PartialEmoji(name="1000032309", id=1508193593439949053),
            disabled=self.page == 0,
            custom_id="utility_help_previous",
        )
        next_button = discord.ui.Button(
            label="Próxima",
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji(name="1000032308", id=1508193552004546590),
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
            emoji=discord.PartialEmoji(name="1000032057", id=1507947249873719497, animated=True),
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

    async def finish_automatically(self, message: discord.Message) -> None:
        if self.ended:
            return

        self.ended = True
        self._render()
        self.stop()
        try:
            await message.edit(view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────


class Utility(commands.Cog):
    """Cog de utilidades avançadas com respostas em Components V2."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._gemini_clients: list[genai.Client] = []
        self._gemini_key_index = 0
        self._ai_lock = asyncio.Lock()

        for env_name in (
            "GEMINI_API_KEY",
            "GEMINI_API_KEY_2",
            "GEMINI_API_KEY_3",
            "GEMINI_API_KEY_4",
            "GEMINI_API_KEY_5",
        ):
            api_key = os.getenv(env_name)
            if not api_key:
                continue
            try:
                self._gemini_clients.append(genai.Client(api_key=api_key))
                logger.info("%s carregada para as utilidades de IA.", env_name)
            except Exception as exc:
                logger.warning(
                    "Não foi possível preparar %s para as utilidades: %s",
                    env_name,
                    type(exc).__name__,
                )

        self._gemini_ready = bool(self._gemini_clients)
        if not self._gemini_ready:
            logger.warning(
                "Comandos de IA do utility desativados: nenhuma GEMINI_API_KEY encontrada."
            )

    @staticmethod
    def _extract_genai_text(response: object) -> str:
        """Extrai texto do SDK atual mesmo quando ``response.text`` não existe."""
        try:
            direct = getattr(response, "text", None)
            if direct:
                return str(direct).strip()
        except Exception:
            pass

        parts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                value = getattr(part, "text", None)
                if value:
                    parts.append(str(value))
        return "\n".join(parts).strip()

    @staticmethod
    def _is_retryable_genai_error(exc: Exception) -> bool:
        error = str(exc).lower()
        return any(
            marker in error
            for marker in (
                "429",
                "quota",
                "resource_exhausted",
                "rate limit",
                "too many requests",
                "503",
                "unavailable",
                "timeout",
                "timed out",
                "connection",
            )
        )

    async def _ai_complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 600,
    ) -> str:
        if not self._gemini_ready:
            return f"{EMOJI_LOADING} IA não configurada. A variável GEMINI_API_KEY está ausente."

        # Evita várias chamadas simultâneas deste cog disputando a mesma chave.
        async with self._ai_lock:
            total_clients = len(self._gemini_clients)
            start_index = self._gemini_key_index % total_clients
            last_error: Exception | None = None

            for offset in range(total_clients):
                index = (start_index + offset) % total_clients
                client = self._gemini_clients[index]

                try:
                    response = await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=AI_MODEL,
                            contents=user,
                            config=types.GenerateContentConfig(
                                system_instruction=system,
                                max_output_tokens=max(64, min(int(max_tokens), 4096)),
                                temperature=0.5,
                            ),
                        ),
                        timeout=AI_TIMEOUT_SECONDS,
                    )
                    result = self._extract_genai_text(response)
                    self._gemini_key_index = index
                    if result:
                        return result

                    logger.warning(
                        "Gemini retornou resposta vazia nas utilidades (chave #%s).",
                        index + 1,
                    )
                    last_error = RuntimeError("Resposta vazia do Gemini")
                except Exception as exc:
                    last_error = exc
                    retryable = self._is_retryable_genai_error(exc)
                    logger.warning(
                        "Falha Gemini nas utilidades | chave #%s | %s | retry=%s",
                        index + 1,
                        type(exc).__name__,
                        retryable,
                    )
                    self._gemini_key_index = (index + 1) % total_clients
                    if not retryable:
                        break

            error_name = type(last_error).__name__ if last_error else "ErroDesconhecido"
            return f"{EMOJI_ERROR} Não foi possível concluir a solicitação ({error_name})."

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

    # ══════════════════════════════════════════
    # SISTEMA DE MENÇÃO — "JARVIS MODE"
    # Intercepta menções antes do ai_chat e
    # executa a utilidade correspondente.
    # Se nenhuma intenção for reconhecida,
    # retorna False e o ai_chat responde normal.
    # ══════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not self.bot.user:
            return
        if self.bot.user not in message.mentions:
            return

        # Remove a menção do conteúdo
        raw = message.content
        content = raw.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()
        if not content:
            return

        low = content.lower()

        # ── Detecta a intenção primeiro (rápido, só regex) e marca o gate
        # imediatamente, antes de qualquer chamada assíncrona (IA, Discord API).
        # Isso garante que o ai_chat.py veja o gate marcado mesmo quando o
        # handler de intenção (ex.: resumo com IA) demora para responder.
        intent_handler = self._detect_intent(message, low, content)
        if intent_handler is None:
            return

        mark_handled(message.id)
        await intent_handler(message, content, low)

    def _detect_intent(self, message: discord.Message, low: str, content: str):
        """Retorna o handler de intenção correspondente, ou None se nenhum bater."""
        checks: list[tuple[bool, Callable]] = [
            (self._matches_userinfo(low), self._intent_userinfo),
            (self._matches_avatar(low), self._intent_avatar),
            (self._matches_banner(low), self._intent_banner),
            (self._matches_serverinfo(low), self._intent_serverinfo),
            (self._matches_roleinfo(low), self._intent_roleinfo),
            (self._matches_canalinfo(low), self._intent_canalinfo),
            (self._matches_poll(low), self._intent_poll),
            (self._matches_lembrete(low), self._intent_lembrete),
            (self._matches_timestamp(low), self._intent_timestamp),
            (self._matches_cor(low, content), self._intent_cor),
            (self._matches_resumir(low), self._intent_resumir),
            (self._matches_traduzir(low), self._intent_traduzir),
        ]
        for matched, handler in checks:
            if matched:
                return handler
        return None

    # ── matchers rápidos (somente detecção, sem efeitos colaterais) ──

    @staticmethod
    def _matches_userinfo(low: str) -> bool:
        # Triggers estritos: exigem palavras específicas de consulta de perfil
        # "quem é" sozinho é muito genérico e causa falso positivo em enquetes
        strict_triggers = ("info do usuário", "info do usuario", "userinfo", "perfil de")
        if any(t in low for t in strict_triggers):
            return True
        # "quem é" só vale se não houver palavras de outros intents no texto
        poll_words = ("enquete", "poll", "votação", "votacao", "melhor", "pior", "favorito")
        if ("quem é" in low or "quem e " in low) and not any(p in low for p in poll_words):
            return True
        return False

    @staticmethod
    def _matches_avatar(low: str) -> bool:
        return "avatar" in low

    @staticmethod
    def _matches_banner(low: str) -> bool:
        return "banner" in low

    @staticmethod
    def _matches_serverinfo(low: str) -> bool:
        triggers = ("info do servidor", "serverinfo", "servidor info", "informações do servidor", "informacoes do servidor")
        return any(t in low for t in triggers)

    @staticmethod
    def _matches_roleinfo(low: str) -> bool:
        triggers = ("info do cargo", "roleinfo", "cargo info", "informações do cargo", "informacoes do cargo")
        return any(t in low for t in triggers)

    @staticmethod
    def _matches_canalinfo(low: str) -> bool:
        triggers = ("info do canal", "canalinfo", "canal info", "informações do canal", "informacoes do canal")
        return any(t in low for t in triggers)

    @staticmethod
    def _matches_poll(low: str) -> bool:
        triggers = ("enquete", "poll", "votação", "votacao")
        return any(t in low for t in triggers)

    @staticmethod
    def _matches_lembrete(low: str) -> bool:
        triggers = ("me lembra", "lembre-me", "lembre me", "lembrete", "lembra em", "lembrar em")
        return any(t in low for t in triggers)

    @staticmethod
    def _matches_timestamp(low: str) -> bool:
        return "timestamp" in low

    @staticmethod
    def _matches_cor(low: str, content: str) -> bool:
        return "cor" in low or "#" in content

    @staticmethod
    def _matches_resumir(low: str) -> bool:
        triggers = ("resume", "resumo de", "resumir", "resuma")
        return any(t in low for t in triggers)

    @staticmethod
    def _matches_traduzir(low: str) -> bool:
        triggers = ("traduz", "traduza", "traduzir", "translate")
        return any(t in low for t in triggers)

    # ── helpers de parsing ────────────────────


    def _resolve_member(
        self, message: discord.Message, text: str
    ) -> Optional[discord.Member]:
        """Tenta encontrar um membro a partir de menção, ID ou nome no texto."""
        guild = message.guild
        if not guild:
            return None
        # Menção explícita na mensagem (exceto o bot)
        for m in message.mentions:
            if m != self.bot.user:
                return guild.get_member(m.id)
        # ID numérico no texto
        match = re.search(r"\b(\d{17,20})\b", text)
        if match:
            member = guild.get_member(int(match.group(1)))
            if member:
                return member
        # Nome por texto (fuzzy simples)
        text_clean = re.sub(r"<@!?\d+>", "", text).strip().lower()
        # Remove palavras-chave comuns para isolar o nome
        for kw in ("do", "da", "de", "info", "avatar", "banner", "usuário", "usuario", "perfil"):
            text_clean = text_clean.replace(kw, "").strip()
        if text_clean:
            for m in guild.members:
                if text_clean in m.display_name.lower() or text_clean in m.name.lower():
                    return m
        return None

    async def _send_card(self, message: discord.Message, card: "V2Card") -> None:
        """Envia um V2Card como reply."""
        try:
            await message.reply(view=card, mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await message.channel.send(view=card)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _send_error(self, message: discord.Message, text: str) -> None:
        try:
            await message.reply(f"{EMOJI_ERROR} {text}", mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── intent handlers ───────────────────────

    async def _intent_userinfo(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("info do usuário", "info do usuario", "userinfo", "perfil de", "quem é", "quem e")
        if not any(t in low for t in triggers):
            return False

        guild = message.guild
        member = self._resolve_member(message, content)
        if not member:
            member = message.author if isinstance(message.author, discord.Member) else None
        if not member or not guild:
            await self._send_error(message, "Não encontrei o usuário. Mencione-o diretamente.")
            return True

        roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
        roles_text = ", ".join(roles[:10]) if roles else "Nenhum cargo"
        if len(roles) > 10:
            roles_text += f" e mais `{len(roles) - 10}`"

        permissions = member.guild_permissions
        key_perms: list[str] = []
        if permissions.administrator: key_perms.append("Administrador")
        if permissions.manage_guild: key_perms.append("Gerenciar servidor")
        if permissions.manage_messages: key_perms.append("Gerenciar mensagens")
        if permissions.ban_members: key_perms.append("Banir membros")
        if permissions.kick_members: key_perms.append("Expulsar membros")
        if permissions.moderate_members: key_perms.append("Moderar membros")

        flags = member.public_flags
        badges: list[str] = []
        if flags.staff: badges.append("Discord Staff")
        if flags.partner: badges.append("Parceiro")
        if flags.bug_hunter: badges.append("Bug Hunter")
        if flags.early_supporter: badges.append("Early Supporter")
        if flags.verified_bot_developer: badges.append("Dev Verificado")
        if flags.active_developer: badges.append("Active Developer")
        if member.premium_since: badges.append("Server Booster")

        created = int(member.created_at.timestamp())
        joined = int(member.joined_at.timestamp()) if member.joined_at else None
        boosting = int(member.premium_since.timestamp()) if member.premium_since else None

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

        color = member.color if member.color != discord.Color.default() else discord.Color.blurple()
        card = V2Card(accent_color=color)
        card.add_header(
            f"{EMOJI_BOT} {member.display_name}",
            subtitle="Informações públicas do perfil neste servidor.",
            thumbnail_url=member.display_avatar.with_size(256).url,
        )
        card.add_separator()
        card.add_text(details)
        if key_perms:
            card.add_separator()
            card.add_text("### Permissões relevantes\n" + ", ".join(key_perms))
        if badges:
            card.add_separator()
            card.add_text("### Badges\n" + " · ".join(badges))
        if member.activities:
            act = member.activities[0]
            act_name = getattr(act, "name", "Atividade desconhecida")
            act_type = getattr(act.type, "name", "atividade").replace("_", " ").title()
            card.add_separator()
            card.add_text(f"### Atividade\n**{act_type}:** {_truncate(act_name, 500)}")
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_avatar(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        if "avatar" not in low:
            return False

        member = self._resolve_member(message, content)
        target = member or message.author

        try:
            url = target.display_avatar.with_size(1024).url
        except Exception:
            url = target.display_avatar.url

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(
            f"Avatar de {target.display_name}",
            subtitle="Imagem em alta resolução.",
            thumbnail_url=target.display_avatar.with_size(256).url,
        )
        card.add_gallery((url, f"Avatar de {target.display_name}"))
        card.add_buttons(
            discord.ui.Button(label="Abrir imagem", style=discord.ButtonStyle.link, url=url)
        )
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_banner(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        if "banner" not in low:
            return False

        member = self._resolve_member(message, content)
        target = member or message.author

        try:
            user = await self.bot.fetch_user(target.id)
        except (discord.NotFound, discord.HTTPException):
            await self._send_error(message, "Não consegui consultar o banner desse usuário.")
            return True

        if not user.banner:
            await self._send_error(message, f"**{target.display_name}** não possui banner.")
            return True

        banner_url = user.banner.with_size(1024).url
        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(
            f"Banner de {target.display_name}",
            thumbnail_url=target.display_avatar.with_size(256).url,
        )
        card.add_gallery((banner_url, f"Banner de {target.display_name}"))
        card.add_buttons(
            discord.ui.Button(label="Abrir imagem", style=discord.ButtonStyle.link, url=banner_url)
        )
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_serverinfo(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("info do servidor", "serverinfo", "servidor info", "informações do servidor", "informacoes do servidor")
        if not any(t in low for t in triggers):
            return False

        guild = message.guild
        if not guild:
            return False

        bots = sum(1 for m in guild.members if m.bot)
        humans = max(0, (guild.member_count or 0) - bots)
        online = sum(1 for m in guild.members if m.status == discord.Status.online)
        idle   = sum(1 for m in guild.members if m.status == discord.Status.idle)
        dnd    = sum(1 for m in guild.members if m.status == discord.Status.dnd)
        offline = max(0, (guild.member_count or 0) - online - idle - dnd)
        forum_count = sum(1 for ch in guild.channels if isinstance(ch, discord.ForumChannel))

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
            f"**Proprietário:** {guild.owner.mention if guild.owner else 'Indisponível'}\n"
            f"**Verificação:** `{str(guild.verification_level).replace('_', ' ').title()}`\n"
            f"**2FA moderadores:** `{'Ativada' if guild.mfa_level else 'Desativada'}`"
        )
        card.add_separator()
        card.add_text(
            "### Membros\n"
            f"**Total:** `{_format_number(guild.member_count or 0)}`\n"
            f"**Pessoas:** `{_format_number(humans)}` · **Bots:** `{_format_number(bots)}`\n"
            f"🟢`{online}` 🟡`{idle}` 🔴`{dnd}` ⚫`{offline}`"
        )
        card.add_separator()
        card.add_text(
            "### Estrutura\n"
            f"**Texto:** `{len(guild.text_channels)}` · **Voz:** `{len(guild.voice_channels)}` · "
            f"**Fórum:** `{forum_count}` · **Categorias:** `{len(guild.categories)}`\n"
            f"**Cargos:** `{len(guild.roles)}` · **Emojis:** `{len(guild.emojis)}` · "
            f"**Stickers:** `{len(guild.stickers)}`\n"
            f"**Boost:** Nível `{guild.premium_tier}` · `{guild.premium_subscription_count or 0}` boost(s)"
        )
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_roleinfo(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("info do cargo", "roleinfo", "cargo info", "informações do cargo", "informacoes do cargo")
        if not any(t in low for t in triggers):
            return False

        guild = message.guild
        if not guild:
            return False

        # Cargo mencionado
        cargo: Optional[discord.Role] = None
        if message.role_mentions:
            cargo = message.role_mentions[0]
        else:
            # Tenta encontrar pelo nome no texto
            text_clean = re.sub(r"<@&?\d+>", "", content).strip().lower()
            for kw in ("info do cargo", "roleinfo", "cargo info", "informações do cargo"):
                text_clean = text_clean.replace(kw, "").strip()
            if text_clean:
                for r in guild.roles:
                    if text_clean in r.name.lower():
                        cargo = r
                        break

        if not cargo:
            await self._send_error(message, "Mencione o cargo que deseja inspecionar. Ex: `@RevolutX info do cargo @Staff`")
            return True

        perms = [name.replace("_", " ").title() for name, enabled in cargo.permissions if enabled]
        member_count = sum(1 for m in guild.members if cargo in m.roles)
        role_color = cargo.color if cargo.color != discord.Color.default() else discord.Color.dark_grey()

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
        if perms:
            card.add_separator()
            shown = ", ".join(perms[:30])
            if len(perms) > 30:
                shown += f" e mais {len(perms) - 30}"
            card.add_text(f"### Permissões ({len(perms)})\n{_truncate(shown, 3000)}")
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_canalinfo(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("info do canal", "canalinfo", "canal info", "informações do canal", "informacoes do canal")
        if not any(t in low for t in triggers):
            return False

        guild = message.guild
        if not guild:
            return False

        channel: Optional[discord.TextChannel] = None
        if message.channel_mentions:
            ch = message.channel_mentions[0]
            if isinstance(ch, discord.TextChannel):
                channel = ch
        if not channel and isinstance(message.channel, discord.TextChannel):
            channel = message.channel

        if not channel:
            await self._send_error(message, "Mencione o canal. Ex: `@RevolutX info do canal #geral`")
            return True

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
        await self._send_card(message, card)
        return True

    async def _intent_poll(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("enquete", "poll", "votação", "votacao")
        if not any(t in low for t in triggers):
            return False

        # Formato esperado: "enquete [pergunta]? op1, op2, op3"
        # Tenta separar pergunta e opções
        # Pergunta: tudo antes do primeiro "?" ou antes das opções (separadas por vírgula)
        lines = content.split("\n", 1)
        first_line = lines[0]

        # Remove a palavra-chave
        for kw in ("crie uma enquete", "criar enquete", "nova enquete", "enquete", "poll", "votação", "votacao"):
            first_line = re.sub(re.escape(kw), "", first_line, flags=re.IGNORECASE).strip()

        # Separa pergunta de opções pelo "?" ou pela presença de vírgulas
        if "?" in first_line:
            parts = first_line.split("?", 1)
            question = parts[0].strip() + "?"
            options_raw = parts[1].strip() if len(parts) > 1 else ""
        else:
            # Tenta separar pela última vírgula que parece lista de opções
            comma_pos = first_line.find(",")
            if comma_pos > 10:
                question = first_line[:comma_pos].rsplit(" ", 1)[0].strip()
                options_raw = first_line[comma_pos - len(first_line.split(",")[0].rsplit(" ", 1)[-1]):].strip()
            else:
                question = first_line
                options_raw = ""

        if lines[1:]:
            options_raw = options_raw + " " + lines[1]

        option_list = [o.strip() for o in re.split(r"[,\n]", options_raw) if o.strip()]

        if not question or len(option_list) < 2:
            await self._send_error(
                message,
                "Não entendi a enquete. Tente:\n"
                "`@RevolutX enquete Qual o melhor? Opção A, Opção B, Opção C`"
            )
            return True

        option_list = option_list[:4]
        duration = 60

        view = PollView(
            question=_truncate(question, 300),
            options=[_truncate(o, 80) for o in option_list],
            creator_id=message.author.id,
            creator_name=message.author.display_name,
            duration_minutes=duration,
        )
        try:
            sent = await message.channel.send(view=view)
            asyncio.create_task(_finish_poll_after(view, sent, duration))
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Falha ao criar enquete por menção: %s", exc)
        return True

    async def _intent_lembrete(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("me lembra", "lembre-me", "lembre me", "lembrete", "lembra em", "lembrar em")
        if not any(t in low for t in triggers):
            return False

        # Extrai minutos: "em X minutos" ou "daqui X min"
        match = re.search(r"(?:em|daqui)\s+(\d+)\s*(?:minutos?|min|m\b)", low)
        minutes = int(match.group(1)) if match else None

        if not minutes or minutes < 1 or minutes > 10080:
            await self._send_error(
                message,
                "Especifique o tempo em minutos. Ex: `@RevolutX me lembra em 30 minutos de tomar água`"
            )
            return True

        # Extrai mensagem do lembrete: após "de " ou "sobre "
        reminder_match = re.search(r"\b(?:de|sobre|para)\s+(.+)$", content, re.IGNORECASE)
        reminder_text = reminder_match.group(1).strip() if reminder_match else content

        # Confirmação no canal (não por DM)
        confirm = V2Card(accent_color=discord.Color.green())
        confirm.add_header(
            f"{EMOJI_CLOCK} Lembrete definido!",
            subtitle=f"Vou te mencionar aqui em **{minutes} minuto(s)**.",
        )
        confirm.add_text(f"**Mensagem:** {_truncate(reminder_text, 500)}")
        confirm.add_footer()
        try:
            await message.reply(view=confirm, mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            pass

        channel = message.channel
        user = message.author

        async def _remind() -> None:
            await asyncio.sleep(minutes * 60)
            try:
                reminder = V2Card(accent_color=discord.Color.gold())
                reminder.add_header(f"{EMOJI_REMINDER} Lembrete!")
                reminder.add_separator()
                reminder.add_text(_truncate(reminder_text, 3900))
                reminder.add_footer(f"Definido há {minutes} minuto(s)")
                await channel.send(content=user.mention, view=reminder)
            except (discord.Forbidden, discord.HTTPException):
                pass

        asyncio.create_task(_remind())
        return True

    async def _intent_timestamp(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        if "timestamp" not in low:
            return False

        # Extrai números: ano mês dia [hora] [minuto]
        numbers = re.findall(r"\b(\d+)\b", re.sub(r"timestamp", "", low))
        if len(numbers) < 3:
            await self._send_error(
                message,
                "Informe: `@RevolutX timestamp [ano] [mês] [dia] [hora] [minuto]`\n"
                "Ex: `@RevolutX timestamp 2026 12 31 23 59`"
            )
            return True

        try:
            ano, mes, dia = int(numbers[0]), int(numbers[1]), int(numbers[2])
            hora   = int(numbers[3]) if len(numbers) > 3 else 0
            minuto = int(numbers[4]) if len(numbers) > 4 else 0
            date = datetime(ano, mes, dia, hora, minuto, tzinfo=timezone.utc)
        except (ValueError, IndexError) as exc:
            await self._send_error(message, f"Data inválida: {exc}")
            return True

        ts = int(date.timestamp())
        formats = [
            ("Data curta", "d"), ("Data longa", "D"),
            ("Hora curta", "t"), ("Hora longa", "T"),
            ("Data e hora", "f"), ("Data e hora longa", "F"),
            ("Relativo", "R"),
        ]
        lines = [
            f"**{label} (`{code}`):** <t:{ts}:{code}> · `<t:{ts}:{code}>`"
            for label, code in formats
        ]

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header("Gerador de Timestamps")
        card.add_text(f"**Unix:** `{ts}`\n**UTC:** `{date.strftime('%Y-%m-%d %H:%M')}`")
        card.add_separator()
        card.add_text("\n".join(lines))
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_cor(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        if "cor" not in low and "#" not in content:
            return False

        hex_match = re.search(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", content)
        if not hex_match:
            return False

        try:
            r, g, b = _hex_to_rgb(hex_match.group(0))
        except ValueError:
            await self._send_error(message, "HEX inválido. Use `#RRGGBB` ou `#RGB`.")
            return True

        clean = f"{r:02X}{g:02X}{b:02X}"
        hue, saturation, lightness = _rgb_to_hsl(r, g, b)
        color = discord.Color.from_rgb(r, g, b)
        preview_url = f"https://singlecolorimage.com/get/{clean}/800x180"

        card = V2Card(accent_color=color)
        card.add_header(f"Cor · #{clean}")
        card.add_gallery((preview_url, f"Prévia da cor #{clean}"))
        card.add_separator()
        card.add_text(
            f"**HEX:** `#{clean}`\n"
            f"**RGB:** `rgb({r}, {g}, {b})`\n"
            f"**HSL:** `hsl({hue}°, {saturation}%, {lightness}%)`\n"
            f"**Decimal:** `{color.value}`"
        )
        card.add_footer()
        await self._send_card(message, card)
        return True

    async def _intent_resumir(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("resume", "resumo de", "resumir", "resuma")
        if not any(t in low for t in triggers):
            return False

        # Extrai o texto após "resume:" ou "resume "
        text_match = re.search(
            r"(?:resume[i]?[r]?|resumo\s+de|resuma)[:\s]+(.+)$",
            content, re.IGNORECASE | re.DOTALL
        )
        texto = text_match.group(1).strip() if text_match else content

        if len(texto) < 50:
            await self._send_error(
                message,
                "Texto muito curto. Envie o texto que deseja resumir após a palavra 'resume:'."
            )
            return True

        try:
            await message.add_reaction("⏳")
        except (discord.Forbidden, discord.HTTPException):
            pass

        result = await self._ai_complete(
            system=(
                "Resuma o texto em português, de forma clara e concisa, em no máximo "
                "três parágrafos. Preserve conceitos, ressalvas e conclusões importantes."
            ),
            user=texto[:4000],
            max_tokens=500,
        )

        try:
            await message.remove_reaction("⏳", self.bot.user)
        except Exception:
            pass

        card = V2Card(accent_color=discord.Color.green())
        card.add_header("Resumo")
        card.add_separator()
        card.add_text(_truncate(result, 4000))
        card.add_footer("Resposta gerada por IA")
        await self._send_card(message, card)
        return True

    async def _intent_traduzir(
        self, message: discord.Message, content: str, low: str
    ) -> bool:
        triggers = ("traduz", "traduza", "traduzir", "translate")
        if not any(t in low for t in triggers):
            return False

        # Detecta idioma: "para [idioma]:" ou "para [idioma] "
        lang_match = re.search(r"para\s+(\w+)[:\s]", low)
        idioma = lang_match.group(1) if lang_match else "inglês"

        # Extrai texto
        text_match = re.search(
            r"(?:traduz[a-z]*|translate)[^:]*:\s*(.+)$",
            content, re.IGNORECASE | re.DOTALL
        )
        if not text_match:
            # Tenta pegar tudo após "para [idioma]"
            text_match = re.search(
                r"para\s+\w+[:\s]+(.+)$",
                content, re.IGNORECASE | re.DOTALL
            )
        texto = text_match.group(1).strip() if text_match else ""

        if not texto or len(texto) < 3:
            await self._send_error(
                message,
                "Não encontrei o texto para traduzir. Tente:\n"
                "`@RevolutX traduz para inglês: Olá, como vai?`"
            )
            return True

        try:
            await message.add_reaction("⏳")
        except (discord.Forbidden, discord.HTTPException):
            pass

        result = await self._ai_complete(
            system=(
                f"Traduza o texto para {idioma}. Retorne apenas a tradução, "
                "sem explicações, notas ou comentários adicionais."
            ),
            user=texto[:3000],
            max_tokens=700,
        )

        try:
            await message.remove_reaction("⏳", self.bot.user)
        except Exception:
            pass

        card = V2Card(accent_color=discord.Color.blurple())
        card.add_header(f"Tradução para {idioma.title()}")
        card.add_separator()
        card.add_text(f"### Original\n{_truncate(texto, 1500)}")
        card.add_separator()
        card.add_text(f"### Tradução\n{_truncate(result, 3500)}")
        card.add_footer("Resposta gerada por IA")
        await self._send_card(message, card)
        return True


async def _finish_poll_after(view: 'PollView', message: discord.Message, duration_minutes: int) -> None:
    """Task auxiliar para encerrar a enquete automaticamente."""
    await asyncio.sleep(duration_minutes * 60)
    if not view.ended:
        try:
            await view.finish_automatically(message)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
