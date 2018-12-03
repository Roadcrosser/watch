class Options():
    # also figure out a way to make this code easier to update ig
    # 1 - reveal invites (don't obfuscate them)
    # 2 - ping the target on mod events

    def __init__(self, value):
        if value == None:
            value = 0
        self.reveal_invites = value & 0b01 == 0b01
        self.ping_target = value & 0b10 == 0b10
    
    def as_num(self):
        ret = 0
        for i, e in enumerate([self.reveal_invites, self.ping_target]):
            if e:
                ret += 2**i
        
        return ret