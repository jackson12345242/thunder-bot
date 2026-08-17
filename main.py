import os
 
import discord
from discord.ext import commands
 
TOKEN = os.environ["DISCORD_TOKEN"]
# Optional: set this on Railway while testing so slash commands sync instantly
# to one server instead of waiting up to an hour for a global sync.
GUILD_ID = os.environ.get("GUILD_ID")
 
intents = discord.Intents.default()
intents.members = True  # needed to add/remove roles and DM requesters
 
bot = commands.Bot(command_prefix="!", intents=intents)
 
 
@bot.event
async def setup_hook():
    from cogs.role_management import RoleRequestView
 
    await bot.load_extension("cogs.role_management")
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
