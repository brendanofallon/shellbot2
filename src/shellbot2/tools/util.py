

class classproperty:
    def __init__(self, func):
        self.fget = func
    
    def __get__(self, obj, owner):
        return self.fget(owner)