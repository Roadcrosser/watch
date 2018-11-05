import discord

async def get_message(bot, channel, message_id):
    pred = lambda m: m.id == message_id
    for m in bot._connection._messages:
        if pred(m):
            return m
            
    try:
        o = discord.Object(id=message_id + 1)
        # don't wanna use get_message due to poor rate limit (1/1s) vs (50/1s)
        msg = await channel.history(limit=1, before=o).next()

        if not pred(msg):
            return None

        return msg
    except Exception:
        return None

async def get_member(bot, member_id):
    user = bot.get_user(member_id)
    
    if not user:
        try:
            user = await bot.get_user_info(member_id)
        except:
            pass
    
    if not user:
        user = discord.Object(id=member_id)
        user.name = "Deleted User"
        user.discriminator = "0000"

    return user

def message_link(message=None, guild_id=None, channel_id=None, message_id=None):
    args = [guild_id, channel_id, message_id]
    if message:
        args = [message.guild.id, message.channel.id, message.id]
    return "https://discordapp.com/channels/{}/{}/{}".format(*args)