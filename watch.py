import discord
import asyncio
import datetime
import random
import asyncpg
import json
import datetime
import re
from emoji import clean_emoji
from event import Event
from options import Options

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
        # await db.execute("CREATE TABLE IF NOT EXISTS events(event_id integer, guild_id bigint REFERENCES guild_configs(guild_id), event_type event_t, reason text, message_id bigint, target_id bigint, target_name text, actor bigint, role_id bigint, role_name text, PRIMARY KEY (event_id, guild_id));")

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
            await post_entries(entries, channel)

        await asyncio.sleep(2)

async def get_guild_configs(guild_id):
    return await bot.db.fetchrow("SELECT * FROM guild_configs WHERE guild_id = $1;", guild_id)

async def check_guild_logs(guild, guild_config):
    recent_events = guild_config.get("recent_events", [])
    if not recent_events:
        recent_events = [discord.utils.time_snowflake(datetime.datetime.utcnow())]

    events = []
    special_roles = guild_config.get("special_roles", [])

    break_signal = False
    oldest = None
    while not break_signal:
        print(f"checking {guild.name} logs...") #TODO: remove this (or make it look better)
        raw_events = await guild.audit_logs(limit=100, before=discord.Object(id=oldest)).flatten() # I think pagination works

        if oldest == None:
            new_recent_events = [e.id for e in raw_events[:3]]

        if not raw_events:
            break
        
        oldest = raw_events[-1].id

        for e in raw_events:
            if e.id <= min(recent_events):
                break_signal = True
                break

            if e.id in recent_events:
                continue
            
            if not e.action in event_t:
                continue

            reason = e.reason.strip() if e.reason else "*None set*"
            event_type = event_t_str[event_t.index(e.action)]
            role = None

            if e.action == discord.AuditLogAction.member_role_update:
                before = [r for r in e.changes.before.roles]
                after = [r for r in e.changes.after.roles]

                for r in before:
                    if r.id in special_roles:
                        event_type = "role_remove"
                        role = r
                        events += [Event(guild.id, event_type, e.target.id, str(e.target), e.user, reason, role.id, role.name)]

                for r in after:
                    if r.id in special_roles:
                        event_type = "role_remove"
                        role = r
                        events += [Event(guild.id, event_type, e.target.id, str(e.target), e.user, reason, role.id, role.name)]

                continue

            events += [Event(guild.id, event_type, e.target.id, str(e.target), e.user, reason, None, None)]
            continue

    await bot.db.execute("""
    UPDATE guild_configs
    SET recent_events = $1
    WHERE guild_id = $2;
    """, new_recent_events, guild.id)

    return events[::-1]

async def post_entries(entries, channel):
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            guild_config = await conn.fetchrow("SELECT * FROM guild_configs WHERE guild_id = $1 FOR UPDATE;", channel.guild.id)
            options = Options(guild_config.get("options"))

            latest_event_count = guild_config.get("latest_event_count")

            for e in entries:
                latest_event_count += 1

                e.set_count(latest_event_count)
                msg = await channel.send(generate_entry(e, options))

                await conn.execute("""INSERT INTO events(
                message_id, guild_id, event_type, target_id, target_name, actor, reason, role_id, role_name, event_id
                ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10);""", msg.id, *e.db_insert())
                
                # await update_entry(msg, e, options)

            await conn.execute("""
            UPDATE guild_configs
            SET latest_event_count = $1
            WHERE guild_id = $2;
            """, latest_event_count, channel.guild.id)

invite_reg = re.compile("((?:https?:\/\/)?discord(?:\.gg|app\.com\/invite)\/(?:#\/)?)([a-zA-Z0-9-]*)")

def generate_entry(event, options):
    ret = "**{}** | Case {}\n".format(event_t_display[event_t_str.index(event.event_type)], event.count)

    name = event.target_name
    if not options.reveal_invites:
        name = invite_reg.sub("\g<1>[INVITE REDACTED]", name)
    name = clean_emoji(name)

    ret += "**User**: {} ({})".format(name, event.target_id)
    if options.ping_target:
        ret += " (<@{}>)".format(event.target_id)

    ret += "\n"
    if event.role_id:
        ret += "**Role**: {} ({})\n".format(event.role_name, event.role_id)

    ret += "**Reason**: {}\n".format(event.reason)
    ret += "**Responsible moderator**: {}#{}".format(clean_emoji(event.actor.name), event.actor.discriminator)

    return ret

async def update_entry(message, event, options=None):
    if not options:
        options = await get_guild_configs(message.guild.id)
        options = Options(options)
    
    await message.edit(content=generate_entry(event, options))

prefixes = [f"<@{cfg['bot_id']}>", f"<@!{cfg['bot_id']}>", "w!", "watch!", "⌚"]

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
                await message.channel.send(str(_))
            except Exception as e:
                await message.channel.send("```\n" + str(e) + "\n```")
        else:
            try:
                _ = eval(args)
                await message.channel.send(str(_))
            except Exception as e:
                await message.channel.send("```\n" + str(e) + "\n```")
        return True

async def close(message, **kwargs):
    if message.author.id == 116138050710536192:
        msg = await message.channel.send("Shutting down...")
        await bot.db.close()
        await bot.logout()
        await bot.close()
        exit()

cmds = {
    "eval": evaluate,
    "quit": close
}

bot.run(cfg["token"])