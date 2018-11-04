class Event():
    def __init__(self, guild_id, event_type, target_id, target_name, actor, reason, role_id, role_name, count=None):
        self.guild_id = guild_id
        self.event_type = event_type
        self.target_id = target_id
        self.target_name = target_name
        self.actor = actor
        self.reason = reason
        self.role_id = role_id
        self.role_name = role_name
        self.count = count
    
    def set_count(self, count):
        self.count = count

    def db_insert(self):
        return (self.guild_id, self.event_type, self.target_id, self.target_name, self.actor.id, self.reason, self.role_id, self.role_name, self.count)