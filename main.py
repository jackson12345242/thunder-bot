""
main.py
 
Single-file Discord bot (no cogs) with:
 
  /role create name:<str> hex_code:<str>
    - Posts an approval embed (with Accept/Deny buttons) into REQUEST_CHANNEL_ID.
    - Accept -> creates the role with the given color, DMs the requester it was made.
    - Deny   -> DMs the requester it was denied.
 
  /role give
    - Posts an embed with a dropdown of every assignable role in the server.
    - Selecting a role toggles it on/off for whoever clicked.
 
Setup:
  1. Just run this one file - no cogs/ folder needed.
  2. Make sure the bot's top role sits above any role it will be asked to create,
     and that it has the "Manage Roles" permission.
  3. Set the DISCORD_TOKEN env var (and optionally GUILD_ID for instant command sync).
"""
 
import os
import re
 
import discord
from discord import app_commands
from discord.ext import commands
 
REQUEST_CHANNEL_ID = 1274373211609501836
TOKEN = os.environ["DISCORD_TOKEN"]
# Optional: set this on Railway while testing so slash commands sync instantly
# to one server instead of waiting up to an hour for a global sync.
GUILD_ID = os.environ.get("GUILD_ID")
 
intents = discord.Intents.default()
intents.members = True  # needed to add/remove roles and DM requesters
 
bot = commands.Bot(command_prefix="!", intents=intents)
 
 
def parse_hex(hex_code: str) -> discord.Color | None:
    """Turns '#5865F2' or '5865F2' into a discord.Color, or None if invalid."""
    cleaned = hex_code.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        return None
    return discord.Color(int(cleaned, 16))
 
 
class RoleRequestView(discord.ui.View):
    """Persistent view: Accept/Deny buttons on a role-request embed.
 
    No per-instance state is stored here on purpose - everything the buttons
    need (role name, hex code, requester id) is read back out of the embed
    at click time. That's what lets discord.py re-attach this view to old
    messages after a restart via bot.add_view(RoleRequestView()).
    """
 
    def __init__(self):
        super().__init__(timeout=None)
 
    @staticmethod
    def _read_embed(embed: discord.Embed) -> tuple[str, str, int]:
        name = discord.utils.get(embed.fields, name="Role Name").value
        hex_code = discord.utils.get(embed.fields, name="Hex Code").value
        requester_id = int(embed.footer.text.split(":")[-1].strip())
        return name, hex_code, requester_id
 
    async def _finalize(self, interaction: discord.Interaction, *, accepted: bool):
        embed = interaction.message.embeds[0]
        name, hex_code, requester_id = self._read_embed(embed)
 
        for item in self.children:
            item.disabled = True
 
        if accepted:
            color = parse_hex(hex_code) or discord.Color.default()
            role = await interaction.guild.create_role(
                name=name, color=color, reason=f"Role request approved by {interaction.user}"
            )
            embed.color = discord.Color.green()
            embed.add_field(name="Status", value=f"✅ Accepted by {interaction.user.mention}", inline=False)
        else:
            role = None
            embed.color = discord.Color.red()
            embed.add_field(name="Status", value=f"❌ Denied by {interaction.user.mention}", inline=False)
 
        await interaction.response.edit_message(embed=embed, view=self)
 
        requester = interaction.guild.get_member(requester_id)
        if requester:
            try:
                if accepted:
                    await requester.send(f"Your role request **{name}** was accepted — {role.mention} has been created.")
                else:
                    await requester.send(f"Your role request **{name}** was denied.")
            except discord.Forbidden:
                pass  # requester has DMs closed
 
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="role_request_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
            return
        await self._finalize(interaction, accepted=True)
 
    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="role_request_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
            return
        await self._finalize(interaction, accepted=False)
 
 
class RoleGiveSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        assignable = [
            role
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if not role.is_default() and not role.managed
        ][:25]  # Discord caps select menus at 25 options
 
        options = [discord.SelectOption(label=role.name, value=str(role.id)) for role in assignable]
        super().__init__(placeholder="Choose a role...", min_values=1, max_values=1, options=options)
 
    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.values[0]))
        if role is None:
            await interaction.response.send_message("That role no longer exists.", ephemeral=True)
            return
 
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role, reason="Self-service role removal")
            await interaction.response.send_message(f"Removed {role.mention}.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Self-service role assignment")
            await interaction.response.send_message(f"Gave you {role.mention}.", ephemeral=True)
 
 
class RoleGiveView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.add_item(RoleGiveSelect(guild))
 
 
role_group = app_commands.Group(name="role", description="Role management commands")
 
 
@role_group.command(name="create", description="Request a new role to be created")
@app_commands.describe(name="The name of the role", hex_code="Hex color, e.g. #5865F2")
async def role_create(interaction: discord.Interaction, name: str, hex_code: str):
    color = parse_hex(hex_code)
    if color is None:
        await interaction.response.send_message(
            "That's not a valid hex code. Use a format like `#5865F2` or `5865F2`.", ephemeral=True
        )
        return
 
    channel = bot.get_channel(REQUEST_CHANNEL_ID) or await bot.fetch_channel(REQUEST_CHANNEL_ID)
 
    embed = discord.Embed(title="New Role Request", color=color)
    embed.add_field(name="Role Name", value=name, inline=True)
    embed.add_field(name="Hex Code", value=f"#{hex_code.strip().lstrip('#').upper()}", inline=True)
    embed.add_field(name="Requested By", value=interaction.user.mention, inline=False)
    embed.set_footer(text=f"Requester ID: {interaction.user.id}")
 
    await channel.send(embed=embed, view=RoleRequestView())
    await interaction.response.send_message("Your role request has been submitted.", ephemeral=True)
 
 
@role_group.command(name="give", description="Post a role picker for members to self-assign a role")
async def role_give(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Pick a Role",
        description="Select a role from the dropdown below. Picking a role you already have removes it.",
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=RoleGiveView(interaction.guild))
 
 
bot.tree.add_command(role_group)
 
 
@bot.event
async def setup_hook():
    bot.add_view(RoleRequestView())  # re-attach persistent Accept/Deny buttons
 
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
 
 
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
 
 
bot.run(TOKEN)
 
