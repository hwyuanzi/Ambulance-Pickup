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
        if len(pers) > 4:
            raise IllegalPlanError('Ignoring line as cannot rescue more than four people at once: %s.' % pers)
        already_rescued = list(filter(lambda p: p.rescued, pers))
        if already_rescued:
            print('Person %s already rescued.' % already_rescued)
        # t: time when end hospital is reached
        rescue_end_time = start_time + 1
        start = self
        for p in pers:
            rescue_end_time += take_time(start, p)
            start = p

        rescue_end_time += len(pers) #Add one minute pickup time per person
        rescue_end_time += take_time(start, end_hospital) #Add time to reach end hospital

        self.amb_time.sort()
        rescued_persons = []
        for (index, t0) in enumerate(self.amb_time): #why not just take first one since it's sorted.
            if (t0 > start_time):
                continue
            rescued_persons = list(set(filter(lambda p: p.expires >= rescue_end_time and p.rescued is False, pers)))
            break

        #Update hosppitals
        self.amb_time.pop(index)
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