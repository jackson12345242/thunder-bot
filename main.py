# main.py
#
# Single-file Discord bot (no cogs) with:
#
#   /role create name:<str> hex_code:<str>
#     - Posts an approval embed (with Accept/Deny buttons) into REQUEST_CHANNEL_ID.
#     - Accept -> creates the role with the given color, DMs the requester it was made,
#       and remembers that this user is the "creator" of that role.
#     - Deny   -> DMs the requester it was denied.
#
#   /role give user:<member> role:<role>
#     - If the caller is the creator of that role (i.e. they had it approved via
#       /role create), it's given immediately, no approval needed - but it's still
#       logged in REQUEST_CHANNEL_ID.
#     - Otherwise, posts an approval embed (with Accept/Deny buttons) into
#       REQUEST_CHANNEL_ID, same as role creation.
#
# Only members with one of the roles in ALLOWED_ROLE_IDS can use any /role command.
#
# Setup:
#   1. Just run this one file - no cogs folder needed.
#   2. Make sure the bots top role sits above any role it will be asked to create
#      or assign, and that it has the "Manage Roles" permission.
#   3. Set the DISCORD_TOKEN env var (and optionally GUILD_ID for instant command sync).
#   4. role_creators.json is used to remember who created which role. On Railway,
#      attach a persistent volume mounted at the working directory if you want this
#      to survive redeploys - otherwise it resets whenever the container is rebuilt.
 
import json
import os
import re
 
import discord
from discord import app_commands
from discord.ext import commands
 
REQUEST_CHANNEL_ID = 1538963159514218526
ALLOWED_ROLE_IDS = {980812623299899392, 1020661717883175002}
ROLE_CREATORS_FILE = "role_creators.json"
TOKEN = os.environ["DISCORD_TOKEN"]
# Optional: set this on Railway while testing so slash commands sync instantly
# to one server instead of waiting up to an hour for a global sync.
GUILD_ID = os.environ.get("GUILD_ID")
 
intents = discord.Intents.default()
intents.members = True  # needed to add/remove roles and DM requesters
 
bot = commands.Bot(command_prefix="!", intents=intents)
 
 
def parse_hex(hex_code: str) -> discord.Color | None:
    # Turns "#5865F2" or "5865F2" into a discord.Color, or None if invalid.
    cleaned = hex_code.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        return None
    return discord.Color(int(cleaned, 16))
 
 
def load_role_creators() -> dict:
    try:
        with open(ROLE_CREATORS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
 
 
def save_role_creators() -> None:
    with open(ROLE_CREATORS_FILE, "w") as f:
        json.dump(role_creators, f)
 
 
# Maps role_id (str) -> creator user id (int). Populated when a /role create
# request is accepted, so /role give can tell whether the caller made this role.
role_creators: dict = load_role_creators()
 
 
class RoleRequestView(discord.ui.View):
    # Persistent view: Accept/Deny buttons on a role-CREATE-request embed.
    #
    # No per-instance state is stored here on purpose - everything the buttons
    # need (role name, hex code, requester id) is read back out of the embed
    # at click time. That is what lets discord.py re-attach this view to old
    # messages after a restart via bot.add_view(RoleRequestView()).
 
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
            role_creators[str(role.id)] = requester_id
            save_role_creators()
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
 
 
class RoleGiveRequestView(discord.ui.View):
    # Persistent view: Accept/Deny buttons on a role-GIVE-request embed.
    # Same "read state back out of the embed" approach as RoleRequestView.
 
    def __init__(self):
        super().__init__(timeout=None)
 
    @staticmethod
    def _read_embed(embed: discord.Embed) -> tuple[int, int, int]:
        role_id = int(discord.utils.get(embed.fields, name="Role ID").value)
        target_id = int(discord.utils.get(embed.fields, name="Target User ID").value)
        requester_id = int(embed.footer.text.split(":")[-1].strip())
        return role_id, target_id, requester_id
 
    async def _finalize(self, interaction: discord.Interaction, *, accepted: bool):
        embed = interaction.message.embeds[0]
        role_id, target_id, requester_id = self._read_embed(embed)
 
        for item in self.children:
            item.disabled = True
 
        role = interaction.guild.get_role(role_id)
        target = interaction.guild.get_member(target_id)
 
        if accepted and role and target:
            await target.add_roles(role, reason=f"Role give approved by {interaction.user}")
            embed.color = discord.Color.green()
            embed.add_field(name="Status", value=f"✅ Accepted by {interaction.user.mention}", inline=False)
        elif accepted:
            embed.color = discord.Color.red()
            embed.add_field(
                name="Status",
                value=f"⚠️ Could not complete (role or member no longer exists) — reviewed by {interaction.user.mention}",
                inline=False,
            )
        else:
            embed.color = discord.Color.red()
            embed.add_field(name="Status", value=f"❌ Denied by {interaction.user.mention}", inline=False)
 
        await interaction.response.edit_message(embed=embed, view=self)
 
        requester = interaction.guild.get_member(requester_id)
        if requester:
            try:
                if accepted and role and target:
                    await requester.send(f"Your request to give {role.name} to {target.display_name} was accepted.")
                elif accepted:
                    await requester.send("Your role-give request was approved but couldn't be completed — the role or member no longer exists.")
                else:
                    await requester.send("Your request to give a role was denied.")
            except discord.Forbidden:
                pass  # requester has DMs closed
 
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="role_give_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
            return
        await self._finalize(interaction, accepted=True)
 
    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="role_give_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
            return
        await self._finalize(interaction, accepted=False)
 
 
class RoleGroup(app_commands.Group):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or not any(role.id in ALLOWED_ROLE_IDS for role in member.roles):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return False
        return True
 
 
role_group = RoleGroup(name="role", description="Role management commands")
 
 
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
 
 
@role_group.command(name="give", description="Give a role to a user")
@app_commands.describe(user="The member to give the role to", role="The role to give")
async def role_give(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    channel = bot.get_channel(REQUEST_CHANNEL_ID) or await bot.fetch_channel(REQUEST_CHANNEL_ID)
    creator_id = role_creators.get(str(role.id))
 
    if creator_id == interaction.user.id:
        # This person created the role via /role create, so they can hand it out
        # without approval - but it still gets logged in the same channel.
        await user.add_roles(role, reason=f"Given by role creator {interaction.user}")
 
        log_embed = discord.Embed(title="Role Given (Auto-Approved)", color=discord.Color.green())
        log_embed.add_field(name="Role", value=role.mention, inline=True)
        log_embed.add_field(name="Given To", value=user.mention, inline=True)
        log_embed.add_field(name="Given By", value=interaction.user.mention, inline=False)
        log_embed.set_footer(text="Auto-approved: given by this role's creator")
        await channel.send(embed=log_embed)
 
        await interaction.response.send_message(f"Gave {role.mention} to {user.mention}.", ephemeral=True)
        return
 
    embed = discord.Embed(title="New Role Give Request", color=discord.Color.blurple())
    embed.add_field(name="Role ID", value=str(role.id), inline=True)
    embed.add_field(name="Role", value=role.mention, inline=True)
    embed.add_field(name="Target User ID", value=str(user.id), inline=True)
    embed.add_field(name="Target User", value=user.mention, inline=True)
    embed.add_field(name="Requested By", value=interaction.user.mention, inline=False)
    embed.set_footer(text=f"Requester ID: {interaction.user.id}")
 
    await channel.send(embed=embed, view=RoleGiveRequestView())
    await interaction.response.send_message("Your request to give this role has been submitted for approval.", ephemeral=True)
 
 
bot.tree.add_command(role_group)
 
 
@bot.event
async def setup_hook():
    bot.add_view(RoleRequestView())      # re-attach persistent create Accept/Deny buttons
    bot.add_view(RoleGiveRequestView())  # re-attach persistent give Accept/Deny buttons
 
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
 
