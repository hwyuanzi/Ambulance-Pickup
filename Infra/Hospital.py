from Infra.exceptions import IllegalPlanError

def take_time(a, b):
    return abs(a.x - b.x) + abs(a.y - b.y)

class Hospital:

    def __init__(self, hid, x, y, num_amb):
        self.hid = hid
        self.x = x
        self.y = y
        # amb_time array represents the time at which each ambulance ended its last tour.
        self.amb_time = [0] * num_amb
        return

    def __repr__(self):
        return 'H%d:(%d,%d)' % (self.hid, self.x, self.y)

    def prettify(self):
        return {self.hid: (self.x, self.y)}

    def rescue(self, pers, end_hospital, start_time):
        if len(self.amb_time) == 0:
            raise IllegalPlanError('No ambulance left at the hospital %s.' % self)
        else:
            self.amb_time.sort()
            if self.amb_time[0] > start_time:
                raise IllegalPlanError('No ambulance left at hospital %s at the start time %d minutes.' % (self, start_time))
        already_rescued = list(filter(lambda p: p.rescued, pers))
        if already_rescued:
            print('Person %s already rescued.' % already_rescued)
        # Only living people occupy seats. A patient remains alive through their
        # expiration minute, including an unload completed at that minute.
        time = start_time
        location = self
        onboard = []
        for p in pers:
            time += take_time(location, p) + 1  # Travel, then one minute to pick up.
            onboard = [rider for rider in onboard if rider.expires >= time]
            if not p.rescued and p.expires >= time:
                onboard.append(p)
            if len(onboard) > 4:
                raise IllegalPlanError('Ignoring line as ambulance capacity is four living patients: %s.' % pers)
            location = p

        rescue_end_time = time + take_time(location, end_hospital) + 1  # Unload.

        self.amb_time.sort()
        rescued_persons = list(set(filter(lambda p: p.expires >= rescue_end_time and p.rescued is False, pers)))

        #Update hosppitals
        self.amb_time.pop(0)
        end_hospital.amb_time.append(rescue_end_time)

        

        for (i,p) in enumerate(rescued_persons):
            p.rescued = True
        if len(rescued_persons)==0:
            print('Nobody will make it by end of %d minutes.' % rescue_end_time)
        else:
            if len(rescued_persons)==len(pers):
                print('Rescued:', ' and '.join(map(str, rescued_persons)), 'at', rescue_end_time , 'minutes | Ended at Hospital', end_hospital)
            else:
                print('Rescued:', ' and '.join(map(str, rescued_persons)), 'at', rescue_end_time, 'minutes | Ended at Hospital', end_hospital, "| Rest could not make it in time.")
        return rescued_persons
