class Event():
    def __init__(self, guild_id, event_type, target_id, target_name, actor, reason, timestamp, role_id=None, role_name=None, count=None, message_id=None):
        self.guild_id = guild_id
        self.event_type = event_type
        self.target_id = target_id
        self.target_name = target_name
        self.actor = actor
        self.reason = reason
        self.timestamp = timestamp
        self.role_id = role_id
        self.role_name = role_name
        self.count = count
        self.message_id = message_id
    
    @classmethod
    def from_row(cls, row, actor=None, reason=None):
        return cls(row.get("guild_id"), row.get("event_type"), row.get("target_id"), row.get("target_name"), row.get("actor") if not actor else actor, row.get("reason") if not reason else reason, row.get("timestamp"), row.get("role_id"), row.get("role_name"), row.get("event_id"), row.get("message_id"))
    
    def set_actor(self, actor):
        self.actor = actor
        
    def set_count(self, count):
        self.count = count

    def db_insert(self):
        return (self.guild_id, self.event_type, self.target_id, self.target_name, self.actor if type(self.actor) == int else self.actor.id, self.reason, self.timestamp, self.role_id, self.role_name, self.count)