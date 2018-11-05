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