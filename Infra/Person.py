class Person:

    def __init__(self, pid, x, y, expires):
        self.pid = pid
        self.x = x
        self.y = y
        self.expires = expires
        self.rescued = False
        return

    def __repr__(self):
        return 'P%d:(%d,%d,%d)' % (self.pid, self.x, self.y, self.expires)

    def prettify(self):
        return {self.pid: (self.x, self.y, self.expires)}