class MergeError(Exception):
    pass
class FastForwardError(Exception):
    pass
class Mismatch(MergeError):
    pass
class Unmergeable(MergeError):
    pass

class Skip(MergeError):
    pass
