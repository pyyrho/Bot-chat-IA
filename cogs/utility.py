"""
utility.py — Revolux · Cog de Utilidades com IA Integrada
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ferramentas avançadas que elevam a experiência do servidor.

Recursos:
  • /ajuda       — painel interativo paginado com botões
  • /ping        — latência + status dos serviços
  • /userinfo    — perfil rico com badges, atividade e risco de moderação
  • /serverinfo  — painel detalhado do servidor
  • /avatar      — avatar em alta resolução
  • /banner      — banner do usuário
  • /roleinfo    — informações detalhadas de um cargo
  • /canalinfo   — informações detalhadas de um canal
  • /botinfo     — painel rico do bot com uptime, stats e identidade
  • /poll        — enquete interativa com botões e contagem em tempo real
  • /lembrete    — lembrete com DM agendada
  • /timestamp   — gera timestamps Discord para qualquer data/hora
  • /cor         — exibe e converte uma cor (HEX/RGB/HSL) com preview
  • /resumir     — resume texto longo com IA
  • /traduzir    — traduz texto com IA
  • /pergunte-ia  — resposta rápida de IA em contexto de servidor
  • /embed-custom — cria embeds customizados para moderadores

Chave de API: GEMINI_API_KEY (usada para IA geral de utilidades)
"""

from __future__ import annotations

import asyncio
import colorsys
import os
import platform
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import google.generativeai as genai

AI_MODEL = os.getenv("UTIL_AI_MODEL", "gemini-2.5-flash")

_START_TIME = time.monotonic()


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
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    return r, g, b


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return round(h * 360, 1), round(s * 100, 1), round(l * 100, 1)


# ──────────────────────────────────────────────
# View: Painel /ajuda paginado
# ──────────────────────────────────────────────

_HELP_PAGES: list[tuple[str, str, str]] = [
    (
        "<:1000032072:1507947958723809340> Inteligência Artificial",
        discord.Color.blurple,
        (
            "`/chat` · Conversa com o Revolux\n"
            "`/pergunte-ia` · Pergunta rápida com resposta IA inline\n"
            "`/resumir` · Resume qualquer texto longo\n"
            "`/traduzir` · Traduz texto para qualquer idioma\n"
            "`/status-ia` · Modelos e configuração de IA ativa\n"
            "`/canal-ia` · Ativar/desativar IA automática em canal\n"
            "`/limpar-conversa` · Apaga histórico de conversa"
        ),
    ),
    (
        "<:1000032064:1507947590652526654> Moderação",
        discord.Color.red,
        (
            "`/ban` · Ban permanente\n"
            "`/tempban` · Ban temporário com auto-desban\n"
            "`/softban` · Ban + desban para limpar mensagens\n"
            "`/kick` · Expulsar usuário\n"
            "`/mute` · Silenciar temporariamente\n"
            "`/unmute` · Remover silenciamento\n"
            "`/warn` · Registrar aviso\n"
            "`/remover-aviso` · Remover aviso por ID\n"
            "`/avisos` · Ver histórico de avisos\n"
            "`/limpar-avisos` · Limpar todos os avisos\n"
            "`/inspecionar` · Perfil completo de moderação com IA\n"
            "`/nota` · Adicionar nota interna sobre usuário\n"
            "`/purge` · Apagar mensagens em massa com filtros\n"
            "`/lockdown` · Bloquear/desbloquear canal\n"
            "`/palavra-proibida` · Gerenciar filtro de palavras\n"
            "`/config-mod` · Configuração completa de moderação"
        ),
    ),
    (
        "<:1000032067:1507947758638600353> Utilidades",
        discord.Color.green,
        (
            "`/userinfo` · Perfil detalhado de usuário\n"
            "`/serverinfo` · Informações do servidor\n"
            "`/avatar` · Avatar em alta resolução\n"
            "`/banner` · Banner do usuário\n"
            "`/roleinfo` · Detalhes de um cargo\n"
            "`/canalinfo` · Detalhes de um canal\n"
            "`/botinfo` · Status e stats do Revolux\n"
            "`/ping` · Latência e saúde dos serviços\n"
            "`/poll` · Enquete interativa com botões\n"
            "`/lembrete` · Lembrete com DM agendada\n"
            "`/timestamp` · Gera timestamp Discord\n"
            "`/cor` · Preview e conversão de cor HEX/RGB\n"
            "`/embed-custom` · Criar embed personalizado\n"
            "`/ajuda` · Este painel"
        ),
    ),
]


class HelpView(discord.ui.View):
    def __init__(self, bot_user: discord.ClientUser) -> None:
        super().__init__(timeout=120)
        self.page = 0
        self.bot_user = bot_user

    def build_embed(self) -> discord.Embed:
        title, color_fn, description = _HELP_PAGES[self.page]
        embed = discord.Embed(
            title=title,
            description=description,
            color=color_fn(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=self.bot_user.display_avatar.url)
        embed.set_footer(text=f"Revolux · Página {self.page + 1}/{len(_HELP_PAGES)}")
        return embed

    def _update_buttons(self) -> None:
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page == len(_HELP_PAGES) - 1

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary, emoji="<:1000032309:1508193593439949053>")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Próxima", style=discord.ButtonStyle.secondary, emoji="<:1000032308:1508193552004546590>")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.danger)
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="<:1000032055:1507947171624910859> Painel fechado.", embed=None, view=None
        )
        self.stop()


# ──────────────────────────────────────────────
# View: Enquete interativa
# ──────────────────────────────────────────────

class PollView(discord.ui.View):
    def __init__(self, options: list[str], creator_id: int, duration_minutes: int) -> None:
        super().__init__(timeout=duration_minutes * 60)
        self.votes: dict[str, set[int]] = {opt: set() for opt in options}
        self.creator_id = creator_id
        self.options = options
        self.ended = False

        for i, opt in enumerate(options):
            btn = discord.ui.Button(
                label=opt[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_{i}",
            )
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

        end_btn = discord.ui.Button(label="Encerrar", style=discord.ButtonStyle.danger, custom_id="poll_end", emoji="<a:1000032057:1507947249873719497>")
        end_btn.callback = self._end_poll_callback
        self.add_item(end_btn)

    def _make_callback(self, option: str):
        async def callback(interaction: discord.Interaction) -> None:
            user_id = interaction.user.id
            # Remove voto anterior
            for opt, voters in self.votes.items():
                voters.discard(user_id)
            self.votes[option].add(user_id)
            await interaction.response.edit_message(embed=self._build_embed())

        return callback

    async def _end_poll_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.creator_id and not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("<:1000032056:1507947210057322637> Apenas o criador da enquete pode encerrá-la.", ephemeral=True)
            return
        self.ended = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=self._build_embed(final=True), view=self)

    def _build_embed(self, *, final: bool = False) -> discord.Embed:
        total = sum(len(v) for v in self.votes.values())
        embed = discord.Embed(
            title=("<:1000032069:1507947817719562250> Enquete" if not final else "<:1000032056:1507947210057322637> Enquete Encerrada"),
            color=discord.Color.blurple() if not final else discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        lines: list[str] = []
        for opt, voters in sorted(self.votes.items(), key=lambda x: -len(x[1])):
            count = len(voters)
            pct = (count / total * 100) if total else 0
            bar_len = int(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            lines.append(f"**{opt}**\n`{bar}` {count} voto(s) ({pct:.1f}%)")
        embed.description = "\n\n".join(lines) or "Sem votos ainda."
        embed.set_footer(text=f"Total de votos: {total}" + (" · Encerrada" if final else ""))
        return embed


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class Utility(commands.Cog):
    """Cog de utilidades avançadas com IA para o Revolux."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self._gemini_ready = True
        else:
            self._gemini_ready = False

    # ── Helpers ───────────────────────────────

    def _base_embed(self, title: str, color: discord.Color, *, icon: str = "<:1000032075:1507948047269888001>") -> discord.Embed:
        embed = discord.Embed(
            title=f"{icon} {title}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        if self.bot.user:
            embed.set_footer(
                text="Revolux",
                icon_url=self.bot.user.display_avatar.url,
            )
        return embed

    async def _ai_complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        if not self._gemini_ready:
            return "<:1000032079:1507948213741813972> IA não configurada (GEMINI_API_KEY ausente)."
        try:
            import asyncio
            model_obj = genai.GenerativeModel(
                model_name=AI_MODEL,
                system_instruction=system,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.5,
                ),
            )
            response = await asyncio.to_thread(model_obj.generate_content, user)
            return response.text.strip()
        except Exception as exc:
            return f"<:1000032079:1507948213741813972> Erro na IA: {exc}"

    # ── /ajuda ────────────────────────────────

    @app_commands.command(name="ajuda", description="Painel interativo com todos os comandos do Revolux.")
    async def help(self, interaction: discord.Interaction) -> None:
        if not self.bot.user:
            await interaction.response.send_message("Bot ainda inicializando.", ephemeral=True)
            return
        view = HelpView(self.bot.user)
        await interaction.response.send_message(embed=view.build_embed(), view=view)

    # ── /ping ─────────────────────────────────

    @app_commands.command(name="ping", description="Mostra a latência e saúde dos serviços.")
    async def ping(self, interaction: discord.Interaction) -> None:
        ws_ms = round(self.bot.latency * 1000)

        # Mede latência de resposta da API do Discord
        start = time.perf_counter()
        await interaction.response.defer()
        api_ms = round((time.perf_counter() - start) * 1000)

        if ws_ms < 100:
            color, status = discord.Color.green(), "<a:9582dsicordveriyblack:1430269158024810598> Excelente"
        elif ws_ms < 200:
            color, status = discord.Color.gold(), "<a:9582dsicordveriyblack:1430269158024810598> Normal"
        else:
            color, status = discord.Color.red(), "<a:9582dsicordveriyblack:1430269158024810598> Alta latência"

        embed = self._base_embed("Status dos Serviços", color, icon="<:1000032077:1507948183290904736>")
        embed.add_field(name="WebSocket", value=f"`{ws_ms}ms`", inline=True)
        embed.add_field(name="API Discord", value=f"`{api_ms}ms`", inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Uptime", value=f"`{_uptime_str()}`", inline=True)
        await interaction.followup.send(embed=embed)

    # ── /botinfo ──────────────────────────────

    @app_commands.command(name="botinfo", description="Painel completo de identidade e estatísticas do Revolux.")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        bot = self.bot
        guilds = len(bot.guilds)
        members = sum(g.member_count or 0 for g in bot.guilds)
        channels = sum(len(g.channels) for g in bot.guilds)
        cmds = len(bot.tree.get_commands())
        latency = round(bot.latency * 1000)
        bot_name = bot.user.name if bot.user else "Revolux"

        if latency < 100:
            status_text = "Excelente"
        elif latency < 200:
            status_text = "Normal"
        else:
            status_text = "Alta latência"

        color = discord.Color.from_rgb(220, 221, 222)  # branco/cinza neutro

        SEPARADOR = "—" * 20

        embed = discord.Embed(
            title=f"<:1000032124:1508195012175728720> | {bot_name}",
            description=(
                f"Olá! Eu sou o **{bot_name}**, uma inteligência artificial criada para conversar, "
                "auxiliar nos estudos e tornar servidores mais organizados e seguros.\n\n"
                "Metade do meu trabalho está voltada para recursos cotidianos de um bot, como utilidades, "
                "lembretes, enquetes, informações de usuários e servidores. A outra metade é dedicada ao "
                "aprendizado e à pesquisa acadêmica.\n\n"
                "Posso ajudar em áreas como **programação, lógica, matemática, filosofia** e outros campos "
                "do conhecimento. Também consigo auxiliar na compreensão de conceitos, explicação de "
                "conteúdos, resolução de exercícios, revisão de textos e organização de estudos.\n\n"
                f"{SEPARADOR}\n\n"
                "- Para oferecer respostas acadêmicas mais completas, conto com conteúdos e referências de "
                "fontes importantes, como a **Stanford Encyclopedia of Philosophy (SEP)**, o **PhilPapers** "
                "e outras plataformas especializadas.\n\n"
                "- Minha base também inclui livros e materiais didáticos adicionados e selecionados "
                "manualmente, incluindo obras utilizadas em estudos avançados, cursos universitários e "
                "programas de pós-graduação.\n\n"
                "- Mesmo com essas fontes, minhas respostas devem ser utilizadas como apoio ao estudo. "
                "Sempre é recomendável consultar as referências originais em trabalhos acadêmicos "
                "importantes.\n\n"
                f"{SEPARADOR}\n\n"
                "Também possuo um sistema avançado de moderação auxiliado por inteligência artificial.\n\n"
                "Em vez de analisar apenas palavras isoladas, consigo considerar o contexto das mensagens "
                "e compará-lo com as regras e diretrizes definidas para o servidor. Isso ajuda a identificar "
                "possíveis ofensas, ameaças, spam, conteúdo inadequado e outras violações com mais "
                "precisão.\n\n"
                "As configurações e decisões finais continuam sob o controle da equipe responsável pelo "
                "servidor.\n\n"
                f"{SEPARADOR}\n\n"
                f"<:1000032053:1507947052183584910> Fui desenvolvido com a linguagem **Python** "
                f"`{platform.python_version()}` e a biblioteca **discord.py**.\n\n"
                "> Fui criado e sou mantido por **Isabelle** 🤍, responsável pelo meu desenvolvimento, pelas "
                "minhas atualizações e pela expansão dos meus recursos, com a ajuda de **Pedro** 🩵 e "
                "**Gustavo** 💚.\n\n"
                f"Atualmente estou presente em **{guilds:,} servidores**, acompanhando aproximadamente "
                f"**{members:,} usuários** e oferecendo **{cmds} comandos**.\n\n"
                "Precisa de ajuda, encontrou algum problema ou deseja enviar uma sugestão? Utilize os "
                "botões abaixo para acessar meu servidor de suporte e meus outros canais oficiais."
            ),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        if bot.user:
            try:
                fetched = await bot.fetch_user(bot.user.id)
                if fetched.banner:
                    embed.set_image(url=fetched.banner.with_size(1024).url)
            except Exception:
                pass
            embed.set_thumbnail(url=bot.user.display_avatar.with_size(256).url)

        embed.add_field(name="<:1000032077:1507948183290904736> Latência", value=f"`{latency}ms`", inline=True)
        embed.add_field(name="<a:9582dsicordveriyblack:1430269158024810598> Status", value=status_text, inline=True)
        embed.add_field(name="<:1000032048:1507946854405505034> Sistema", value=f"`{platform.system()} {platform.release()}`", inline=True)

        embed.set_footer(
            text=f"ID: {bot.user.id if bot.user else '—'} · Revolux",
            icon_url=bot.user.display_avatar.url if bot.user else None,
        )

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Servidor de Suporte",
                style=discord.ButtonStyle.link,
                url="https://discord.gg/suporte",
                emoji="<:1000032065:1507947691848630282>",
            )
        )
        view.add_item(
            discord.ui.Button(
                label="Adicionar ao servidor",
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/oauth2/authorize?client_id={bot.user.id if bot.user else 0}&permissions=8&scope=bot%20applications.commands",
                emoji="<:1000032050:1507946943639191573>",
            )
        )

        await interaction.followup.send(embed=embed, view=view)

    # ── /userinfo ─────────────────────────────

    @app_commands.command(name="userinfo", description="Perfil completo de um usuário.")
    @app_commands.describe(membro="Usuário a consultar (padrão: você)")
    async def userinfo(self, interaction: discord.Interaction, membro: Optional[discord.Member] = None) -> None:
        membro = membro or interaction.user

        roles = [r.mention for r in reversed(membro.roles) if r.name != "@everyone"]
        roles_text = ", ".join(roles[:10]) if roles else "Nenhum cargo"
        if len(roles) > 10:
            roles_text += f" e mais `{len(roles) - 10}`"

        perms = membro.guild_permissions
        key_perms: list[str] = []
        if perms.administrator:
            key_perms.append("Administrador")
        if perms.manage_guild:
            key_perms.append("Gerenciar Servidor")
        if perms.manage_channels:
            key_perms.append("Gerenciar Canais")
        if perms.manage_messages:
            key_perms.append("Gerenciar Mensagens")
        if perms.ban_members:
            key_perms.append("Banir")
        if perms.kick_members:
            key_perms.append("Expulsar")
        if perms.moderate_members:
            key_perms.append("Moderar Membros")

        # Badges
        flags = membro.public_flags
        badges: list[str] = []
        if flags.staff:            badges.append("<:1000032081:1507948313549209720> Discord Staff")
        if flags.partner:          badges.append("<:1000032066:1507947724560011375> Parceiro")
        if flags.bug_hunter:       badges.append("<:1000032080:1507948244683194439> Bug Hunter")
        if flags.early_supporter:  badges.append("<a:1000032071:1507947918752092301> Early Supporter")
        if flags.verified_bot_developer: badges.append("<:1000032068:1507947786367402105> Dev Verificado")
        if flags.active_developer: badges.append("<:1000032072:1507947958723809340> Dev Ativo")
        if membro.premium_since:   badges.append("<:1000032075:1507948047269888001> Server Booster")

        color = membro.color if membro.color != discord.Color.default() else discord.Color.blurple()
        embed = self._base_embed(membro.display_name, color, icon="<:1000032124:1508195012175728720>")
        embed.set_thumbnail(url=membro.display_avatar.url)

        embed.add_field(name="Tag completa", value=str(membro), inline=True)
        embed.add_field(name="ID", value=f"`{membro.id}`", inline=True)
        embed.add_field(name="Bot", value="Sim" if membro.bot else "Não", inline=True)
        embed.add_field(
            name="Conta criada",
            value=f"<t:{int(membro.created_at.timestamp())}:D>\n<t:{int(membro.created_at.timestamp())}:R>",
            inline=True,
        )
        if membro.joined_at:
            embed.add_field(
                name="Entrou no servidor",
                value=f"<t:{int(membro.joined_at.timestamp())}:D>\n<t:{int(membro.joined_at.timestamp())}:R>",
                inline=True,
            )
        if membro.premium_since:
            embed.add_field(
                name="Boostando desde",
                value=f"<t:{int(membro.premium_since.timestamp())}:D>",
                inline=True,
            )
        embed.add_field(name="Cargo mais alto", value=membro.top_role.mention, inline=True)
        embed.add_field(name=f"Cargos ({len(roles)})", value=roles_text, inline=False)

        if key_perms:
            embed.add_field(name="Permissões chave", value=", ".join(key_perms), inline=False)
        if badges:
            embed.add_field(name="Badges", value="\n".join(badges), inline=False)

        if membro.activities:
            activity = membro.activities[0]
            act_text = f"{activity.type.name.title()}: {getattr(activity, 'name', '—')}"
            embed.add_field(name="Atividade", value=act_text, inline=True)

        await interaction.response.send_message(embed=embed)

    # ── /serverinfo ───────────────────────────

    @app_commands.command(name="serverinfo", description="Painel completo de informações do servidor.")
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        g = interaction.guild
        bots   = sum(1 for m in g.members if m.bot)
        humans = (g.member_count or 0) - bots

        # Distribuição de cargos de status
        online  = sum(1 for m in g.members if m.status == discord.Status.online)
        idle    = sum(1 for m in g.members if m.status == discord.Status.idle)
        dnd     = sum(1 for m in g.members if m.status == discord.Status.dnd)
        offline = (g.member_count or 0) - online - idle - dnd

        embed = self._base_embed(g.name, discord.Color.blurple(), icon="<:1000032076:1507948144011509820>")
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="ID", value=f"`{g.id}`", inline=True)
        embed.add_field(name="Dono", value=g.owner.mention if g.owner else "—", inline=True)
        embed.add_field(name="Criado em", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)
        embed.add_field(
            name="Membros",
            value=f"Total `{g.member_count}` · <:1000032081:1507948313549209720> `{humans}` · <:1000032072:1507947958723809340> `{bots}`",
            inline=False,
        )
        embed.add_field(
            name="Status",
            value=f"<:1000032061:1507947461900111903>`{online}` <:1000032079:1507948213741813972>`{idle}` <a:1000032057:1507947249873719497>`{dnd}` <:1000032054:1507947088590274580>`{offline}`",
            inline=False,
        )
        embed.add_field(
            name="Canais",
            value=(
                f"Texto `{len(g.text_channels)}` · "
                f"Voz `{len(g.voice_channels)}` · "
                f"Fórum `{len([c for c in g.channels if isinstance(c, discord.ForumChannel)])}` · "
                f"Stage `{len(g.stage_channels)}` · "
                f"Categorias `{len(g.categories)}`"
            ),
            inline=False,
        )
        embed.add_field(name="Cargos", value=f"`{len(g.roles)}`", inline=True)
        embed.add_field(name="Emojis", value=f"`{len(g.emojis)}`", inline=True)
        embed.add_field(name="Stickers", value=f"`{len(g.stickers)}`", inline=True)
        embed.add_field(
            name="Boost",
            value=f"Nível `{g.premium_tier}` · `{g.premium_subscription_count}` boost(s)",
            inline=True,
        )
        embed.add_field(
            name="Verificação",
            value=str(g.verification_level).replace("_", " ").title(),
            inline=True,
        )
        embed.add_field(
            name="2FA para moderadores",
            value="Sim" if g.mfa_level else "Não",
            inline=True,
        )
        await interaction.response.send_message(embed=embed)

    # ── /avatar ───────────────────────────────

    @app_commands.command(name="avatar", description="Exibe o avatar de um usuário em alta resolução.")
    @app_commands.describe(membro="Usuário (padrão: você)", formato="jpg | png | webp | gif")
    async def avatar(
        self,
        interaction: discord.Interaction,
        membro: Optional[discord.Member] = None,
        formato: Optional[str] = None,
    ) -> None:
        membro = membro or interaction.user
        valid_fmts = {"jpg", "png", "webp", "gif"}
        fmt_str = formato.lower() if formato and formato.lower() in valid_fmts else None

        try:
            if fmt_str:
                url = membro.display_avatar.with_format(fmt_str).with_size(1024).url  # type: ignore
            else:
                url = membro.display_avatar.with_size(1024).url
        except Exception:
            url = membro.display_avatar.url

        embed = discord.Embed(
            title=f"Avatar · {membro.display_name}",
            color=membro.color if membro.color != discord.Color.default() else discord.Color.blurple(),
        )
        embed.set_image(url=url)
        embed.add_field(name="Link direto", value=f"[Abrir imagem]({url})", inline=False)
        await interaction.response.send_message(embed=embed)

    # ── /banner ───────────────────────────────

    @app_commands.command(name="banner", description="Exibe o banner de um usuário.")
    @app_commands.describe(membro="Usuário (padrão: você)")
    async def banner(
        self, interaction: discord.Interaction, membro: Optional[discord.Member] = None
    ) -> None:
        target = membro or interaction.user
        await interaction.response.defer()
        try:
            user = await self.bot.fetch_user(target.id)
        except discord.NotFound:
            await interaction.followup.send("<:1000032056:1507947210057322637> Usuário não encontrado.", ephemeral=True)
            return

        if not user.banner:
            await interaction.followup.send(
                f"<:1000032056:1507947210057322637> {target.display_name} não possui banner.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Banner · {target.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_image(url=user.banner.with_size(1024).url)
        await interaction.followup.send(embed=embed)

    # ── /roleinfo ─────────────────────────────

    @app_commands.command(name="roleinfo", description="Detalhes de um cargo.")
    @app_commands.describe(cargo="Cargo a inspecionar")
    async def roleinfo(self, interaction: discord.Interaction, cargo: discord.Role) -> None:
        perms = [name.replace("_", " ").title() for name, value in cargo.permissions if value]
        members_with = sum(1 for m in interaction.guild.members if cargo in m.roles)

        embed = self._base_embed(f"Cargo · {cargo.name}", cargo.color or discord.Color.default(), icon="<:1000032064:1507947590652526654>")
        embed.add_field(name="ID", value=f"`{cargo.id}`", inline=True)
        embed.add_field(name="Posição", value=f"`{cargo.position}`", inline=True)
        embed.add_field(name="Membros", value=f"`{members_with}`", inline=True)
        embed.add_field(name="Cor", value=f"`{cargo.color}`", inline=True)
        embed.add_field(name="Mencionável", value="Sim" if cargo.mentionable else "Não", inline=True)
        embed.add_field(name="Exibido separado", value="Sim" if cargo.hoist else "Não", inline=True)
        embed.add_field(name="Gerenciado", value="Sim (bot/integração)" if cargo.managed else "Não", inline=True)
        embed.add_field(name="Criado em", value=f"<t:{int(cargo.created_at.timestamp())}:D>", inline=True)
        if perms:
            embed.add_field(
                name=f"Permissões ({len(perms)})",
                value=", ".join(perms[:20]) + ("…" if len(perms) > 20 else ""),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    # ── /canalinfo ────────────────────────────

    @app_commands.command(name="canalinfo", description="Detalhes de um canal de texto.")
    @app_commands.describe(canal="Canal a inspecionar (padrão: atual)")
    async def canalinfo(
        self, interaction: discord.Interaction, canal: Optional[discord.TextChannel] = None
    ) -> None:
        ch = canal or interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("<:1000032056:1507947210057322637> Canal inválido.", ephemeral=True)
            return

        embed = self._base_embed(f"Canal · #{ch.name}", discord.Color.blurple(), icon="<:1000032049:1507946904124919949>")
        embed.add_field(name="ID", value=f"`{ch.id}`", inline=True)
        embed.add_field(name="Categoria", value=ch.category.name if ch.category else "Nenhuma", inline=True)
        embed.add_field(name="Criado em", value=f"<t:{int(ch.created_at.timestamp())}:D>", inline=True)
        embed.add_field(name="NSFW", value="Sim" if ch.is_nsfw() else "Não", inline=True)
        embed.add_field(name="Slow-mode", value=f"`{ch.slowmode_delay}s`", inline=True)
        embed.add_field(name="Posição", value=f"`{ch.position}`", inline=True)
        if ch.topic:
            embed.add_field(name="Tópico", value=ch.topic[:200], inline=False)
        await interaction.response.send_message(embed=embed)

    # ── /poll ─────────────────────────────────

    @app_commands.command(name="poll", description="Cria uma enquete interativa com botões.")
    @app_commands.describe(
        pergunta="Pergunta da enquete",
        opcoes="Opções separadas por vírgula (2-5 opções)",
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
        option_list = [o.strip() for o in opcoes.split(",") if o.strip()]
        if len(option_list) < 2 or len(option_list) > 5:
            await interaction.response.send_message(
                "<:1000032056:1507947210057322637> Forneça entre 2 e 5 opções separadas por vírgula.", ephemeral=True
            )
            return

        view = PollView(option_list, interaction.user.id, max(1, min(duracao, 10080)))
        embed = discord.Embed(
            title=f"<:1000032068:1507947786367402105> {pergunta}",
            description="Clique nas opções abaixo para votar.",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Criada por {interaction.user.display_name} · Duração: {duracao}min")
        await interaction.response.send_message(embed=embed, view=view)

        async def _auto_end() -> None:
            await asyncio.sleep(duracao * 60)
            if not view.ended:
                view.ended = True
                view.stop()
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.edit_original_response(
                        embed=view._build_embed(final=True), view=view
                    )
                except Exception:
                    pass

        asyncio.create_task(_auto_end())

    # ── /lembrete ─────────────────────────────

    @app_commands.command(name="lembrete", description="Define um lembrete que chegará no seu DM.")
    @app_commands.describe(minutos="Em quantos minutos lembrar", mensagem="O que lembrar")
    async def lembrete(
        self, interaction: discord.Interaction, minutos: int, mensagem: str
    ) -> None:
        if minutos < 1 or minutos > 10080:
            await interaction.response.send_message(
                "<:1000032056:1507947210057322637> Defina entre 1 minuto e 7 dias (10080 minutos).", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"<:1000032074:1507948021013549166> Lembrete definido! Vou te avisar em **{minutos} minuto(s)**.", ephemeral=True
        )
        user = interaction.user

        async def _remind() -> None:
            await asyncio.sleep(minutos * 60)
            try:
                dm_embed = discord.Embed(
                    title="<:1000032058:1507947336616251574> Lembrete!",
                    description=mensagem,
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc),
                )
                dm_embed.set_footer(text=f"Definido em {interaction.guild.name} há {minutos} minuto(s)")
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        asyncio.create_task(_remind())

    # ── /timestamp ────────────────────────────

    @app_commands.command(name="timestamp", description="Gera timestamps formatados para o Discord.")
    @app_commands.describe(
        ano="Ano", mes="Mês (1-12)", dia="Dia", hora="Hora (0-23)", minuto="Minuto (0-59)"
    )
    async def timestamp(
        self,
        interaction: discord.Interaction,
        ano: int, mes: int, dia: int,
        hora: int = 0, minuto: int = 0,
    ) -> None:
        try:
            dt = datetime(ano, mes, dia, hora, minuto, tzinfo=timezone.utc)
        except ValueError as exc:
            await interaction.response.send_message(f"<:1000032056:1507947210057322637> Data inválida: {exc}", ephemeral=True)
            return

        ts = int(dt.timestamp())
        formats = {
            "Data curta (`d`)":     f"<t:{ts}:d>",
            "Data longa (`D`)":     f"<t:{ts}:D>",
            "Hora curta (`t`)":     f"<t:{ts}:t>",
            "Hora longa (`T`)":     f"<t:{ts}:T>",
            "Data + hora (`f`)":    f"<t:{ts}:f>",
            "Data + hora longa (`F`)": f"<t:{ts}:F>",
            "Relativo (`R`)":       f"<t:{ts}:R>",
        }
        embed = self._base_embed("Gerador de Timestamps", discord.Color.blurple(), icon="<a:1000032071:1507947918752092301>")
        embed.description = f"Unix: `{ts}`\nUTC: `{dt.strftime('%Y-%m-%d %H:%M')}`"
        for label, fmt in formats.items():
            embed.add_field(name=label, value=f"{fmt}\n`{fmt}`", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /cor ──────────────────────────────────

    @app_commands.command(name="cor", description="Exibe e converte uma cor HEX, mostra preview.")
    @app_commands.describe(hex_code="Código HEX da cor (ex: #FF5733 ou FF5733)")
    async def cor(self, interaction: discord.Interaction, hex_code: str) -> None:
        clean = hex_code.lstrip("#").strip()
        if len(clean) not in (3, 6) or not all(c in "0123456789abcdefABCDEF" for c in clean):
            await interaction.response.send_message("<:1000032056:1507947210057322637> HEX inválido. Use formato `#RRGGBB` ou `#RGB`.", ephemeral=True)
            return
        r, g, b = _hex_to_rgb(clean if len(clean) == 6 else "".join(c * 2 for c in clean))
        h, s, l = _rgb_to_hsl(r, g, b)
        color = discord.Color.from_rgb(r, g, b)
        embed = self._base_embed(f"Cor · #{clean.upper()}", color, icon="<:1000032075:1507948047269888001>")
        embed.add_field(name="HEX", value=f"`#{clean.upper()}`", inline=True)
        embed.add_field(name="RGB", value=f"`rgb({r}, {g}, {b})`", inline=True)
        embed.add_field(name="HSL", value=f"`hsl({h}°, {s}%, {l}%)`", inline=True)
        embed.add_field(name="Valor decimal", value=f"`{color.value}`", inline=True)
        embed.set_image(url=f"https://singlecolorimage.com/get/{clean.upper()}/400x80")
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════
    # COMANDOS COM IA
    # ══════════════════════════════════════════

    @app_commands.command(name="resumir", description="Resume um texto longo com IA.")
    @app_commands.describe(texto="Texto a ser resumido", idioma="Idioma do resumo (padrão: português)")
    async def resumir(
        self,
        interaction: discord.Interaction,
        texto: str,
        idioma: str = "português",
    ) -> None:
        await interaction.response.defer()
        result = await self._ai_complete(
            system=f"Resuma o texto fornecido em {idioma}, de forma clara e concisa, em no máximo 3 parágrafos.",
            user=texto[:4000],
            max_tokens=500,
        )
        embed = self._base_embed("Resumo (IA)", discord.Color.green(), icon="<:1000032049:1507946904124919949>")
        embed.description = result[:4000]
        embed.set_footer(text=f"Revolux IA · {AI_MODEL}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="traduzir", description="Traduz um texto com IA.")
    @app_commands.describe(texto="Texto a traduzir", idioma_destino="Idioma de destino (ex: inglês, espanhol)")
    async def traduzir(
        self,
        interaction: discord.Interaction,
        texto: str,
        idioma_destino: str = "inglês",
    ) -> None:
        await interaction.response.defer()
        result = await self._ai_complete(
            system=(
                f"Você é um tradutor profissional. Traduza o texto fornecido para {idioma_destino}. "
                "Retorne APENAS a tradução, sem explicações ou comentários."
            ),
            user=texto[:3000],
            max_tokens=600,
        )
        embed = self._base_embed(f"Tradução para {idioma_destino.title()}", discord.Color.blurple(), icon="<:1000032073:1507947991737176134>")
        embed.add_field(name="Original", value=texto[:500], inline=False)
        embed.add_field(name="Tradução", value=result[:1000], inline=False)
        embed.set_footer(text=f"Revolux IA · {AI_MODEL}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pergunte-ia", description="Faça uma pergunta rápida para a IA do Revolux.")
    @app_commands.describe(pergunta="Sua pergunta")
    async def pergunte_ia(self, interaction: discord.Interaction, pergunta: str) -> None:
        await interaction.response.defer()
        guild_context = (
            f"Você está no servidor Discord '{interaction.guild.name}'. "
            "Responda em português de forma clara e objetiva, em no máximo 3 parágrafos."
        )
        result = await self._ai_complete(
            system=guild_context,
            user=pergunta[:2000],
            max_tokens=600,
        )
        embed = self._base_embed("Resposta da IA", discord.Color.blurple(), icon="<:1000032069:1507947817719562250>")
        embed.add_field(name="Pergunta", value=f"> {pergunta[:500]}", inline=False)
        embed.add_field(name="Resposta", value=result[:1800], inline=False)
        embed.set_footer(text=f"Revolux IA · {AI_MODEL} · {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    # ── /embed-custom ─────────────────────────

    @app_commands.command(name="embed-custom", description="Cria um embed personalizado no canal.")
    @app_commands.describe(
        titulo="Título do embed",
        descricao="Descrição (conteúdo)",
        cor="Cor HEX (ex: #5865F2)",
        imagem="URL de imagem de destaque",
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
            await interaction.response.send_message("<:1000032056:1507947210057322637> Canal inválido.", ephemeral=True)
            return

        try:
            r, g, b = _hex_to_rgb(cor.lstrip("#"))
            color = discord.Color.from_rgb(r, g, b)
        except Exception:
            color = discord.Color.blurple()

        embed = discord.Embed(
            title=titulo,
            description=descricao,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        if imagem:
            embed.set_image(url=imagem)
        if rodape:
            embed.set_footer(text=rodape)
        if self.bot.user:
            embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        await target.send(embed=embed)
        await interaction.response.send_message(f"<:1000032055:1507947171624910859> Embed enviado em {target.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
