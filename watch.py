import discord
import asyncio
import datetime
import random
import asyncpg
import json
import datetime
import re
from emoji import clean_emoji

bot = discord.Client()

bot.timestamp = 0
bot._guild_check_queue = []

with open("config.json") as w:
    cfg = json.loads(w.read())

@bot.event
async def on_ready():
    print("Watching...")
    if not bot.timestamp:

        credentials = {"user": "watchbot", "password": cfg["db_pass"], "database": "watchdata", "host": "localhost"}
        db = await asyncpg.create_pool(**credentials)

        # await db.execute("CREATE TABLE IF NOT EXISTS guild_configs(guild_id bigint PRIMARY KEY, post_channel bigint, prefix text DEFAULT '!', options integer DEFAULT 0, latest_event_count integer, special_roles bigint[], recent_events bigint[]);")
        # await db.execute("CREATE TYPE event_t AS enum('kick', 'ban', 'unban', 'role_add', 'role_remove');")
        # await db.execute("CREATE TABLE IF NOT EXISTS events(event_id integer, guild_id bigint REFERENCES guild_configs(guild_id), event_type event_t, reason text, message_id bigint, target bigint, actor bigint, role_id bigint, PRIMARY KEY (event_id, guild_id));")

        # Look like CREATE TYPE IF NOT EXISTS isn't a thing so just run those in the db before starting the bot ever

        bot.db = db

        bot.dispatch("run_check_loop")
        bot.timestamp = datetime.datetime.utcnow().timestamp()

        watching_choices = ["you.", "carefully", "closely"]
        while True:
            await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=random.choice(watching_choices)))
            await asyncio.sleep(3600)

event_t = [discord.AuditLogAction.kick, discord.AuditLogAction.ban, discord.AuditLogAction.unban, discord.AuditLogAction.member_role_update]
event_t_str = ["kick", "ban", "unban", "role_update", "role_add", "role_remove"]
event_t_display = ["Kick", "Ban", "Unban", "Special Role Modified", "Special Role Added", "Special Role Removed"]

@bot.event
async def on_run_check_loop():
    while True:
        to_check = set(bot._guild_check_queue)
        # inb4 another value is added here before I clear it haha
        bot._guild_check_queue = []

        for guild in to_check:

            # Check if guild can be posted to
            if not guild.me.guild_permissions.view_audit_log:
                continue
            
            guild_config = await get_guild_configs(guild.id)
            if not guild_config:
                continue
            
            channel = guild_config.get("post_channel", 0)
            channel = guild.get_channel(channel)

            if not channel or not channel.permissions_for(guild.me).send_messages:
                continue
            
            # Get entries
            entries = await check_guild_logs(guild, guild_config)
            await post_entries(channel, entries)

        await asyncio.sleep(2)

async def get_guild_configs(guild_id):
    return await bot.db.fetchrow("SELECT * FROM guild_configs WHERE guild_id = $1;", guild_id)

async def check_guild_logs(guild, guild_config):
    recent_events = guild_config.get("recent_events", [])
    if not recent_events:
        recent_events = [discord.utils.time_snowflake(datetime.datetime.utcnow())]

    events = []
    special_roles = guild_config.get("special_roles", [])

    oldest = None
    while oldest == None or oldest > min(recent_events):
        raw_events = await guild.audit_logs(limit=4, before=discord.Object(id=oldest)).flatten() # set to 4 to test pagination

        if oldest == None:
            new_recent_events = [e.id for e in raw_events[:3]]

        oldest = raw_events[-1].id

        for e in raw_events:
            if e.id in recent_events:
                continue
            
            if not e.action in event_t:
                continue
            
            to_add = {
                "target": e.target,
                "actor": e.user,
                "reason": e.reason if e.reason else "*None set*",
                "type": event_t_str[event_t.index(e.action)],
                "role": None
                }

            if e.action == discord.AuditLogAction.member_role_update:
                before = [r for r in e.changes.before.roles]
                after = [r for r in e.changes.after.roles]

                for r in before:
                    if r.id in special_roles:
                        events += [
                            {
                            **to_add,
                            "type": "role_remove",
                            "role": r
                            }
                            ]

                for r in after:
                    if r.id in special_roles:
                        events += [
                            {
                            **to_add,
                            "type": "role_add",
                            "role": r
                            }
                            ]

                continue

            events += [to_add]
            continue

    await bot.db.execute("""
    UPDATE guild_configs
    SET recent_events = $1
    WHERE guild_id = $2;
    """, new_recent_events, guild.id)

    return events[::-1]

def decode_options(options):
    # also figure out a way to make this code easier to update ig
    # 1 - reveal invites (don't obfuscate them)

    return {
        "reveal_invites": options & 0b1 == 0b1,
        # "option_2": options & 0b10 == 0b10,
        # "option_4": options & 0b100 != 0b100,
    }

async def post_entries(entries, channel):
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            guild_config = await conn.fetchrow("SELECT FOR UPDATE * FROM guild_configs WHERE guild_id = $1;", channel.guild.id)
            options = decode_options(guild_config.get("options"))

            latest_event_count = guild_config.get("latest_event_count")

            for e in entries:
                msg = await channel.send("Loading...")
                latest_event_count += 1
                await conn.execute("""INSERT INTO events(
                event_id, guild_id, event_type, reason, message_id, target, actor, role_id
                ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8);""",
                latest_event_count,
                channel.guild.id,
                e["type"],
                e["reason"],
                msg.id,
                e["target"].id,
                e["actor"].id,
                e["role"].id if e["role"] else None
                )
                
                await update_entry(msg, {**e, "count": latest_event_count}, options)

            await bot.db.execute("""
            UPDATE guild_configs
            SET latest_event_count = $1
            WHERE guild_id = $2;
            """, latest_event_count, channel.guild.id)

invite_reg = re.compile("((?:https?:\/\/)?discord(?:\.gg|app\.com\/invite)\/(?:#\/)?)([a-zA-Z0-9-]*)")

async def update_entry(message, event, options=None):
    if not options:
        options = await get_guild_configs(message.guild.id)
        options = decode_options(options)
    
    ret = "**{}** | Case {}".format(event_t_display[event_t_str.index(event["type"])], event["count"])

    name = event["target"].name
    if not options["reveal_invites"]:
        name = invite_reg.sub("\g<1>[INVITE REDACTED]", name)
    name = clean_emoji(name)

    ret += "**User**: {1}#{2} ({0}) (<@{0}>)".format(event["target"].id, name, event["target"].discriminator)
    
    if event["role"]:
        ret += "**Role**: {0.name} ({0.id})".format(event["role"])

    ret += "**Reason**: {}".format(event["reason"])
    ret += "**Responsible moderator**: {}#{}".format(clean_emoji(event["actor"].name), event["actor"].discriminator)

    await message.edit(content=ret)

prefixes = [f"<@{cfg['bot_id']}>", f"<@!{cfg['bot_id']}>", "w!", "watch!"]

@bot.event
async def on_message(message):
    if (not bot.timestamp or message.author.bot or not message.content or 
    (isinstance(message.channel, discord.abc.GuildChannel) and
    not message.channel.permissions_for(message.guild.me).send_messages
    )):
        return

    msg = None
    for p in prefixes: # TODO: check (also make) per-guild prefix cache
        if message.content.lower().startswith(p):
            msg = message.content[len(p):].strip()
            break

    if not msg:
        return

    split = msg.split(None, 1)

    if len(split) == 0:
        return

    cmd = split[0].lower()

    if cmd in cmds:
        if isinstance(message.channel, discord.abc.GuildChannel):
            print("{0.created_at} - {0.guild.name}#{0.channel.name} - {0.author.name}: {0.content}".format(message))
        else:
            print("{0.created_at} - DM - {0.author.name}: {0.content}".format(message))

        args = None
        if len(split) > 1:
            args = split[1]
        kwargs = {"message": message, "cmd": cmd, "args": args}
        func = await cmds[cmd](**kwargs)

_ = None

async def evaluate(message, args, **kwargs):
    if message.author.id == 116138050710536192 and args:
        global _
        ctx = message
        if args.split(' ', 1)[0] == 'await':
            try:
                _ = await eval(args.split(' ', 1)[1])
                await message.channel.send(_)
            except Exception as e:
                await message.channel.send("```\n" + str(e) + "\n```")
        else:
            try:
                _ = eval(args)
                await message.channel.send(_)
            except Exception as e:
                await message.channel.send("```\n" + str(e) + "\n```")
        return True

cmds = {
    "eval": evaluate
}

bot.run(cfg["token"])