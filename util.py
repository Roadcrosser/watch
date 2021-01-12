import discord
import codecs
import base64

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
            user = await bot.fetch_user(member_id)
        except:
            pass
    
    if not user:
        user = discord.Object(id=member_id)
        user.name = "Deleted User"
        user.discriminator = "0000"

    return user

def get_color(member):
    return member.color if member.color.value != 0 else discord.Embed.Empty

def encode(text):
    return base64.b64encode(codecs.encode(text.encode("utf-8"), "zlib")).decode()

def decode(text):
    return codecs.decode(base64.b64decode(text.encode("utf-8")), "zlib").decode()