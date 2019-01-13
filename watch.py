import discord
import asyncio
import datetime
import random
import asyncpg
import json
import datetime
import inspect
import re
import json
from io import BytesIO
import util
from emoji import clean_emoji
from event import Event
from options import Options
from configs import Configs
from util import encode, decode

bot = discord.Client()

bot.timestamp = 0
bot._guild_check_queue = []
bot._guild_prefix_cache = {}

with open("config.json") as w:
    cfg = json.loads(w.read())

@bot.event
async def on_ready():
    print("Watching...")
    if not bot.timestamp:

        credentials = {"user": "watchbot", "password": cfg["db_pass"], "database": "watchdata", "host": "localhost"}
        db = await asyncpg.create_pool(**credentials)

        # await db.execute("CREATE TABLE IF NOT EXISTS guild_configs(guild_id bigint PRIMARY KEY, post_channel bigint, prefix text DEFAULT '!', options integer DEFAULT 0, latest_event_count integer, special_roles bigint[], recent_events bigint[], _offset integer DEFAULT 0);")
        # await db.execute("CREATE TYPE event_t AS enum('kick', 'ban', 'unban', 'role_add', 'role_remove');")
        # await db.execute("CREATE TABLE IF NOT EXISTS events(event_id integer, guild_id bigint REFERENCES guild_configs(guild_id), event_type event_t, reason text, timestamp TIMESTAMP, message_id bigint, target_id bigint, target_name text, actor bigint, role_id bigint, role_name text, PRIMARY KEY (event_id, guild_id));")

        # Look like CREATE TYPE IF NOT EXISTS isn't a thing so just run those in the db before starting the bot ever

        bot.db = db

        bot._guild_check_queue = list(bot.guilds)
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

            try:
                # Check if guild can be posted to
                if not guild.me.guild_permissions.view_audit_log:
                    continue
                
                guild_config = await get_guild_configs(guild.id)
                if not guild_config.guild_id:
                    continue
                
                channel = guild_config.post_channel
                channel = guild.get_channel(channel)

                if not channel or not channel.permissions_for(guild.me).send_messages:
                    continue
                
                # Get entries
                entries = await check_guild_logs(guild, guild_config)
                await post_entries(entries, channel, guild_config)
            except Exception as e:
                print(f"Error in guild {guild.id}")
                print(e.__traceback__)

                # TODO: Add webhook reporting

        await asyncio.sleep(2)

@bot.event
async def on_member_ban(guild, user):
    bot._guild_check_queue += [guild]

@bot.event
async def on_member_unban(guild, user):
    bot._guild_check_queue += [guild]

@bot.event
async def on_member_remove(member):
    bot._guild_check_queue += [member.guild]

@bot.event
async def on_member_update(before, after):
    if before.roles != after.roles:
        bot._guild_check_queue += [before.guild]

async def get_guild_configs(guild_id):
    ret = await bot.db.fetchrow("SELECT * FROM guild_configs WHERE guild_id = $1;", guild_id)
    ret = ret if ret else {}
    return Configs.from_row(ret)

async def check_guild_logs(guild, guild_config):
    recent_events = guild_config.recent_events
    if not recent_events:
        recent_events = [discord.utils.time_snowflake(datetime.datetime.utcnow())]

    events = []
    special_roles = guild_config.roles

    break_signal = False
    oldest = None
    while not break_signal:
        raw_events = await guild.audit_logs(limit=100, before=discord.Object(id=oldest)).flatten()

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

            reason = e.reason.strip() if e.reason else None
            event_type = event_t_str[event_t.index(e.action)]
            role = None

            if e.action == discord.AuditLogAction.member_role_update:

                for r in e.changes.before.roles:
                    if r.id in special_roles:
                        event_type = "role_remove"
                        role = r
                        events += [Event(guild.id, event_type, e.target.id, str(e.target), e.user, reason, e.created_at, role.id, role.name)]

                for r in e.changes.after.roles:
                    if r.id in special_roles:
                        event_type = "role_add"
                        role = r
                        events += [Event(guild.id, event_type, e.target.id, str(e.target), e.user, reason, e.created_at, role.id, role.name)]

                continue

            events += [Event(guild.id, event_type, e.target.id, str(e.target), e.user, reason, e.created_at, None, None)]
            continue

    events = events [::-1]
    
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT FROM guild_configs WHERE guild_id = $1 FOR UPDATE;", guild.id) # That's how you're supposed to lock it right?
            
            latest_event_count = guild_config.latest_event_count

            for e in events:
                latest_event_count += 1
                e.set_count(latest_event_count)

                await conn.execute("""INSERT INTO events(
                    guild_id, event_type, target_id, target_name, actor, reason, timestamp, role_id, role_name, event_id
                    ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10);""", *e.db_insert())
                    
            await conn.execute("""
            UPDATE guild_configs
            SET recent_events = $1,
            latest_event_count = $2
            WHERE guild_id = $3;
            """, new_recent_events, latest_event_count, guild.id)
        
        return events

async def post_entries(entries, channel, guild_config):
    ret = []
    for e in entries:
        print(f"Posting case {e.count} to {channel.guild.id}")
        msg = await channel.send(generate_entry(e, guild_config))
        await bot.db.execute("""
        UPDATE events
        SET message_id = $1
        WHERE guild_id = $2
        AND event_id = $3;
        """, msg.id, channel.guild.id, e.count)
        ret += [msg]
    
    return ret


invite_reg = re.compile("((?:https?:\/\/)?discord(?:\.gg|app\.com\/invite)\/(?:#\/)?)([a-zA-Z0-9-]*)")

def generate_entry(event, config, default_reason="_Responsible moderator, please do `reason {} <reason>`_"):
    case_num = event.count + config.offset
    ret = "**{}** | Case {}\n".format(event_t_display[event_t_str.index(event.event_type)], case_num)

    name = event.target_name
    if not config.options.reveal_invites:
        name = invite_reg.sub("\g<1>[INVITE REDACTED]", name)
    name = clean_emoji(name)

    ret += "**User**: {} ({})".format(name, event.target_id)
    if config.options.ping_target:
        ret += " (<@{}>)".format(event.target_id)

    ret += "\n"
    if event.role_id:
        ret += "**Role**: {} ({})\n".format(event.role_name, event.role_id)

    ret += "**Reason**: {}\n".format(event.reason if event.reason else default_reason.format(case_num))
    ret += "**Responsible moderator**: "
    if type(event.actor) == int:
        ret += f"{event.actor}"
    else:
        ret += "{}#{}".format(clean_emoji(event.actor.name), event.actor.discriminator)

    ret = ret.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    return ret

async def update_entry(message, event, configs=None):
    if not configs:
        configs = await get_guild_configs(message.guild.id)
    
    print(f"Updating case {event.count} in {message.guild.id}")
    await message.edit(content=generate_entry(event, configs))

prefixes = [f"<@{cfg['bot_id']}>", f"<@!{cfg['bot_id']}>", "w!", "watch!", "⌚", "\⌚"]

@bot.event
async def on_message(message):
    if (not bot.timestamp or
    message.author.bot or
    not message.content or
    not isinstance(message.channel, discord.abc.GuildChannel) or
    not message.channel.permissions_for(message.guild.me).send_messages
    ):
        return

    msg = None

    if not message.guild.id in bot._guild_prefix_cache:
        configs = await get_guild_configs(message.guild.id)
        if not configs.guild_id:
            guild_prefix = "!"
        else:
            guild_prefix = configs.prefix
        if guild_prefix:
            guild_prefix = guild_prefix.strip().lower()
        bot._guild_prefix_cache[message.guild.id] = guild_prefix

    custom_prefix = [bot._guild_prefix_cache[message.guild.id]]
    if not custom_prefix[0]:
        custom_prefix = []

    for p in prefixes + custom_prefix:
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

async def time(message, args, **kwargs):
    now = datetime.datetime.utcnow()
    await message.channel.send(f"\⌚ The time is now `{now.strftime('%H:%M')}` UTC.")

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

async def sudo(message, args, **kwargs):
    if message.author.id == 116138050710536192:
        sudo_funcs = {
            "reset": (_reset, (message, None)),
            "forcecheckall": (bot._guild_check_queue.extend, (bot.guilds,)),
            "forcecheckthis": (bot._guild_check_queue.append, (message.guild,))
        }
        if args:
            a = args.split(" ", 1)
            cmd = a[0].lower()
            arg = a[1] if len(a) > 1 else None
            if cmd in sudo_funcs:
                try:
                    if inspect.iscoroutinefunction(sudo_funcs[cmd][0]):
                        ret = await sudo_funcs[cmd][0](*sudo_funcs[cmd][1])
                    else:
                        ret = sudo_funcs[cmd][0](*sudo_funcs[cmd][1])
                except Exception as e:
                    ret = str(e)

                if ret == None:
                    ret = "no u"
                
                await message.channel.send(f"```\n{ret}\n```")
                return True
                
        else:
            await message.channel.send(f"All sudo commands:\n```\n{', '.join(sudo_funcs.keys())}\n```")
            return True


async def close(message, **kwargs):
    if message.author.id == 116138050710536192:
        msg = await message.channel.send("Shutting down...")
        await bot.db.close()
        await bot.logout()
        await bot.close()
        exit()

def get_case_number(num, max_num, offset=0, allow_case_range=False):
    ret = []

    num = str(num).lower()
    rng = [num]

    if allow_case_range:
        rng = num.split("..")
        if len(rng) > 2:
            raise ValueError("Invalid case number")

    for end in rng:

        if end == "":
            end = "l"

        pc = end.split("~")

        if pc[0] in ("i", "|"):
            raise ValueError("You realise that `L` is supposed to stand for `latest`, right?")

        if len(pc) > 2 or (len(pc) == 2 and not pc[0] in ("l", "latest")):
            raise ValueError("Invalid case number")

        add = max_num
        try:
            if not pc[0] in ("l", "latest"):
                add = int(pc[0])
                add -= offset
            
            if len(pc) == 2:
                add -= int(pc[1])

        except:
            raise ValueError("Invalid case number.")

        if add > max_num:
            raise ValueError("Invalid case number.")
        if add <= 0:
            raise ValueError("Invalid case number.")
        
        ret += [add]

    if not allow_case_range:
        ret = ret[0]
    else:
        ret = sorted(ret)

    return ret

def is_mod(member):
    perms = member.guild_permissions
    return any((perms.ban_members, perms.kick_members, perms.manage_roles))

async def reason(message, args, **kwargs):
    if not args:
        return

    perms = message.author.guild_permissions
    if not is_mod(message.author):
        return

    configs = await get_guild_configs(message.guild.id)
    channel = message.guild.get_channel(configs.post_channel)

    if not (configs.guild_id and channel and channel.permissions_for(message.guild.me).send_messages):
        await message.channel.send("This guild has not been (or is improperly) set up. Please use the `setup` command to get started.")
        return

    num = configs.latest_event_count

    arg = args.split(None, 1)

    offset = configs.offset

    try:
        num = get_case_number(arg[0], num, offset, allow_case_range=True)
    except ValueError as e:
        await message.channel.send(str(e))
        return


    if len(arg) < 2:
        await message.channel.send("No reason was given!")
        return

    reason = arg[1]

    events = await bot.db.fetch("SELECT * FROM events WHERE guild_id = $1 AND event_id BETWEEN $2 AND $3;", message.guild.id, num[0], num[-1])
    if not events:
        await message.channel.send("!!! That event doesn't exist. You shouldn't be seeing this. Please contact the bot maintainer.")
        return

    events = [Event.from_row(e, message.author, reason) for e in events]

    event_perms = set()
    if perms.ban_members:
        event_perms.update({"ban", "unban"})
    if perms.kick_members:
        event_perms.update({"kick"})
    if perms.manage_roles:
        event_perms.update({"role_add", "role_remove"})

    for e in events:
        if not e.event_type in event_perms:
            msg = "You have insufficient permissions to update that reason."
            if len(events) > 1:
                msg = f"You have insufficient permissions to update at least one of those reasons. (Check halted at case {e.count+offset})"
            await message.channel.send(msg)
            return

    if len(events) > 3:
        await message.channel.send(f"This will update cases **{num[0]+offset}** to **{num[-1]+offset}**.\nAre you sure you want to update **{len(events)}** cases? (Say `{len(events)}` to confirm)")

        def check(m):
            return (m.author.id == message.author.id and
                    m.channel.id == message.channel.id)

        try:
            msg = await bot.wait_for("message", check=check)
        except asyncio.TimeoutError:
            return
        
        if (not msg.content) or msg.content.lower() != str(len(events)):
            await message.channel.send("Reason aborted.")
            return

    msgs = []
    for e in events:
        msg = e.message_id
        if msg:
            msg = await util.get_message(bot, channel, msg)
            if msg:
                msgs += [(msg, e)]
    
    await bot.db.execute(f"""
    UPDATE events
    SET reason = $1,
    actor = $2
    WHERE guild_id = $3
    AND event_id BETWEEN $4 AND $5;
    """, reason, message.author.id, message.guild.id, num[0], num[-1])

    ret = "👌"
    if len(events) > 1:
        ret += f"\nUpdated **{len(events)}** cases."

    async with message.channel.typing():
        for m in msgs:
            await update_entry(m[0], m[1], configs)
    
    if len(events) != len(msgs):
        msg = f"\nUnfortunately, the message tied to this case cannot be found. Please `recall` this case to resend it. (Case {num[0]+offset})"
        if len(events) > 1:
            msg = f"\n\nUnfortunately, at least one message tied to these cases cannot be found. Please `recall` the missing cases to resend it. (Check cases {num[0]+offset} to {num[-1]+offset})"
        ret += msg

    await message.channel.send(ret)
    return True    

async def recall(message, args, **kwargs):
    if not args:
        return

    configs = await get_guild_configs(message.guild.id)
    channel = message.guild.get_channel(configs.post_channel)

    if not (configs.guild_id and channel and channel.permissions_for(message.guild.me).send_messages):
        return

    num = configs.latest_event_count

    try:
        num = get_case_number(args, num, configs.offset)
    except ValueError as e:
        await message.channel.send(str(e))
        return

    event = await bot.db.fetchrow("SELECT * FROM events WHERE guild_id = $1 AND event_id = $2;", message.guild.id, num)
    if not event:
        await message.channel.send("!!! That event doesn't exist. You shouldn't be seeing this. Please contact the bot maintainer.")
        return
    
    new_entry = Event.from_row(event)

    msg = event.get("message_id")
    if msg:
        msg = await util.get_message(bot, channel, msg)

    ret = None

    if not msg:
        ret = "This entry has been deleted. Please ask a mod to run this command to reinstate it."
        if is_mod(message.author):
            ret = "This entry has been reinstated."
            actor = await util.get_member(bot, event.get("actor"))
            new_entry.set_actor(actor)
            msg = await post_entries([new_entry], channel, configs)
            msg = msg[0]

    if msg:
        embed = discord.Embed(
            title=ret,
            color=util.get_color(message.guild.me),
            description="\n".join([e if i != 0 else " | ".join([v if u != e.count(" | ") else f"[{v}]({msg.jump_url})" for u, v in enumerate(e.split(" | "))]) for i, e in enumerate(msg.content.split("\n"))]), # this is so bad aaaaaaaaaaa
            timestamp=new_entry.timestamp
            )
        await message.channel.send(embed=embed)
    else:
        await message.channel.send(ret)
    return True

async def setup(message, args, **kwargs):
    if not message.author.guild_permissions.manage_guild:
        await message.channel.send("You require the `MANAGE_GUILD` permission to use this command!")
        return
    
    configs = await get_guild_configs(message.guild.id)

    if not args:
        if not (message.channel.permissions_for(message.guild.me).embed_links and message.channel.permissions_for(message.guild.me).attach_files):
            await message.channel.send("I require the `EMBED_LINKS` and `ATTACH_FILES` permissions to use this command!")
            return
            
        embed = discord.Embed(color=util.get_color(message.guild.me))
    
        config_export = "None generated."

        files = []
        if configs.guild_id:
            config_export = configs.export()

        if len(config_export) > 1024:
            b = BytesIO()
            b.write(config_export.encode("utf-8"))
            b.seek(0)
            config_export = "This string was too long to send. Please check the uploaded file."
            files += [discord.File(b, "config_export.txt")]

        embed.add_field(name="Config Export", value=config_export)

        guild_file = None
        guild_export = {
            "roles": [[i.name, str(i.id), str(i.color)] for i in sorted(message.guild.roles, key=lambda x: x.position, reverse=True) if i.id != message.guild.id],
            "channels": [[i.name, str(i.id)] for i in message.guild.text_channels if i.permissions_for(message.guild.me).send_messages]
        }

        guild_export = encode(json.dumps(guild_export))
        
        full_guild_export = guild_export

        if len(guild_export) > 2048:
            b = BytesIO()
            b.write(guild_export.encode("utf-8"))
            b.seek(0)
            guild_export = "This string was too long to send. Please check the uploaded file."
            files += [discord.File(b, "guild_data_export.txt")]
        
        elif len(guild_export) > 1024:
            embed.title = "Guild Data Export (Full code)"
            embed.description = guild_export
            guild_export = "This string was too long to put in here. Please check the long bit of text above."

        embed.add_field(name="Guild Data Export", value=guild_export)

        ret = "Welcome to the ⌚ setup!\nPlease go to https://sink.discord.bot/⌚ to generate an import code!\nRun this command with the Import config to set up the bot on this guild."
        if len(full_guild_export) <= 2000 and message.author.is_on_mobile():
            ret += "\n\nI am detecting that you are currently on a mobile device. React to this message with ☎ (`telephone`) to receive a DM with the data that can easily be copied."
        
        msg = await message.channel.send(ret, embed=embed, files=files)

        if len(full_guild_export) <= 2000:
            def check(reaction, user):
                return (reaction.message.id == msg.id and
                        reaction.emoji == "☎" and
                        user.id == message.author.id)

            try:
                reaction, user = await bot.wait_for("reaction_add", check=check)
            except asyncio.TimeoutError:
                return
            
            if reaction:
                try:
                    await message.author.send(full_guild_export)
                except:
                    await message.channel.send("DM failed. Please ensure your DMs are enabled and run the command again.")
    
        return True
    
    else:
        if not (message.channel.permissions_for(message.guild.me).embed_links and message.channel.permissions_for(message.guild.me).add_reactions):
            await message.channel.send("I require the `EMBED_LINKS` and `ADD_REACTIONS` permissions to use this command!")
            return
        
        channel = None
        try:
            args = json.loads(decode(args))
            args["guild_id"] = message.guild.id
            args["post_channel"] = configs.post_channel
            args["special_roles"] = [int(r) for r in args["roles"]]
            args["prefix"] = args["prefix"].strip()[:32] if args["prefix"] else None
            args["options"] = int(args["options"])
            offset = 0 if not args["offset"] else args["offset"]
            args["_offset"] = max(0, min(2147483647, int(offset)) - 1)
            
            if not configs.guild_id:
                args["post_channel"] = int(args["channel"])
                channel = message.guild.get_channel(args["post_channel"])
                if not channel:
                    raise ValueError
        except:
            await message.channel.send("Invalid input!")
            return
        
        if configs:
            args["offset"] = configs.offset

        emotes = ["✅", "❎"]

        args = Configs.from_row(args)

        msg = await message.channel.send("Here are your imported settings! Please react with ✅ to confirm them. (You can check then again later with the `settings` command)", embed=args.as_embed(message.guild))

        for e in emotes:
            await msg.add_reaction(e)

        def check(reaction, user):
            return (reaction.message.id == msg.id and
                    reaction.emoji in emotes and
                    user.id == message.author.id)

        try:
            reaction, user = await bot.wait_for("reaction_add", check=check)
        except asyncio.TimeoutError:
            return
        
        if reaction.emoji == "✅":
            await bot.db.execute("""
            INSERT INTO guild_configs (
            guild_id, post_channel, prefix, options, latest_event_count, special_roles, recent_events, _offset
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (guild_id)
            DO UPDATE SET
                prefix = EXCLUDED.prefix,
                options = EXCLUDED.options,
                special_roles = EXCLUDED.special_roles
            ;""", *args.db_insert())

            bot._guild_prefix_cache[message.guild.id] = args.prefix

            await message.channel.send("Your settings have been updated.")
        else:
            await message.channel.send("Process aborted.")

    return True

async def settings(message, **kwargs):
    if not is_mod(message.author):
        return

    configs = await get_guild_configs(message.guild.id)
    if not configs:
        return

    if not message.channel.permissions_for(message.guild.me).embed_links:
        await message.channel.send("I require the `EMBED_LINKS` permission to use this command!")
        return
    
    await message.channel.send(f"Settings for **{message.guild.name}**: (You can use the `setup` command to change them)", embed=configs.as_embed(message.guild))
    return True

async def reset(message, **kwargs):
    if is_mod(message.author):
        if message.author.id != message.guild.owner.id:
            await message.channel.send("Only the server owner can run this command!")
            return

    configs = await get_guild_configs(message.guild.id)
    if not configs.guild_id:
        await message.channel.send("You have nothing to reset.")
        return

    return await _reset(message, configs)

async def _reset(message, configs):

    if not configs:
        configs = await get_guild_configs(message.guild.id)

    await message.channel.send("**!! WARNING !!**\nDANGER ZONE\n**!! WARNING !!**\n\nThis command will delete all bot configs and events related to this guild. All already-logged messages will be dissociated and uneditable.\n\n**Are you sure you want to do this?**\nEnter `Yes, please wipe everything` to confirm.")
    
    def check(m):
        return (m.author.id == message.author.id and
                m.channel.id == message.channel.id)

    try:
        msg = await bot.wait_for("message", check=check)
    except asyncio.TimeoutError:
        return
    
    if (not msg.content) or msg.content.lower() != "yes, please wipe everything":
        await message.channel.send("Reset aborted.")
        return
    
    channel = message.guild.get_channel(configs.post_channel)
    if channel and channel.permissions_for(message.guild.me):
        await channel.send("**==================**\nGood night, sweet prince\n**==================**")
    
    await bot.db.execute("DELETE FROM events WHERE guild_id = $1;", message.guild.id)
    await bot.db.execute("DELETE FROM guild_configs WHERE guild_id = $1;", message.guild.id)

    await message.channel.send(f"Data deleted. For postierity, your guild settings were:\n```\n{configs.export()}\n```")

    return True


async def invite(message, **kwargs):
    await message.channel.send(f"<https://discordapp.com/oauth2/authorize?client_id={cfg['bot_id']}&scope=bot&permissions=128>")
    return True

cmds = {
    "time": time,
    "eval": evaluate,
    "sudo": sudo,
    "quit": close,
    "reason": reason,
    "recall": recall,
    "setup": setup,
    "settings": settings,
    "reset": reset,
    "invite": invite,
}

bot.run(cfg["token"])