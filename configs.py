from options import Options
from util import encode
import json
import discord

class Configs():
    def __init__(self, guild_id, post_channel, options, prefix, offset, roles, latest_event_count):
        self.guild_id = guild_id
        self.post_channel = post_channel
        self.options = options
        self.prefix = prefix
        self.offset = offset
        self.roles = roles
        self.latest_event_count = latest_event_count

    @classmethod
    def from_row(cls, row):
        return cls(row.get("guild_id"), row.get("post_channel"), Options(row.get("options")), row.get("prefix"), row.get("_offset"), row.get("special_roles", []), row.get("latest_event_count"))

    def db_insert(self):
        return (self.guild_id, self.post_channel, self.prefix, self.options, 0, self.roles, [], self.offset)
    
    def export(self):
        return encode(json.dumps({
                "roles": [str(i) for i in self.roles],
                "channel": str(self.post_channel) if self.post_channel else None,
                "options": self.options.as_num(),
                "prefix": self.prefix,
                "offset": self.offset + 1
                    }))
    
    def as_embed(self, guild):
        embed = discord.Embed(color=guild.me.color)
        embed.add_field(name="Channel", value=f"<#{self.post_channel}>")

        guild_roles = {i.id for i in guild.roles}
        roles = [f"<@&{i}>" for i in self.roles if i in guild_roles]
        roles = "\n".join(roles) if roles else "None"

        embed.add_field(name="Roles", value=roles)

        option_text = f"{'✅' if self.options.reveal_invites else '❎'} **Reveal Invites**"
        option_text += f"\n{'✅' if self.options.ping_target else '❎'} **Ping Target**"

        embed.add_field(name="Options", value=option_text)
        embed.add_field(name="Custom Prefix", value=self.prefix)
        embed.add_field(name="Count Offset", value=self.offset + 1)
        return embed